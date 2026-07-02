"""Coordinator for generation background run and resume execution."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from api.db.generation_results_db import save_generation_result
from api.db.logging_db import write_log_async
from api.db.paused_generation_db import mark_paused_generation_completed, save_paused_generation_session
from api.utils.file_handler import cleanup_temp_files
from api.utils.logger import get_logger
from api.utils.logging_context import set_request_id, set_user_id
from api.utils.result_cache import (
    get_generation_methodology,
    get_generation_status,
    set_generation_methodology,
    set_generation_status,
    store_generation_error,
    store_result,
    unregister_generation_task,
)
from content_gen.exceptions import (
    ContentGenerationError,
    LLMAPIError,
    LLMRateLimitError,
    LLMTimeoutError,
    ValidationError,
)
from content_gen.llm.factory import create_llm_client
from content_gen.models.schemas import ProjectSeed
from content_gen.orchestrator import Orchestrator

from .generation_failure_handler import GenerationFailureHandler
from .generation_pause_persistence import MethodologyPausePersister
from .generation_result_persistence import GenerationResultPersister
from .generation_workflow_service import GenerationWorkflowService
from .methodology_review_artifacts import methodology_human_review_enabled

logger = get_logger("generation")


class GenerationResumeService:
    """Run generation and resume jobs; delegate persistence and failure policy."""

    def __init__(
        self,
        *,
        status_getter: Callable[[str], str | None] = get_generation_status,
        status_setter: Callable[[str, str], Any] = set_generation_status,
        methodology_getter: Callable[[str], dict[str, Any] | None] = get_generation_methodology,
        methodology_setter: Callable[[str, dict[str, Any]], Any] = set_generation_methodology,
        error_store: Callable[[str, str], Any] = store_generation_error,
        result_store: Callable[..., Any] = store_result,
        task_unregister: Callable[[str], Any] = unregister_generation_task,
        result_saver: Callable[..., Any] = save_generation_result,
        paused_saver: Callable[..., Any] = save_paused_generation_session,
        paused_completed_marker: Callable[[str], Any] = mark_paused_generation_completed,
        log_writer: Callable[..., Awaitable[Any]] = write_log_async,
        llm_factory: Callable[..., Any] | None = None,
        orchestrator_cls: type[Orchestrator] = Orchestrator,
        temp_cleanup: Callable[[str], Awaitable[Any]] = cleanup_temp_files,
        completed_saver: Callable[..., Awaitable[bool]] | None = None,
        result_persister: GenerationResultPersister | None = None,
        pause_persister: MethodologyPausePersister | None = None,
        failure_handler: GenerationFailureHandler | None = None,
        workflow_service: GenerationWorkflowService | None = None,
    ) -> None:
        self._status_getter = status_getter
        self._status_setter = status_setter
        self._methodology_setter = methodology_setter
        self._task_unregister = task_unregister
        self._paused_completed_marker = paused_completed_marker
        self._llm_factory = llm_factory or (
            lambda provider=None: create_llm_client(
                provider=provider,
                enable_cache=True,
                enable_batching=True,
            )
        )
        self._orchestrator_cls = orchestrator_cls
        self._temp_cleanup = temp_cleanup
        self._completed_saver = completed_saver
        self._workflow_service = workflow_service or GenerationWorkflowService()
        self._result_persister = result_persister or GenerationResultPersister(
            status_setter=status_setter,
            error_store=error_store,
            result_store=result_store,
            result_saver=result_saver,
            log_writer=log_writer,
        )
        self._pause_persister = pause_persister or MethodologyPausePersister(
            status_setter=status_setter,
            error_store=error_store,
            methodology_getter=methodology_getter,
            paused_saver=paused_saver,
            log_writer=log_writer,
            workflow_service=self._workflow_service,
        )
        self._failure_handler = failure_handler or GenerationFailureHandler(
            status_setter=status_setter,
            error_store=error_store,
            log_writer=log_writer,
            pause_persister=self._pause_persister,
            workflow_service=self._workflow_service,
        )

    async def save_completed_generation(
        self,
        *,
        request_id: str,
        user_id: str,
        project_seed_payload: dict[str, Any],
        result: Any,
    ) -> bool:
        """Compatibility entrypoint for completed result persistence."""
        return await self._result_persister.save_completed_generation(
            request_id=request_id,
            user_id=user_id,
            project_seed_payload=project_seed_payload,
            result=result,
        )

    async def store_methodology_pause(
        self,
        *,
        request_id: str,
        user_id: str,
        project_seed_dict: dict[str, Any],
        track_paths: list[str],
        error: ContentGenerationError,
    ) -> bool:
        """Compatibility entrypoint for methodology pause persistence."""
        return await self._pause_persister.store_methodology_pause(
            request_id=request_id,
            user_id=user_id,
            project_seed_dict=project_seed_dict,
            track_paths=track_paths,
            error=error,
        )

    async def run_generation_background(
        self,
        request_id: str,
        user_id: str,
        project_seed_dict: dict[str, Any],
        track_paths: list[str],
        temp_dir: str | None = None,
    ) -> None:
        """Run full generation in the background."""
        try:
            status = self._status_getter(request_id)
            if status == "cancelled":
                logger.info("🛑 Генерация отменена до начала: request_id=%s", request_id)
                return
            set_request_id(request_id)
            set_user_id(user_id)
            self._status_setter(request_id, "in_progress")
            self._workflow_service.mark_running(request_id=request_id, user_id=user_id)

            logger.info("🔍 _run_generation_background: язык из project_seed_dict: %r", project_seed_dict.get("language"))
            project_seed = ProjectSeed(**project_seed_dict)
            logger.info(
                "🔍 _run_generation_background: язык из project_seed: %r (тип: %s)",
                project_seed.language,
                type(project_seed.language).__name__,
            )
            if project_seed.llm_provider:
                logger.info("🤖 _run_generation_background: LLM provider из project_seed: %s", project_seed.llm_provider)

            result = await asyncio.to_thread(
                self._build_orchestrator(
                    human_review_enabled=bool(project_seed.methodology_human_review),
                    request_id=request_id,
                    user_id=user_id,
                    llm_provider=project_seed.llm_provider,
                ).run,
                raw_input=project_seed.model_dump(),
                track_files=track_paths,
            )

            saved = await self._save_completed_result(
                request_id=request_id,
                user_id=user_id,
                project_seed_payload=project_seed.model_dump(),
                result=result,
            )
            if saved:
                self._workflow_service.mark_completed(request_id=request_id, user_id=user_id)
            else:
                self._workflow_service.mark_failed(
                    request_id=request_id,
                    user_id=user_id,
                    error="Результат генерации не был сохранен",
                )
        except ValidationError as exc:
            await self._failure_handler.handle_validation_error(request_id, user_id, exc)
        except (LLMTimeoutError, LLMRateLimitError) as exc:
            await self._failure_handler.handle_llm_timeout_or_rate_limit(request_id, user_id, exc)
        except LLMAPIError as exc:
            await self._failure_handler.handle_llm_api_error(request_id, user_id, exc)
        except ContentGenerationError as exc:
            await self._failure_handler.handle_content_generation_error(
                request_id=request_id,
                user_id=user_id,
                project_seed_dict=project_seed_dict,
                track_paths=track_paths,
                error=exc,
            )
        except Exception as exc:  # noqa: BLE001
            await self._failure_handler.handle_unexpected_error(request_id, user_id, exc)
        finally:
            self._task_unregister(request_id)
            if temp_dir:
                await self._temp_cleanup(temp_dir)

    async def resume_generation_background(
        self,
        request_id: str,
        user_id: str,
        paused_session: dict[str, Any],
        review_comment: str | None = None,
    ) -> None:
        """Resume generation after methodologist approval from saved flow context."""
        try:
            status = self._status_getter(request_id)
            if status == "cancelled":
                logger.info("🛑 Resume отменен до старта: request_id=%s", request_id)
                return

            set_request_id(request_id)
            set_user_id(user_id)
            self._status_setter(request_id, "in_progress")
            self._workflow_service.mark_resuming(request_id=request_id, user_id=user_id, comment=review_comment)

            context = paused_session["context"]
            self._attach_review_actions(context, paused_session, review_comment, user_id)
            human_review_enabled = methodology_human_review_enabled(
                paused_session.get("project_seed") or {},
                context,
            )
            result = await asyncio.to_thread(
                self._build_orchestrator(
                    human_review_enabled=human_review_enabled,
                    request_id=request_id,
                    user_id=user_id,
                    llm_provider=self._resolve_session_llm_provider(context, paused_session),
                ).resume_from_pause,
                context=context,
                resume_from_index=int(paused_session.get("resume_from_index", 0)),
                previous_steps=paused_session.get("steps") or [],
            )

            saved = await self._save_completed_result(
                request_id=request_id,
                user_id=user_id,
                project_seed_payload=paused_session.get("project_seed") or {},
                result=result,
            )
            if saved:
                self._workflow_service.mark_completed(request_id=request_id, user_id=user_id)
                await asyncio.to_thread(self._paused_completed_marker, request_id)
            else:
                self._workflow_service.mark_failed(
                    request_id=request_id,
                    user_id=user_id,
                    error="Результат продолжения генерации не был сохранен",
                )
        except ContentGenerationError as exc:
            await self._failure_handler.handle_resume_content_error(
                request_id=request_id,
                user_id=user_id,
                paused_session=paused_session,
                error=exc,
            )
        except Exception as exc:  # noqa: BLE001
            await self._failure_handler.handle_unexpected_error(
                request_id,
                user_id,
                exc,
                phase="resume_unexpected_error",
                message_prefix="Неожиданная ошибка продолжения генерации",
                error_prefix="Ошибка продолжения генерации",
            )
        finally:
            self._task_unregister(request_id)

    async def run_workflow_command_background(
        self,
        request_id: str,
        user_id: str,
        *,
        command: str,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Execute a durable workflow command such as retry_node or regenerate_section."""
        payload = dict(payload or {})
        try:
            if command == "cancel":
                self._status_setter(request_id, "cancelled")
                self._workflow_service.mark_cancelled(request_id=request_id, user_id=user_id)
                return
            if command == "retry_node" and node_id:
                self._workflow_service.mark_retry_node(
                    request_id=request_id,
                    user_id=user_id,
                    node_id=node_id,
                    reason=str(payload.get("reason") or "") or None,
                )
            elif command == "regenerate_section":
                self._workflow_service.mark_regenerate_section(
                    request_id=request_id,
                    user_id=user_id,
                    section=str(payload.get("section") or node_id or ""),
                    payload=payload,
                )
            else:
                self._workflow_service.mark_resuming(
                    request_id=request_id,
                    user_id=user_id,
                    comment=str(payload.get("comment") or "") or None,
                )

            session = self._workflow_service.build_recovery_session(
                request_id=request_id,
                command=command,
                node_id=node_id,
                payload=payload,
            )
            if not session:
                raise ContentGenerationError(
                    "Durable workflow session not found",
                    context={"phase": "workflow_command", "request_id": request_id},
                )

            set_request_id(request_id)
            set_user_id(user_id)
            self._status_setter(request_id, "in_progress")
            self._workflow_service.mark_running(request_id=request_id, user_id=user_id)
            result = await self._run_recovery_session(request_id, user_id, session)
            project_seed_payload = session.get("project_seed") or session.get("raw_input") or {}
            saved = await self._save_completed_result(
                request_id=request_id,
                user_id=user_id,
                project_seed_payload=project_seed_payload,
                result=result,
            )
            if saved:
                self._workflow_service.mark_completed(request_id=request_id, user_id=user_id)
            else:
                self._workflow_service.mark_failed(
                    request_id=request_id,
                    user_id=user_id,
                    error="Результат workflow-команды не был сохранен",
                )
        except ContentGenerationError as exc:
            await self._failure_handler.handle_content_generation_error(
                request_id=request_id,
                user_id=user_id,
                project_seed_dict=payload,
                track_paths=[],
                error=exc,
            )
        except Exception as exc:  # noqa: BLE001
            await self._failure_handler.handle_unexpected_error(
                request_id,
                user_id,
                exc,
                phase="workflow_command_error",
                message_prefix="Неожиданная ошибка workflow-команды",
                error_prefix="Ошибка workflow-команды",
            )
        finally:
            self._task_unregister(request_id)

    async def _save_completed_result(
        self,
        *,
        request_id: str,
        user_id: str,
        project_seed_payload: dict[str, Any],
        result: Any,
    ) -> bool:
        """Use injected compatibility saver when a router-level test monkeypatches it."""
        if self._completed_saver is not None:
            return await self._completed_saver(
                request_id=request_id,
                user_id=user_id,
                project_seed_payload=project_seed_payload,
                result=result,
            )
        return await self.save_completed_generation(
            request_id=request_id,
            user_id=user_id,
            project_seed_payload=project_seed_payload,
            result=result,
        )

    def _create_llm_client(self, llm_provider: str | None = None) -> Any:
        """Create a run-scoped LLM client while preserving legacy no-arg factories."""
        try:
            return self._llm_factory(provider=llm_provider)
        except TypeError:
            return self._llm_factory()

    def _build_orchestrator(
        self,
        *,
        human_review_enabled: bool,
        request_id: str,
        user_id: str,
        llm_provider: str | None = None,
    ) -> Orchestrator:
        """Create an orchestrator with optional methodology progress callback."""
        llm_client = self._create_llm_client(llm_provider)
        configure_context = getattr(llm_client, "configure_run_context", None)
        if callable(configure_context):
            configure_context(user_id=user_id, run_id=request_id)
        methodology_callback = (
            (lambda payload: self._methodology_setter(request_id, payload))
            if human_review_enabled
            else None
        )
        orchestrator_kwargs = {
            "methodology_progress_callback": methodology_callback,
            "human_approval_enabled": human_review_enabled,
            "run_id": request_id,
            "user_id": user_id,
            "workflow_checkpoint_callback": (
                lambda payload: self._workflow_service.record_node_checkpoint(
                    request_id=request_id,
                    user_id=user_id,
                    payload=payload,
                )
            ),
            "workflow_node_started_callback": (
                lambda payload: self._workflow_service.mark_node_running(
                    request_id=request_id,
                    user_id=user_id,
                    payload=payload,
                )
            ),
        }
        try:
            signature = inspect.signature(self._orchestrator_cls)
            supports_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
            if not supports_kwargs:
                orchestrator_kwargs = {
                    key: value for key, value in orchestrator_kwargs.items() if key in signature.parameters
                }
        except (TypeError, ValueError):
            pass
        return self._orchestrator_cls(llm_client, **orchestrator_kwargs)

    async def _run_recovery_session(
        self,
        request_id: str,
        user_id: str,
        session: dict[str, Any],
    ) -> Any:
        """Run an orchestrator from a recovered context or from the original seed."""
        context = session.get("context")
        if isinstance(context, dict):
            raw_input = context.get("raw_input") if isinstance(context.get("raw_input"), dict) else {}
            llm_provider = self._resolve_session_llm_provider(raw_input, session)
            human_review_enabled = methodology_human_review_enabled(
                raw_input or session.get("project_seed") or {},
                context,
            )
            return await asyncio.to_thread(
                self._build_orchestrator(
                    human_review_enabled=human_review_enabled,
                    request_id=request_id,
                    user_id=user_id,
                    llm_provider=llm_provider,
                ).resume_from_workflow_checkpoint,
                context=context,
                start_index=int(session.get("start_index") or 0),
                previous_steps=session.get("previous_steps") or [],
            )

        raw_input = session.get("raw_input") if isinstance(session.get("raw_input"), dict) else {}
        project_seed = ProjectSeed(**raw_input)
        return await asyncio.to_thread(
            self._build_orchestrator(
                human_review_enabled=bool(project_seed.methodology_human_review),
                request_id=request_id,
                user_id=user_id,
                llm_provider=project_seed.llm_provider,
            ).run,
            raw_input=project_seed.model_dump(),
            track_files=session.get("track_paths") or [],
        )

    @staticmethod
    def _resolve_session_llm_provider(raw_input: dict[str, Any], session: dict[str, Any]) -> str | None:
        """Resolve provider from recovered context before falling back to default env routing."""
        if raw_input.get("llm_provider"):
            return str(raw_input["llm_provider"])
        nested_raw_input = raw_input.get("raw_input")
        if isinstance(nested_raw_input, dict) and nested_raw_input.get("llm_provider"):
            return str(nested_raw_input["llm_provider"])
        project_seed = session.get("project_seed")
        if isinstance(project_seed, dict) and project_seed.get("llm_provider"):
            return str(project_seed["llm_provider"])
        return None

    @staticmethod
    def _attach_review_actions(
        context: dict[str, Any],
        paused_session: dict[str, Any],
        review_comment: str | None,
        user_id: str,
    ) -> None:
        """Attach durable review audit actions to resumed flow context."""
        stored_review_actions = paused_session.get("review_actions") or []
        if stored_review_actions:
            context["methodology_review_actions"] = list(stored_review_actions)
        elif review_comment:
            context.setdefault("methodology_review_actions", []).append(
                {"action": "approved", "comment": review_comment, "user_id": user_id}
            )
