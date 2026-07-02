"""Persistence component for paused methodology generation sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from api.db.logging_db import write_log_async
from api.db.paused_generation_db import save_paused_generation_session
from api.utils.result_cache import (
    get_generation_methodology,
    set_generation_status,
    store_generation_error,
)
from content_gen.exceptions import ContentGenerationError

from .generation_workflow_service import GenerationWorkflowService
from .methodology_review_artifacts import methodology_human_review_enabled


class MethodologyPausePersister:
    """Persist MethodologyGateInterrupt state and expose needs_review status."""

    def __init__(
        self,
        *,
        status_setter: Callable[[str, str], Any] = set_generation_status,
        error_store: Callable[[str, str], Any] = store_generation_error,
        methodology_getter: Callable[[str], dict[str, Any] | None] = get_generation_methodology,
        paused_saver: Callable[..., Any] = save_paused_generation_session,
        log_writer: Callable[..., Awaitable[Any]] = write_log_async,
        workflow_service: GenerationWorkflowService | None = None,
    ) -> None:
        self._status_setter = status_setter
        self._error_store = error_store
        self._methodology_getter = methodology_getter
        self._paused_saver = paused_saver
        self._log_writer = log_writer
        self._workflow_service = workflow_service or GenerationWorkflowService()

    async def store_methodology_pause(
        self,
        *,
        request_id: str,
        user_id: str,
        project_seed_dict: dict[str, Any],
        track_paths: list[str],
        error: ContentGenerationError,
    ) -> bool:
        """Persist pause state from MethodologyGateInterrupt and expose needs_review status."""
        flow_context = getattr(error, "flow_context", None)
        flow_steps = getattr(error, "flow_steps", None)
        resume_from_index = getattr(error, "resume_from_index", None)
        if flow_context is None or resume_from_index is None:
            self._status_setter(request_id, "failed")
            self._error_store(
                request_id,
                "Методологический gate остановил генерацию, но resume-state не был сохранен.",
            )
            self._workflow_service.mark_failed(
                request_id=request_id,
                user_id=user_id,
                error="Методологический gate остановил генерацию, но resume-state не был сохранен.",
            )
            return False

        flow_context["methodology_human_review_enabled"] = methodology_human_review_enabled(
            project_seed_dict,
            flow_context,
        )
        await asyncio.to_thread(
            self._paused_saver,
            request_id,
            user_id=user_id,
            project_seed=project_seed_dict,
            track_paths=track_paths,
            context=flow_context,
            steps=flow_steps or [],
            resume_from_index=resume_from_index,
            methodology=self._methodology_getter(request_id),
        )
        if error.context.get("error_type") == "HumanApprovalCheckpoint":
            user_friendly_message = (
                "Генерация остановлена на контрольной точке: требуется подтверждение методолога."
            )
        else:
            user_friendly_message = (
                "Генерация остановлена методологическим gate: требуется ручная проверка перед продолжением."
            )
        await self._log_writer(
            request_id=request_id,
            level="WARNING",
            message=user_friendly_message,
            user_id=user_id,
            phase=error.context.get("phase", "methodology_gate"),
            metadata={"context": error.context},
        )
        self._status_setter(request_id, "needs_review")
        self._error_store(request_id, user_friendly_message)
        resume_from_node = None
        if isinstance(flow_steps, list) and flow_steps:
            last_step = flow_steps[-1]
            if isinstance(last_step, dict):
                resume_from_node = last_step.get("node_id")
            else:
                resume_from_node = getattr(last_step, "node_id", None)
        self._workflow_service.mark_needs_review(
            request_id=request_id,
            user_id=user_id,
            resume_from_node=str(resume_from_node) if resume_from_node else None,
            metadata={"resume_from_index": resume_from_index, "methodology_error_type": error.context.get("error_type")},
        )
        return True
