"""Выборочная сборка РПД на реальном ДПО-шаблоне ПК (B1).

Проверяем: УП-разделы заполнены; boilerplate (нормативка) сохранён; проза/скрины/пробелы помечены.
"""

from __future__ import annotations

from pathlib import Path

import docx

from content_factory.rpd.dpo_spec import Cat, category_for
from content_factory.rpd.render_selective import render_rpd_pk
from content_factory.rpd.transfer import GAP_MARKER
from content_factory.rpd.up_models import from_registry_plan

_PLAN = {
    "title": "Тестовый УП",
    "meta": {"total_hours": 300},
    "rows": [
        {"block_title": "Модуль 1", "outcomes_know": "знает основы", "outcomes_can": "умеет применять", "outcomes_skills": "", "effort_hours": 150, "skills": ["Анализ данных"]},
        {"block_title": "Модуль 2", "outcomes_know": "", "outcomes_can": "строит модели", "outcomes_skills": "", "effort_hours": 150, "skills": ["Моделирование"]},
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
    assert category_for("3.3.1", "Пример оформления учебного модуля") is Cat.PLATFORM
    assert category_for("4.3", "Паспорт оценочных материалов") is Cat.GAP


def test_render_is_valid_docx(tmp_path: Path) -> None:
    up = from_registry_plan(_PLAN)
    data = render_rpd_pk(up)
    assert docx.Document(__import__("io").BytesIO(data)) is not None


def test_up_sections_are_filled(tmp_path: Path) -> None:
    _paras, cells = _render(tmp_path)
    assert "Модуль 1" in cells and "Модуль 2" in cells  # учебный план из УП
    assert "150" in cells  # часы модуля
    assert "Анализ данных" in cells  # компетенции из skills


def test_boilerplate_is_kept(tmp_path: Path) -> None:
    paragraphs, _cells = _render(tmp_path)
    assert "273-ФЗ" in paragraphs  # нормативка ФОРМЫ сохранена, не затёрта


def test_prose_and_platform_and_gaps_are_marked(tmp_path: Path) -> None:
    paragraphs, _cells = _render(tmp_path)
    assert "[Будет сгенерировано]" in paragraphs  # актуальность/цели — под генерацию
    assert "Вставьте скрин" in paragraphs  # примеры/оценка — скрин с платформы
    assert GAP_MARKER in paragraphs  # оценочные/разработчики — пробел методисту


def test_empty_up_keeps_boilerplate_and_gaps_up_sections(tmp_path: Path) -> None:
    from content_factory.rpd.up_models import UpPlan

    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_pk(UpPlan()))
    doc = docx.Document(str(out))
    paragraphs = "\n".join(p.text for p in doc.paragraphs)
    assert "273-ФЗ" in paragraphs  # boilerplate всё равно на месте
    assert GAP_MARKER in paragraphs  # УП-разделы без данных → пробел
