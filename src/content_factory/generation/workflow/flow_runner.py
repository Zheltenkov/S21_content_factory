"""Workflow runtime for AgentFlow execution.

The legacy import path ``content_gen.agents.flow`` re-exports these contracts
for paused-session and older caller compatibility.
"""

from __future__ import annotations

import ast
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..methodology.decision import MethodologyGateInterrupt
from ..node_contracts import NodeContract, load_node_contracts
from ..observability import NodeTraceEvent
from ..utils.cancellation import CancellationToken, CancelledError
from ..utils.progress import ProgressTracker


class FlowNodeConfig(BaseModel):
    """Узел графа агента."""

    id: str
    name: str
    handler: str
    type: str = "agent"
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    conditions: dict[str, str] = Field(default_factory=dict)


class FlowEdgeConfig(BaseModel):
    """Ребро графа (упорядочивает узлы и описывает условия перехода)."""

    source: str
    target: str
    condition: str | None = None


class FlowDefinition(BaseModel):
    """Полный граф AgentFlow."""

    name: str
    version: str
    nodes: list[FlowNodeConfig]
    edges: list[FlowEdgeConfig]


class FlowLibrary(BaseModel):
    """Коллекция доступных флоу."""

    flows: dict[str, FlowDefinition]


@dataclass
class FlowNodeOutput:
    """Стандартный ответ узла для рантайма."""

    updates: dict[str, object] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    status: Literal["success", "skipped", "error"] = "success"


@dataclass
class FlowExecutionStep:
    """Лог одного шага Flow."""

    node_id: str
    node_name: str
    status: Literal["success", "skipped", "error", "cancelled", "paused"]
    duration_ms: float
    issues: list[str] = field(default_factory=list)

    def as_dict(self, index: int) -> dict[str, object]:
        return {
            "step_index": index,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
            "issues": self.issues,
        }


