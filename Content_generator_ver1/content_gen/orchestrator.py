"""
content_gen/orchestrator.py

Оркестратор пайплайна генерации контента.

Главный координатор AgentFlow-пайплайна генерации.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Настраиваем logger для orchestrator
logger = logging.getLogger("content_gen.orchestrator")

# Thread pool для синхронного вызова async функций логирования
_log_executor = ThreadPoolExecutor(max_workers=2)

# Кэш последних фаз для дедупликации (request_id -> last_phase)
_last_phase_cache: dict[str, str] = {}

from .agents.task_planner import TaskPlanner
from .exceptions import ContentGenerationError
from .flow_handlers import GenerationFlowHandlers
from .flow_result import FlowResultFinalizer
from .llm.observed_client import ObservedLLMClient
from .methodology import (
    HumanApprovalCheckpointPolicy,
    MethodologyGate,
    MethodologyGateDecision,
    MethodologyGatePolicy,
    MethodologyTraceRecorder,
    ScopedRevisionExecutor,
)
from .methodology.repair import MethodologyRepairController
from .models.flow_state import ProjectFlowState
from .models.result import OrchestratorResult
from .generation_runtime import GenerationRuntimeContainer
from .observability import LLMTraceRecorder, UnifiedTraceSink, build_default_observability_exporters
from .node_executor_bundle import GenerationNodeExecutorBundle
from .node_services import SectionContextRecorder
from .result_assembly import ResultAssembler
from .utils.cancellation import CancellationToken
from .utils.progress import ProgressTracker
from .workflow.flow_runner import AgentFlowRunner, FlowExecutionStep, FlowNodeOutput, load_flow_definition


class Orchestrator:
    """
    Оркестратор пайплайна генерации контента.
    
    Использует AgentFlow с независимыми node services и встроенными проверками.
    """

    def __init__(
        self,
        llm_client,
        cancellation_token: CancellationToken = None,
        progress_tracker: ProgressTracker = None,
        methodology_progress_callback: Callable[[dict[str, Any]], None] | None = None,
        human_approval_enabled: bool | None = None,
        workflow_checkpoint_callback: Callable[[dict[str, Any]], None] | None = None,
        workflow_node_started_callback: Callable[[dict[str, Any]], None] | None = None,
        run_id: str | None = None,
        user_id: str | None = None,
    ):
        """
        Инициализация оркестратора.

        Args:
            llm_client: LLM клиент для генерации
            cancellation_token: Токен для отмены операций
            progress_tracker: Трекер прогресса генерации
            methodology_progress_callback: Callback для live-снимков автоматических проверок методолога
            human_approval_enabled: Явно включает/выключает human-in-the-loop checkpoint'ы
            workflow_checkpoint_callback: Callback для durable checkpoint'ов узлов
            workflow_node_started_callback: Callback для durable текущего узла
            run_id: Durable run id for observability correlation
            user_id: Durable user id for observability correlation
        """
        self.observability_sink = UnifiedTraceSink(
            run_id=run_id,
            user_id=user_id,
            exporters=build_default_observability_exporters(),
        )
        self.llm_trace_recorder = LLMTraceRecorder(sink=self.observability_sink)
        self.raw_llm = llm_client
        self.llm = ObservedLLMClient(
            llm_client,
            self.llm_trace_recorder,
            node="generation",
            agent="orchestrator",
            prompt_version="content_generation",
        )
        self.cancellation_token = cancellation_token or CancellationToken()
        self.progress_tracker = progress_tracker or ProgressTracker()
        self.runtime = GenerationRuntimeContainer(
            self.llm,
            cancellation_token=self.cancellation_token,
            progress_tracker=self.progress_tracker
        )
        self.runtime.observability_sink = self.observability_sink
        self.node_executors = GenerationNodeExecutorBundle.from_runtime(self.runtime)

        # Агенты, используемые в run_v2 для извлечения данных
        self.title_annot = self.runtime.title_annot
        self.task_planner = TaskPlanner()
        self.methodology_gate = MethodologyGate()
        self.methodology_gate_policy = MethodologyGatePolicy.from_env()
        if human_approval_enabled is False:
            self.human_checkpoint_policy = HumanApprovalCheckpointPolicy(set())
        elif human_approval_enabled is True:
            self.human_checkpoint_policy = HumanApprovalCheckpointPolicy(
                set(HumanApprovalCheckpointPolicy.DEFAULT_CHECKPOINTS)
            )
        else:
            self.human_checkpoint_policy = HumanApprovalCheckpointPolicy.from_env(
                enabled_by_default=methodology_progress_callback is not None,
            )
        self.methodology_progress_callback = methodology_progress_callback
        self.methodology_repair = MethodologyRepairController()
        self.methodology_trace = MethodologyTraceRecorder()
        self.scoped_revision_executor = ScopedRevisionExecutor(
            self.runtime.llm_for("methodology_review", "ScopedRevisionExecutor", "scoped_revision")
        )
        self.flow_result_finalizer = FlowResultFinalizer(self.methodology_trace)
        self.section_context_recorder = SectionContextRecorder()
        self.result_assembler = ResultAssembler(
            llm_client=self.llm,
            title_annotation_agent=self.title_annot,
            intro_splitter=self.runtime.intro._split_intro_instruction,
            theory_parts_parser=self.methodology_repair.parse_theory_parts,
            practice_tasks_parser=self.methodology_repair.parse_practice_tasks,
        )
        self.flow_handlers = GenerationFlowHandlers.from_node_executors(
            node_executors=self.node_executors,
            task_planner=self.task_planner,
            result_assembler=self.result_assembler,
            log_phase=self._log_phase_to_db,
            section_context_recorder=self.section_context_recorder,
        )
        self.flow_runner = AgentFlowRunner(
            load_flow_definition("content_generation"),
            cancellation_token=self.cancellation_token,
            progress_tracker=self.progress_tracker,
            stage_review_hook=self._review_stage,
            workflow_checkpoint_hook=workflow_checkpoint_callback,
            workflow_node_started_hook=workflow_node_started_callback,
        )

    def _log_phase(self, phase: str, agent: str = ""):
        """Логирует текущую фазу генерации в терминал."""
        if agent:
            logger.info(f"🔄 Фаза: {phase} | Агент: {agent}")
        else:
            logger.info(f"🔄 Фаза: {phase}")

    def _update_progress(self, phase: str, current: int, total: int, message: str = ""):
        """
        Обновляет прогресс генерации.
        
        Args:
            phase: Название фазы
            current: Текущий элемент (1-based)
            total: Всего элементов
            message: Дополнительное сообщение
        """
        self.progress_tracker.update(phase, current, total, message)
        self._log_phase_to_db(phase, message or f"{phase}: {current}/{total}")

    def _log_phase_to_db(self, phase: str, message: str = ""):
        """
        Логирует текущую фазу в базу данных для отображения в UI.
        
        Оптимизация: не логирует, если фаза не изменилась (дедупликация).
        
        Args:
            phase: Название фазы (например, 'context', 'skeleton', 'theory')
            message: Дополнительное сообщение
        """
        try:
            from api.db.logging_db import write_log
            from api.db.session import SessionLocal
            from api.utils.logging_context import get_request_id, get_user_id

            request_id = get_request_id()
            user_id = get_user_id()

            if not request_id:
                return  # Нет request_id, пропускаем логирование в БД

            # Дедупликация: не логируем, если фаза не изменилась
            last_phase = _last_phase_cache.get(request_id)
            if last_phase == phase:
                return  # Фаза не изменилась, пропускаем логирование

            # Обновляем кэш
            _last_phase_cache[request_id] = phase

            # Используем thread pool для синхронной записи лога
            def _write_log_sync():
                try:
                    db = SessionLocal()
                    try:
                        write_log(
                            db=db,
                            request_id=request_id,
                            level="INFO",
                            message=message or f"Выполняется фаза: {phase}",
                            user_id=user_id,
                            phase=phase,
                            metadata={"agent": phase}
                        )
                    finally:
                        db.close()
                except Exception as e:
                    logger.debug(f"Не удалось записать лог фазы в БД: {e}")

            # Выполняем в thread pool, чтобы не блокировать основной поток
            _log_executor.submit(_write_log_sync)
        except Exception as e:
            logger.debug(f"Ошибка при логировании фазы в БД: {e}")

    def _build_initial_context(self, raw_input: dict[str, Any], track_files: list[str] | None) -> dict[str, Any]:
        state = ProjectFlowState.from_initial_input(raw_input, track_files)
        context = state.to_context()
        context["llm_traces"] = self.llm_trace_recorder.events
        context["observability_sink"] = self.observability_sink
        return context

    def _review_stage(self, node, context: dict[str, Any], _output: FlowNodeOutput) -> list[str]:
        """Attach methodology review and bounded repair feedback after a node completes."""
        review = self.methodology_gate.review(node.id, context)
        self.methodology_trace.append_review(context, review)
        decision = self._record_gate_decision(context, review)

        messages: list[str] = []
        if review.status not in {"passed", "skipped"}:
            messages.extend(review.flow_issue_messages())
        messages.extend(decision.flow_issue_messages())

        repair = self.methodology_repair.repair(node.id, context, review)
        if repair is not None:
            self.methodology_trace.append_repair(context, repair)
            messages.extend(repair.flow_issue_messages())
            self.methodology_trace.sync_state(context)

            if repair.status == "applied":
                post_review = self.methodology_gate.review(node.id, context)
                post_review.evidence["after_repair"] = True
                post_review.evidence["repair_actions"] = repair.actions
                self.methodology_trace.append_review(context, post_review)
                post_decision = self._record_gate_decision(context, post_review)
                if post_review.status not in {"passed", "skipped"}:
                    messages.extend(post_review.flow_issue_messages())
                messages.extend(post_decision.flow_issue_messages())

        if node.id == "practice":
            dataset_review = self.methodology_gate.review("dataset_generation", context)
            self.methodology_trace.append_review(context, dataset_review)
            dataset_decision = self._record_gate_decision(context, dataset_review)
            if dataset_review.status not in {"passed", "skipped"}:
                messages.extend(dataset_review.flow_issue_messages())
            messages.extend(dataset_decision.flow_issue_messages())
            self.methodology_trace.sync_state(context)

        self.human_checkpoint_policy.maybe_raise(node.id, context)
        return messages

    def _record_gate_decision(
        self,
        context: dict[str, Any],
        review: Any,
    ) -> MethodologyGateDecision:
        """Persist a gate decision and publish a live UI snapshot when configured."""
        decision = self.methodology_gate_policy.decide(review)
        self.methodology_trace.append_decision(context, decision)

        payload = self.methodology_trace.gate_payload(context)
        if self.methodology_progress_callback is not None:
            try:
                self.methodology_progress_callback(payload)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Не удалось обновить live methodology snapshot: %s", exc)

        if decision.blocking:
            context["methodology_gate_blocking_decision"] = decision
            state = context.get("state")
            if hasattr(state, "stopped_at"):
                state.stopped_at = decision.stage
            if hasattr(state, "stopped_reason"):
                state.stopped_reason = decision.summary
            raise self.methodology_gate_policy.interrupt(decision)

        return decision

    @staticmethod
    def _serialize_issues(issues: list[Any]) -> list[Any]:
        """Serialize issue objects for report/json storage."""
        return GenerationFlowHandlers._serialize_issues(issues)

    @staticmethod
    def _has_hard_issues(issues: list[Any]) -> bool:
        """Check whether a list of validator issues contains hard failures."""
        return GenerationFlowHandlers._has_hard_issues(issues)

    @staticmethod
    def _issue_messages(issues: list[Any]) -> list[str]:
        """Extract human-readable messages from validator issues."""
        return GenerationFlowHandlers._issue_messages(issues)

    def run(self, raw_input: dict[str, Any], track_files: list[str] = None) -> OrchestratorResult:
        """
        Запускает полный пайплайн генерации.
        
        Использует AgentFlow с независимыми node services и встроенными проверками.

        Args:
            raw_input: Сырые входные данные от методолога
            track_files: Legacy аргумент; runtime-контекст берется из curriculum input

        Returns:
            OrchestratorResult с результатами генерации
        """
        return self.run_v2(raw_input, track_files)

    def run_v2(self, raw_input: dict[str, Any], track_files: list[str] = None) -> OrchestratorResult:
        """
        Запускает пайплайн генерации через AgentFlow.
        """
        context = self._build_initial_context(raw_input, track_files)
        return self._run_flow_from_context(context)

    def resume_from_pause(
        self,
        context: dict[str, Any],
        resume_from_index: int,
        previous_steps: list[FlowExecutionStep] | None = None,
    ) -> OrchestratorResult:
        """Continue a paused AgentFlow from the next node without rerunning completed nodes."""
        revision_results = self.scoped_revision_executor.apply_pending_change_requests(context)
        if not revision_results:
            revision_results = self.scoped_revision_executor.approved_preview_results_for_resume(context)
        resume_plan = self.scoped_revision_executor.build_resume_plan(
            resume_from_index,
            self.flow_runner.execution_plan,
            revision_results,
        )
        context["methodology_resume_plan"] = resume_plan.model_dump(mode="json")
        state = context.get("state")
        if hasattr(state, "sync_from_context"):
            state.sync_from_context(context)
        previous_steps = self.scoped_revision_executor.trim_previous_steps_for_resume(
            previous_steps,
            resume_plan.resume_from_index,
            self.flow_runner.execution_plan,
        )
        return self._run_flow_from_context(
            context,
            start_index=resume_plan.resume_from_index,
            previous_steps=previous_steps,
        )

    def resume_from_workflow_checkpoint(
        self,
        context: dict[str, Any],
        start_index: int,
        previous_steps: list[FlowExecutionStep] | None = None,
    ) -> OrchestratorResult:
        """Continue from a durable workflow checkpoint without methodology-specific edits."""
        state = context.get("state")
        if hasattr(state, "sync_from_context"):
            state.sync_from_context(context)
        return self._run_flow_from_context(
            context,
            start_index=max(0, int(start_index or 0)),
            previous_steps=previous_steps,
        )

    def _run_flow_from_context(
        self,
        context: dict[str, Any],
        start_index: int = 0,
        previous_steps: list[FlowExecutionStep] | None = None,
    ) -> OrchestratorResult:
        """Run or resume the configured flow over an existing mutable context."""
        try:
            context["llm_traces"] = self.llm_trace_recorder.events
            context["observability_sink"] = self.observability_sink
            registry = self.flow_handlers.registry()
            steps = self.flow_runner.run(
                context,
                registry,
                start_index=start_index,
                previous_steps=previous_steps,
            )
            context["llm_traces"] = self.llm_trace_recorder.events
            result = self.flow_result_finalizer.finalize(context, steps)
            self.observability_sink.flush()
            return result
        except Exception as exc:
            # Проверяем, не была ли это отмена
            from .utils.cancellation import CancelledError
            if isinstance(exc, CancelledError):
                raise
            if isinstance(exc, ContentGenerationError):
                raise
            raise ContentGenerationError(
                f"Ошибка в AgentFlow: {exc}",
                context={"phase": "flow", "error_type": type(exc).__name__},
            ) from exc
