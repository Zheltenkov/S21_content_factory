"""Semantic publication gate + waiver + profile interpretation (redirect step 2)."""

from __future__ import annotations

from dataclasses import replace

from content_factory.catalog.pipeline.curriculum.methodology_profile import (
    DEFAULT_PROFILE,
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


def test_v1_reproduces_current_gate_clean_passes() -> None:
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


def test_waiver_releases_a_failure() -> None:
    metrics = {**_CLEAN, "single_skill_project_pct": 60.0, "single_skill_count_by_type": {"project": 3}}
    waiver = PublicationWaiver(code="single_skill_excess", author="method", reason="labs by design")
    result = _gate(metrics, waivers=[waiver])
    assert result.passed is True
    assert {f.code for f in result.waived} == {"single_skill_excess"}
