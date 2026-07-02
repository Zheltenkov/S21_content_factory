"""Typed contracts for methodology stage reviews."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

IssueSeverity = Literal["info", "minor", "major", "critical"]
ReviewStatus = Literal["passed", "warning", "failed", "skipped"]
RepairStatus = Literal["applied", "skipped", "failed"]


class StageReviewIssue(BaseModel):
    """Single methodologist-style issue found after a flow stage."""

    code: str
    message: str
    severity: IssueSeverity = "minor"
    repair_hint: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class StageReviewResult(BaseModel):
    """Methodology review result attached to flow trace and report_json."""

    stage: str
    status: ReviewStatus
    issues: list[StageReviewIssue] = Field(default_factory=list)
    repair_instructions: list[str] = Field(default_factory=list)
    human_review_required: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0

    def flow_issue_messages(self) -> list[str]:
        """Compact representation for flow_trace issues."""
        messages: list[str] = []
        for issue in self.issues:
            messages.append(f"methodology:{issue.severity}:{issue.code}: {issue.message}")
        return messages


class StageRepairResult(BaseModel):
    """Bounded deterministic repair result attached to flow trace and report_json."""

    stage: str
    status: RepairStatus
    issue_codes: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    updated_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    skipped_reason: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0

    def flow_issue_messages(self) -> list[str]:
        """Compact representation for flow_trace issues."""
        if self.status == "applied":
            action_summary = "; ".join(self.actions[:3]) or "repair applied"
            return [f"methodology_repair:applied:{self.stage}: {action_summary}"]
        if self.status == "failed":
            warning_summary = "; ".join(self.warnings[:3]) or "repair failed"
            return [f"methodology_repair:failed:{self.stage}: {warning_summary}"]
        if self.skipped_reason:
            return [f"methodology_repair:skipped:{self.stage}: {self.skipped_reason}"]
        return []
