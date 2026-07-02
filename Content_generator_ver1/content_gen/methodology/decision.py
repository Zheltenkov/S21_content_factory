"""Execution decisions derived from methodology reviews."""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field

from content_gen.exceptions import ContentGenerationError

from .models import StageReviewResult

GateAction = Literal["continue", "warn", "pause", "fail"]
GateMode = Literal["observe", "approval", "strict"]


class MethodologyGateDecision(BaseModel):
    """UI- and orchestration-friendly decision for a methodology review."""

    stage: str
    action: GateAction
    mode: GateMode = "observe"
    status: str = "passed"
    title: str = ""
    summary: str = ""
    issues: list[dict[str, Any]] = Field(default_factory=list)
    human_review_required: bool = False
    can_continue: bool = True
    blocking: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)

    def flow_issue_messages(self) -> list[str]:
        """Compact representation for flow trace."""
        if self.action == "continue":
            return []
        return [
            (
                f"methodology_gate:{self.action}:{self.stage}: "
                f"{self.summary or self.title or self.status}"
            )
        ]


class MethodologyGateInterrupt(ContentGenerationError):
    """Controlled stop raised when the gate policy blocks a stage."""

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        super().__init__(message, context=context)
        self.flow_context: dict[str, Any] | None = None
        self.flow_steps: list[Any] = []
        self.resume_from_index: int | None = None

    def attach_flow_state(
        self,
        flow_context: dict[str, Any],
        flow_steps: list[Any],
        resume_from_index: int,
    ) -> None:
        """Attach in-memory resume data captured by AgentFlowRunner."""
        self.flow_context = flow_context
        self.flow_steps = flow_steps
        self.resume_from_index = resume_from_index


class MethodologyGatePolicy:
    """Convert deterministic reviews into execution decisions."""

    def __init__(self, mode: GateMode = "observe") -> None:
        self.mode = mode

    @classmethod
    def from_env(cls) -> "MethodologyGatePolicy":
        """Build policy from env without making strict gating the default."""
        raw_mode = os.getenv("METHODOLOGY_GATE_MODE", "observe").strip().lower()
        mode: GateMode = raw_mode if raw_mode in {"observe", "approval", "strict"} else "observe"  # type: ignore[assignment]
        return cls(mode=mode)

    def decide(self, review: StageReviewResult) -> MethodologyGateDecision:
        """Return an action that orchestration and UI can understand."""
        issues = [issue.model_dump() for issue in review.issues]
        severity_counts: dict[str, int] = {}
        for issue in review.issues:
            severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1

        has_critical = severity_counts.get("critical", 0) > 0
        has_major = severity_counts.get("major", 0) > 0

        action: GateAction = "continue"
        if review.status == "skipped":
            action = "continue"
        elif review.status == "passed":
            action = "continue"
        elif self.mode == "strict" and has_critical:
            action = "fail"
        elif self.mode == "approval" and (has_critical or review.human_review_required):
            action = "pause"
        elif review.status in {"warning", "failed"} or has_major:
            action = "warn"

        blocking = action in {"pause", "fail"}
        return MethodologyGateDecision(
            stage=review.stage,
            action=action,
            mode=self.mode,
            status=review.status,
            title=self._title(review.stage, action),
            summary=self._summary(review, action, severity_counts),
            issues=issues,
            human_review_required=review.human_review_required or action == "pause",
            can_continue=not blocking,
            blocking=blocking,
            metrics={
                "severity_counts": severity_counts,
                "issues_count": len(review.issues),
                "duration_ms": review.duration_ms,
            },
        )

    @staticmethod
    def interrupt(decision: MethodologyGateDecision) -> MethodologyGateInterrupt:
        """Build a controlled exception for blocking decisions."""
        error_type = "MethodologyGatePause" if decision.action == "pause" else "MethodologyGateFail"
        return MethodologyGateInterrupt(
            decision.summary or decision.title,
            context={
                "phase": decision.stage,
                "error_type": error_type,
                "methodology_gate_decision": decision.model_dump(),
            },
        )

    @staticmethod
    def _title(stage: str, action: GateAction) -> str:
        action_titles = {
            "continue": "Методологическая проверка пройдена",
            "warn": "Есть методологические предупреждения",
            "pause": "Нужна проверка методолога",
            "fail": "Методологический gate остановил этап",
        }
        return f"{action_titles[action]}: {stage}"

    @staticmethod
    def _summary(
        review: StageReviewResult,
        action: GateAction,
        severity_counts: dict[str, int],
    ) -> str:
        if action == "continue":
            return "Этап соответствует текущим методологическим контрактам."

        parts: list[str] = []
        for severity in ("critical", "major", "minor", "info"):
            count = severity_counts.get(severity, 0)
            if count:
                parts.append(f"{severity}: {count}")
        severity_text = ", ".join(parts) or f"status: {review.status}"

        if action == "pause":
            return f"Этап требует ручной проверки перед продолжением ({severity_text})."
        if action == "fail":
            return f"Этап не прошел strict-gate ({severity_text})."
        return f"Генерация продолжена, но есть замечания методолога ({severity_text})."
