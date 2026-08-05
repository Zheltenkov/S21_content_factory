"""Маппинг УП → слоты шаблона ДПО (Slice A).

Источник — УП (учебный план): его содержание кладём в разделы шаблона. Разделы, которых в УП нет
(цель, актуальность), становятся пробелами-вопросами. Пример каркаса наружу не течёт.
"""

from __future__ import annotations

from pathlib import Path

from content_factory.rpd.render_docx import render_rpd_docx
from content_factory.rpd.template import RpdTemplate
from content_factory.rpd.transfer import GAP_MARKER
from content_factory.rpd.up_models import UpPlan, from_registry_plan
from content_factory.rpd.up_transfer import build_rpd_from_up

_PLAN = {
    "plan_id": 7,
    "title": "Тестовый учебный план",
    "meta": {"total_hours": 300},
    "rows": [
        {"block_title": "Модуль 1", "outcomes_know": "знает основы анализа", "outcomes_can": "умеет применять инструменты", "outcomes_skills": "навык обработки данных", "effort_hours": 150, "skills": ["Анализ данных"]},
        {"block_title": "Модуль 1", "outcomes_know": "знает методы", "outcomes_can": "", "outcomes_skills": "", "effort_hours": 50, "skills": ["Обработка"]},
        {"block_title": "Модуль 2", "outcomes_know": "", "outcomes_can": "строит модели", "outcomes_skills": "", "effort_hours": 100, "skills": ["Моделирование"]},
    ],
}


def test_from_registry_plan_normalizes_modules_and_hours() -> None:
    up = from_registry_plan(_PLAN)
    assert isinstance(up, UpPlan)
    assert up.title == "Тестовый учебный план"
    assert "300" in up.total_hours
    assert [m.title for m in up.modules] == ["Модуль 1", "Модуль 2"]
    assert len(up.modules[0].rows) == 2  # две строки в первом блоке


def test_outcomes_and_competencies_aggregate() -> None:
    up = from_registry_plan(_PLAN)
    outcomes = up.all_outcomes()
    assert any("знает основы" in o for o in outcomes)
    assert any("строит модели" in o for o in outcomes)
    codes = [c.code for c in up.competencies()]
    assert "Анализ данных" in codes and "Моделирование" in codes


def test_up_fills_hours_results_competencies_and_modules(template: RpdTemplate) -> None:
    up = from_registry_plan(_PLAN)
    draft = build_rpd_from_up(up, template)
    volume = draft.section("1.6")
    assert volume is not None and volume.filled and any("300" in p for p in volume.paragraphs)
    results = draft.section("1.11")
    assert results is not None and results.filled and any("знает основы" in p for p in results.paragraphs)
    comps = draft.section("1.12")
    assert comps is not None and comps.filled and comps.tables
    structure = draft.section("2")
    assert structure is not None and structure.filled  # учебный план / модули из УП


def test_prose_sections_absent_in_up_become_gaps(template: RpdTemplate) -> None:
    up = from_registry_plan(_PLAN)
    draft = build_rpd_from_up(up, template)
    goal = draft.section("1.1")  # цель — не из УП
    assert goal is not None and not goal.filled and goal.paragraphs == [GAP_MARKER]
    relevance = draft.section("1.3")
    assert relevance is not None and not relevance.filled
    keys = {g.section_number for g in draft.gaps}
    assert "1.1" in keys and "1.3" in keys


def test_coverage_and_no_invention(template: RpdTemplate) -> None:
    draft = build_rpd_from_up(from_registry_plan(_PLAN), template)
    assert 0.0 < draft.coverage < 1.0
    for section in draft.sections:
        if not section.filled:
            assert section.paragraphs == [GAP_MARKER] and not section.tables


def test_empty_plan_all_gaps() -> None:
    from content_factory.rpd.template import default_skeleton_path, load_rpd_template
    template = load_rpd_template(default_skeleton_path())
    draft = build_rpd_from_up(UpPlan(), template)
    assert draft.coverage == 0.0


def test_render_carries_up_content_not_example(template: RpdTemplate, skeleton_path: Path, tmp_path: Path) -> None:
    up = from_registry_plan(_PLAN)
    draft = build_rpd_from_up(up, template)
    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_docx(skeleton_path, draft, program_title=up.title))
    from content_factory.rpd.ingest import parse_dpo_program

    text = "\n".join(s.text for s in parse_dpo_program(out).sections)
    assert "знает основы" in text  # содержание УП дошло
    assert "ПРИМЕР" not in text  # текст каркаса-образца не протёк
    assert GAP_MARKER in text  # цель/актуальность помечены пробелом
