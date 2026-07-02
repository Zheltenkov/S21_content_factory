"""Валидатор теоретического раздела."""

import re
from dataclasses import dataclass

from ..config.thresholds import THRESHOLDS
from ..didactics.patterns import THEORY_PART_TITLE_PATTERN
from ..models.readme_document import ReadmeDocument, ReadmeSection
from ..utils.text_analysis import count_words


@dataclass
class Issue:
    """Проблема валидации."""

    path: str
    level: str
    message: str


class TheoryValidator:
    """Валидатор Главы 2 (Теория)."""

    def __init__(self):
        pass

    def validate_markdown(self, md: str) -> list[Issue]:
        """Validate Chapter 2 from a Markdown boundary payload."""
        return self.validate_document(ReadmeDocument.from_markdown(md))

    def validate_document(self, document: ReadmeDocument) -> list[Issue]:
        """Validate Chapter 2 against the typed README document tree."""
        issues: list[Issue] = []
        part_sections = self._theory_part_sections(document)
        n_parts = len(part_sections)
        lo, hi = THRESHOLDS["theory_parts"]
        if n_parts < lo or n_parts > hi:
            issues.append(
                Issue("theory", "error", f"Количество теоретических разделов {n_parts} вне диапазона {lo}–{hi}.")
            )

        for i, section in enumerate(part_sections):
            main = section.body_before_label("Пример")
            words = count_words(main, "ru")
            plo, phi = THRESHOLDS["theory_words_per_part"]
            if words < plo or words > phi:
                issues.append(
                    Issue(
                        f"theory.parts[{i}].body",
                        "error",
                        f"Длина раздела 2.{i + 1} = {words} слов (ожидалось {plo}–{phi}).",
                    )
                )
            if not section.has_label("Пример"):
                issues.append(Issue(f"theory.parts[{i}].example", "error", "Нет блока **Пример:**"))
            if not section.has_label("Вопросы к практике"):
                issues.append(
                    Issue(f"theory.parts[{i}].bridge_questions", "error", "Нет блока **Вопросы к практике:**")
                )
            if "[LO: нет прямого покрытия]" in section.body_markdown():
                issues.append(
                    Issue(
                        f"theory.parts[{i}].covers_outcomes",
                        "warn",
                        f"Раздел 2.{i + 1} не покрывает ни один LO по смыслу.",
                    )
                )
        return issues

    @staticmethod
    def _theory_part_sections(document: ReadmeDocument) -> list[ReadmeSection]:
        """Return typed theory part sections from Chapter 2 or partial H3 snippets."""
        title_re = re.compile(THEORY_PART_TITLE_PATTERN.replace(r"^###\s+", r"^"), flags=re.I)
        chapter = document.chapter_section(2)
        source = chapter.children if chapter is not None else document.sections
        return [section for section in source if section.level == 3 and title_re.search(section.title)]
