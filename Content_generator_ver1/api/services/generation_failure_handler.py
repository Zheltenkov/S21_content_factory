"""Failure handling policy for generation background jobs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from api.db.logging_db import write_log_async
from api.utils.logger import get_logger
from api.utils.result_cache import set_generation_status, store_generation_error
from content_gen.exceptions import (
    ContentGenerationError,
    LLMAPIError,
    LLMRateLimitError,
    LLMTimeoutError,
    ValidationError,
)

from .generation_pause_persistence import MethodologyPausePersister
from .generation_workflow_service import GenerationWorkflowService

logger = get_logger("generation")


class GenerationFailureHandler:
    """Map technical generation exceptions to durable status, logs and UI messages."""

    def __init__(
        self,
        *,
        status_setter: Callable[[str, str], Any] = set_generation_status,
        error_store: Callable[[str, str], Any] = store_generation_error,
        log_writer: Callable[..., Awaitable[Any]] = write_log_async,
        pause_persister: MethodologyPausePersister | None = None,
        workflow_service: GenerationWorkflowService | None = None,
    ) -> None:
        self._status_setter = status_setter
        self._error_store = error_store
        self._log_writer = log_writer
        self._pause_persister = pause_persister
        self._workflow_service = workflow_service or GenerationWorkflowService()

    async def handle_validation_error(self, request_id: str, user_id: str, exc: ValidationError) -> None:
        """Handle deterministic validation failures."""
        logger.error("❌ Ошибка валидации: %s", str(exc))
        await self._log_writer(
            request_id=request_id,
            level="ERROR",
            message=f"Ошибка валидации: {str(exc)}",
            user_id=user_id,
            phase="validation_error",
            metadata={"error_type": type(exc).__name__, "error_message": str(exc), "context": exc.context},
        )
        self._status_setter(request_id, "failed")
        self._error_store(request_id, f"Ошибка валидации: {str(exc)}")
        self._workflow_service.mark_failed(request_id=request_id, user_id=user_id, error=f"Ошибка валидации: {str(exc)}")

    async def handle_llm_timeout_or_rate_limit(
        self,
        request_id: str,
        user_id: str,
        exc: LLMTimeoutError | LLMRateLimitError,
    ) -> None:
        """Handle retry-worthy LLM timeout/rate-limit failures."""
        logger.error("⚠️ Ошибка LLM (таймаут/rate limit): %s", str(exc))
        await self._log_writer(
            request_id=request_id,
            level="ERROR",
            message=f"Ошибка LLM: {str(exc)}",
            user_id=user_id,
            phase="llm_error",
            metadata={"error_type": type(exc).__name__, "error_message": str(exc), "context": exc.context},
        )
        self._status_setter(request_id, "failed")
        if isinstance(exc, LLMTimeoutError):
            message = (
                "Генерация заняла слишком много времени (таймаут). Попробуйте снова или упростите объём проекта."
            )
            self._error_store(
                request_id,
                message,
            )
        else:
            message = f"Ошибка LLM сервиса: {str(exc)}"
            self._error_store(request_id, message)
        self._workflow_service.mark_failed(request_id=request_id, user_id=user_id, error=message)

    async def handle_llm_api_error(self, request_id: str, user_id: str, exc: LLMAPIError) -> None:
        """Handle non-timeout LLM provider API failures."""
        await self._log_writer(
            request_id=request_id,
            level="ERROR",
            message=f"Ошибка LLM API: {str(exc)}",
            user_id=user_id,
            phase="llm_error",
            metadata={"error_type": type(exc).__name__, "error_message": str(exc), "context": exc.context},
        )
        self._status_setter(request_id, "failed")
        message = f"Ошибка LLM API: {str(exc)}"
        self._error_store(request_id, message)
        self._workflow_service.mark_failed(request_id=request_id, user_id=user_id, error=message)

    async def handle_content_generation_error(
        self,
        *,
        request_id: str,
        user_id: str,
        project_seed_dict: dict[str, Any],
        track_paths: list[str],
        error: ContentGenerationError,
    ) -> None:
        """Handle domain generation errors, including methodology pauses."""
        logger.error("❌ Ошибка генерации: %s", str(error), exc_info=True)
        if error.context.get("error_type") in {"MethodologyGatePause", "HumanApprovalCheckpoint"}:
            if self._pause_persister is None:
                raise RuntimeError("Methodology pause persister is required for pause errors")
            stored = await self._store_pause_or_fail(
                request_id=request_id,
                user_id=user_id,
                project_seed_dict=project_seed_dict,
                track_paths=track_paths,
                error=error,
                phase="methodology_pause_persistence",
            )
            if stored:
                return
            return

        error_message = str(error)
        user_friendly_message = self.friendly_openai_error(error_message)
        await self._log_writer(
            request_id=request_id,
            level="ERROR",
            message=f"Ошибка генерации: {error_message}",
            user_id=user_id,
            phase="generation_error",
            metadata={
                "error_type": type(error).__name__,
                "error_message": error_message,
                "user_friendly_message": user_friendly_message,
                "context": error.context,
            },
        )
        self._status_setter(request_id, "failed")
        self._error_store(request_id, user_friendly_message)
        self._workflow_service.mark_failed(request_id=request_id, user_id=user_id, error=user_friendly_message)

    async def handle_resume_content_error(
        self,
        *,
        request_id: str,
        user_id: str,
        paused_session: dict[str, Any],
        error: ContentGenerationError,
    ) -> None:
        """Handle domain errors thrown while resuming from a pause."""
        logger.error("❌ Ошибка resume генерации: %s", str(error), exc_info=True)
        if error.context.get("error_type") in {"MethodologyGatePause", "HumanApprovalCheckpoint"}:
            if self._pause_persister is None:
                raise RuntimeError("Methodology pause persister is required for pause errors")
            stored = await self._store_pause_or_fail(
                request_id=request_id,
                user_id=user_id,
                project_seed_dict=paused_session.get("project_seed") or {},
                track_paths=paused_session.get("track_paths") or [],
                error=error,
                phase="resume_methodology_pause_persistence",
            )
            if stored:
                return
            return

        self._status_setter(request_id, "failed")
        self._error_store(request_id, str(error))
        self._workflow_service.mark_failed(request_id=request_id, user_id=user_id, error=str(error))
        await self._log_writer(
            request_id=request_id,
            level="ERROR",
            message=f"Ошибка продолжения генерации: {str(error)}",
            user_id=user_id,
            phase="resume_error",
            metadata={"error_type": type(error).__name__, "context": error.context},
        )

    async def handle_unexpected_error(
        self,
        request_id: str,
        user_id: str,
        exc: Exception,
        *,
        phase: str = "unexpected_error",
        message_prefix: str = "Неожиданная ошибка",
        error_prefix: str = "Внутренняя ошибка сервера",
    ) -> None:
        """Handle unexpected exceptions with a stable UI-facing message."""
        logger.error("💥 %s: %s", message_prefix, str(exc), exc_info=True)
        error_message = str(exc)
        user_friendly_message = self.friendly_openai_error(
            error_message,
            default=f"{error_prefix}: {error_message}",
        )
        await self._log_writer(
            request_id=request_id,
            level="ERROR",
            message=f"{message_prefix}: {error_message}",
            user_id=user_id,
            phase=phase,
            metadata={
                "error_type": type(exc).__name__,
                "error_message": error_message,
                "user_friendly_message": user_friendly_message,
            },
        )
        self._status_setter(request_id, "failed")
        self._error_store(request_id, user_friendly_message)
        self._workflow_service.mark_failed(request_id=request_id, user_id=user_id, error=user_friendly_message)

    async def _store_pause_or_fail(
        self,
        *,
        request_id: str,
        user_id: str,
        project_seed_dict: dict[str, Any],
        track_paths: list[str],
        error: ContentGenerationError,
        phase: str,
    ) -> bool:
        """Persist methodology pause or fail explicitly instead of leaving stale in_progress status."""
        try:
            await self._pause_persister.store_methodology_pause(
                request_id=request_id,
                user_id=user_id,
                project_seed_dict=project_seed_dict,
                track_paths=track_paths,
                error=error,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("💥 Не удалось сохранить методологическую паузу: %s", str(exc), exc_info=True)
            message = (
                "Генерация дошла до контрольной точки методолога, "
                f"но состояние паузы не удалось сохранить: {exc}"
            )
            await self._log_writer(
                request_id=request_id,
                level="ERROR",
                message=message,
                user_id=user_id,
                phase=phase,
                metadata={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "original_context": error.context,
                },
            )
            self._status_setter(request_id, "failed")
            self._error_store(request_id, message)
            self._workflow_service.mark_failed(request_id=request_id, user_id=user_id, error=message)
            return False

    @staticmethod
    def friendly_openai_error(error_message: str, *, default: str | None = None) -> str:
        """Map common OpenAI access failures to clearer UI-facing text."""
        user_friendly_message = default or error_message
        error_str_lower = error_message.lower()
        if (
            "unsupported_country_region_territory" in error_str_lower
            or "country, region, or territory not supported" in error_str_lower
        ):
            return (
                "Ошибка доступа к OpenAI API: ваш регион не поддерживается. "
                "Пожалуйста, используйте VPN или обратитесь к администратору системы."
            )
        if "403" in error_message and ("openai" in error_str_lower or "permission" in error_str_lower):
            return (
                "Ошибка доступа к OpenAI API (403 Forbidden). "
                "Возможные причины: неподдерживаемый регион, проблемы с API ключом или ограничения доступа."
            )
        if "permissiondeniederror" in error_str_lower or "permission denied" in error_str_lower:
            return (
                "Ошибка доступа к OpenAI API: доступ запрещен. "
                "Проверьте настройки API ключа и доступность сервиса в вашем регионе."
            )
        return user_friendly_message
