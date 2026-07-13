"""Report-only project-quality metrics for the curriculum plan.

Slice 1 of the project-contract epic (see ``docs/project-contract-epic.md``): these
functions *measure* the defects surfaced by the real CSV run — long titles, generic
artifacts, generic (non-testable) acceptance criteria, single-skill projects — without
changing any behaviour or blocking anything. Later slices (title policy, artifact policy
registry, quality gate) will *fix* these; the gate slice turns the same numbers into
publish-blocking thresholds. Detectors match the deterministic fallbacks emitted by
``planner._artifact_for`` and ``stage_dag_to_up._project_assessment_criteria``.

Pure/stdlib leaf: operates on the final UP row dicts, no domain or DB imports.
"""

from __future__ import annotations

from typing import Any

#: First-cut ProjectTitlePolicy thresholds (the full policy lands in a later slice).
TITLE_MAX_CHARS = 72
TITLE_MAX_WORDS = 8

#: Prefixes emitted by ``planner._artifact_for`` when no specialised artifact is bound.
_GENERIC_ARTIFACT_PREFIXES = (
    "Проверяемый артефакт (",
    "Интегративный артефакт (",
)

#: Markers of the generic assessment-criteria fallback in ``_project_assessment_criteria``.
_GENERIC_CRITERION_MARKERS = (
    "создан и предъявлен",
    "результат можно проверить по заявленным ЗУН",
)


def title_violations(title: str) -> tuple[str, ...]:
    """First-cut title-policy violations for a project title (report-only)."""
    text = str(title or "").strip()
    reasons: list[str] = []
    if len(text) > TITLE_MAX_CHARS:
        reasons.append("too_long")
    if len(text.split()) > TITLE_MAX_WORDS:
        reasons.append("too_many_words")
    return tuple(reasons)


def is_generic_artifact(artifact: str) -> bool:
    """True when the artifact is the deterministic generic fallback, not a bound one."""
    text = str(artifact or "").strip()
    return any(text.startswith(prefix) for prefix in _GENERIC_ARTIFACT_PREFIXES)


def is_generic_criterion(criterion: str) -> bool:
    """True when the assessment criterion is the generic fallback (not project-specific)."""
    text = str(criterion or "")
    return all(marker in text for marker in _GENERIC_CRITERION_MARKERS)


def report_only_quality_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the report-only contract-quality metrics over final UP rows.

    Slice-1 semantics: ``testable_criteria_coverage_pct`` counts criteria that are *not*
    the generic fallback (a proxy until AcceptanceCriterion is introduced). All values
    are informational; nothing blocks. Metrics that require later infrastructure
    (template/policy binding coverage, blocking questions) are added in their own slices.
    """
    project_count = len(rows)
    if not project_count:
        return {
            "title_violation_count": 0,
            "generic_artifact_count": 0,
            "generic_criterion_count": 0,
            "testable_criteria_coverage_pct": 0.0,
            "single_skill_project_pct": 0.0,
            "unclassified_policy_area_count": 0,
            "policy_area_coverage_pct": 0.0,
        }
    title_violation_count = sum(
        1 for row in rows if title_violations(row.get("project_name") or row.get("title") or "")
    )
    generic_artifact_count = sum(1 for row in rows if is_generic_artifact(row.get("artifact") or ""))
    generic_criterion_count = sum(
        1 for row in rows if is_generic_criterion(row.get("validation_criteria") or "")
    )
    single_skill_project_count = sum(1 for row in rows if len(row.get("node_ids") or []) <= 1)
    unclassified_policy_area_count = sum(1 for row in rows if not str(row.get("policy_area") or "").strip())
    return {
        "title_violation_count": title_violation_count,
        "generic_artifact_count": generic_artifact_count,
        "generic_criterion_count": generic_criterion_count,
        "testable_criteria_coverage_pct": round(
            (project_count - generic_criterion_count) / project_count * 100, 1
        ),
        "single_skill_project_pct": round(single_skill_project_count / project_count * 100, 1),
        "unclassified_policy_area_count": unclassified_policy_area_count,
        "policy_area_coverage_pct": round(
            (project_count - unclassified_policy_area_count) / project_count * 100, 1
        ),
    }
