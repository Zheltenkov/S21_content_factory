"""Endpoint для отдельной проверки пользовательского README по рубрике."""

import asyncio
import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db.user_runs_db import upsert_user_run
from api.db.logging_db import write_log_async
from api.dependencies import get_current_user
from api.utils.logger import get_logger
from api.utils.logging_context import set_request_id, set_user_id
from content_gen.llm.factory import create_llm_client
from content_gen.utils.rubric_export import criteria_to_json
from content_gen.validators.rubric import RubricScorer
from utils.token_counter import count_tokens

logger = get_logger("readme_check")
router = APIRouter()


def calculate_text_stats(text: str) -> dict[str, Any]:
    """Простая статистика по тексту README."""
    chars = len(text or "")
    words = len((text or "").split())
    sentences = len(re.split(r"[.!?]+", text or "")) - 1
    if sentences < 0:
        sentences = 0
    tokens = count_tokens(text or "")
    return {
        "chars": chars,
        "words": words,
        "sentences": sentences,
        "tokens": tokens,
    }


def _readme_title(markdown: str) -> str:
    """Extract a compact title for dashboard history without running a parser."""
    for line in (markdown or "").splitlines():
        clean = line.strip()
        if clean.startswith("# "):
            return clean.lstrip("#").strip()[:160] or "Проверка README"
    return "Проверка README"


def _rubric_score(rubric: dict[str, Any]) -> dict[str, Any]:
    total = rubric.get("total")
    maximum = rubric.get("max_score") or rubric.get("max")
    label = f"{total}/{maximum}" if total is not None and maximum is not None else "—"
    return {"total": total, "max": maximum, "label": label}


class CheckReadmeRequest(BaseModel):
    """Запрос на проверку пользовательского README."""

    markdown: str = Field(..., description="Содержимое README в формате Markdown")
    language: str = Field("ru", description="Язык README (ru/en/...)")
    llm_provider: Literal["polza", "openrouter", "openai", "deepseek", "gigachat"] | None = Field(
        default=None,
        description="Предпочитаемый LLM provider для AI-критериев",
    )
    learning_outcomes: list[str] | None = Field(
        default=None,
        description="Необязательный список образовательных результатов для критериев 2.x",
    )


class CheckReadmeResponse(BaseModel):
    """Ответ с результатами проверки README."""

    request_id: str | None = None
    rubric: dict[str, Any]
    text_stats: dict[str, Any]


@router.post("/readme/check", response_model=CheckReadmeResponse)
async def check_readme(
    request: CheckReadmeRequest,
    user: dict = Depends(get_current_user),
) -> CheckReadmeResponse:
    """
    Проверяет произвольный README по всем критериям рубрики.

    Не выполняет генерацию, только анализ уже готового Markdown.
    """
    if not request.markdown or not request.markdown.strip():
        raise HTTPException(status_code=400, detail="Поле markdown не должно быть пустым")

    request_id = str(uuid.uuid4())
    user_id = user.get("id", "anonymous")

    # Контекст логирования
    set_request_id(request_id)
    set_user_id(user_id)

    logger.info("🔎 Начало проверки пользовательского README")
    await write_log_async(
        request_id=request_id,
        level="INFO",
        message="Начало проверки пользовательского README",
        user_id=user_id,
        phase="readme_check",
        metadata={
            "markdown_length": len(request.markdown or ""),
            "language": request.language,
            "llm_provider": request.llm_provider,
            "learning_outcomes_count": len(request.learning_outcomes or []),
        },
    )

    try:
        # LLM клиент передается в RubricScorer для AI‑критериев
        try:
            llm_client = create_llm_client(
                provider=request.llm_provider,
                default_role="critic",
                enable_cache=True,
                enable_batching=True,
                user_id=user_id,
                run_id=request_id,
            )
            logger.info("✅ LLM клиент создан успешно")
        except Exception as e:
            logger.error(f"❌ Ошибка создания LLM клиента: {e}", exc_info=True)
            # Создаем scorer без LLM клиента (некоторые критерии не будут работать)
            llm_client = None
            logger.warning("⚠️ Продолжаем проверку без LLM клиента (некоторые AI-критерии будут пропущены)")

        scorer = RubricScorer(language=request.language, llm_client=llm_client)

        rubric_report = await asyncio.to_thread(
            scorer.score,
            request.markdown,
            learning_outcomes=request.learning_outcomes or None,
        )
        rubric_json = criteria_to_json(rubric_report)
        text_stats = calculate_text_stats(request.markdown)
        await asyncio.to_thread(
            upsert_user_run,
            request_id=request_id,
            user_id=user_id,
            kind="checker",
            status="completed",
            title=_readme_title(request.markdown),
            score=_rubric_score(rubric_json),
            metadata={"language": request.language, "markdown_length": len(request.markdown or "")},
        )

        await write_log_async(
            request_id=request_id,
            level="INFO",
            message="Проверка README завершена успешно",
            user_id=user_id,
            phase="readme_check_complete",
            metadata={
                "total_score": rubric_json.get("total"),
                "max_score": rubric_json.get("max_score"),
            },
        )

        return CheckReadmeResponse(request_id=request_id, rubric=rubric_json, text_stats=text_stats)

    except Exception as e:
        logger.error("❌ Ошибка проверки README: %s", e, exc_info=True)
        await asyncio.to_thread(
            upsert_user_run,
            request_id=request_id,
            user_id=user_id,
            kind="checker",
            status="failed",
            title=_readme_title(request.markdown),
            metadata={"error": str(e), "language": request.language},
        )
        await write_log_async(
            request_id=request_id,
            level="ERROR",
            message=f"Ошибка проверки README: {e}",
            user_id=user_id,
            phase="readme_check_error",
            metadata={"error_type": type(e).__name__, "error_message": str(e)},
        )
        raise HTTPException(status_code=500, detail=f"Ошибка проверки README: {e}")



