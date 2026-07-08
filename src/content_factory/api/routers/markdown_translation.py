"""README / Markdown text translation service.

Extracted from ``api/routers/readme_translate.py``: builds the translation seed,
derives the document title, and runs the Markdown/README translation job via the
TranslatorAgent. The ``/translate/readme`` route re-imports the entry points here.
"""

from typing import cast

from content_factory.api.db.user_runs_db import upsert_user_run
from content_factory.api.utils.logger import get_logger
from content_factory.api.utils.result_cache import set_translation_job, set_translation_phase
from content_factory.generation.agents.base.llm_client import LLMClientProtocol
from content_factory.generation.agents.translator import TranslatorAgent
from content_factory.generation.models.schemas import ProjectSeed
from content_factory.platform.llm.factory import create_llm_client

logger = get_logger("translate-readme")

def _markdown_title(markdown: str, fallback: str = "Перевод документа") -> str:
    """Extract a compact dashboard title from the first Markdown H1."""
    for line in (markdown or "").splitlines():
        clean = line.strip()
        if clean.startswith("# "):
            return clean.lstrip("#").strip()[:160] or fallback
    return fallback


def _build_translation_seed(
    *,
    llm_provider: str | None,
    thematic_block: str | None,
    title_seed: str | None,
    project_description: str,
) -> ProjectSeed:
    """Собирает минимальный ProjectSeed для переводческого LLM-контекста."""
    return ProjectSeed(
        language="ru",
        llm_provider=llm_provider,
        project_type="individual",
        thematic_block=thematic_block or "GEN",
        audience_level="base",
        required_tools=[],
        title_seed=title_seed or "",
        project_description=project_description[:1000],
        learning_outcomes=[],
        skills=[],
        tasks_count=None,
        task_complexity=None,
        bonus_wish=None,
        context_track_dir=None,
        last_known_order=None,
        group_size=None,
        repo_base_url=None,
        repo_path_template=None,
        is_programming_project=None,
        target_languages=None,
        zun=None,
    )


def _run_translation(
    request_id: str,
    user_id: str,
    markdown: str,
    target_language: str,
    translation_mode: str,
    seed: ProjectSeed,
) -> None:
    """Синхронный запуск перевода в отдельном потоке; обновляет кэш по завершении."""
    def progress_callback(phase: str) -> None:
        set_translation_phase(request_id, phase)

    llm_client = create_llm_client(
        provider=seed.llm_provider,
        default_role="translator",
        enable_cache=True,
        enable_batching=True,
        user_id=user_id,
        run_id=request_id,
    )
    translator = TranslatorAgent(cast(LLMClientProtocol, llm_client))
    try:
        translated_md = translator.translate(
            markdown,
            target_language,
            seed,
            translation_mode=translation_mode,
            progress_callback=progress_callback,
            strict=True,
        )
        set_translation_job(
            request_id=request_id,
            status="completed",
            user_id=user_id,
            phase="combine" if translation_mode == "combined" else "translate",
            original_markdown=markdown,
            translated_markdown=translated_md,
            target_language=target_language,
        )
        upsert_user_run(
            request_id=request_id,
            user_id=user_id,
            kind="translation",
            status="completed",
            title=_markdown_title(markdown),
            result_url=f"/api/v1/translate/status/{request_id}",
            metadata={"target_language": target_language, "translation_mode": translation_mode},
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Ошибка при переводе README: %s", e, exc_info=True)
        set_translation_job(
            request_id=request_id,
            status="failed",
            user_id=user_id,
            original_markdown=markdown,
            target_language=target_language,
            error=str(e),
        )
        upsert_user_run(
            request_id=request_id,
            user_id=user_id,
            kind="translation",
            status="failed",
            title=_markdown_title(markdown),
            result_url=f"/api/v1/translate/status/{request_id}",
            metadata={"target_language": target_language, "translation_mode": translation_mode, "error": str(e)},
        )

