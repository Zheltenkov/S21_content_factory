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

from .title_policy import title_violations  # noqa: F401 — re-exported: full policy lives in title_policy

#: Prefixes emitted by ``planner._artifact_for`` when no specialised artifact is bound.
_GENERIC_ARTIFACT_PREFIXES = (
    "Проверяемый артефакт (",
    "Интегративный артефакт (",
)

#: Substrings of stale/formal artifacts that read as generic (e.g. the old capstone
#: "интеграционный артефакт" that is not a runnable result).
_GENERIC_ARTIFACT_MARKERS = (
    "интеграционный артефакт",
    "интеграционного артефакта",
)

#: Markers of the generic assessment-criteria fallback in ``_project_assessment_criteria``.
_GENERIC_CRITERION_MARKERS = (
    "создан и предъявлен",
    "результат можно проверить по заявленным ЗУН",
)


def is_generic_artifact(artifact: str) -> bool:
    """True when the artifact is the deterministic generic fallback, not a bound one."""
    text = str(artifact or "").strip()
    if any(text.startswith(prefix) for prefix in _GENERIC_ARTIFACT_PREFIXES):
        return True
    lowered = text.casefold()
    return any(marker in lowered for marker in _GENERIC_ARTIFACT_MARKERS)


def is_generic_criterion(criterion: str) -> bool:
    """True when the assessment criterion is the generic fallback (not project-specific)."""
    text = str(criterion or "")
    return all(marker in text for marker in _GENERIC_CRITERION_MARKERS)


def is_classified(row: dict[str, Any]) -> bool:
    """A project counts as classified when confirmed by a methodologist or auto-classified
    with high/medium confidence. Low/none confidence (or empty area) needs attention.
    Legacy rows without a confidence field but with an area are treated as classified."""
    if str(row.get("policy_area_source") or "auto") == "confirmed":
        return True
    area = str(row.get("policy_area") or "").strip()
    if not area:
        return False
    confidence = str(row.get("policy_area_confidence") or "")
    if not confidence:  # legacy plan persisted before confidence existed
        return True
    return confidence in {"high", "medium"}


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
            "low_confidence_classification_count": 0,
        }
    title_violation_count = sum(
        1
        for row in rows
        if title_violations(
            row.get("project_name") or row.get("title") or "",
            stage_title=str(row.get("block_title") or ""),
        )
    )
    generic_artifact_count = sum(1 for row in rows if is_generic_artifact(row.get("artifact") or ""))
    generic_criterion_count = sum(
        1 for row in rows if is_generic_criterion(row.get("validation_criteria") or "")
    )
    single_skill_project_count = sum(1 for row in rows if len(row.get("node_ids") or []) <= 1)
    unclassified_policy_area_count = sum(1 for row in rows if not is_classified(row))
    low_confidence_classification_count = sum(
        1
        for row in rows
        if str(row.get("policy_area_source") or "auto") == "auto"
        and str(row.get("policy_area_confidence") or "") in {"low", "none"}
    )
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
        "low_confidence_classification_count": low_confidence_classification_count,
    }
