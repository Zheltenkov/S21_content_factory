"""Cross-branch overview: ranking by activity, aggregation, and self-contained HTML.

Real bundled DWH snapshot drives activity, so project names are the actual ones.
"""

from __future__ import annotations

from content_factory.audit.branch import BranchProjectResult, BranchReport
from content_factory.audit.overview import build_overview, render_overview_html


def _report(*projects: BranchProjectResult) -> BranchReport:
    return BranchReport(projects=list(projects), skipped=[])


def _proj(name: str, findings: int = 0, by_severity: dict | None = None) -> BranchProjectResult:
    return BranchProjectResult(
        name=name,
        status="completed",
        summary={"findings_total": findings, "by_severity": by_severity or {}},
    )


def _sample() -> list[tuple[str, str, str, BranchReport]]:
    return [
        # active branch, big reach
        ("b1", "BIG.zip", "2026-08-20T10:00", _report(
            _proj("BSA00_Decomposition", 10, {"critical": 1, "major": 3, "minor": 6}),  # активен 4418
        )),
        # active branch, smaller reach
        ("b2", "SMALL.zip", "2026-08-21T10:00", _report(
            _proj("SQLB1_Basics", 5, {"major": 2}),  # активен 2423
        )),
        # archived-only branch
        ("b3", "ARCH.zip", "2026-08-19T10:00", _report(
            _proj("Python 3.Project 1", 8, {"critical": 2}),  # архив, students 0
        )),
        # all no-data branch -> unknown section
        ("b4", "JUNK.zip", "2026-08-18T10:00", _report(
            _proj("totally_made_up_xyz", 0, {}),
        )),
    ]


def test_ranks_active_branches_first_by_students() -> None:
    ov = build_overview(_sample())
    assert [b.archive_name for b in ov.ranked] == ["BIG.zip", "SMALL.zip", "ARCH.zip"]
    assert ov.ranked[0].tier == 0 and ov.ranked[0].students_last_365d == 4418
    assert ov.ranked[2].tier == 2  # archived-only
    # the all-no-data branch is pulled out, not ranked
    assert [b.archive_name for b in ov.unknown] == ["JUNK.zip"]
    assert ov.unknown[0].tier == 3


def test_aggregates_findings_and_critmaj_on_active() -> None:
    ov = build_overview(_sample())
    assert ov.total_branches == 4 and ov.total_projects == 4
    assert ov.total_findings == 23  # 10+5+8+0
    assert ov.total_critical == 3 and ov.total_major == 5
    assert ov.total_students == 4418 + 2423
    assert ov.active_projects == 2 and ov.archived_projects == 1 and ov.no_data_projects == 1
    big = ov.ranked[0]
    assert big.findings == 10 and big.critmaj == 4
    assert big.critmaj_on_active == 4  # BSA00 is active -> its crit+major count
    assert ov.ranked[2].critmaj_on_active == 0  # archived project does not count as fix-first
    assert big.days_since_last == 0  # BSA00 last submission is fresh in the snapshot


def test_branch_freshness_none_when_all_unmatched() -> None:
    ov = build_overview(_sample())
    assert ov.unknown[0].days_since_last is None
    assert ov.unknown[0].pct_active == 0


def test_render_is_self_contained_and_shows_sections() -> None:
    html = render_overview_html(build_overview(_sample()))
    assert html.strip().startswith("<!DOCTYPE html")
    # no external assets — opens offline
    assert "http://" not in html and "https://" not in html
    assert 'src="' not in html and 'href="' not in html
    # banner + ranked branches + collapsible unknown section
    assert "веток аудировано" in html
    assert "BIG.zip" in html and "<details" in html
    assert "Актуальность неизвестна" in html and "JUNK.zip" in html
    # fix-first metric surfaced
    assert "на живых" in html


def test_render_escapes_branch_and_project_names() -> None:
    branches = [("x", "<script>x</script>.zip", "2026-08-20T10:00", _report(_proj("<b>evil</b>", 1, {"critical": 1})))]
    html = render_overview_html(build_overview(branches))
    assert "<script>x</script>.zip" not in html and "&lt;script&gt;" in html
    assert "<b>evil</b>" not in html and "&lt;b&gt;evil" in html


def test_empty_input_renders_without_error() -> None:
    ov = build_overview([])
    assert ov.total_branches == 0 and ov.ranked == () and ov.unknown == ()
    html = render_overview_html(ov)
    assert html.strip().startswith("<!DOCTYPE html") and "веток аудировано" in html
