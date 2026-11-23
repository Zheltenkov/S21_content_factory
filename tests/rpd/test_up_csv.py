"""Парсер УП из CSV-экспорта Реестра (путь D — файл-УП)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from content_factory.rpd.source_fidelity import canonicalize_up_plan
from content_factory.rpd.up_models import from_registry_plan, parse_up_csv

_HEADER = [
    "Тематический блок", "Цели блока", "№", "Название контентной единицы (проекта)", "Краткое описание",
    "Обр. результаты — что узнает (ЗНАТЬ)", "Обр. результаты — что умеет (УМЕТЬ)", "Обр. результаты — какой навык (НАВЫКИ)",
    "Необходимое ПО", "Формат", "Трудоёмкость, астр. часы", "Список навыков (развесовка)",
]


def _write_csv(path: Path) -> None:
    rows = [
        _HEADER,
        ["Модуль А", "Цель А", "1", "Проект 1", "описание1", "знает X", "умеет Y", "навык Z", "Python", "индивидуальный", "10", "Навык Альфа: 30%"],
        ["", "Цель А", "2", "Проект 2", "описание2", "знает W", "", "", "Python", "индивидуальный", "20", "Навык Альфа: 20%"],
        ["Модуль Б", "Цель Б", "3", "Проект 3", "описание3", "", "умеет V", "", "R", "групповой", "5", "Навык Бета"],
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)


def test_parse_csv_groups_projects_into_modules(tmp_path: Path) -> None:
    path = tmp_path / "up.csv"
    _write_csv(path)
    up = parse_up_csv(path, title="Тест")
    assert up.title == "Тест"
    assert [m.title for m in up.modules] == ["Модуль А", "Модуль Б"]
    assert len(up.modules[0].rows) == 2  # блок А удержан для второй строки (пустой блок)
    assert up.modules[0].goals == "Цель А"
    assert up.total_hours == "35"


def test_parse_csv_outcomes_and_skills(tmp_path: Path) -> None:
    path = tmp_path / "up.csv"
    _write_csv(path)
    up = parse_up_csv(path)
    groups = up.outcomes_by_group()
    assert "знает X" in groups["know"] and "умеет Y" in groups["can"]
    codes = [c.code for c in up.competencies()]
    assert "Навык Альфа" in codes and "Навык Бета" in codes  # развесовка «: 30%» отброшена
    assert codes.count("Навык Альфа") == 1  # дедуп


def test_render_from_csv_has_full_columns(tmp_path: Path) -> None:
    import docx

    from content_factory.rpd.render_selective import render_rpd_pk

    path = tmp_path / "up.csv"
    _write_csv(path)
    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_pk(parse_up_csv(path, title="Тест")))
    doc = docx.Document(str(out))

    def headers(table: object) -> list[str]:
        return [c.text.strip() for c in table.rows[0].cells]  # type: ignore[attr-defined]

    plan = next(t for t in doc.tables if "Наименование модуля" in headers(t))
    assert "Практические занятия" in headers(plan) and "Контроль" in headers(plan)
    plan_rows = [[cell.text.strip() for cell in row.cells] for row in plan.rows]
    assert plan_rows[-1] == ["", "ИТОГО", "35", "0", "35", "0", "0"]
    assert all(row[6] == "—" for row in plan_rows[1:-1])
    assert "p2p-проверка" not in "\n".join(cell for row in plan_rows for cell in row)
    comp = next(t for t in doc.tables if headers(t)[0] == "Код")
    assert headers(comp) == [
        "Код",
        "Профессиональная компетенция",
        "Исходный уровень",
        "Качественное изменение",
        "После обучения слушатель способен",
        "Основание",
        "Оценка",
    ]
    modules = next(t for t in doc.tables if "Тема и краткое содержание" in headers(t))
    cells = "\n".join(c.text for r in modules.rows for c in r.cells)
    assert "Проект 1" in cells  # разложено по проектам (имя из УП)


def test_registry_adapter_carries_forward_blank_block_title() -> None:
    up = from_registry_plan(
        {
            "title": "Тест",
            "meta": {"total_hours": 35},
            "rows": [
                {"block_title": "Модуль А", "project_name": "Проект 1", "effort_hours": 10},
                {"block_title": "", "project_name": "Проект 2", "effort_hours": 20},
                {"block_title": "Модуль Б", "project_name": "Проект 3", "effort_hours": 5},
            ],
        }
    )

    assert [module.title for module in up.modules] == ["Модуль А", "Модуль Б"]
    assert [module.hours for module in up.modules] == ["30", "5"]
    assert [len(module.rows) for module in up.modules] == [2, 1]


def test_curriculum_plan_14_golden_source_fidelity() -> None:
    fixture = Path(__file__).parent / "fixtures" / "curriculum_plan_14_golden.csv"
    spec = canonicalize_up_plan(parse_up_csv(fixture, title="curriculum_plan_14"))

    assert spec.module_count == 9
    assert spec.project_count == 24
    assert spec.competency_count == 23
    assert spec.declared_total_hours == "478"
    assert spec.calculated_total_hours == "478"
    assert [module.hours for module in spec.modules] == ["62", "64", "78", "38", "84", "40", "42", "44", "26"]
    assert len(spec.fingerprint) == 64


def test_curriculum_plan_14_public_table_has_clean_titles_and_exact_total() -> None:
    from io import BytesIO

    import docx

    from content_factory.rpd.build import build_rpd_sections
    from content_factory.rpd.render_selective import render_build

    fixture = Path(__file__).parent / "fixtures" / "curriculum_plan_14_golden.csv"
    build = build_rpd_sections(parse_up_csv(fixture, title="curriculum_plan_14"))
    plan = next(section for section in build.sections if section.category == "up_plan")

    assert len(plan.table_rows) == 10  # девять модулей + обязательная строка ИТОГО
    assert plan.table_rows[-1] == ["", "ИТОГО", "478", "0", "478", "0", "0"]
    public_titles = [row[1] for row in plan.table_rows[:-1]]
    assert all(title[0].isupper() for title in public_titles)
    assert all("→" not in title and title != "Модуль" for title in public_titles)
    assert all(2 <= len(title.split()) <= 12 for title in public_titles)
    assert len(public_titles) == len({title.casefold() for title in public_titles})
    assert all(row[6] in {"—", "0"} for row in plan.table_rows[:-1])
    assert build.curriculum_trace is not None
    assert build.curriculum_trace.inferred_workload_rows == 0
    assert all(item.provenance == "source_exact" for item in build.curriculum_trace.workload_projection)
    schedule = next(section for section in build.sections if section.category == "up_grafik")
    assert [row[0] for row in schedule.table_rows[:-2]] == public_titles[:-1]
    assert schedule.table_rows[-2][0] in {public_titles[-1], "Итоговая аттестация"}
    assert schedule.table_rows[-1] == ["ИТОГО", "", "478", "", ""]
    assert all(row[1] for row in schedule.table_rows[:-1])
    assert all(row[4] and "p2p" not in row[4].casefold() for row in schedule.table_rows[:-1])
    assert schedule.table_rows[-2][0] == "Итоговая аттестация"
    modules = next(section for section in build.sections if section.category == "up_modules")
    assert len(build.module_evidence) == 48  # 24 задания × (скрин задания + скрин контроля)
    assert modules.kind == "module_blocks"
    assert len(modules.module_blocks) == 9
    assert sum(len(block.topics_rows) - 1 for block in modules.module_blocks) == 24
    assert all(len(block.passport_rows) == 6 for block in modules.module_blocks)
    assert all(block.topics_rows[-1][1] == "ИТОГО" for block in modules.module_blocks)
    evidence_rows = [row for block in modules.module_blocks for row in block.evidence_rows]
    assert len(evidence_rows) == 48
    assert sum("вставьте скрин задания" in row[0] for row in evidence_rows) == 24
    assert sum("вставьте скрин контроля" in row[0] for row in evidence_rows) == 24
    assert any(issue.code == "module_platform_screenshots_missing" for issue in build.quality.issues)
    current_control = next(section for section in build.sections if section.category == "current_control")
    assert [row[0] for row in current_control.table_rows] == public_titles[:-1]
    assert all("LMS/ЭИОС" in row[2] and "p2p" not in row[1].casefold() for row in current_control.table_rows)
    final_attestation = next(section for section in build.sections if section.category == "final_attestation")
    final_values = {row[0]: row[1] for row in final_attestation.table_rows}
    assert final_values["Форма"] == "Защита итоговой работы"
    assert final_values["Продолжительность"].startswith("26 академических часов")
    assert "Интегральный результат обучения" in final_values["Проверяемый результат"]
    assert "70 %" in final_values["Порог успешного прохождения"]
    methodological = next(section for section in build.sections if section.category == "methodological_support")
    assert [row[0] for row in methodological.table_rows] == [
        "Основная литература",
        "Нормативные документы",
        "Электронные ресурсы",
        "Методические материалы",
    ]
    material = next(section for section in build.sections if section.category == "material_conditions")
    assert [row[0] for row in material.table_rows] == ["Лекции", "Практические занятия"]
    assert all("интернет" in row[2].casefold() for row in material.table_rows)
    staffing = next(section for section in build.sections if section.category == "staffing")
    assert staffing.table_rows[0][0] == "Преподаватель"
    assert "квалификация" in staffing.table_rows[0][1].casefold()

    rendered = docx.Document(BytesIO(render_build(build)))
    headers = [
        [cell.text.strip() for cell in table.rows[0].cells]
        for table in rendered.tables
        if table.rows
    ]
    assert headers.count(["№", "Тема и краткое содержание", "Вид работы", "Часы", "Методы", "Контроль"]) == 9
    module_passports = [
        table
        for table in rendered.tables
        if table.rows
        and [cell.text.strip() for cell in table.rows[0].cells]
        == ["Параметр", "Минимальный пример заполнения"]
        and any(row.cells[0].text.strip() == "Наименование" for row in table.rows[1:])
    ]
    assert len(module_passports) == 9
    screenshot_headers = [
        header
        for header in headers
        if header
        == ["Здесь нужен скрин из задания (если программа реализуется в очно-заочной форме)."]
    ]
    assert len(screenshot_headers) == 10  # девять модулей + отдельный пример оценки 4.4


_REAL = Path(__file__).resolve().parents[2] / "УП_ИИ_предприниматель.csv"


@pytest.mark.skipif(not _REAL.exists(), reason="реальный УП-CSV отсутствует в корне репо")
def test_real_up_csv_parses() -> None:
    up = parse_up_csv(_REAL, title="ИИ для предпринимателя")
    assert len(up.modules) >= 5
    assert up.total_hours == "72"
    assert len(up.competencies()) > 0
    groups = up.outcomes_by_group()
    assert groups["know"] and groups["can"]
