"""Application service for generation status/result state."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from api.schemas import GenerationStatusResponse
from content_gen.utils.markdown_display_normalizer import normalize_markdown_display_blocks
from content_gen.workflow_profiles import resolve_workflow_profile, workflow_profile_payload

from .generation_errors import GenerationServiceError
from .generation_workflow_service import GenerationWorkflowService


class GenerationStatusService:
    """Resolve generation status from volatile cache, paused sessions and results."""

    def __init__(
        self,
        *,
        status_getter: Callable[[str], str | None],
        status_setter: Callable[[str, str], Any],
        result_getter: Callable[[str], dict[str, Any] | None],
        error_getter: Callable[[str], str | None],
        task_canceller: Callable[[str], bool],
        owner_getter: Callable[[str], str | None] | None = None,
        methodology_getter: Callable[[str], dict[str, Any] | None],
        methodology_setter: Callable[[str, dict[str, Any]], Any],
        paused_loader: Callable[[str], dict[str, Any] | None],
        log_writer: Callable[..., Awaitable[Any]],
        logger: Any,
        workflow_service: GenerationWorkflowService | None = None,
    ) -> None:
        self._status_getter = status_getter
        self._status_setter = status_setter
        self._result_getter = result_getter
        self._error_getter = error_getter
        self._task_canceller = task_canceller
        self._owner_getter = owner_getter or (lambda _request_id: None)
        self._methodology_getter = methodology_getter
        self._methodology_setter = methodology_setter
        self._paused_loader = paused_loader
        self._log_writer = log_writer
        self._logger = logger
        self._workflow_service = workflow_service or GenerationWorkflowService()

    async def get_status(self, request_id: str, user_id: str | None = None) -> GenerationStatusResponse:
        """Return current status and result payload when generation is complete."""
        status = self._status_getter(request_id)
        workflow = await asyncio.to_thread(self._workflow_service.get, request_id)
        paused_session: dict[str, Any] | None = None

        if status is None:
            paused_session = await asyncio.to_thread(self._paused_loader, request_id)
            if paused_session:
                status = "needs_review"
                self._status_setter(request_id, status)
                if paused_session.get("methodology"):
                    self._methodology_setter(request_id, paused_session["methodology"])
            elif workflow:
                status = self._public_status_from_workflow(str(workflow.get("status") or "pending"))
                self._status_setter(request_id, status)
            else:
                raise GenerationServiceError(404, "Запрос генерации не найден")
        elif workflow:
            workflow_status = self._public_status_from_workflow(str(workflow.get("status") or "pending"))
            if self._workflow_status_has_precedence(status, workflow_status):
                status = workflow_status
                self._status_setter(request_id, status)

        if status == "needs_review" and paused_session is None:
            paused_session = await asyncio.to_thread(self._paused_loader, request_id)
            if paused_session and paused_session.get("methodology"):
                self._methodology_setter(request_id, paused_session["methodology"])

        self._ensure_user_access(
            request_id,
            user_id=user_id,
            workflow=workflow,
            paused_session=paused_session,
        )

        profile = self._workflow_profile_payload(
            workflow=workflow,
            paused_session=paused_session,
            status=status,
        )
        if status == "completed":
            return self._completed_response(request_id, status, workflow=workflow)
        if status == "failed":
            return GenerationStatusResponse(
                request_id=request_id,
                status=status,
                error=self._error_getter(request_id) or "Неизвестная ошибка",
                methodology=self._methodology_getter(request_id),
                workflow=workflow,
                workflow_profile=profile,
            )
        if status == "needs_review":
            return GenerationStatusResponse(
                request_id=request_id,
                status=status,
                error=self._error_getter(request_id) or "Требуется ручная методологическая проверка",
                methodology=self._methodology_getter(request_id),
                workflow=workflow,
                workflow_profile=profile,
            )
        if status == "cancelled":
            return GenerationStatusResponse(
                request_id=request_id,
                status=status,
                error=self._error_getter(request_id) or "Генерация была остановлена пользователем",
                methodology=self._methodology_getter(request_id),
                workflow=workflow,
                workflow_profile=profile,
            )
        if status == "interrupted":
            return GenerationStatusResponse(
                request_id=request_id,
                status=status,
                error=(
                    self._error_getter(request_id)
                    or (str(workflow.get("error")) if isinstance(workflow, dict) and workflow.get("error") else None)
                    or "Процесс генерации был прерван. Запуск можно восстановить командой resume."
                ),
                methodology=self._methodology_getter(request_id),
                workflow=workflow,
                workflow_profile=profile,
            )
        return GenerationStatusResponse(
            request_id=request_id,
            status=status,
            methodology=self._methodology_getter(request_id),
            workflow=workflow,
            workflow_profile=profile,
        )

    async def cancel(self, request_id: str, user_id: str) -> dict[str, Any]:
        """Cancel active generation task and record the user action."""
        status = self._status_getter(request_id)
        workflow = await asyncio.to_thread(self._workflow_service.get, request_id)
        if status is None and workflow:
            status = self._public_status_from_workflow(str(workflow.get("status") or "pending"))
            self._status_setter(request_id, status)
        if status is None:
            raise GenerationServiceError(404, "Запрос генерации не найден")
        self._ensure_user_access(request_id, user_id=user_id, workflow=workflow)
        if status in ("completed", "failed", "cancelled"):
            raise GenerationServiceError(400, f"Невозможно остановить генерацию: статус уже {status}")
        cancelled_task = self._task_canceller(request_id)
        if not cancelled_task and not workflow:
            raise GenerationServiceError(500, "Не удалось остановить генерацию")
        self._workflow_service.mark_cancelled(request_id=request_id, user_id=user_id)
        self._status_setter(request_id, "cancelled")

        self._logger.info("🛑 Генерация остановлена пользователем %s: request_id=%s", user_id, request_id)
        await self._log_writer(
            request_id=request_id,
            level="INFO",
            message="Генерация остановлена пользователем",
            user_id=user_id,
            phase="cancelled",
            metadata={"cancelled_by": user_id},
        )
        return {"success": True, "message": "Генерация успешно остановлена"}

    def _ensure_user_access(
        self,
        request_id: str,
        *,
        user_id: str | None,
        workflow: dict[str, Any] | None = None,
        paused_session: dict[str, Any] | None = None,
    ) -> None:
        """Reject cross-user access to runtime status/results before exposing payloads."""
        if not user_id:
            return
        owner_candidates = [
            workflow.get("user_id") if isinstance(workflow, dict) else None,
            paused_session.get("user_id") if isinstance(paused_session, dict) else None,
            self._owner_getter(request_id),
        ]
        owners = [str(owner) for owner in owner_candidates if owner]
        if owners and user_id not in owners:
            raise GenerationServiceError(403, "Нет доступа к запуску другого пользователя")
        if not owners:
            raise GenerationServiceError(403, "Владелец запуска не определен")

    def _completed_response(
        self,
        request_id: str,
        status: str,
        *,
        workflow: dict[str, Any] | None,
    ) -> GenerationStatusResponse:
        cached = self._result_getter(request_id)
        if not cached:
            self._logger.warning(
                "⚠️ Результат не найден в кэше для request_id=%s, хотя статус completed",
                request_id,
            )
            return GenerationStatusResponse(
                request_id=request_id,
                status="failed",
                error="Результат генерации истек или был удален",
                workflow=workflow,
                workflow_profile=self._workflow_profile_payload(workflow=workflow, status="failed"),
            )

        report_json = cached.get("report_json")
        if report_json is None:
            self._logger.warning("⚠️ report_json отсутствует в кэше для request_id=%s", request_id)
            return GenerationStatusResponse(
                request_id=request_id,
                status="failed",
                error="Результат генерации поврежден",
                workflow=workflow,
                workflow_profile=self._workflow_profile_payload(workflow=workflow, status="failed"),
            )
        if isinstance(report_json, dict):
            report_json = dict(report_json)
            if report_json.get("markdown"):
                report_json["markdown"] = normalize_markdown_display_blocks(report_json["markdown"])
            if report_json.get("translated_markdown"):
                report_json["translated_markdown"] = normalize_markdown_display_blocks(
                    report_json["translated_markdown"]
                )
        self._logger.debug(
            "✅ Возвращаем результат для request_id=%s, report_json keys: %s",
            request_id,
            list(report_json.keys()) if isinstance(report_json, dict) else "not a dict",
        )
        return GenerationStatusResponse(
            request_id=request_id,
            status=status,
            result=report_json,
            warnings=cached.get("warnings", []),
            methodology=(
                report_json.get("methodology_gate") or cached.get("methodology") or self._methodology_getter(request_id)
                if isinstance(report_json, dict)
                else cached.get("methodology")
            ),
            workflow=workflow,
            workflow_profile=self._workflow_profile_payload(
                workflow=workflow,
                report_json=report_json if isinstance(report_json, dict) else None,
                status=status,
            ),
        )

    @staticmethod
    def _public_status_from_workflow(status: str) -> str:
        """Keep public API status compatible while workflow has richer states."""
        if status in {"created"}:
            return "pending"
        if status in {"running", "node_completed", "resuming"}:
            return "in_progress"
        if status == "needs_review":
            return "needs_review"
        if status == "interrupted":
            return "interrupted"
        if status in {"completed", "failed", "cancelled"}:
            return status
        return "pending"

    @staticmethod
    def _workflow_status_has_precedence(cached_status: str | None, workflow_status: str) -> bool:
        """Prefer durable terminal/review states over stale volatile active states."""
        if cached_status in {None, ""}:
            return True
        active_cached = {"pending", "in_progress", "resuming"}
        authoritative_workflow = {"needs_review", "interrupted", "completed", "failed", "cancelled"}
        if cached_status in active_cached and workflow_status in authoritative_workflow:
            return True
        return cached_status == "pending" and workflow_status == "in_progress"

    @staticmethod
    def _workflow_profile_payload(
        *,
        workflow: dict[str, Any] | None,
        paused_session: dict[str, Any] | None = None,
        report_json: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Resolve profile from durable metadata with safe fallbacks for old runs."""
        metadata = workflow.get("metadata") if isinstance(workflow, dict) else {}
        if isinstance(metadata, dict):
            stored_profile = metadata.get("workflow_profile")
            if isinstance(stored_profile, dict) and stored_profile.get("id"):
                return stored_profile
            if metadata.get("workflow_profile_id"):
                return workflow_profile_payload(str(metadata.get("workflow_profile_id")))
            project_seed_payload = metadata.get("project_seed_payload")
            if isinstance(project_seed_payload, dict):
                return workflow_profile_payload(resolve_workflow_profile(project_seed_payload))

        if isinstance(paused_session, dict):
            project_seed_payload = paused_session.get("project_seed")
            if isinstance(project_seed_payload, dict):
                return workflow_profile_payload(resolve_workflow_profile(project_seed_payload))

        if isinstance(report_json, dict):
            stored_profile = report_json.get("workflow_profile")
            if isinstance(stored_profile, dict) and stored_profile.get("id"):
                return stored_profile

        if status == "needs_review":
            return workflow_profile_payload("methodology")
        return workflow_profile_payload("standard")
