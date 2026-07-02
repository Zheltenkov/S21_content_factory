"""Endpoint для перегенерации контента."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.db.logging_db import write_log_async
from api.dependencies import get_current_user
from api.utils.logger import get_logger
from api.utils.logging_context import set_request_id, set_user_id
from api.utils.result_cache import get_generation_owner
from api.services.regeneration_service import (
    RegenerationCommand,
    RegenerationService,
    RegenerationValidationError,
)

logger = get_logger("regeneration")
router = APIRouter()
_regeneration_service = RegenerationService()


class RegenerateRequest(BaseModel):
    """Запрос на перегенерацию контента."""
    original_request_id: str | None = None  # ID оригинального запроса (для сохранения в кэш)
    original_md: str
    comments: str
    language: str = "ru"
    project_seed: dict[str, Any] | None = Field(
        default=None,
        description="Текущий ProjectSeed из формы/УП. Основной источник контекста для перегенерации.",
    )
    curriculum_project: dict[str, Any] | None = Field(
        default=None,
        description="Проект из учебного плана, если seed еще не собран на клиенте.",
    )


class RegenerateResponse(BaseModel):
    """Ответ с результатом перегенерации."""
    request_id: str
    regenerated_md: str
    changes: list[str]
    rubric: dict[str, Any]
    text_stats: dict[str, Any]
    learning_outcomes: list[str] = Field(default_factory=list, description="Извлеченные образовательные результаты")
    skills: list[str] = Field(default_factory=list, description="Извлеченные навыки")
    seed_source: str | None = Field(default=None, description="Источник ProjectSeed для перегенерации")
    learning_context_source: str | None = Field(default=None, description="Источник learning_outcomes/skills")
    accepted: bool = Field(default=True, description="Применена ли перегенерация к текущему результату")
    warnings: list[str] = Field(default_factory=list, description="Предупреждения, которые нужно показать пользователю")
    rubric_regression: dict[str, Any] | None = Field(
        default=None,
        description="Детали ухудшения rubric, если перегенерация не была применена",
    )
    validation_report: dict[str, Any] | None = Field(
        default=None,
        description="Schema-first отчёт: выбранные секции, патчи, применение и предупреждения",
    )


@router.post("/regenerate", response_model=RegenerateResponse)
async def regenerate(
    request: RegenerateRequest,
    user: dict = Depends(get_current_user)
):
    """
    Перегенерирует контент на основе комментариев.
    
    Args:
        request: Запрос с оригинальным markdown и комментариями
        user: Данные пользователя из аутентификации
        
    Returns:
        Результат перегенерации
    """
    request_id = str(uuid.uuid4())
    user_id = user.get("id", "anonymous")

    # Устанавливаем контекст для логирования
    set_request_id(request_id)
    set_user_id(user_id)
    if request.original_request_id:
        owner_id = get_generation_owner(request.original_request_id)
        if owner_id and owner_id != user_id:
            raise HTTPException(status_code=403, detail="Нет доступа к запуску другого пользователя")
        if not owner_id:
            raise HTTPException(status_code=403, detail="Владелец исходного запуска не определен")

    try:
        result = await _regeneration_service.regenerate(
            RegenerationCommand(
                request_id=request_id,
                user_id=user_id,
                original_request_id=request.original_request_id,
                original_md=request.original_md,
                comments=request.comments,
                language=request.language,
                project_seed=request.project_seed,
                curriculum_project=request.curriculum_project,
            )
        )
        return RegenerateResponse(
            request_id=result.request_id,
            regenerated_md=result.regenerated_md,
            changes=result.changes,
            rubric=result.rubric,
            text_stats=result.text_stats,
            learning_outcomes=result.learning_outcomes,
            skills=result.skills,
            seed_source=result.seed_source,
            learning_context_source=result.learning_context_source,
            accepted=result.accepted,
            warnings=result.warnings,
            rubric_regression=result.rubric_regression,
            validation_report=result.validation_report,
        )
    except RegenerationValidationError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    except Exception as e:
        logger.error("❌ Ошибка перегенерации: %s", str(e), exc_info=True)
        await write_log_async(
            request_id=request_id,
            level="ERROR",
            message=f"Ошибка перегенерации: {str(e)}",
            user_id=user_id,
            phase="regeneration_error",
            metadata={"error_type": type(e).__name__, "error_message": str(e)}
        )
        raise HTTPException(status_code=500, detail="Ошибка перегенерации") from e
