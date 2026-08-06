"""Редактируемый черновик РПД: УП → структурированные разделы (для human-in-the-loop).

Разделяет пайплайн: build (здесь) собирает содержимое каждого редактируемого раздела в
:class:`RpdBuild`; :func:`render_selective.render_build` кладёт черновик в DOCX по шаблону. Между
ними встаёт редактор — методист правит разделы, потом экспорт. boilerplate-разделы (форма ДПО) в
черновик не попадают: их держит шаблон.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .dpo_spec import Cat, category_for
from .prose import generate_prose
from .template import default_pk_skeleton_path, load_rpd_template
from .transfer import GAP_MARKER
from .up_models import UpPlan

_GENERATE_PLACEHOLDER = "[Будет сгенерировано]"
_PLATFORM_NOTE = "[Методист: вставьте скрин задания с платформы/LMS]"
_GAP_NOTE = "[Методист: проверьте и дополните раздел под программу]"

#: Понятные методисту ярлыки источника раздела для навигатора редактора.
_CATEGORY_LABEL = {
    Cat.UP_PLAN: "из УП",
    Cat.UP_GRAFIK: "из УП",
    Cat.UP_MODULES: "из УП",
    Cat.UP_COMP: "из УП",
    Cat.GENERATE: "сгенерировано",
    Cat.PLATFORM: "с платформы",
    Cat.GAP: "методист",
}


class RpdSection(BaseModel):
    """Один редактируемый раздел черновика: либо абзацы (проза/пробел), либо таблица (данные УП)."""

    number: str = ""
    title: str = ""
    category: str = ""
    label: str = ""
    kind: str = "paragraphs"  # "paragraphs" | "table"
    paragraphs: list[str] = Field(default_factory=list)
    table_header: list[str] = Field(default_factory=list)
    table_rows: list[list[str]] = Field(default_factory=list)
    done: bool = False  # методист отметил раздел готовым


class RpdBuild(BaseModel):
    """Черновик РПД: название программы + редактируемые разделы (без boilerplate — тот в шаблоне)."""

    program_title: str = ""
    sections: list[RpdSection] = Field(default_factory=list)

    def section(self, number: str, title: str) -> RpdSection | None:
        for section in self.sections:
            if section.number == number and section.title == title:
                return section
        return None


def build_rpd_sections(up: UpPlan, template_path: str | Path | None = None, llm_client: Any = None) -> RpdBuild:
    """Собирает редактируемый черновик РПД из УП (+ проза через LLM, если дан клиент)."""
    template = load_rpd_template(Path(template_path) if template_path else default_pk_skeleton_path())
    sections: list[RpdSection] = []
    for target in template.sections:
        category = category_for(target.number, target.title)
        if category is Cat.BOILERPLATE:
            continue  # форма ДПО — держит шаблон, не редактируем
        sections.append(_section_content(target.number, target.title, category, up, llm_client))
    return RpdBuild(program_title=up.title, sections=sections)


def _section_content(number: str, title: str, category: Cat, up: UpPlan, client: Any) -> RpdSection:
    base = RpdSection(number=number, title=title, category=category.value, label=_CATEGORY_LABEL.get(category, ""))
    if category is Cat.GENERATE:
        paragraphs = generate_prose(title, up, client) if client is not None else []
        base.kind = "paragraphs"
        base.paragraphs = paragraphs or [_GENERATE_PLACEHOLDER]
        return base
    if category is Cat.PLATFORM:
        base.kind = "paragraphs"
        draft = generate_prose(title, up, client) if client is not None else []
        base.paragraphs = [*draft, _PLATFORM_NOTE]
        return base
    if category in {Cat.UP_PLAN, Cat.UP_GRAFIK, Cat.UP_MODULES, Cat.UP_COMP}:
        table = _table_for(category, up)
        if table is not None:
            base.kind = "table"
            base.table_header, base.table_rows = table
            return base
        base.paragraphs = [GAP_MARKER]  # нет данных УП
        return base
    # Cat.GAP — раздел для методиста: даём общий черновик (если есть LLM) + явную пометку.
    base.kind = "paragraphs"
    draft = generate_prose(title, up, client) if client is not None else []
    base.paragraphs = [*draft, _GAP_NOTE]
    return base


def _table_for(category: Cat, up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    if category is Cat.UP_PLAN:
        return _uchebnyi_plan(up)
    if category is Cat.UP_GRAFIK:
        return _grafik(up)
    if category is Cat.UP_MODULES:
        return _modules(up)
    if category is Cat.UP_COMP:
        return _competencies(up)
    return None


def _uchebnyi_plan(up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    if not up.modules:
        return None
    header = ["№", "Наименование модуля", "Всего, ч", "Лекции", "Практические", "Самостоятельная", "Контроль"]
    rows = [[str(i), m.title, m.hours, "", m.hours, "", m.control()] for i, m in enumerate(up.modules, start=1)]
    return (header, rows)


def _grafik(up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    if not up.modules:
        return None
    header = ["Компонент", "Период", "Часы", "Форма организации", "Контроль"]
    rows = [[m.title, "", m.hours, m.fmt(), m.control()] for m in up.modules]
    return (header, rows)


def _modules(up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    if not up.modules:
        return None
    header = ["№", "Тема и краткое содержание", "Вид работы", "Часы", "Методы", "Контроль"]
    rows: list[list[str]] = []
    index = 1
    for module in up.modules:
        for row in module.rows:
            theme = f"[{module.title}] {row.name}".strip()
            if row.description:
                theme = f"{theme}: {row.description}"
            rows.append([str(index), theme, row.fmt, row.hours, "", row.control()])
            index += 1
    return (header, rows) if rows else None


def _competencies(up: UpPlan) -> tuple[list[str], list[list[str]]] | None:
    comp_rows = up.competency_rows()
    if not comp_rows:
        return None
    header = ["Код", "Профессиональная компетенция", "Исходный уровень", "Качественное изменение", "После обучения", "Основание"]
    rows = [[code, name, "", "", after, ""] for code, name, after in comp_rows]
    return (header, rows)
