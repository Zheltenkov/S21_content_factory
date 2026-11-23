"""Deterministic recommendation and coverage, separate from model judgments."""
from collections import Counter

from .assessment import normalized
from .findings import grouped
from .models import Evaluation

SCORED_THROUGH = 40
VERSION = "2.0"


def presentation(report: Evaluation) -> Evaluation:
    """Project old and new reports into the current policy without rewriting stored history."""
    result = report.model_copy(deep=True)
    result.verdicts = normalized(report)
    for criterion in result.profile.criteria:
        if criterion.number > SCORED_THROUGH:
            criterion.blocker = False
            criterion.title = criterion.title.replace(" [блок]", "")
    return result


def summary(report: Evaluation, stale: bool = False) -> dict[str, object]:
    # Coverage, defects, and recommendation are separate dimensions; there is no calibrated quality score.
    verdicts = normalized(report)
    scored = [v for v in verdicts if v.criterion <= SCORED_THROUGH]
    reviews = [v for v in verdicts if v.criterion > SCORED_THROUGH]
    counts = Counter(v.state for v in scored)
    assessed = [v for v in scored if v.state == "assessed" and v.mark is not None]
    base = [v.mark for v in assessed if v.mark is not None]
    blockers = [v.criterion for v in assessed if v.confirmed_blocker and v.mark == -1]
    model_assessed = any(v.method not in {"code", "execution"} for v in assessed)
    unverified = SCORED_THROUGH - len(assessed) - counts["not_applicable"]
    # A partial/interrupted job can have finished all 40 scored checks and stopped only during expert review.
    incomplete = bool(unverified or counts["not_applicable"] == SCORED_THROUGH or report.snapshot.limitations or report.history_warnings)
    findings = grouped(report,verdicts)
    finding_counts = Counter(f.level for f in findings)
    recommendation = "insufficient"
    if blockers:
        recommendation = "rework"
    elif findings:
        recommendation = "targeted_revision"
    elif incomplete:
        recommendation = "preliminary" if assessed else "insufficient"
    else:
        recommendation = "ready_recommended"
    if stale:
        recommendation = "stale"
    return {"policy_version": VERSION, "recommendation": recommendation, "score": None,
            "score_basis": "Универсальный балл качества не откалиброван. Итог определяется по подтверждённым замечаниям и полноте проверки.",
            "findings": [f.model_dump() for f in findings], "finding_count": len(findings), "finding_counts": dict(finding_counts),
            "review_queue": [v.model_dump() for v in scored if not v.evidence_valid],
            "fulfilled": sum(v.mark == 1 for v in assessed),
            "coverage_percent": round(len(assessed)/(SCORED_THROUGH-counts["not_applicable"])*100,1) if counts["not_applicable"]<SCORED_THROUGH else None,
            "stage": "content_review" if model_assessed else "preflight", "base_assessed": len(base),
            "reviewed": sum(v.state == "assessed" for v in reviews), "review_counts": dict(Counter(v.state for v in reviews)),
            "review_total": 16, "counts": dict(counts), "assessed": len(assessed),
            "applicable": SCORED_THROUGH - counts["not_applicable"], "unverified": unverified,
            "blockers": blockers, "problems": sum(v.mark == -1 for v in assessed),
            "partial": sum(v.mark == 0 for v in assessed), "incomplete": incomplete, "stale": stale,
            "experimental": report.profile.experimental, "blocks_workflow": False}
