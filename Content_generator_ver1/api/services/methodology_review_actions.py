"""Command handlers for durable methodology review actions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from content_gen.methodology import (
    MethodologistChangeRequest,
    has_hard_conflicts,
    validate_methodologist_change_request,
)
from content_gen.methodology.state_machine import (
    MethodologyRuntimeAction,
    MethodologyRuntimeState,
    MethodologyStateMachine,
)

from .generation_errors import GenerationServiceError
from .methodology_review_state import build_methodology_review_state

JsonDict = dict[str, Any]


class ReviewActionCommandService:
    """Persist methodologist decisions that change paused review state."""

    def __init__(
        self,
        *,
        status_setter: Callable[[str, str], Any],
        error_store: Callable[[str, str], Any],
        record_change_request: Callable[..., JsonDict | None],
        approve_diff: Callable[..., JsonDict | None],
        log_writer: Callable[..., Awaitable[Any]],
        state_machine: MethodologyStateMachine,
    ) -> None:
        self._status_setter = status_setter
        self._error_store = error_store
        self._record_change_request = record_change_request
        self._approve_diff = approve_diff
        self._log_writer = log_writer
        self._state_machine = state_machine

    async def approve_diff(
        self,
        request_id: str,
        *,
        user_id: str,
        paused_session: JsonDict,
        comment: str | None,
    ) -> JsonDict:
        """Approve the latest persisted scoped preview."""
        review_state = build_methodology_review_state(paused_session)
        if review_state["review_state"] == "no_changes":
            return {
                "success": True,
                "status": "needs_review",
                "review_state": "no_changes",
                "message": "Нет pending правок для подтверждения.",
            }
        if review_state["review_state"] != "preview_ready":
            raise GenerationServiceError(
                409,
                {
                    "message": "Перед подтверждением diff нужно выполнить актуальный preview",
                    "review_state": review_state["review_state"],
                    "pending_change_ids": review_state["pending_change_ids"],
                },
            )
        if review_state["preview_has_rejections"]:
            raise GenerationServiceError(
                409,
                {
                    "message": "Diff содержит отклоненные правки. Нельзя подтверждать до исправления запросов.",
                    "revision_results": review_state["revision_results"],
                },
            )

        saved_session = await asyncio.to_thread(
            self._approve_diff,
            request_id,
            user_id=user_id,
            approved_action_ids=review_state.get("diff_approvable_action_ids") or review_state["pending_change_ids"],
            preview_hash=review_state["preview_hash"],
            comment=comment,
        )
        if not saved_session:
            raise GenerationServiceError(410, "Состояние продолжения истекло или недоступно")

        self._state_machine.transition(MethodologyRuntimeState.CHANGES_REQUESTED, MethodologyRuntimeAction.APPROVE_DIFF)
        next_state = build_methodology_review_state(saved_session)
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Методолог подтвердил diff правок",
            user_id=user_id,
            phase="methodology_review",
            metadata={
                "approved_action_ids": next_state["approved_action_ids"],
                "preview_hash": next_state["preview_hash"],
                "comment": comment or "",
            },
        )
        return {
            "success": True,
            "status": "needs_review",
            "message": "Diff правок подтвержден. Теперь можно продолжить генерацию.",
            **next_state,
        }

    async def request_changes(
        self,
        request_id: str,
        *,
        user_id: str,
        change_request: MethodologistChangeRequest,
        assistant_command: JsonDict | None = None,
    ) -> JsonDict:
        """Validate and persist a scoped change request while generation is paused."""
        conflicts = validate_methodologist_change_request(change_request)
        conflict_payload = [conflict.model_dump(mode="json") for conflict in conflicts]
        if has_hard_conflicts(conflicts):
            await self._log_writer(
                request_id=request_id,
                level="WARNING",
                message="Запрос правок методолога отклонен hard guard",
                user_id=user_id,
                phase="methodology_review",
                metadata={
                    "change_request": change_request.model_dump(mode="json"),
                    "conflicts": conflict_payload,
                },
            )
            raise GenerationServiceError(
                409,
                {
                    "message": "Запрос правок конфликтует с hard rules генератора",
                    "conflicts": conflict_payload,
                },
            )

        saved_session = await asyncio.to_thread(
            self._record_change_request,
            request_id,
            user_id=user_id,
            change_request=change_request.model_dump(mode="json"),
            conflicts=conflict_payload,
            assistant_command=assistant_command,
        )
        if not saved_session:
            raise GenerationServiceError(410, "Состояние продолжения истекло или недоступно")

        self._state_machine.transition(MethodologyRuntimeState.NEEDS_REVIEW, MethodologyRuntimeAction.REQUEST_CHANGES)
        review_state = build_methodology_review_state(saved_session)
        message = "Методолог запросил правки. Генерация остается на паузе до подтверждения."
        self._status_setter(request_id, "needs_review")
        self._error_store(request_id, message)
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message=message,
            user_id=user_id,
            phase="methodology_review",
            metadata={
                "change_request": change_request.model_dump(mode="json"),
                "conflicts": conflict_payload,
                "assistant_command": assistant_command or {},
                "review_actions_count": len(saved_session.get("review_actions") or []),
            },
        )
        return {
            "success": True,
            "status": "needs_review",
            "message": message,
            "change_request": change_request.model_dump(mode="json"),
            "conflicts": conflict_payload,
            **review_state,
        }
