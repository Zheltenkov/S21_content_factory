"""WorkloadContract builder (project-contract epic, slice 2)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.workload import build_workload_contract


def test_derives_weeks_and_months_from_brief_intensity() -> None:
    wc = build_workload_contract(478, {"hours_per_week": 20}, default_hours_per_week=20)
    assert wc.total_hours == 478
    assert wc.hours_per_week == 20
    assert wc.duration_weeks == 23.9
    assert wc.duration_months == 5.5
    assert wc.study_days_per_week is None


def test_falls_back_to_default_intensity_when_brief_silent() -> None:
    wc = build_workload_contract(200, {}, default_hours_per_week=20)
    assert wc.hours_per_week == 20
    assert wc.duration_weeks == 10.0


def test_study_days_only_when_brief_provides_it() -> None:
    wc = build_workload_contract(100, {"hours_per_week": 10, "study_days_per_week": 5}, default_hours_per_week=20)
    assert wc.study_days_per_week == 5


def test_zero_hours_yields_zero_duration() -> None:
    wc = build_workload_contract(0, None, default_hours_per_week=20)
    assert wc.total_hours == 0
    assert wc.duration_weeks == 0.0
    assert wc.duration_months == 0.0


def test_as_dict_shape() -> None:
    wc = build_workload_contract(478, {"hours_per_week": 20}, default_hours_per_week=20)
    d = wc.as_dict()
    assert set(d) == {"total_hours", "hours_per_week", "duration_weeks", "duration_months", "study_days_per_week"}
