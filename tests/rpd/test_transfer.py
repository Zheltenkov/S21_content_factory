"""Перенос программы в РПД по каркасу-шаблону (Slice 1).

Каркас задаёт целевой набор разделов. Каждый раздел заполняется из одноимённого раздела программы;
отсутствует — становится маркером-пробелом + вопросом методисту (без выдумок и без утечки примера).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.rpd.ingest import parse_dpo_program
from content_factory.rpd.models import DpoProgram
from content_factory.rpd.template import RpdTemplate, load_rpd_template
from content_factory.rpd.transfer import GAP_MARKER, GapQuestion, RpdDraft, build_rpd

# ``skeleton_path`` / ``template`` / ``sample_program`` fixtures live in conftest.py.


def test_template_lists_skeleton_sections(template: RpdTemplate) -> None:
    numbers = [s.number for s in template.sections]
    assert "1.1" in numbers and "1.3" in numbers and "1.12" in numbers and "2" in numbers


def test_present_sections_are_transferred_from_the_program(sample_program: DpoProgram, template: RpdTemplate) -> None:
    draft = build_rpd(sample_program, template)
    goal = draft.section("1.1")
    assert goal is not None and goal.filled
    # контент из ПРОГРАММЫ, а не из каркаса-примера
    assert any("Программа профессиональной переподготовки" in p for p in goal.paragraphs)
    assert all("ПРИМЕР" not in p for p in goal.paragraphs)
    volume = draft.section("1.6")
    assert volume is not None and volume.filled and any("300" in p for p in volume.paragraphs)


def test_competency_table_is_carried_from_the_program(sample_program: DpoProgram, template: RpdTemplate) -> None:
    draft = build_rpd(sample_program, template)
    comp = draft.section("1.12")
    assert comp is not None and comp.filled and comp.tables
    flat = [cell for row in comp.tables[0].rows for cell in row]
    assert any("ПК - 1" in cell for cell in flat)
    assert not any("ПК - 9" in cell for cell in flat)  # пример из каркаса не протёк


def test_missing_section_becomes_a_gap_marker_not_example_text(sample_program: DpoProgram, template: RpdTemplate) -> None:
    draft = build_rpd(sample_program, template)
    relevance = draft.section("1.3")  # 1.3 есть в каркасе, нет в программе
    assert relevance is not None and not relevance.filled
    assert relevance.paragraphs == [GAP_MARKER]
    assert all("ПРИМЕР" not in p for p in relevance.paragraphs)  # текст каркаса не утёк
    keys = [g.section_number for g in draft.gaps]
    assert "1.3" in keys
    gap = next(g for g in draft.gaps if g.section_number == "1.3")
    assert isinstance(gap, GapQuestion) and gap.question.strip()


def test_empty_section_in_program_is_a_gap(sample_program: DpoProgram, template: RpdTemplate) -> None:
    # раздел «2» в программе есть, но без тела -> пробел
    draft = build_rpd(sample_program, template)
    structure = draft.section("2")
    assert structure is not None and not structure.filled
    assert "2" in [g.section_number for g in draft.gaps]


def test_coverage_and_no_invention(sample_program: DpoProgram, template: RpdTemplate) -> None:
    draft = build_rpd(sample_program, template)
    assert 0.0 < draft.coverage < 1.0
    # ни один незаполненный раздел не содержит ничего кроме маркера-пробела
    for section in draft.sections:
        if not section.filled:
            assert section.paragraphs == [GAP_MARKER] and not section.tables


def test_empty_program_yields_all_gaps(template: RpdTemplate) -> None:
    draft = build_rpd(DpoProgram(), template)
    assert draft.coverage == 0.0
    # контейнерные заголовки (напр. «1. ХАРАКТЕРИСТИКА») — структурные, не пробелы; остальные — gap
    graded = [s for s in draft.sections if not s.is_container]
    assert len(draft.gaps) == len(graded)
    assert all(not s.filled for s in graded)


_REAL = Path(__file__).resolve().parents[2] / "Биоинформатика_и_искусственный_интеллект_в_медицине_итог.docx"


@pytest.mark.skipif(not _REAL.exists(), reason="реальный образец ДПО отсутствует в корне репо")
def test_real_sample_transfers_onto_its_own_skeleton() -> None:
    # Каркас и программа — один и тот же документ: перенос должен покрыть ~всё, пробелов почти нет.
    template = load_rpd_template(_REAL)
    program = parse_dpo_program(_REAL)
    draft = build_rpd(program, template)
    assert draft.coverage > 0.8
    assert isinstance(draft, RpdDraft)
