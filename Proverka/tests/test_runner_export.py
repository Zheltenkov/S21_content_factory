import csv
from pathlib import Path
from zipfile import ZipFile

from content_audit.domain import (
    AuditReport,
    AuditSettings,
    Criterion,
    Evidence,
    Finding,
    RunSummary,
    Severity,
    TextLocation,
    Verdict,
)
from content_audit.exporters import write_report
from content_audit.orchestrator import AuditRunner


def test_runner_writes_reports(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    output = workspace_tmp_path / "reports"
    project.mkdir()
    (project / "README_RUS.md").write_text("[link](https://example.com)\nUse Java 21.\n", encoding="utf-8")
    (project / "check-list.yml").write_text("sections: []\n", encoding="utf-8")

    settings = AuditSettings(input_path=project, output_path=output, allow_network=False)
    report = AuditRunner(settings).run()
    write_report(report, output)

    assert report.summary.units_total == 1
    assert report.summary.files_total == 2
    assert report.summary.affected_units_total >= 1
    assert report.summary.by_unit
    assert report.summary.by_branch
    assert [step.name for step in report.summary.steps] == [
        "Загрузка файлов",
        "Подготовка проверок",
        "Извлечение и проверки",
        "Сборка отчёта",
    ]
    assert (output / "report.json").exists()
    assert (output / "report.csv").exists()
    assert (output / "report.xlsx").exists()
    assert (output / "run_summary.json").exists()
    csv_text = (output / "report.csv").read_text(encoding="utf-8-sig")
    assert "Источник" in csv_text
    assert "Дата проверки" in csv_text
    assert "Статус поддержки" in csv_text
    assert "Последняя версия" in csv_text
    assert "Рекомендуемая версия" in csv_text
    assert "Фрагмент" in csv_text
    assert "Цитата" not in csv_text
    with ZipFile(output / "report.xlsx") as workbook:
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Критерий" in sheet
    assert "Рекомендуемая версия" in sheet
    assert "Фрагмент" in sheet


def test_runner_includes_code_similarity_in_rights_findings(workspace_tmp_path: Path) -> None:
    corpus = workspace_tmp_path / "corpus"
    first = corpus / "first"
    second = corpus / "second"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    code = """
def normalize_value(value):
    value = value.strip().lower()
    return value.replace(' ', '-')

def build_slug(parts):
    return '-'.join(normalize_value(part) for part in parts)
"""
    for project, filename in ((first, "main.py"), (second, "solution.py")):
        (project / "README.md").write_text("# Проект\n", encoding="utf-8")
        (project / "LICENSE").write_text("MIT\n", encoding="utf-8")
        (project / filename).write_text(code, encoding="utf-8")

    report = AuditRunner(AuditSettings(input_path=corpus, output_path=workspace_tmp_path / "out")).run()

    matches = [finding for finding in report.findings if finding.extra.get("kind") == "code_similarity"]
    assert matches
    assert all(finding.criterion == Criterion.RIGHTS for finding in matches)
    assert all(finding.severity == Severity.MINOR for finding in matches)


def test_exporter_does_not_write_pass_findings(workspace_tmp_path: Path) -> None:
    output = workspace_tmp_path / "reports"
    report = AuditReport(
        summary=RunSummary(started_at="2026-06-08T00:00:00+00:00", input_path=str(workspace_tmp_path)),
        units=[],
        entities=[],
        findings=[
            Finding(
                finding_id="pass",
                unit_id="unit",
                branch=None,
                criterion=Criterion.CHECKLIST_ALIGNMENT,
                severity=Severity.INFO,
                verdict=Verdict.PASS,
                confidence=0.9,
                recommendation="Ничего не делать.",
                checker_name="checklist_checker",
            )
        ],
    )

    write_report(report, output)

    assert "Проверено" not in (output / "report.csv").read_text(encoding="utf-8-sig")
    assert '"findings": []' in (output / "report.json").read_text(encoding="utf-8")
    with ZipFile(output / "report.xlsx") as workbook:
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Проверено" not in sheet


def test_exporter_merges_recommendation_into_explanation(workspace_tmp_path: Path) -> None:
    output = workspace_tmp_path / "reports"
    report = AuditReport(
        summary=RunSummary(started_at="2026-06-08T00:00:00+00:00", input_path=str(workspace_tmp_path)),
        units=[],
        entities=[],
        findings=[
            Finding(
                finding_id="human",
                unit_id="unit",
                branch=None,
                criterion=Criterion.CORRECTNESS,
                severity=Severity.MAJOR,
                verdict=Verdict.FAIL,
                confidence=0.8,
                quote="Cacheability: the server stores responses for future use.",
                location=TextLocation(file_path="README.md", line_start=89),
                evidence=[
                    Evidence(
                        title="Проверка README",
                        detail="Проверка README: сервер не обязан хранить ответы для будущего использования",
                    )
                ],
                recommendation="Уточнить определение кэширования REST.",
                checker_name="readme_fact_actuality_checker",
            )
        ],
    )

    write_report(report, output)

    with (output / "report.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["Фрагмент"] == "Cacheability: the server stores responses for future use."
    assert "Рекомендация" not in rows[0]
    assert "Что найдено:" in rows[0]["Обоснование"]
    assert "Что сделать:" in rows[0]["Обоснование"]
