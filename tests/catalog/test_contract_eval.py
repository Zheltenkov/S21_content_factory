"""Golden contract eval harness (project-contract epic, slice 9)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.contract_eval import (
    gate_passed,
    render_summary,
    summarize_plan,
)

_PLAN = {
    "plan_id": 31,
    "status": "built",
    "summary": {
        "projects": 20,
        "workload": {"total_hours": 478, "hours_per_week": 20, "duration_weeks": 23.9, "duration_months": 5.5},
    },
    "report": {
        "quality_metrics": {
            "title_violation_count": 0,
            "generic_artifact_count": 0,
            "generic_criterion_count": 0,
            "policy_area_coverage_pct": 100.0,
            "testable_criteria_coverage_pct": 100.0,
            "blocking_question_count": 0,
            "single_skill_project_pct": 20.0,
            "template_bound_project_count": 6,
        },
        "publication_gate": {"passed": True, "failures": [], "waived": [], "waivers": []},
    },
}


def test_summarize_extracts_metrics_gate_workload() -> None:
    summary = summarize_plan(_PLAN)
    assert summary["plan_id"] == 31
    assert summary["projects"] == 20
    assert summary["metrics"]["title_violation_count"] == 0
    assert summary["workload"]["duration_weeks"] == 23.9
    assert gate_passed(summary) is True


def test_render_shows_pass_and_workload() -> None:
    text = render_summary(summarize_plan(_PLAN))
    assert "ГОТОВ" in text
    assert "478 ч" in text
    assert "23.9 нед" in text


def test_render_before_after_with_baseline() -> None:
    baseline = {"metrics": {"title_violation_count": 13, "single_skill_project_pct": 40.0}}
    text = render_summary(summarize_plan(_PLAN), baseline)
    assert "13 -> 0" in text
    assert "40.0 -> 20.0" in text


def test_render_blocked_lists_failures() -> None:
    blocked = {
        **_PLAN,
        "report": {
            "quality_metrics": _PLAN["report"]["quality_metrics"],
            "publication_gate": {
                "passed": False,
                "failures": [{"code": "title_violations", "message": "Есть длинные названия", "value": 13, "threshold": 0}],
                "waived": [],
                "waivers": [],
            },
        },
    }
    summary = summarize_plan(blocked)
    assert gate_passed(summary) is False
    text = render_summary(summary)
    assert "ЗАБЛОКИРОВАН" in text
    assert "Есть длинные названия" in text
