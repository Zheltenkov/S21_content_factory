"""Перенос содержания программы в РПД по каркасу-шаблону.

Только перенос утверждённого текста, без домыслов. На каждый целевой раздел каркаса берём одноимённый
раздел программы (по номеру, затем по заголовку). Есть контент → переносим дословно. Нет — раздел
становится маркером-пробелом ``GAP_MARKER`` и вопросом методисту; текст каркаса-образца НЕ подставляем
(иначе пример протёк бы в чужую РПД).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .models import DpoProgram, ProgramTable
from .template import RpdTemplate, TargetSection

#: Плейсхолдер для незаполненного раздела — попадает и в выходной DOCX, и в список вопросов.
GAP_MARKER = "[ПРОБЕЛ: раздел не найден в программе — заполнить]"


class FilledSection(BaseModel):
    """Раздел РПД после переноса. ``filled`` — есть контент из программы; для контейнеров ``filled``
    тоже True (структурный заголовок, переносить нечего), но ``is_container`` их отделяет."""

    number: str = ""
    title: str = ""
    level: int = 0
    is_container: bool = False
    paragraphs: list[str] = Field(default_factory=list)
    tables: list[ProgramTable] = Field(default_factory=list)
    filled: bool = False


class GapQuestion(BaseModel):
    section_number: str
    section_title: str
    question: str


class RpdDraft(BaseModel):
    sections: list[FilledSection] = Field(default_factory=list)
    gaps: list[GapQuestion] = Field(default_factory=list)

    def section(self, number: str) -> FilledSection | None:
        for section in self.sections:
            if section.number == number:
                return section
        return None

    @property
    def coverage(self) -> float:
        """Доля перенесённых разделов среди содержательных (контейнеры-заголовки не в счёте)."""
        graded = [section for section in self.sections if not section.is_container]
        if not graded:
            return 0.0
        return sum(1 for section in graded if section.filled) / len(graded)


def build_rpd(program: DpoProgram, template: RpdTemplate) -> RpdDraft:
    """Собирает черновик РПД: переносит содержательные разделы, отсутствующие → пробел + вопрос."""
    sections: list[FilledSection] = []
    gaps: list[GapQuestion] = []
    for target in template.sections:
        filled = FilledSection(
            number=target.number,
            title=target.title,
            level=target.level,
            is_container=target.is_container,
        )
        if target.is_container:
            filled.filled = True  # структурный заголовок — ни контента, ни пробела
            sections.append(filled)
            continue

        source = program.section(target.number) or (program.find(target.title) if target.title else None)
        if source is not None and (source.paragraphs or source.tables):
            filled.paragraphs = list(source.paragraphs)
            filled.tables = list(source.tables)
            filled.filled = True
        else:
            filled.paragraphs = [GAP_MARKER]
            gaps.append(
                GapQuestion(
                    section_number=target.number,
                    section_title=target.title,
                    question=_gap_question(target),
                )
            )
        sections.append(filled)
    return RpdDraft(sections=sections, gaps=gaps)


def _gap_question(target: TargetSection) -> str:
    label = target.title or f"раздел {target.number}".strip()
    return (
        f"В программе не найден раздел «{label}». Укажите его содержание для переноса в РПД "
        "или отметьте, что раздел неприменим."
    )
