"""Post-processing for completed AgentFlow executions."""

from __future__ import annotations

from typing import Any

from .exceptions import ContentGenerationError
from .methodology.trace import MethodologyTraceRecorder
from .models.result import OrchestratorResult
from .workflow.flow_runner import FlowExecutionStep


class FlowResultFinalizer:
    """Validate flow output and attach execution trace artifacts to the result."""

    def __init__(self, methodology_trace: MethodologyTraceRecorder) -> None:
        self.methodology_trace = methodology_trace

    def finalize(
        self,
        context: dict[str, Any],
        steps: list[FlowExecutionStep],
    ) -> OrchestratorResult:
        """Return OrchestratorResult or raise a domain error for incomplete flow."""
        result: OrchestratorResult | None = context.get("result")
        if result is None:
            self._raise_missing_result(steps)

        flow_trace = [step.as_dict(index) for index, step in enumerate(steps)]
        self.methodology_trace.attach_to_result(result, context, flow_trace)
        return result

    @staticmethod
    def _raise_missing_result(steps: list[FlowExecutionStep]) -> None:
        last_step = steps[-1] if steps else None
        if last_step and last_step.status == "error":
            issue_summary = "; ".join(last_step.issues[:3]) or "критические проверки не пройдены"
            raise ContentGenerationError(
                f"Flow остановлен на узле '{last_step.node_name}': {issue_summary}",
                context={"phase": last_step.node_id, "error_type": "FlowNodeError"},
            )
        raise ContentGenerationError(
            "Flow завершился без результата",
            context={"phase": "finalize", "error_type": "MissingResult"},
        )
