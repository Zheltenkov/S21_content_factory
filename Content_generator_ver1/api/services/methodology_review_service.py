"""Application command service for human methodology review sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from content_gen.llm.factory import create_llm_client
from content_gen.methodology import (
    MethodologyAssistantCommandParser,
    MethodologyAssistantParseContext,
    MethodologistChangeRequest,
    ScopedRevisionExecutor,
)
from content_gen.methodology.state_machine import (
    MethodologyRuntimeAction,
    MethodologyRuntimeState,
    MethodologyStateMachine,
)

from .generation_errors import GenerationServiceError
from .generation_workflow_service import GenerationWorkflowService
from .methodology_review_state import build_methodology_review_state
from .methodology_review_actions import ReviewActionCommandService
from .methodology_scoped_revision_preview import ScopedRevisionPreviewService

JsonDict = dict[str, Any]
ResumeBackground = Callable[..., Awaitable[None]]
WorkflowCommandBackground = Callable[..., Awaitable[None]]


class MethodologyReviewService:
    """Coordinate review commands, scoped preview, diff approval and resume."""

    def __init__(
        self,
        *,
        status_getter: Callable[[str], str | None],
        status_setter: Callable[[str, str], Any],
        error_store: Callable[[str, str], Any],
        paused_loader: Callable[[str], JsonDict | None],
        approve_paused: Callable[..., JsonDict | None],
        reject_paused: Callable[..., bool],
        record_change_request: Callable[..., JsonDict | None],
        record_preview: Callable[..., JsonDict | None],
        approve_diff: Callable[..., JsonDict | None],
        task_registrar: Callable[[str, asyncio.Task[Any]], Any],
        resume_background: ResumeBackground,
        log_writer: Callable[..., Awaitable[Any]],
        workflow_command_background: WorkflowCommandBackground | None = None,
        llm_factory: Callable[[], Any] | None = None,
        revision_executor_cls: type[ScopedRevisionExecutor] = ScopedRevisionExecutor,
        state_machine: MethodologyStateMachine | None = None,
        preview_service: ScopedRevisionPreviewService | None = None,
        action_service: ReviewActionCommandService | None = None,
        workflow_service: GenerationWorkflowService | None = None,
        assistant_parser: MethodologyAssistantCommandParser | None = None,
    ) -> None:
        self._status_getter = status_getter
        self._status_setter = status_setter
        self._error_store = error_store
        self._paused_loader = paused_loader
        self._approve_paused = approve_paused
        self._reject_paused = reject_paused
        self._task_registrar = task_registrar
        self._resume_background = resume_background
        self._workflow_command_background = workflow_command_background
        self._log_writer = log_writer
        self._llm_factory = llm_factory or (
            lambda: create_llm_client(default_role="critic", enable_cache=True, enable_batching=True)
        )
        self._state_machine = state_machine or MethodologyStateMachine()
        self._workflow_service = workflow_service or GenerationWorkflowService()
        self._assistant_parser = assistant_parser or MethodologyAssistantCommandParser()
        self._preview_service = preview_service or ScopedRevisionPreviewService(
            record_preview=record_preview,
            log_writer=log_writer,
            llm_factory=self._llm_factory,
            revision_executor_cls=revision_executor_cls,
        )
        self._action_service = action_service or ReviewActionCommandService(
            status_setter=status_setter,
            error_store=error_store,
            record_change_request=record_change_request,
            approve_diff=approve_diff,
            log_writer=log_writer,
            state_machine=self._state_machine,
        )

    async def approve_review(self, request_id: str, *, user_id: str, comment: str | None) -> JsonDict:
        """Approve review and start resume background task."""
        self._require_status(request_id, expected="needs_review")
        paused_session = await self._load_paused_for_user(request_id, user_id, missing_status=410)

        review_state = build_methodology_review_state(paused_session)
        if review_state["requires_diff_approval"]:
            raise GenerationServiceError(
                409,
                {
                    "message": "Перед продолжением нужно выполнить preview и подтвердить diff правок методолога",
                    "review_state": review_state["review_state"],
                    "pending_change_ids": review_state["pending_change_ids"],
                },
            )

        approved_session = await asyncio.to_thread(
            self._approve_paused,
            request_id,
            user_id=user_id,
            comment=comment,
        )
        if not approved_session:
            raise GenerationServiceError(410, "Состояние продолжения истекло или недоступно")

        self._state_machine.transition(MethodologyRuntimeState.NEEDS_REVIEW, MethodologyRuntimeAction.APPROVE_REVIEW)
        generation_task = asyncio.create_task(
            self._resume_background(
                request_id=request_id,
                user_id=user_id,
                paused_session=approved_session,
                review_comment=comment,
            )
        )
        self._task_registrar(request_id, generation_task)
        self._status_setter(request_id, "in_progress")
        self._workflow_service.mark_resuming(request_id=request_id, user_id=user_id, comment=comment)
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Методолог подтвердил продолжение генерации",
            user_id=user_id,
            phase="methodology_review",
            metadata={"comment": comment or ""},
        )
        return {"request_id": request_id, "status": "in_progress"}

    async def reject_review(self, request_id: str, *, user_id: str, comment: str | None) -> JsonDict:
        """Reject review and mark generation as failed."""
        self._require_status(request_id, expected="needs_review")
        await self._load_paused_for_user(request_id, user_id, missing_status=410)

        rejected = await asyncio.to_thread(
            self._reject_paused,
            request_id,
            user_id=user_id,
            comment=comment,
        )
        if not rejected:
            raise GenerationServiceError(410, "Состояние продолжения истекло или недоступно")
        message = "Генерация отклонена методологом"
        if comment:
            message = f"{message}: {comment}"
        self._status_setter(request_id, "failed")
        self._error_store(request_id, message)
        self._workflow_service.mark_failed(request_id=request_id, user_id=user_id, error=message)
        await self._log_writer(
            request_id=request_id,
            level="WARNING",
            message=message,
            user_id=user_id,
            phase="methodology_review",
            metadata={"comment": comment or ""},
        )
        return {"success": True, "status": "failed", "message": message}

    async def get_review_state(self, request_id: str, *, user_id: str) -> JsonDict:
        """Return durable review state for UI history and target selection."""
        paused_session = await self._load_paused_for_user(request_id, user_id, missing_status=404)
        return build_methodology_review_state(paused_session)

    async def preview_changes(self, request_id: str, *, user_id: str) -> JsonDict:
        """Run pending scoped revisions on copied paused context and persist preview."""
        status = self._status_getter(request_id)
        if status is not None and status != "needs_review":
            raise GenerationServiceError(400, f"Генерация не ожидает методолога: статус {status}")

        paused_session = await self._load_paused_for_user(request_id, user_id, missing_status=404)
        return await self._preview_service.preview(
            request_id,
            user_id=user_id,
            paused_session=paused_session,
        )

    async def approve_review_diff(self, request_id: str, *, user_id: str, comment: str | None) -> JsonDict:
        """Approve the latest persisted scoped preview."""
        status = self._status_getter(request_id)
        if status is not None and status != "needs_review":
            raise GenerationServiceError(400, f"Генерация не ожидает методолога: статус {status}")

        paused_session = await self._load_paused_for_user(request_id, user_id, missing_status=404)
        return await self._action_service.approve_diff(
            request_id,
            user_id=user_id,
            paused_session=paused_session,
            comment=comment,
        )

    async def request_changes(
        self,
        request_id: str,
        *,
        user_id: str,
        change_request: MethodologistChangeRequest,
    ) -> JsonDict:
        """Record a scoped change request while keeping generation paused."""
        self._require_status(request_id, expected="needs_review")
        await self._load_paused_for_user(request_id, user_id, missing_status=410)
        return await self._action_service.request_changes(
            request_id,
            user_id=user_id,
            change_request=change_request,
        )

    async def run_assistant_command(
        self,
        request_id: str,
        *,
        user_id: str,
        message: str,
        selected_target_id: str | None = None,
    ) -> JsonDict:
        """Parse a methodology chat message and dispatch it to a checkpoint command."""
        self._require_status(request_id, expected="needs_review")
        paused_session = await self._load_paused_for_user(request_id, user_id, missing_status=410)
        review_state = build_methodology_review_state(paused_session)
        command = await self._assistant_parser.parse_async(
            message,
            MethodologyAssistantParseContext(
                checkpoint=review_state.get("checkpoint") or {},
                target_registry=review_state.get("target_registry") or {},
                review_state=review_state,
                selected_target_id=selected_target_id or "",
            ),
        )
        command_payload = command.model_dump(mode="json")
        self._workflow_service.record_methodology_assistant_command(
            request_id=request_id,
            user_id=user_id,
            command_payload=command_payload,
            status="needs_review",
        )
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Методологический ассистент распознал команду",
            user_id=user_id,
            phase="methodology_review",
            metadata={"assistant_command": command_payload},
        )

        if command.command == "approve":
            result = await self.approve_review(request_id, user_id=user_id, comment=message)
            return {
                **result,
                "success": True,
                "assistant_command": command_payload,
            }

        if command.command == "regenerate_section":
            return await self._run_regenerate_section_command(
                request_id,
                user_id=user_id,
                message=message,
                command_payload=command_payload,
                review_state=review_state,
            )

        change_request = command.to_change_request()
        result = await self._action_service.request_changes(
            request_id,
            user_id=user_id,
            change_request=change_request,
            assistant_command=command_payload,
        )
        return {
            **result,
            "assistant_command": command_payload,
            "change_request": change_request.model_dump(mode="json"),
        }

    async def _run_regenerate_section_command(
        self,
        request_id: str,
        *,
        user_id: str,
        message: str,
        command_payload: JsonDict,
        review_state: JsonDict,
    ) -> JsonDict:
        """Turn a chat regenerate command into a durable workflow command."""
        if not self._workflow_command_background:
            raise GenerationServiceError(
                503,
                "Workflow command runner is not configured for regenerate_section",
            )
        if review_state.get("requires_diff_approval"):
            raise GenerationServiceError(
                409,
                {
                    "message": "Перед перегенерацией раздела нужно подтвердить или очистить pending diff правок",
                    "review_state": review_state.get("review_state"),
                    "pending_change_ids": review_state.get("pending_change_ids") or [],
                },
            )
        approved_session = await asyncio.to_thread(
            self._approve_paused,
            request_id,
            user_id=user_id,
            comment=message,
        )
        if not approved_session:
            raise GenerationServiceError(410, "Состояние продолжения истекло или недоступно")

        node_id = str(command_payload.get("workflow_node_id") or command_payload.get("target_stage") or "")
        payload = {
            "source": "methodology_assistant",
            "assistant_command": command_payload,
            "checkpoint_id": command_payload.get("checkpoint_id"),
            "target_stage": command_payload.get("target_stage"),
            "target_id": command_payload.get("target_id"),
            "message": message,
        }
        generation_task = asyncio.create_task(
            self._workflow_command_background(
                request_id=request_id,
                user_id=user_id,
                command="regenerate_section",
                node_id=node_id or None,
                payload=payload,
            )
        )
        self._task_registrar(request_id, generation_task)
        self._status_setter(request_id, "in_progress")
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Методологический ассистент запустил перегенерацию раздела",
            user_id=user_id,
            phase="methodology_review",
            metadata=payload,
        )
        return {
            "success": True,
            "status": "in_progress",
            "message": "Раздел отправлен на перегенерацию из durable checkpoint.",
            "assistant_command": command_payload,
            "workflow_command": {
                "command": "regenerate_section",
                "node_id": node_id,
                "payload": payload,
            },
        }

    def _require_status(self, request_id: str, *, expected: str) -> None:
        status = self._status_getter(request_id)
        if status is None:
            raise GenerationServiceError(404, "Запрос генерации не найден")
        if status != expected:
            raise GenerationServiceError(400, f"Генерация не ожидает методолога: статус {status}")

    async def _load_paused_for_user(
        self,
        request_id: str,
        user_id: str,
        *,
        missing_status: int,
    ) -> JsonDict:
        paused_session = await asyncio.to_thread(self._paused_loader, request_id)
        if not paused_session:
            detail = (
                "Запрос генерации не найден или не ожидает методолога"
                if missing_status == 404
                else "Состояние продолжения истекло или недоступно"
            )
            raise GenerationServiceError(missing_status, detail)
        if paused_session.get("user_id") != user_id:
            raise GenerationServiceError(403, "Нет доступа к запросу генерации")
        return paused_session
