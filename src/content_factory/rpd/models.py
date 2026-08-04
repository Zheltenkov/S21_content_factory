"""Типизированная модель действующей ДПО-программы (вход РПД-генератора).

Толерантна к отсутствию блоков: разные программы могут содержать/не содержать разделы. Слот-
аксессоры (``goal``, ``planned_results``, ``competencies`` …) возвращают пусто, если раздела нет —
это сигнал «пробел → вопрос методисту», а НЕ повод что-то выдумать.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProgramTable(BaseModel):
    """Таблица раздела: строка заголовка отдельно, ниже — только строки данных."""

    header: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class ProgramSection(BaseModel):
    """Один раздел программы: номер («1.1»), заголовок, тело (абзацы) и таблицы."""

    number: str = ""
    title: str = ""
    level: int = 0
    paragraphs: list[str] = Field(default_factory=list)
    tables: list[ProgramTable] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.paragraphs).strip()


class Competency(BaseModel):
    """Профессиональная компетенция: код (ПК-1) и её описание."""

    code: str
    description: str = ""


class DpoProgram(BaseModel):
    """Разобранная ДПО-программа — набор разделов + удобные слот-аксессоры для переноса."""

    title: str = ""
    sections: list[ProgramSection] = Field(default_factory=list)

    def section(self, number: str) -> ProgramSection | None:
        """Раздел по точному номеру («1.11»); ``None`` — раздела нет (пробел)."""
        for section in self.sections:
            if section.number == number:
                return section
        return None

    def find(self, *title_substrings: str) -> ProgramSection | None:
        """Первый раздел, чей заголовок содержит любую из подстрок (регистронезависимо)."""
        for section in self.sections:
            low = section.title.lower()
            if any(sub.lower() in low for sub in title_substrings):
                return section
        return None

    def _slot(self, number: str, *title_substrings: str) -> ProgramSection | None:
        return self.section(number) or (self.find(*title_substrings) if title_substrings else None)

    def _slot_text(self, number: str, *title_substrings: str) -> str:
        section = self._slot(number, *title_substrings)
        return section.text if section else ""

    @property
    def goal(self) -> str:
        return self._slot_text("1.1", "цель")

    @property
    def relevance(self) -> str:
        return self._slot_text("1.3", "актуальность")

    @property
    def audience_requirements(self) -> str:
        return self._slot_text("1.4", "требования к обучающимся")

    @property
    def study_form(self) -> str:
        return self._slot_text("1.5", "форма обучения")

    @property
    def volume_hours(self) -> str:
        return self._slot_text("1.6", "объем", "объём")

    @property
    def study_mode(self) -> str:
        return self._slot_text("1.7", "режим обучения")

    @property
    def planned_results(self) -> list[str]:
        """Строки планируемых результатов; вводная строка с двоеточием («…обучающийся:») отбрасывается."""
        section = self._slot("1.11", "планируемые результаты")
        if section is None:
            return []
        lines = [line.strip() for line in section.paragraphs if line.strip()]
        return [line for line in lines if not line.endswith(":")]

    @property
    def competencies(self) -> list[Competency]:
        """Компетенции из таблицы раздела 1.12 (заголовок таблицы в список не попадает)."""
        section = self._slot("1.12", "профессиональные компетенции", "компетенц")
        if section is None:
            return []
        for table in section.tables:
            head = [cell.lower() for cell in table.header]
            if head and any(hint in head[0] for hint in ("компетенц", "код")):
                return _competencies_from_rows(table.rows)
        # запасной путь: первая непустая таблица раздела
        for table in section.tables:
            if table.rows:
                return _competencies_from_rows(table.rows)
        return []


def _competencies_from_rows(rows: list[list[str]]) -> list[Competency]:
    out: list[Competency] = []
    for row in rows:
        code = (row[0].strip() if row else "")
        if not code:
            continue
        description = row[1].strip() if len(row) > 1 else ""
        out.append(Competency(code=code, description=description))
    return out
