"""Semantic publication gate + waiver (project-contract epic, slice 8)."""

from __future__ import annotations

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
    "capstone_required": True,
    "capstone_present": True,
}


def test_clean_metrics_pass() -> None:
    result = evaluate_publication_gate(_CLEAN)
    assert result.passed is True
    assert result.failures == ()


def test_each_violation_blocks() -> None:
    cases = [
        ("title_violation_count", 2, "title_violations"),
        ("generic_artifact_count", 1, "generic_artifacts"),
        ("policy_area_coverage_pct", 80.0, "policy_area_incomplete"),
        ("testable_criteria_coverage_pct", 90.0, "untestable_criteria"),
        ("blocking_question_count", 3, "blocking_questions"),
        ("single_skill_project_pct", 40.0, "single_skill_excess"),
    ]
    for key, value, code in cases:
        metrics = {**_CLEAN, key: value}
        result = evaluate_publication_gate(metrics)
        assert result.passed is False
        assert code in {failure.code for failure in result.failures}


def test_capstone_required_but_missing_blocks() -> None:
    result = evaluate_publication_gate({**_CLEAN, "capstone_present": False})
    assert result.passed is False
    assert "capstone_missing" in {failure.code for failure in result.failures}


def test_waiver_releases_a_failure() -> None:
    metrics = {**_CLEAN, "single_skill_project_pct": 40.0}
    waiver = PublicationWaiver(code="single_skill_excess", author="method", reason="labs by design")
    result = evaluate_publication_gate(metrics, waivers=[waiver])
    assert result.passed is True
    assert {failure.code for failure in result.waived} == {"single_skill_excess"}


def test_waiver_scoped_to_up_version() -> None:
    metrics = {**_CLEAN, "blocking_question_count": 1}
    waiver = PublicationWaiver(code="blocking_questions", author="m", reason="r", up_version="v2")
    # waiver for a different version does not apply
    assert evaluate_publication_gate(metrics, waivers=[waiver], up_version="v3").passed is False
    assert evaluate_publication_gate(metrics, waivers=[waiver], up_version="v2").passed is True
