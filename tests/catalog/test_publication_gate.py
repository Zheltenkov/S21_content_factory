"""Semantic publication gate + waiver + profile interpretation (redirect step 2)."""

from __future__ import annotations

from dataclasses import replace

from content_factory.catalog.pipeline.curriculum.methodology_profile import (
    DEFAULT_PROFILE,
    DIGITAL_PRODUCT_PROJECT_BASED_V1,
    PublicationThresholds,
)
from content_factory.catalog.pipeline.curriculum.publication_gate import (
    PublicationWaiver,
    evaluate_publication_gate,
)

_CLEAN = {
    "title_violation_count": 0,
    "generic_artifact_count": 0,
    "policy_area_coverage_pct": 100.0,
    "activity_archetype_coverage_pct": 100.0,
    "activity_archetype_count_by_type": {"construct": 5},
    "artifact_contract_coverage_pct": 100.0,
    "artifact_merge_error_count": 0,
    "testable_criteria_coverage_pct": 100.0,
    "blocking_question_count": 0,
    "single_skill_project_pct": 20.0,
    "project_count": 5,
    "single_skill_count_by_type": {"project": 1},
    "capstone_required": True,
    "capstone_present": True,
}


def _gate(metrics: dict, **kw):
    return evaluate_publication_gate(metrics, profile=DEFAULT_PROFILE, **kw)


def test_v2_clean_metrics_pass() -> None:
    result = _gate(_CLEAN)
    assert result.passed is True
    assert result.failures == ()


def test_each_violation_blocks_under_v1() -> None:
    cases = [
        ("title_violation_count", 2, "title_violations"),
        ("generic_artifact_count", 1, "generic_artifacts"),
        ("policy_area_coverage_pct", 80.0, "policy_area_incomplete"),
        ("testable_criteria_coverage_pct", 90.0, "untestable_criteria"),
        ("blocking_question_count", 3, "blocking_questions"),
    ]
    for key, value, code in cases:
        result = _gate({**_CLEAN, key: value})
        assert result.passed is False
        assert code in {failure.code for failure in result.failures}


def test_labs_exempt_from_single_skill_via_profile() -> None:
    # 3 of 5 projects single-skill, but all are labs → exempt under V1 → passes.
    metrics = {**_CLEAN, "project_count": 5, "single_skill_project_pct": 60.0, "single_skill_count_by_type": {"lab": 3}}
    assert _gate(metrics).passed is True
    # same raw share but non-lab → blocks.
    metrics_project = {**metrics, "single_skill_count_by_type": {"project": 3}}
    result = _gate(metrics_project)
    assert result.passed is False
    assert "single_skill_excess" in {f.code for f in result.failures}


def test_capstone_required_but_missing_blocks() -> None:
    result = _gate({**_CLEAN, "capstone_present": False})
    assert "capstone_missing" in {f.code for f in result.failures}


def test_unknown_profile_blocks_publish_not_draft() -> None:
    result = evaluate_publication_gate(_CLEAN, profile=None)
    assert result.passed is False
    assert [f.code for f in result.failures] == ["methodology_profile_unavailable"]


def test_same_metrics_different_profile_changes_only_the_verdict() -> None:
    # A lab-practicum-style profile that allows a high single-skill share: same facts pass.
    lab_profile = replace(
        DEFAULT_PROFILE,
        publication_thresholds=PublicationThresholds(required_policy_coverage_pct=100.0, single_skill_max_pct=90.0),
    )
    metrics = {**_CLEAN, "single_skill_project_pct": 60.0, "single_skill_count_by_type": {"project": 3}}
    assert evaluate_publication_gate(metrics, profile=DEFAULT_PROFILE).passed is False
    assert evaluate_publication_gate(metrics, profile=lab_profile).passed is True


def test_v2_blocks_incomplete_activity_and_contract_but_v1_stays_frozen() -> None:
    metrics = {
        **_CLEAN,
        "activity_archetype_coverage_pct": 80.0,
        "artifact_contract_coverage_pct": 60.0,
    }

    current = evaluate_publication_gate(metrics, profile=DEFAULT_PROFILE)
    assert {failure.code for failure in current.failures} >= {
        "activity_archetype_incomplete",
        "artifact_contract_incomplete",
    }
    assert evaluate_publication_gate(metrics, profile=DIGITAL_PRODUCT_PROJECT_BASED_V1).passed is True


def test_v2_blocks_activity_archetype_outside_profile_policy() -> None:
    metrics = {
        **_CLEAN,
        "activity_archetype_count_by_type": {"construct": 4, "memorize": 1},
    }

    result = _gate(metrics)

    failure = next(item for item in result.failures if item.code == "activity_archetype_not_allowed")
    assert failure.value == 1
    assert "memorize" in failure.message


def test_v2_blocks_artifact_merge_errors() -> None:
    result = _gate({**_CLEAN, "artifact_merge_error_count": 2})

    failure = next(item for item in result.failures if item.code == "artifact_contract_merge_errors")
    assert failure.value == 2
    assert failure.threshold == 0


def test_new_profile_failure_can_be_waived_for_specific_up_version() -> None:
    metrics = {**_CLEAN, "activity_archetype_coverage_pct": 80.0}
    waiver = PublicationWaiver(
        code="activity_archetype_incomplete",
        author="methodologist",
        reason="Пилотный выпуск с ручной проверкой",
        up_version="12",
    )

    matching = _gate(metrics, waivers=[waiver], up_version="12")
    mismatched = _gate(metrics, waivers=[waiver], up_version="13")

    assert matching.passed is True
    assert {failure.code for failure in matching.waived} == {"activity_archetype_incomplete"}
    assert mismatched.passed is False


def test_waiver_releases_a_failure() -> None:
    metrics = {**_CLEAN, "single_skill_project_pct": 60.0, "single_skill_count_by_type": {"project": 3}}
    waiver = PublicationWaiver(code="single_skill_excess", author="method", reason="labs by design")
    result = _gate(metrics, waivers=[waiver])
    assert result.passed is True
    assert {f.code for f in result.waived} == {"single_skill_excess"}
