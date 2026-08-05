"""Маппинг УП → слоты шаблона ДПО.

На каждый содержательный раздел шаблона решаем, наполняется ли он из УП (объём, результаты,
компетенции, учебный план/модули). Прозаические разделы, которых в УП нет (цель, актуальность,
аттестация, условия) → пробел-вопрос методисту. Переиспользует типы черновика из :mod:`.transfer`.
"""

from __future__ import annotations

from .models import ProgramTable
from .template import RpdTemplate, TargetSection
from .transfer import GAP_MARKER, FilledSection, GapQuestion, RpdDraft, _gap_question
from .up_models import UpModule, UpPlan

_Content = tuple[list[str], list[ProgramTable]]


def build_rpd_from_up(up: UpPlan, template: RpdTemplate) -> RpdDraft:
    """Собирает черновик РПД, наполняя разделы шаблона из УП; недостающее → пробел-вопрос."""
    sections: list[FilledSection] = []
    gaps: list[GapQuestion] = []
    for target in template.sections:
        filled = FilledSection(
            number=target.number, title=target.title, level=target.level, is_container=target.is_container
        )
        if target.is_container:
            filled.filled = True
            sections.append(filled)
            continue

        content = _fill_from_up(target, up)
        if content is not None and (content[0] or content[1]):
            filled.paragraphs, filled.tables = content
            filled.filled = True
        else:
            filled.paragraphs = [GAP_MARKER]
            gaps.append(
                GapQuestion(section_number=target.number, section_title=target.title, question=_gap_question(target))
            )
        sections.append(filled)
    return RpdDraft(sections=sections, gaps=gaps)


def _fill_from_up(target: TargetSection, up: UpPlan) -> _Content | None:
    number = target.number
    title = target.title.lower()

    def has(*subs: str) -> bool:
        return any(sub in title for sub in subs)

    if number == "1.6" or has("объем", "объём"):
        return ([f"{up.total_hours} академических часов."], []) if up.total_hours else None

    if number == "1.11" or has("планируемые результаты", "результаты обучения"):
        outcomes = up.all_outcomes()
        return (outcomes, []) if outcomes else None

    if number == "1.12" or has("компетенц"):
        competencies = up.competencies()
        if not competencies:
            return None
        table = ProgramTable(
            header=["Компетенции", "Описание компетенции"],
            rows=[[comp.code, comp.description] for comp in competencies],
        )
        return ([], [table])

    if number in {"2", "2.1", "2.3"} or has("учебный план", "структура и содержание", "рабочая программа", "модул"):
        return _modules_content(up)

    return None


def _modules_content(up: UpPlan) -> _Content | None:
    if not up.modules:
        return None
    rows = [[module.title, module.hours, _module_outcomes(module)] for module in up.modules]
    table = ProgramTable(header=["Модуль", "Часы", "Результаты"], rows=rows)
    return ([], [table])


def _module_outcomes(module: UpModule) -> str:
    outcomes: list[str] = []
    for row in module.rows:
        for outcome in row.outcomes():
            if outcome not in outcomes:
                outcomes.append(outcome)
    return "; ".join(outcomes)
