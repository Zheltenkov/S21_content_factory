"""Экспорт отчётов в JSON, CSV и XLSX."""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from content_factory.audit.domain import (
    CRITERION_LABELS,
    ISSUE_KIND_LABELS,
    SEVERITY_LABELS,
    VERDICT_LABELS,
    AuditReport,
    Finding,
    Verdict,
)
from content_factory.audit.report_formatting import format_finding_explanation, format_finding_fragment
from content_factory.audit.triage import is_fix_tier


def write_report(report: AuditReport, output_path: Path) -> None:
    """Записываем полный отчёт, таблицу для методологов и краткую сводку."""

    output_path.mkdir(parents=True, exist_ok=True)
    report = _without_pass_findings(report)
    (output_path / "report.json").write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_path / "run_summary.json").write_text(
        json.dumps(report.summary.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(report, output_path / "report.csv")
    _write_xlsx(report, output_path / "report.xlsx")


def _without_pass_findings(report: AuditReport) -> AuditReport:
    """Защищает выгрузки от успешных строк даже при ручной сборке отчёта."""

    findings = [finding for finding in report.findings if finding.verdict != Verdict.PASS]
    if len(findings) == len(report.findings):
        return report
    return report.model_copy(update={"findings": findings})


def _write_csv(report: AuditReport, path: Path) -> None:
    """Формируем таблицу результата в виде, близком к ТЗ."""

    rows = _report_rows(report)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else _empty_fieldnames())
        writer.writeheader()
        writer.writerows(rows)


def _report_rows(report: AuditReport) -> list[dict[str, object]]:
    """Собирает табличные строки отчёта для CSV и XLSX."""

    unit_by_id = {unit.unit_id: unit for unit in report.units}
    rows: list[dict[str, object]] = []
    for finding in report.findings:
        unit = unit_by_id.get(finding.unit_id)
        rows.append(
            {
                "Уровень": "К исправлению" if is_fix_tier(finding) else "На просмотр",
                "Ветка": finding.branch or "",
                "ID единицы": finding.unit_id,
                "Название единицы": unit.name if unit else "",
                "Критерий": CRITERION_LABELS[finding.criterion],
                "Тип": ISSUE_KIND_LABELS[finding.issue_kind],
                "Файл": finding.location.file_path if finding.location else "",
                "Строка": finding.location.line_start if finding.location else "",
                "Фрагмент": format_finding_fragment(finding),
                "Вердикт": VERDICT_LABELS[finding.verdict],
                "Критичность": SEVERITY_LABELS[finding.severity],
                "Уверенность": f"{finding.confidence:.2f}",
                "Обоснование": format_finding_explanation(finding),
                "Источник": finding.source or "",
                "Дата проверки": _format_checked_at(finding),
                "Статус поддержки": finding.support_status or "",
                "Последняя версия": finding.latest_version or "",
                "Рекомендуемая версия": finding.recommended_version or "",
                "Версия модельного запроса": finding.prompt_version or "",
                "Нужен человек": "да" if finding.needs_human_review else "нет",
                "Проверяющий модуль": finding.checker_name,
            }
        )
    return rows


def _write_xlsx(report: AuditReport, path: Path) -> None:
    """Пишет простой XLSX без внешних зависимостей."""

    rows = _report_rows(report)
    fieldnames = list(rows[0].keys()) if rows else _empty_fieldnames()
    sheet_rows: list[list[object]] = [
        [*fieldnames],
        *[[row.get(field, "") for field in fieldnames] for row in rows],
    ]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types())
        archive.writestr("_rels/.rels", _xlsx_root_rels())
        archive.writestr("xl/workbook.xml", _xlsx_workbook())
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels())
        archive.writestr("xl/styles.xml", _xlsx_styles())
        archive.writestr("xl/worksheets/sheet1.xml", _xlsx_sheet(sheet_rows))


def _xlsx_content_types() -> str:
    """Описывает типы частей XLSX-архива."""

    return """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""


def _xlsx_root_rels() -> str:
    """Связывает корень XLSX с книгой."""

    return """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _xlsx_workbook() -> str:
    """Создаёт книгу с одним листом."""

    return """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Отчёт" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""


def _xlsx_workbook_rels() -> str:
    """Связывает книгу с листом и стилями."""

    return """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _xlsx_styles() -> str:
    """Минимальные стили: обычная строка и жирный заголовок."""

    return """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="2"><font/><font><b/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="2">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
  </cellXfs>
</styleSheet>"""


def _xlsx_sheet(rows: list[list[object]]) -> str:
    """Собирает XML листа с текстовыми ячейками."""

    if not rows:
        return """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData/>
</worksheet>"""
    columns_xml = _xlsx_columns(rows[0])
    auto_filter_ref = f"A1:{_xlsx_column_name(len(rows[0]))}{len(rows)}"
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            ref = f"{_xlsx_column_name(column_index)}{row_index}"
            style = ' s="1"' if row_index == 1 else ""
            cells.append(f'<c r="{ref}" t="inlineStr"{style}><is><t>{escape(str(value))}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  {columns_xml}
  <sheetData>{''.join(row_xml)}</sheetData>
  <autoFilter ref="{auto_filter_ref}"/>
</worksheet>"""


def _xlsx_columns(fieldnames: list[object]) -> str:
    """Задаёт прикладные ширины колонок для удобного просмотра отчёта."""

    width_by_name = {
        "Уровень": 16,
        "Ветка": 18,
        "ID единицы": 24,
        "Название единицы": 30,
        "Критерий": 28,
        "Тип": 14,
        "Файл": 32,
        "Строка": 10,
        "Фрагмент": 42,
        "Вердикт": 18,
        "Критичность": 16,
        "Уверенность": 14,
        "Обоснование": 68,
        "Источник": 36,
        "Дата проверки": 24,
        "Статус поддержки": 20,
        "Последняя версия": 18,
        "Рекомендуемая версия": 22,
        "Версия модельного запроса": 28,
        "Нужен человек": 16,
        "Проверяющий модуль": 26,
    }
    columns = []
    for index, field in enumerate(fieldnames, start=1):
        width = width_by_name.get(str(field), 18)
        columns.append(f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>')
    return f"<cols>{''.join(columns)}</cols>"


def _xlsx_column_name(index: int) -> str:
    """Преобразует номер колонки в Excel-обозначение."""

    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _empty_fieldnames() -> list[str]:
    """Возвращаем заголовки даже для пустого отчёта."""

    return [
        "Уровень",
        "Ветка",
        "ID единицы",
        "Название единицы",
        "Критерий",
        "Тип",
        "Файл",
        "Строка",
        "Фрагмент",
        "Вердикт",
        "Критичность",
        "Уверенность",
        "Обоснование",
        "Источник",
        "Дата проверки",
        "Статус поддержки",
        "Последняя версия",
        "Рекомендуемая версия",
        "Версия модельного запроса",
        "Нужен человек",
        "Проверяющий модуль",
    ]


def _format_checked_at(finding: Finding) -> str:
    """Форматируем дату проверки для табличной выгрузки."""

    if not finding.checked_at:
        return ""
    return str(finding.checked_at.isoformat())