class AgentFlowRunner:
    """Исполнитель графа агентов по конфигу."""

    def __init__(
        self,
        definition: FlowDefinition,
        cancellation_token: CancellationToken = None,
        progress_tracker: ProgressTracker = None,
        stage_review_hook: Callable[[FlowNodeConfig, dict[str, object], FlowNodeOutput], list[str]] | None = None,
        workflow_checkpoint_hook: Callable[[dict[str, Any]], None] | None = None,
        workflow_node_started_hook: Callable[[dict[str, Any]], None] | None = None,
        node_contracts: dict[str, NodeContract] | None = None,
    ):
        self.definition = definition
        self.node_map = {node.id: node for node in definition.nodes}
        self.execution_plan = self._build_execution_plan(definition)
        self.cancellation_token = cancellation_token or CancellationToken()
        self.progress_tracker = progress_tracker or ProgressTracker()
        self.stage_review_hook = stage_review_hook
        self.workflow_checkpoint_hook = workflow_checkpoint_hook
        self.workflow_node_started_hook = workflow_node_started_hook
        self.node_contracts = node_contracts if node_contracts is not None else self._load_node_contracts()

    def run(
        self,
        context: dict[str, object],
        registry: dict[str, Callable[[dict[str, object]], FlowNodeOutput]],
        start_index: int = 0,
        previous_steps: list[FlowExecutionStep] | None = None,
    ) -> list[FlowExecutionStep]:
        """Выполняет граф, возвращает лог шагов."""
        steps: list[FlowExecutionStep] = list(previous_steps or [])
        total_nodes = len(self.execution_plan)
        start_index = max(0, min(start_index, total_nodes))

        for idx, node_id in enumerate(self.execution_plan[start_index:], start_index + 1):
            # Проверка на отмену перед каждым узлом
            self.cancellation_token.check()

            # Обновляем прогресс
            self.progress_tracker.update(
                phase="flow",
                current=idx,
                total=total_nodes,
                message=f"Выполнение узла: {node_id}"
            )

            node = self.node_map[node_id]
            self._emit_workflow_node_started(
                node=node,
                checkpoint_index=idx,
                total_nodes=total_nodes,
            )
            if self._should_skip_node(node, context):
                trace_event = self._build_node_trace(
                    node=node,
                    context=context,
                    duration_ms=0.0,
                    status="skipped",
                    issues=["condition=false"],
                    output_schema=None,
                )
                context.setdefault("node_traces", []).append(trace_event.model_dump(mode="json"))
                skip_step = FlowExecutionStep(
                    node_id=node.id,
                    node_name=node.name,
                    status="skipped",
                    duration_ms=0.0,
                    issues=["condition=false"],
                )
                steps.append(skip_step)
                self._emit_workflow_checkpoint(
                    step=skip_step,
                    trace_event=trace_event,
                    checkpoint_index=idx,
                    output_artifact={},
                    context=context,
                )
                self._record_node_observability(context, trace_event, {})
                state = context.get("state")
                if hasattr(state, "sync_from_context"):
                    state.sync_from_context(context)
                continue
            handler = registry.get(node.handler)
            start = time.time()
            if handler is None:
                raise RuntimeError(f"Handler '{node.handler}' is not registered for node '{node.id}'")
            try:
                output = handler(context)
                if not isinstance(output, FlowNodeOutput):
                    raise TypeError(
                        f"Handler '{node.handler}' должен возвращать FlowNodeOutput, получено {type(output)}"
                    )

                # Проверка на отмену после обработки
                self.cancellation_token.check()

                context.update(output.updates or {})
                state = context.get("state")
                if hasattr(state, "apply_updates"):
                    state.apply_updates(output.updates or {})
                if hasattr(state, "sync_from_context"):
                    state.sync_from_context(context)
                review_issues: list[str] = []
                if self.stage_review_hook is not None:
                    try:
                        review_issues = self.stage_review_hook(node, context, output) or []
                    except Exception as exc:  # noqa: BLE001
                        if isinstance(exc, MethodologyGateInterrupt):
                            raise
                        review_issues = [f"methodology_gate_error: {exc}"]
                    if hasattr(state, "sync_from_context"):
                        state.sync_from_context(context)
                duration_ms = (time.time() - start) * 1000
                trace_event = self._build_node_trace(
                    node=node,
                    context=context,
                    duration_ms=duration_ms,
                    status=output.status,
                    issues=[*(output.issues or []), *review_issues],
                    output_schema=",".join(sorted((output.updates or {}).keys())) or None,
                )
                context.setdefault("node_traces", []).append(trace_event.model_dump(mode="json"))
                step = FlowExecutionStep(
                    node_id=node.id,
                    node_name=node.name,
                    status=output.status,
                    duration_ms=duration_ms,
                    issues=[*(output.issues or []), *review_issues],
                )
                steps.append(step)
                self._emit_workflow_checkpoint(
                    step=step,
                    trace_event=trace_event,
                    checkpoint_index=idx,
                    output_artifact=self._compact_artifact(output.updates or {}),
                    context=context,
                )
                self._record_node_observability(
                    context,
                    trace_event,
                    self._compact_artifact(output.updates or {}),
                )
                if output.status == "error":
                    break
            except MethodologyGateInterrupt as exc:
                duration_ms = (time.time() - start) * 1000
                trace_event = self._build_node_trace(
                    node=node,
                    context=context,
                    duration_ms=duration_ms,
                    status="paused",
                    issues=[str(exc)],
                    output_schema=None,
                )
                context.setdefault("node_traces", []).append(trace_event.model_dump(mode="json"))
                pause_step = FlowExecutionStep(
                    node_id=node.id,
                    node_name=node.name,
                    status="paused",
                    duration_ms=duration_ms,
                    issues=[str(exc)],
                )
                steps.append(pause_step)
                self._emit_workflow_checkpoint(
                    step=pause_step,
                    trace_event=trace_event,
                    checkpoint_index=idx,
                    output_artifact={},
                    context=context,
                )
                self._record_node_observability(context, trace_event, {})
                # idx is 1-based, therefore it is also the zero-based index of the next node.
                exc.attach_flow_state(
                    flow_context=context,
                    flow_steps=steps,
                    resume_from_index=idx,
                )
                raise
            except CancelledError as exc:
                duration_ms = (time.time() - start) * 1000
                trace_event = self._build_node_trace(
                    node=node,
                    context=context,
                    duration_ms=duration_ms,
                    status="cancelled",
                    issues=[f"Отменено: {exc.reason}"],
                    output_schema=None,
                )
                context.setdefault("node_traces", []).append(trace_event.model_dump(mode="json"))
                cancel_step = FlowExecutionStep(
                    node_id=node.id,
                    node_name=node.name,
                    status="cancelled",
                    duration_ms=duration_ms,
                    issues=[f"Отменено: {exc.reason}"],
                )
                steps.append(cancel_step)
                self._emit_workflow_checkpoint(
                    step=cancel_step,
                    trace_event=trace_event,
                    checkpoint_index=idx,
                    output_artifact={},
                    context=context,
                )
                self._record_node_observability(context, trace_event, {})
                break
            except Exception as exc:  # noqa: BLE001
                duration_ms = (time.time() - start) * 1000
                trace_event = self._build_node_trace(
                    node=node,
                    context=context,
                    duration_ms=duration_ms,
                    status="error",
                    issues=[str(exc)],
                    output_schema=None,
                )
                context.setdefault("node_traces", []).append(trace_event.model_dump(mode="json"))
                error_step = FlowExecutionStep(
                    node_id=node.id,
                    node_name=node.name,
                    status="error",
                    duration_ms=duration_ms,
                    issues=[str(exc)],
                )
                steps.append(error_step)
                self._emit_workflow_checkpoint(
                    step=error_step,
                    trace_event=trace_event,
                    checkpoint_index=idx,
                    output_artifact={},
                    context=context,
                )
                self._record_node_observability(context, trace_event, {})
                raise
        return steps

    def _build_node_trace(
        self,
        *,
        node: FlowNodeConfig,
        context: dict[str, object],
        duration_ms: float,
        status: str,
        issues: list[str],
        output_schema: str | None,
    ) -> NodeTraceEvent:
        """Create a typed observability trace for one node execution."""
        input_payload = {
            key: context.get(key)
            for key in node.inputs
            if key in context and key not in {"state"}
        }
        if not input_payload:
            input_payload = {"context_keys": sorted(str(key) for key in context.keys() if key != "state")}
        model = context.get("model")
        contract = self.node_contracts.get(node.id)
        metadata: dict[str, Any] = {"node_name": node.name, "handler": node.handler}
        if contract is not None:
            metadata.update(contract.trace_metadata())
        declared_output_schema = ",".join(contract.output_schema) if contract is not None else None
        return NodeTraceEvent.from_node_execution(
            node=node.id,
            inputs=input_payload,
            latency_ms=duration_ms,
            status=status,
            issues=issues,
            prompt_version=contract.prompt_version if contract is not None else self.definition.version,
            model=str(model) if model else None,
            output_schema=output_schema or declared_output_schema,
            metadata=metadata,
        )

    @staticmethod
    def _load_node_contracts() -> dict[str, NodeContract]:
        """Load node contracts for trace metadata without making flow startup fragile."""
        try:
            return load_node_contracts()
        except Exception:
            return {}

    def _emit_workflow_node_started(
        self,
        *,
        node: FlowNodeConfig,
        checkpoint_index: int,
        total_nodes: int,
    ) -> None:
        """Notify the durable workflow layer that a node became active."""
        if self.workflow_node_started_hook is None:
            return
        try:
            self.workflow_node_started_hook(
                {
                    "node_id": node.id,
                    "node_name": node.name,
                    "checkpoint_index": checkpoint_index,
                    "total_nodes": total_nodes,
                }
            )
        except Exception:
            # Workflow persistence must never make the generation node fail.
            return

    @staticmethod
    def _record_node_observability(
        context: dict[str, object],
        trace_event: NodeTraceEvent,
        output_artifact: dict[str, Any],
    ) -> None:
        """Send node traces to the run-scoped observability sink when present."""
        sink = context.get("observability_sink")
        if sink is None or not hasattr(sink, "record_node_trace"):
            return
        try:
            sink.record_node_trace(trace_event, output_artifact=output_artifact)
        except Exception:
            return

    def _emit_workflow_checkpoint(
        self,
        *,
        step: FlowExecutionStep,
        trace_event: NodeTraceEvent,
        checkpoint_index: int,
        output_artifact: dict[str, Any],
        context: dict[str, object],
    ) -> None:
        """Notify the durable workflow layer about a completed/paused node."""
        if self.workflow_checkpoint_hook is None:
            return
        try:
            self.workflow_checkpoint_hook(
                {
                    "node_id": step.node_id,
                    "node_name": step.node_name,
                    "checkpoint_index": checkpoint_index,
                    "status": step.status,
                    "duration_ms": step.duration_ms,
                    "issues": step.issues,
                    "input_hash": trace_event.input_hash,
                    "retry_count": trace_event.repair_attempts,
                    "validation": trace_event.validation.model_dump(mode="json"),
                    "output_artifact": output_artifact,
                    "context_snapshot": context,
                }
            )
        except Exception:
            return

    @classmethod
    def _compact_artifact(cls, artifact: dict[str, object]) -> dict[str, Any]:
        """Store node outputs as compact JSON-safe checkpoint artifacts."""
        return {str(key): cls._compact_value(value) for key, value in artifact.items()}

    @classmethod
    def _compact_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"type": "str", "chars": len(value), "preview": value[:700]}
        if isinstance(value, BaseModel):
            return cls._compact_value(value.model_dump(mode="json"))
        if isinstance(value, dict):
            items = list(value.items())[:20]
            compact = {str(key): cls._compact_value(item_value) for key, item_value in items}
            if len(value) > len(items):
                compact["_truncated_keys"] = len(value) - len(items)
            return compact
        if isinstance(value, list):
            preview = [cls._compact_value(item) for item in value[:10]]
            if len(value) > len(preview):
                preview.append({"_truncated_items": len(value) - len(preview)})
            return preview
        if isinstance(value, (tuple, set)):
            return cls._compact_value(list(value))
        return value

    def _should_skip_node(self, node: FlowNodeConfig, context: dict[str, object]) -> bool:
        """Evaluate optional node conditions from YAML config."""
        if not node.conditions:
            return False

        run_if = node.conditions.get("run_if")
        if run_if and not self._evaluate_condition(run_if, context):
            return True

        skip_if = node.conditions.get("skip_if")
        if skip_if and self._evaluate_condition(skip_if, context):
            return True

        return False

    def _evaluate_condition(self, expression: str, context: dict[str, object]) -> bool:
        """Safely evaluate a small boolean expression against the flow context."""
        safe_globals = {"__builtins__": {}}
        safe_locals = {
            key: value
            for key, value in context.items()
            if key != "state"
        }
        state = context.get("state")
        if hasattr(state, "model_dump"):
            try:
                safe_locals.update(state.model_dump(exclude_none=True, mode="python"))
            except Exception:
                pass

        try:
            parsed = ast.parse(expression, mode="eval")
            compiled = compile(parsed, "<flow-condition>", "eval")
            return bool(eval(compiled, safe_globals, safe_locals))
        except Exception:
            return False

    def _build_execution_plan(self, definition: FlowDefinition) -> list[str]:
        """Вычисляет топологический порядок исполнения."""
        node_ids = [node.id for node in definition.nodes]
        duplicate_ids = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
        if duplicate_ids:
            raise RuntimeError(f"AgentFlow содержит повторяющиеся node id: {', '.join(duplicate_ids)}")

        indegree: dict[str, int] = {node.id: 0 for node in definition.nodes}
        adjacency: dict[str, list[str]] = {node.id: [] for node in definition.nodes}
        for edge in definition.edges:
            if edge.source not in adjacency:
                raise RuntimeError(
                    f"AgentFlow edge source '{edge.source}' не найден среди узлов flow"
                )
            if edge.target not in indegree:
                raise RuntimeError(
                    f"AgentFlow edge target '{edge.target}' не найден среди узлов flow"
                )
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

        queue = [node_id for node_id, deg in indegree.items() if deg == 0]
        plan: list[str] = []
        while queue:
            current = queue.pop(0)
            plan.append(current)
            for neighbor in adjacency[current]:
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)

        if len(plan) != len(definition.nodes):
            raise RuntimeError("AgentFlow содержит цикл или некорректные зависимости")
        return plan


def load_flow_definition(flow_name: str = "content_generation") -> FlowDefinition:
    """Загружает FlowDefinition из YAML."""
    config_path = Path(__file__).resolve().parents[1] / "config" / "flow.yaml"
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    library = FlowLibrary(**raw)
    if flow_name not in library.flows:
        raise KeyError(f"Flow '{flow_name}' не найден в config/flow.yaml")
    return library.flows[flow_name]

