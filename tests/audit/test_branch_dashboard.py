"""Whole-branch consolidated dashboard: the EXACT single-project dashboard layout, fed one merged
report across all projects, plus a 'Проект — фильтр таблицы' plaque that narrows the standard table
to one project. Same summary strip, severity chips, criterion plaque, filter bar and results table
the single-project view uses — the branch axis is a client-side filter, not a new layout.
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


def _report(findings: list[Finding], files_total: int = 3) -> AuditReport:
    summary = RunSummary(
        started_at=datetime.now(UTC), input_path="in", findings_total=len(findings), files_total=files_total
    )
    return AuditReport(summary=summary, units=[], entities=[], findings=findings)


def _projects() -> list[tuple[str, AuditReport]]:
    return [
        (
            "DSB1_UNIX.zip",
            _report(
                [
                    _finding(
                        finding_id="l1",
                        criterion=Criterion.LINKS,
                        severity=Severity.MAJOR,
                        verdict=Verdict.FAIL,
                        checker="link_checker",
                        file_path="materials/hh.json",
                    ),
                    _finding(
                        finding_id="s1",
                        criterion=Criterion.READABILITY,
                        severity=Severity.MINOR,
                        verdict=Verdict.WARNING,
                        checker="spelling_wording_checker",
                    ),
                ],
                files_total=5,
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
                ],
                files_total=4,
            ),
        ),
    ]


def test_dashboard_is_a_self_contained_page() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert html.strip().lower().startswith("<!doctype html")
    assert "</html>" in html


def test_uses_the_standard_summary_strip_across_all_projects() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    # 3 merged cases · files summed (5 + 4)
    assert '<section id="summary" class="summary-strip">' in html
    assert ">3<" in html and "случаев · 9 файлов" in html
    assert "severity-chip-major" in html and "severity-chip-minor" in html


def test_has_the_standard_criterion_plaque() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert "Критерий — фильтр таблицы" in html
    assert 'data-criterion-filter="all"' in html


def test_project_filter_plaque_lists_every_project_with_counts() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert "Проект — фильтр таблицы" in html
    assert 'data-branch-filter="all"' in html
    assert 'data-branch-filter="DSB1_UNIX.zip"' in html
    assert 'data-branch-filter="DSB2_Syntax.zip"' in html


def test_filter_bar_carries_the_project_chip() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert 'id="active-branch-label"' in html and "Проект: все" in html


def test_table_is_the_standard_columns_and_rows_carry_project() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    # standard single-project columns (no bespoke branch column)
    assert ">Критерий<" in html and ">Тип<" in html and ">Обоснование<" in html
    assert ">Проект<" not in html  # project is a filter, not a column
    # every row is tagged with its project for the client-side filter
    assert 'data-branch="DSB1_UNIX.zip"' in html
    assert 'data-branch="DSB2_Syntax.zip"' in html


def test_downloads_present() -> None:
    html = render_branch_dashboard("BR", _projects(), [])
    assert "/app/auditor/branch/BR/download?file=report.xlsx" in html
    assert "/app/auditor/branch/BR/download?file=report.csv" in html


def test_skips_are_listed() -> None:
    skipped = [BranchSkip(name="notes.docx", reason="loose_file", message="Отдельный файл — пропущено.")]
    html = render_branch_dashboard("BR", _projects(), skipped)
    assert "notes.docx" in html and "пропущено" in html.lower()


def test_pass_findings_are_excluded() -> None:
    projects = [
        (
            "P.zip",
            _report(
                [
                    _finding(
                        finding_id="ok",
                        criterion=Criterion.LINKS,
                        severity=Severity.INFO,
                        verdict=Verdict.PASS,
                        checker="link_checker",
                    )
                ]
            ),
        )
    ]
    html = render_branch_dashboard("BR", projects, [])
    # no non-pass cases -> the project chip does not appear (nothing to filter)
    assert 'data-branch-filter="P.zip"' not in html


def test_project_names_are_escaped() -> None:
    projects = [("<script>x</script>.zip", _report([
        _finding(
            finding_id="x",
            criterion=Criterion.LINKS,
            severity=Severity.MINOR,
            verdict=Verdict.WARNING,
            checker="link_checker",
        )
    ]))]
    html = render_branch_dashboard("BR", projects, [])
    assert "<script>x</script>.zip" not in html
    assert "&lt;script&gt;" in html
