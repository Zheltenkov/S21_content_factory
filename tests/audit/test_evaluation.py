import json
from datetime import datetime, timezone

from content_factory.audit.domain import AuditReport, Criterion, Finding, RunSummary, Severity, TextLocation, Verdict
from content_factory.audit.evaluation import evaluate_report


def test_evaluation_computes_precision_and_recall(workspace_tmp_path) -> None:
    report = AuditReport(
        summary=RunSummary(started_at=datetime.now(timezone.utc), input_path=str(workspace_tmp_path)),
        units=[],
        entities=[],
        findings=[
            Finding(
                finding_id="fnd_1",
                unit_id="unit-1",
                branch=None,
                criterion=Criterion.ACTUALITY,
                severity=Severity.CRITICAL,
                verdict=Verdict.FAIL,
                confidence=0.9,
                location=TextLocation(file_path="README.md", line_start=10, line_end=10),
                recommendation="Обновить.",
                checker_name="test",
            ),
            Finding(
                finding_id="fnd_2",
                unit_id="unit-1",
                branch=None,
                criterion=Criterion.RIGHTS,
                severity=Severity.MINOR,
                verdict=Verdict.UNKNOWN,
                confidence=0.5,
                location=TextLocation(file_path="README.md", line_start=20, line_end=20),
                recommendation="Проверить.",
                checker_name="test",
            ),
        ],
    )
    gold_path = workspace_tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            [
                {
                    "unit_id": "unit-1",
                    "criterion": "actuality",
                    "severity": "critical",
                    "file_path": "README.md",
                    "line_start": 10,
                },
                {
                    "unit_id": "unit-2",
                    "criterion": "correctness",
                    "severity": "major",
                    "file_path": "README.md",
                    "line_start": 3,
                },
            ]
        ),
        encoding="utf-8",
    )

    summary = evaluate_report(report, gold_path)

    assert summary.true_positive == 1
    assert summary.false_positive == 1
    assert summary.false_negative == 1
    assert summary.precision == 0.5
    assert summary.recall == 0.5
    assert summary.critical_recall == 1.0


def test_evaluation_matches_gold_without_file_path(workspace_tmp_path) -> None:
    report = AuditReport(
        summary=RunSummary(started_at=datetime.now(timezone.utc), input_path=str(workspace_tmp_path)),
        units=[],
        entities=[],
        findings=[
            Finding(
                finding_id="fnd_1",
                unit_id="unit-1",
                branch=None,
                criterion=Criterion.READABILITY,
                severity=Severity.MINOR,
                verdict=Verdict.WARNING,
                confidence=0.9,
                location=TextLocation(file_path="README_RUS.md", line_start=119, line_end=119),
                recommendation="Добавить двоеточие.",
                checker_name="test",
            )
        ],
    )
    gold_path = workspace_tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            [
                {
                    "unit_id": "unit-1",
                    "criterion": "readability",
                    "severity": "minor",
                    "line_start": 119,
                }
            ]
        ),
        encoding="utf-8",
    )

    summary = evaluate_report(report, gold_path)

    assert summary.true_positive == 1
    assert summary.false_positive == 0
    assert summary.false_negative == 0
    assert summary.precision == 1.0
    assert summary.recall == 1.0


def test_evaluation_matches_near_line(workspace_tmp_path) -> None:
    report = AuditReport(
        summary=RunSummary(started_at=datetime.now(timezone.utc), input_path=str(workspace_tmp_path)),
        units=[],
        entities=[],
        findings=[
            Finding(
                finding_id="fnd_1",
                unit_id="unit-1",
                branch=None,
                criterion=Criterion.ACTUALITY,
                severity=Severity.MAJOR,
                verdict=Verdict.FAIL,
                confidence=0.9,
                location=TextLocation(file_path="README.md", line_start=12, line_end=12),
                recommendation="Исправить ссылку.",
                checker_name="test",
            )
        ],
    )
    gold_path = workspace_tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            [
                {
                    "unit_id": "unit-1",
                    "criterion": "actuality",
                    "severity": "major",
                    "file_path": "README.md",
                    "line_start": 10,
                }
            ]
        ),
        encoding="utf-8",
    )

    summary = evaluate_report(report, gold_path)

    assert summary.true_positive == 1
    assert summary.false_positive == 0
    assert summary.false_negative == 0
