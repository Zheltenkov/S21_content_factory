"""Persistence component for completed generation results."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from api.db.generation_results_db import save_generation_result
from api.db.logging_db import write_log_async
from api.utils.logger import get_logger
from api.utils.result_cache import set_generation_status, store_generation_error, store_result
from content_gen.utils.latex_validator import build_latex_agent_hint, collect_latex_issues
from content_gen.utils.markdown_display_normalizer import normalize_markdown_display_blocks

logger = get_logger("generation")


class GenerationResultPersister:
    """Validate and persist completed generation artifacts."""

    def __init__(
        self,
        *,
        status_setter: Callable[[str, str], Any] = set_generation_status,
        error_store: Callable[[str, str], Any] = store_generation_error,
        result_store: Callable[..., Any] = store_result,
        result_saver: Callable[..., Any] = save_generation_result,
        log_writer: Callable[..., Awaitable[Any]] = write_log_async,
    ) -> None:
        self._status_setter = status_setter
        self._error_store = error_store
        self._result_store = result_store
        self._result_saver = result_saver
        self._log_writer = log_writer

    async def save_completed_generation(
        self,
        *,
        request_id: str,
        user_id: str,
        project_seed_payload: dict[str, Any],
        result: Any,
    ) -> bool:
        """Validate Markdown, persist generation result and write completion logs."""
        markdown = normalize_markdown_display_blocks(result.report_json.get("markdown", ""))
        result.report_json["markdown"] = markdown
        latex_issues = collect_latex_issues(markdown)
        if latex_issues:
            issue_text = "; ".join(latex_issues[:3])
            agent_hint = build_latex_agent_hint(latex_issues)
            logger.error("❌ Ошибка валидации LaTeX: %s", issue_text)
            await self._log_writer(
                request_id=request_id,
                level="ERROR",
                message="Найдены ошибки LaTeX в сгенерированном README",
                user_id=user_id,
                phase="validation",
                metadata={"issues": latex_issues[:5], "agent_hint": agent_hint},
            )
            error_msg = f"Найдены проблемы с LaTeX формулами: {issue_text}. Подсказка для агента: {agent_hint}"
            self._status_setter(request_id, "failed")
            self._error_store(request_id, error_msg)
            return False

        self._result_store(
            request_id,
            result,
            user_id=user_id,
            project_seed_payload=project_seed_payload,
        )

        try:
            await asyncio.to_thread(
                self._result_saver,
                request_id,
                user_id,
                project_seed_payload,
                markdown,
                result.report_json.get("rubric"),
                result.report_json,
                result.report_json.get("text_stats"),
                result.report_json.get("task_plan"),
                result.report_json.get("issues"),
                result.practice_critic_issues,
                result.agent_config_versions,
                result.flow_trace,
            )
            logger.info("🗄️ Результат %s сохранён в БД", request_id)
        except Exception as db_err:  # noqa: BLE001
            logger.warning("⚠️ Не удалось сохранить результат в БД: %s", db_err)

        markdown_len = len(result.report_json.get("markdown", ""))
        logger.info("✅ Генерация завершена успешно: markdown=%s символов", markdown_len)
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Генерация контента завершена успешно",
            user_id=user_id,
            phase="completion",
            metadata={
                "agent_config_versions": result.agent_config_versions,
                "practice_critic_issues": len(result.practice_critic_issues or []),
                "flow_trace": result.flow_trace,
                "node_traces": result.report_json.get("node_traces", []),
            },
        )
        return True
