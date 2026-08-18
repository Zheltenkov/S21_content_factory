"""Consolidated branch dashboard: one findings table across all projects of a branch.

The branch-run mode presents its final result as a single self-contained page — the same
audit dashboard the single project view uses, extended with a **project (branch)** axis:
a project filter, a "по проектам" distribution, and per-row project + action-level columns.
Level ("К исправлению" / "На проверку") mirrors ``triage.is_fix_tier``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from content_factory.audit.branch import BranchSkip
from content_factory.audit.domain import (
    AuditReport,
    Criterion,
    Evidence,
    Finding,
    RunSummary,
    Severity,
    Verdict,
)
from content_factory.audit.web_rendering import render_branch_dashboard


def _finding(
    *,
    finding_id: str,
    criterion: Criterion,
    severity: Severity,
    verdict: Verdict,
    checker: str,
    file_path: str = "README.md",
) -> Finding:
    return Finding(
        finding_id=finding_id,
        unit_id="unit",
        branch=None,
        criterion=criterion,
        severity=severity,
        verdict=verdict,
        confidence=0.9,
        quote="фрагмент",
        evidence=[Evidence(title="Проверка", detail="Что найдено: деталь.", url="https://example.org")],
        recommendation="Что сделать.",
        checker_name=checker,
    )


def _report(findings: list[Finding]) -> AuditReport:
    summary = RunSummary(started_at=datetime.now(UTC), input_path="in", findings_total=len(findings))
    return AuditReport(summary=summary, units=[], entities=[], findings=findings)


def _projects() -> list[tuple[str, AuditReport]]:
    return [
        (
            "DSB1_UNIX.zip",
            _report(
                [
                    # link_checker is high-precision → fix tier → "К исправлению"
                    _finding(
                        finding_id="l1",
                        criterion=Criterion.LINKS,
                        severity=Severity.MAJOR,
                        verdict=Verdict.FAIL,
                        checker="link_checker",
                        file_path="materials/hh.json",
                    ),
                    # spelling minor warning → review tier → "На проверку"
                    _finding(
                        finding_id="s1",
                        criterion=Criterion.READABILITY,
                        severity=Severity.MINOR,
                        verdict=Verdict.WARNING,
                        checker="spelling_wording_checker",
                    ),
                ]
            ),
        ),
        (
            "DSB2_Syntax.zip",
            _report(
                [
                    _finding(
                        finding_id="s2",
                        criterion=Criterion.READABILITY,
                        severity=Severity.MINOR,
                        verdict=Verdict.WARNING,
                        checker="spelling_wording_checker",
                    ),
                ]
            ),
        ),
    ]


def test_branch_dashboard_is_a_self_contained_page() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert html.strip().lower().startswith("<!doctype html")
    assert "</html>" in html


def test_severity_chips_count_across_all_projects() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert "severity-chip-major" in html and "1 major" in html
    assert "severity-chip-minor" in html and "2 minor" in html


def test_project_filter_chips_carry_per_project_counts() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert 'id="branch-chips"' in html
    assert 'data-branch-filter="all"' in html
    assert 'data-branch-filter="DSB1_UNIX.zip"' in html
    assert 'data-branch-filter="DSB2_Syntax.zip"' in html


def test_criterion_filter_present() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert 'id="criterion-chips"' in html


def test_three_distribution_panels() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert "по проектам" in html.lower()
    assert "по критериям" in html.lower()
    assert "по модулям" in html.lower()
    # module distribution keys off checker_name
    assert "spelling_wording_checker" in html and "link_checker" in html


def test_table_has_project_and_level_columns() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert "col-branch" in html and "col-level" in html
    assert ">Проект<" in html and ">Уровень<" in html


def test_rows_carry_project_and_action_level_from_triage() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    # every row tagged with its project for client-side filtering
    assert 'data-branch="DSB1_UNIX.zip"' in html
    assert 'data-branch="DSB2_Syntax.zip"' in html
    # link_checker (high-precision) → fix tier
    assert "К исправлению" in html
    # spelling minor warning → review tier
    assert "На проверку" in html
    # module column
    assert "link_checker" in html


def test_search_and_hide_unknown_controls_present() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert 'id="flt-search"' in html
    assert 'id="flt-hide-unknown"' in html
    # branch state wired in the client script
    assert "branch-chips" in html and "state.branch" in html


def test_download_buttons_live_in_the_filter_bar_and_are_filter_aware() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    # buttons sit in the filter bar's download group, not a standalone section
    assert 'id="branch-downloads"' in html
    assert 'data-base="/app/auditor/branch/BR/download"' in html
    assert 'data-file="report.xlsx"' in html and 'data-file="report.csv"' in html
    # the client script rewrites the href with the active project filter
    assert "branch-downloads" in html and "'&branch=' + encodeURIComponent(state.branch)" in html


def test_skips_are_listed() -> None:
    skipped = [BranchSkip(name="notes.docx", reason="loose_file", message="Отдельный файл — пропущено.")]
    html = render_branch_dashboard("BR", _projects(), skipped)
    assert "notes.docx" in html and "пропущено" in html.lower()


def test_project_names_are_escaped() -> None:
    projects = [("<script>x</script>.zip", _report([]))]
    html = render_branch_dashboard("BR", projects, [])
    assert "<script>x</script>.zip" not in html
    assert "&lt;script&gt;" in html
