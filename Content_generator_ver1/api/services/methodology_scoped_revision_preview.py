"""Scoped revision preview executor for methodology review."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from typing import Any

from content_gen.llm.factory import create_llm_client
from content_gen.methodology import ScopedRevisionExecutor, build_section_target_registry

from .generation_errors import GenerationServiceError
from .methodology_review_artifacts import context_preview_markdown, refresh_checkpoint_artifact
from .methodology_review_state import (
    build_methodology_review_state,
    change_action_ids,
    current_review_action_slice,
    preview_hash,
    revision_results_for_action_ids,
)

JsonDict = dict[str, Any]


class ScopedRevisionPreviewService:
    """Build and persist previews for pending methodology scoped changes."""

    def __init__(
        self,
        *,
        record_preview: Callable[..., JsonDict | None],
        log_writer: Callable[..., Awaitable[Any]],
        llm_factory: Callable[[], Any] | None = None,
        revision_executor_cls: type[ScopedRevisionExecutor] = ScopedRevisionExecutor,
    ) -> None:
        self._record_preview = record_preview
        self._log_writer = log_writer
        self._llm_factory = llm_factory or (
            lambda: create_llm_client(default_role="critic", enable_cache=True, enable_batching=True)
        )
        self._revision_executor_cls = revision_executor_cls

    async def preview(self, request_id: str, *, user_id: str, paused_session: JsonDict) -> JsonDict:
        """Run pending scoped revisions on copied paused context and persist preview."""
        active_start_index, active_review_actions = current_review_action_slice(paused_session.get("review_actions") or [])
        active_change_ids = change_action_ids(active_review_actions, start_index=active_start_index)
        preview_context = copy.deepcopy(paused_session.get("context") or {})
        preview_context["methodology_review_actions"] = list(paused_session.get("review_actions") or [])
        llm_client = self._llm_factory()
        configure_context = getattr(llm_client, "configure_run_context", None)
        if callable(configure_context):
            configure_context(user_id=user_id, run_id=request_id)
        executor = self._revision_executor_cls(llm_client)
        results = await asyncio.to_thread(
            executor.apply_pending_change_requests,
            preview_context,
            raise_on_rejected=False,
        )
        refresh_checkpoint_artifact(preview_context)
        all_revision_results = preview_context.get("methodology_revision_results") or [
            result.model_dump(mode="json") for result in results
        ]
        payload = revision_results_for_action_ids(all_revision_results, active_change_ids)
        target_registry = build_section_target_registry(preview_context).model_dump(mode="json")
        preview_markdown = context_preview_markdown(preview_context)
        preview_hash_value = preview_hash(payload)
        saved_session = await asyncio.to_thread(
            self._record_preview,
            request_id,
            user_id=user_id,
            revision_results=payload,
            target_registry=target_registry,
            preview_hash=preview_hash_value,
            preview_context=preview_context,
            preview_markdown=preview_markdown,
        )
        if not saved_session:
            raise GenerationServiceError(410, "Состояние продолжения истекло или недоступно")
        review_state = build_methodology_review_state(saved_session)
        review_state["checkpoint"] = preview_context.get("human_approval_checkpoint")
        review_state["preview_markdown"] = preview_markdown
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Предпросмотр методологических правок выполнен",
            user_id=user_id,
            phase="methodology_review",
            metadata={"revision_results": payload, "preview_hash": preview_hash_value},
        )
        return {
            "success": True,
            **review_state,
            "status": "needs_review",
            "preview_hash": preview_hash_value,
            "revision_results": payload,
            "target_registry": target_registry,
            "preview_markdown": preview_markdown,
        }
