"""DWH activity matcher: project name -> learning activity, and branch-level rollup.

Uses the real bundled snapshot (2026-08-26), so the assertions double as a guard that the
matching logic still lines up with the shipped data.
"""

from __future__ import annotations

from content_factory.audit import activity
from content_factory.audit.activity import (
    ACTIVE,
    ARCHIVED,
    NO_DATA,
    lookup,
    summarize_branch,
)


def test_exact_name_matches_and_carries_students() -> None:
    info = lookup("BSA00_Decomposition")
    assert info.matched and info.activity == ACTIVE
    assert info.students_last_365d == 4418
    assert info.in_sber_catalog is True
    assert info.goal_ids == ("69540",)
    assert info.ambiguous is False


def test_id_suffix_is_stripped_before_matching() -> None:
    # audit project names carry the CMS task id; it must not break the DWH join
    with_id = lookup("BSA00_Decomposition.ID_356285")
    assert with_id.matched and with_id.students_last_365d == 4418


def test_separator_mismatch_is_normalised() -> None:
    # repo "SQLB1_Basics" vs any spacing/case — norm() collapses both
    assert lookup("sqlb1 basics").students_last_365d == lookup("SQLB1_Basics").students_last_365d == 2423


def test_dead_course_is_archived() -> None:
    info = lookup("Python 3.Project 1")  # 20 total, 0 last year, last 2025 -> архив
    assert info.matched and info.activity == ARCHIVED
    assert info.students_last_365d == 0


def test_unmatched_name_is_no_data_not_dead() -> None:
    info = lookup("totally_made_up_project_xyz")
    assert info.matched is False and info.activity == NO_DATA
    assert info.students_last_365d == 0 and info.goal_ids == ()


def test_one_name_two_goal_ids_sums_and_flags_ambiguous() -> None:
    # PjM14_EffNeg -> 73461 (активен, 109) + 73288 (архив, 4); aggregate is live
    info = lookup("PjM14_EffNeg")
    assert info.ambiguous is True
    assert info.students_last_365d == 113
    assert info.activity == ACTIVE  # recomputed from the aggregate, not per-row


def test_summarize_branch_counts_and_sorts() -> None:
    summary = summarize_branch(
        [
            "BSA00_Decomposition",  # активен 4418
            "SQLB1_Basics",  # активен 2423
            "Python 3.Project 1",  # архив 0
            "totally_made_up_project_xyz",  # нет данных
        ]
    )
    assert summary.total == 4
    assert summary.active == 2 and summary.archived == 1 and summary.no_data == 1
    assert summary.matched == 3
    assert summary.students_last_365d == 4418 + 2423
    # active first, then by students desc -> the busiest project leads
    assert summary.items[0][0] == "BSA00_Decomposition"
    assert summary.items[-1][1].activity == NO_DATA


def test_data_files_are_bundled() -> None:
    assert activity._ACTIVITY_CSV.exists() and activity._INDEX_CSV.exists()
