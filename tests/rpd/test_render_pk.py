"""Выборочная сборка РПД на реальном ДПО-шаблоне ПК (B1).

Проверяем: УП-разделы заполнены; boilerplate (нормативка) сохранён; проза/скрины/пробелы помечены.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import docx
from docx.oxml.ns import qn

from content_factory.rpd.build import build_rpd_sections
from content_factory.rpd.dpo_spec import Cat, category_for
from content_factory.rpd.module_evidence import synchronize_module_evidence
from content_factory.rpd.render_selective import render_build, render_rpd_pk
from content_factory.rpd.template import default_pk_skeleton_path
from content_factory.rpd.transfer import GAP_MARKER
from content_factory.rpd.up_models import from_registry_plan

_PLAN = {
    "title": "Тестовый УП",
    "meta": {"total_hours": 300},
    "rows": [
        {
            "block_title": "Модуль 1",
            "outcomes_know": "знает основы",
            "outcomes_can": "умеет применять",
            "outcomes_skills": "",
            "effort_hours": 150,
            "skills": ["Анализ данных"],
        },
        {
            "block_title": "Модуль 2",
            "outcomes_know": "",
            "outcomes_can": "строит модели",
            "outcomes_skills": "",
            "effort_hours": 150,
            "skills": ["Моделирование"],
        },
    ],
}


def _render(tmp_path: Path) -> tuple[str, str]:
    up = from_registry_plan(_PLAN)
    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_pk(up))
    doc = docx.Document(str(out))
    paragraphs = "\n".join(p.text for p in doc.paragraphs)
    cells = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    return paragraphs, cells


def test_category_for_key_sections() -> None:
    assert category_for("3.1", "Учебный план") is Cat.UP_PLAN
    assert category_for("2.1", "Профессиональные компетенции") is Cat.UP_COMP
    assert category_for("1.3", "Актуальность программы") is Cat.GENERATE
    assert category_for("1.2", "Нормативно-правовые основания") is Cat.BOILERPLATE
    assert category_for("3.3.1", "Пример оформления учебного модуля") is Cat.UP_MODULES
    assert category_for("4.3", "Паспорт оценочных материалов") is Cat.GAP


def test_render_is_valid_docx(tmp_path: Path) -> None:
    up = from_registry_plan(_PLAN)
    data = render_rpd_pk(up)
    assert docx.Document(__import__("io").BytesIO(data)) is not None


def test_up_sections_are_filled(tmp_path: Path) -> None:
    _paras, cells = _render(tmp_path)
    assert "Практический раздел 1" in cells and "Практический раздел 2" in cells
    assert "150" in cells  # часы модуля
    assert "Анализ данных" in cells  # компетенции из skills


def test_competency_semantic_columns_are_filled_and_rendered_without_llm() -> None:
    build = build_rpd_sections(from_registry_plan(_PLAN))
    section = next(item for item in build.sections if item.category == "up_comp")

    assert all(row[2] and row[3] and row[6] for row in section.table_rows)

    rendered = docx.Document(BytesIO(render_build(build)))
    table = next(
        item
        for item in rendered.tables
        if item.rows
        and [cell.text.strip() for cell in item.rows[0].cells[:4]]
        == ["Код", "Профессиональная компетенция", "Исходный уровень", "Качественное изменение"]
    )
    assert all(
        row.cells[2].text.strip() and row.cells[3].text.strip() and row.cells[6].text.strip() for row in table.rows[1:]
    )


def test_boilerplate_is_kept(tmp_path: Path) -> None:
    template = docx.Document(str(default_pk_skeleton_path()))
    rendered = docx.Document(BytesIO(render_rpd_pk(from_registry_plan(_PLAN))))
    normative = [
        paragraph
        for paragraph in template.paragraphs
        if "273-ФЗ" in paragraph.text
        or "24.03.2025 № 266" in paragraph.text
        or "Локальный нормативный акт" in paragraph.text
        or "Применимый профессиональный стандарт" in paragraph.text
    ]

    assert len(normative) == 4
    for source in normative:
        exported = next(paragraph for paragraph in rendered.paragraphs if paragraph.text == source.text)
        assert exported.style.name == source.style.name


def test_prose_and_platform_and_gaps_are_marked(tmp_path: Path) -> None:
    paragraphs, cells = _render(tmp_path)
    assert "[Будет сгенерировано]" in paragraphs  # актуальность/цели — под генерацию (без LLM)
    assert "скрин" in cells.lower()  # рабочая программа/пример оценки — доказательства с платформы
    assert "Методист" in cells  # задания и контроли ждут загрузки скриншотов


def test_assessment_materials_follow_internal_template_contract() -> None:
    build = build_rpd_sections(from_registry_plan(_PLAN))
    passport = build.section("4.3", "Паспорт оценочных материалов")
    example = build.section("4.4", "Пример оценки")

    assert passport is not None and passport.preserve_template and passport.editable
    assert passport.paragraphs[0].startswith("! Все вопросы итоговой аттестации")
    assert example is not None and example.category == "assessment_evidence"
    assert example.kind == "table" and not example.editable
    assert len(example.table_rows) == sum(requirement.kind == "control" for requirement in build.module_evidence)

    rendered = docx.Document(BytesIO(render_build(build)))
    paragraphs = "\n".join(paragraph.text for paragraph in rendered.paragraphs)
    assessment_table = [
        table
        for table in rendered.tables
        if table.rows
        and table.rows[0].cells[0].text.strip()
        == "Здесь нужен скрин из задания (если программа реализуется в очно-заочной форме)."
    ][-1]
    cells = "\n".join(cell.text for row in assessment_table.rows for cell in row.cells)

    assert "заранее опубликованы без ключей" in paragraphs
    assert "Ключи должны быть в отдельном файле" in paragraphs
    assert "Модуль 1" in cells and "Модуль 2" in cells
    assert "RPD_EVIDENCE" not in cells


def test_no_comments_leak_into_output(tmp_path: Path) -> None:
    import io
    import zipfile

    data = render_rpd_pk(from_registry_plan(_PLAN))
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert "word/comments.xml" not in archive.namelist()  # заметки методиста вырезаны
    xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "commentReference" not in xml and "commentRangeStart" not in xml


def test_render_preserves_template_sections_and_orientation() -> None:
    template = docx.Document(str(default_pk_skeleton_path()))
    rendered = docx.Document(BytesIO(render_rpd_pk(from_registry_plan(_PLAN))))

    assert len(rendered.sections) == len(template.sections) == 4
    assert [section.orientation for section in rendered.sections] == [
        section.orientation for section in template.sections
    ]


def test_module_program_combines_up_rows_with_platform_evidence_gate() -> None:
    rendered = docx.Document(BytesIO(render_rpd_pk(from_registry_plan(_PLAN))))
    module_tables = [
        table
        for table in rendered.tables
        if table.rows
        and [cell.text.strip() for cell in table.rows[0].cells]
        == ["№", "Тема и краткое содержание", "Вид работы", "Часы", "Методы", "Контроль"]
    ]
    module_passports = [
        table
        for table in rendered.tables
        if table.rows
        and [cell.text.strip() for cell in table.rows[0].cells] == ["Параметр", "Минимальный пример заполнения"]
        and any(row.cells[0].text.strip() == "Наименование" for row in table.rows[1:])
    ]
    screenshot_areas = [
        table
        for table in rendered.tables
        if table.rows
        and table.rows[0].cells[0].text.strip()
        == "Здесь нужен скрин из задания (если программа реализуется в очно-заочной форме)."
    ][:-1]

    assert len(module_passports) == len(module_tables) == len(screenshot_areas) == 2
    assert [table.rows[1].cells[1].text for table in module_passports] == [
        "Практический раздел 1",
        "Практический раздел 2",
    ]
    assert all(table.rows[-1].cells[1].text == "ИТОГО" for table in module_tables)
    assert all(
        "вставьте скрин" not in "\n".join(cell.text for row in table.rows for cell in row.cells)
        for table in module_tables
    )
    screenshot_text = "\n".join(cell.text for table in screenshot_areas for row in table.rows for cell in row.cells)
    assert screenshot_text.count("вставьте скрин задания") == 2
    assert screenshot_text.count("вставьте скрин контроля") == 2
    assert "p2p-проверка" not in screenshot_text


def test_corrupt_uploaded_screenshot_degrades_to_blocking_marker(tmp_path: Path) -> None:
    build = build_rpd_sections(from_registry_plan(_PLAN))
    requirement = build.module_evidence[0]
    requirement.status = "uploaded"
    requirement.relative_path = f"evidence/{requirement.evidence_id}.png"
    requirement.original_filename = "broken.png"
    requirement.content_type = "image/png"
    requirement.sha256 = "a" * 64
    screenshot = tmp_path / requirement.relative_path
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"broken image")
    section = next(item for item in build.sections if item.category == "up_modules")
    assert synchronize_module_evidence(section.module_blocks, requirement)

    rendered = docx.Document(BytesIO(render_build(build, evidence_root=tmp_path)))
    text = "\n".join(cell.text for table in rendered.tables for row in table.rows for cell in row.cells)

    assert "файл скриншота задания повреждён" in text


def test_passport_binding_does_not_overwrite_final_schedule_period() -> None:
    rendered = docx.Document(BytesIO(render_rpd_pk(from_registry_plan(_PLAN))))
    schedule = next(
        table
        for table in rendered.tables
        if table.rows
        and [cell.text.strip() for cell in table.rows[0].cells]
        == ["Компонент", "Период", "Часы", "Форма организации", "Контроль"]
    )
    rows = [[cell.text.strip() for cell in row.cells] for row in schedule.rows]

    assert rows[-2][1].endswith("неделя")
    assert "Требует уточнения" not in rows[-2][1]
    assert rows[-2][4] == "Защита итоговой работы"
    assert rows[-1] == ["ИТОГО", "", "300", "", ""]

    # Built-in layout keeps assessment names readable instead of splitting them
    # inside a word in the narrow template column.
    grid_widths = [int(column.get(qn("w:w"))) for column in schedule._tbl.tblGrid.gridCol_lst]
    assert grid_widths == [3300, 1350, 850, 2200, 1939]
    assert schedule._tbl.tblPr.tblLayout.get(qn("w:type")) == "fixed"


def test_empty_up_keeps_boilerplate_and_gaps_up_sections(tmp_path: Path) -> None:
    from content_factory.rpd.up_models import UpPlan

    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_pk(UpPlan()))
    doc = docx.Document(str(out))
    paragraphs = "\n".join(p.text for p in doc.paragraphs)
    assert "273-ФЗ" in paragraphs  # boilerplate всё равно на месте
    assert GAP_MARKER in paragraphs  # УП-разделы без данных → пробел
