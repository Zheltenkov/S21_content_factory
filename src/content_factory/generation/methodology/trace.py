"""Trace recording and report serialization for methodology reviews."""

from __future__ import annotations

from typing import Any

from .decision import MethodologyGateDecision
from .models import StageRepairResult, StageReviewResult


class MethodologyTraceRecorder:
    """Persist methodology review/repair objects in flow context and reports."""

    reviews_key = "methodology_reviews"
    repairs_key = "methodology_repairs"
    decisions_key = "methodology_gate_decisions"

    def append_review(self, context: dict[str, Any], review: StageReviewResult) -> None:
        """Append a stage review and sync typed flow state."""
        reviews = context.setdefault(self.reviews_key, [])
        reviews.append(review)
        self.sync_state(context)

    def append_repair(self, context: dict[str, Any], repair: StageRepairResult) -> None:
        """Append a stage repair and sync typed flow state."""
        repairs = context.setdefault(self.repairs_key, [])
        repairs.append(repair)
        self.sync_state(context)

    def append_decision(self, context: dict[str, Any], decision: MethodologyGateDecision) -> None:
        """Append a gate decision and sync typed flow state."""
        decisions = context.setdefault(self.decisions_key, [])
        decisions.append(decision)
        self.sync_state(context)

    def sync_state(self, context: dict[str, Any]) -> None:
        """Mirror methodology trace lists into ProjectFlowState when present."""
        state = context.get("state")
        if hasattr(state, self.reviews_key):
            setattr(state, self.reviews_key, context.get(self.reviews_key, []))
        if hasattr(state, self.repairs_key):
            setattr(state, self.repairs_key, context.get(self.repairs_key, []))
        if hasattr(state, self.decisions_key):
            setattr(state, self.decisions_key, context.get(self.decisions_key, []))

    def attach_to_result(
        self,
        result: Any,
        context: dict[str, Any],
        flow_trace: list[dict[str, Any]],
    ) -> None:
        """Attach serialized methodology trace to result and report_json."""
        reviews = self.serialize_reviews(context.get(self.reviews_key))
        repairs = self.serialize_repairs(context.get(self.repairs_key))
        decisions = self.serialize_decisions(context.get(self.decisions_key))
        gate_summary = self.gate_summary(decisions)

        result.flow_trace = flow_trace
        result.methodology_reviews = reviews
        result.methodology_repairs = repairs
        result.methodology_gate_decisions = decisions

        if not isinstance(getattr(result, "report_json", None), dict):
            return

        result.report_json["flow_trace"] = flow_trace
        result.report_json["methodology_reviews"] = reviews
        result.report_json["methodology_summary"] = self.review_summary(reviews)
        result.report_json["methodology_repairs"] = repairs
        result.report_json["methodology_repair_summary"] = self.repair_summary(repairs)
        result.report_json["methodology_gate_decisions"] = decisions
        result.report_json["methodology_gate_summary"] = gate_summary
        result.report_json["methodology_gate"] = {
            "summary": gate_summary,
            "decisions": decisions,
        }

    @staticmethod
    def serialize_reviews(reviews: list[Any] | None) -> list[dict[str, Any]]:
        """Serialize StageReviewResult-like values for report/json storage."""
        serialized: list[dict[str, Any]] = []
        for review in reviews or []:
            if isinstance(review, StageReviewResult):
                serialized.append(review.model_dump())
            elif hasattr(review, "model_dump"):
                serialized.append(review.model_dump())
            elif isinstance(review, dict):
                serialized.append(review)
        return serialized

    @staticmethod
    def serialize_repairs(repairs: list[Any] | None) -> list[dict[str, Any]]:
        """Serialize StageRepairResult-like values for report/json storage."""
        serialized: list[dict[str, Any]] = []
        for repair in repairs or []:
            if isinstance(repair, StageRepairResult):
                serialized.append(repair.model_dump())
            elif hasattr(repair, "model_dump"):
                serialized.append(repair.model_dump())
            elif isinstance(repair, dict):
                serialized.append(repair)
        return serialized

    @staticmethod
    def serialize_decisions(decisions: list[Any] | None) -> list[dict[str, Any]]:
        """Serialize MethodologyGateDecision-like values for report/json storage."""
        serialized: list[dict[str, Any]] = []
        for decision in decisions or []:
            if isinstance(decision, MethodologyGateDecision):
                serialized.append(decision.model_dump())
            elif hasattr(decision, "model_dump"):
                serialized.append(decision.model_dump())
            elif isinstance(decision, dict):
                serialized.append(decision)
        return serialized

    @staticmethod
    def review_summary(reviews: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate methodology review status/severity counts."""
        statuses: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        human_review_required = False
        for review in reviews:
            status = str(review.get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
            human_review_required = human_review_required or bool(review.get("human_review_required"))
            for issue in review.get("issues", []) or []:
                if not isinstance(issue, dict):
                    continue
                severity = str(issue.get("severity", "unknown"))
                severity_counts[severity] = severity_counts.get(severity, 0) + 1
        return {
            "total_reviews": len(reviews),
            "statuses": statuses,
            "severity_counts": severity_counts,
            "human_review_required": human_review_required,
        }

    @staticmethod
    def repair_summary(repairs: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate methodology repair status and updated field counts."""
        statuses: dict[str, int] = {}
        updated_fields: dict[str, int] = {}
        for repair in repairs:
            status = str(repair.get("status", "unknown"))
            statuses[status] = statuses.get(status, 0) + 1
            for field in repair.get("updated_fields", []) or []:
                field_name = str(field)
                updated_fields[field_name] = updated_fields.get(field_name, 0) + 1
        return {
            "total_repairs": len(repairs),
            "statuses": statuses,
            "updated_fields": updated_fields,
        }

    @staticmethod
    def gate_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
        """Aggregate methodology gate actions for UI and status endpoints."""
        actions: dict[str, int] = {}
        statuses: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        human_review_required = False
        blocking = False
        latest_stage: str | None = None
        latest_action: str | None = None

        for decision in decisions:
            action = str(decision.get("action", "unknown"))
            status = str(decision.get("status", "unknown"))
            actions[action] = actions.get(action, 0) + 1
            statuses[status] = statuses.get(status, 0) + 1
            human_review_required = human_review_required or bool(decision.get("human_review_required"))
            blocking = blocking or bool(decision.get("blocking"))
            latest_stage = str(decision.get("stage", "")) or latest_stage
            latest_action = action
            metrics = decision.get("metrics") if isinstance(decision.get("metrics"), dict) else {}
            for severity, count in (metrics.get("severity_counts") or {}).items():
                severity_name = str(severity)
                severity_counts[severity_name] = severity_counts.get(severity_name, 0) + int(count or 0)

        return {
            "total_decisions": len(decisions),
            "actions": actions,
            "statuses": statuses,
            "severity_counts": severity_counts,
            "human_review_required": human_review_required,
            "blocking": blocking,
            "can_continue": not blocking,
            "latest_stage": latest_stage,
            "latest_action": latest_action,
        }

    def gate_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        """Return the current gate payload for live status updates."""
        decisions = self.serialize_decisions(context.get(self.decisions_key))
        return {
            "summary": self.gate_summary(decisions),
            "decisions": decisions,
        }
