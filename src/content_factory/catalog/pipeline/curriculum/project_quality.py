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
    if "→" in text:
        return False
    return all(marker in text for marker in _GENERIC_CRITERION_MARKERS)


def is_testable_criterion(criterion: str) -> bool:
    """True when the criterion is *structurally* verifiable, not merely non-generic.

    A verifiable criterion links a check to an expected result (the ``→`` produced by the
    policy acceptance renderer). Free prose that happens not to be the generic fallback is
    NOT counted as testable — being non-generic is not the same as being checkable.
    """
    text = str(criterion or "")
    return "→" in text


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
            "single_skill_count": 0,
            "project_count": 0,
            "project_count_by_type": {},
            "single_skill_count_by_type": {},
            "unclassified_policy_area_count": 0,
            "policy_area_coverage_pct": 0.0,
            "low_confidence_classification_count": 0,
            "activity_archetype_coverage_pct": 0.0,
            "activity_archetype_unclassified_count": 0,
            "activity_archetype_ambiguous_count": 0,
            "activity_archetype_count_by_type": {},
            "activity_modifier_count_by_type": {},
            "artifact_contract_coverage_pct": 0.0,
            "artifact_contract_unresolved_count": 0,
            "artifact_merge_warning_count": 0,
            "artifact_merge_error_count": 0,
            "artifact_contract_source_count_by_type": {},
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
    # FACTS only — the single-skill EXEMPTION (labs etc.) is policy, applied by the gate via
    # the profile's single_skill_exempt_kinds, not baked into the measurement.
    single_skill_count = sum(1 for row in rows if len(row.get("node_ids") or []) <= 1)
    project_count_by_type: dict[str, int] = {}
    single_skill_count_by_type: dict[str, int] = {}
    for row in rows:
        project_type = str(row.get("project_type") or "project")
        project_count_by_type[project_type] = project_count_by_type.get(project_type, 0) + 1
        if len(row.get("node_ids") or []) <= 1:
            single_skill_count_by_type[project_type] = single_skill_count_by_type.get(project_type, 0) + 1
    testable_criteria_count = sum(1 for row in rows if is_testable_criterion(row.get("validation_criteria") or ""))
    unclassified_policy_area_count = sum(1 for row in rows if not is_classified(row))
    low_confidence_classification_count = sum(
        1
        for row in rows
        if str(row.get("policy_area_source") or "auto") == "auto"
        and str(row.get("policy_area_confidence") or "") in {"low", "none"}
    )
    activity_archetype_count_by_type: dict[str, int] = {}
    activity_modifier_count_by_type: dict[str, int] = {}
    activity_archetype_unclassified_count = 0
    activity_archetype_ambiguous_count = 0
    artifact_contract_count = 0
    artifact_merge_warning_count = 0
    artifact_merge_error_count = 0
    artifact_contract_source_count_by_type: dict[str, int] = {}
    for row in rows:
        archetype = str(row.get("activity_archetype") or "").strip()
        suggestion = str(row.get("activity_archetype_suggestion") or "").strip()
        if archetype:
            activity_archetype_count_by_type[archetype] = activity_archetype_count_by_type.get(archetype, 0) + 1
        else:
            activity_archetype_unclassified_count += 1
            if suggestion:
                activity_archetype_ambiguous_count += 1
        modifiers = row.get("activity_archetype_modifiers") or []
        if isinstance(modifiers, (list, tuple)):
            for modifier in modifiers:
                key = str(modifier or "").strip()
                if key:
                    activity_modifier_count_by_type[key] = activity_modifier_count_by_type.get(key, 0) + 1
        if isinstance(row.get("artifact_contract"), dict) and row.get("artifact_contract"):
            artifact_contract_count += 1
        sources = row.get("artifact_contract_sources") or []
        if isinstance(sources, (list, tuple)):
            for source in sources:
                key = str(source or "").strip()
                if key:
                    artifact_contract_source_count_by_type[key] = (
                        artifact_contract_source_count_by_type.get(key, 0) + 1
                    )
        diagnostics = row.get("artifact_merge_diagnostics") or []
        if isinstance(diagnostics, list):
            for diagnostic in diagnostics:
                if not isinstance(diagnostic, dict):
                    continue
                severity = str(diagnostic.get("severity") or "").strip()
                artifact_merge_warning_count += int(severity == "warning")
                artifact_merge_error_count += int(severity == "error")
    return {
        "title_violation_count": title_violation_count,
        "generic_artifact_count": generic_artifact_count,
        "generic_criterion_count": generic_criterion_count,
        "testable_criteria_coverage_pct": round(testable_criteria_count / project_count * 100, 1),
        # RAW share of single-skill projects (NO exemptions) — the gate applies the profile's
        # single_skill_exempt_kinds. Kept for backward compatibility with existing consumers.
        "single_skill_project_pct": round(single_skill_count / project_count * 100, 1),
        "single_skill_count": single_skill_count,
        "project_count": project_count,
        "project_count_by_type": project_count_by_type,
        "single_skill_count_by_type": single_skill_count_by_type,
        "unclassified_policy_area_count": unclassified_policy_area_count,
        "policy_area_coverage_pct": round(
            (project_count - unclassified_policy_area_count) / project_count * 100, 1
        ),
        "low_confidence_classification_count": low_confidence_classification_count,
        "activity_archetype_coverage_pct": round(
            (project_count - activity_archetype_unclassified_count) / project_count * 100,
            1,
        ),
        "activity_archetype_unclassified_count": activity_archetype_unclassified_count,
        "activity_archetype_ambiguous_count": activity_archetype_ambiguous_count,
        "activity_archetype_count_by_type": activity_archetype_count_by_type,
        "activity_modifier_count_by_type": activity_modifier_count_by_type,
        "artifact_contract_coverage_pct": round(artifact_contract_count / project_count * 100, 1),
        "artifact_contract_unresolved_count": project_count - artifact_contract_count,
        "artifact_merge_warning_count": artifact_merge_warning_count,
        "artifact_merge_error_count": artifact_merge_error_count,
        "artifact_contract_source_count_by_type": artifact_contract_source_count_by_type,
    }
