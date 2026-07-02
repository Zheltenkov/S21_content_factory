"""Валидатор структуры документа."""

import re
from dataclasses import dataclass

from ..config.thresholds import THRESHOLDS
from ..models.readme_document import ReadmeDocument, ReadmeSection
from ..utils.text_analysis import count_words


@dataclass
class Issue:
    """Проблема валидации."""

    path: str
    level: str
    message: str


class IntroValidator:
    """Валидатор Главы 1 (Введение и инструкция)."""

    def __init__(self):
        self.rx_intro = re.compile(r"^###\s+Введение\s*(.+?)(?=^###\s+Инструкция|\Z)", re.S | re.M)
        self.rx_instr = re.compile(r"^###\s+Инструкция\s*(.+)$", re.S | re.M)

    def validate_markdown(self, md: str) -> list[Issue]:
        """Validate Chapter 1 from a Markdown boundary payload."""
        return self.validate_document(ReadmeDocument.from_markdown(md))

    def validate_document(self, document: ReadmeDocument) -> list[Issue]:
        """Validate Chapter 1 against the typed README document tree."""
        issues: list[Issue] = []
        intro_section = self._find_h3_section(document, "Введение")
        instruction_section = self._find_h3_section(document, "Инструкция")
        if intro_section is None:
            issues.append(Issue("intro", "error", "Отсутствует секция «Введение»."))
            return issues
        if instruction_section is None:
            issues.append(Issue("instruction", "error", "Отсутствует секция «Инструкция»."))
            return issues

        intro = intro_section.body.strip()
        instr = instruction_section.body.strip()

        lo, hi = THRESHOLDS["intro_words"]
        # Используем универсальную функцию подсчета слов (по умолчанию русский)
        words_intro = count_words(intro, "ru")
        if words_intro < lo or words_intro > hi:
            issues.append(
                Issue("intro.intro_text", "error", f"Длина введения {words_intro} слов вне диапазона {lo}–{hi}.")
            )

        intro_low = intro.lower()
        markers = ["используется для", "в реальной задаче", "применяется", "основная идея", "что решает", "зачем"]
        if not any(k in intro_low for k in markers):
            issues.append(
                Issue(
                    "intro.intro_text",
                    "warn",
                    "Во «Введении» нет маркеров контекста (зачем/что решает/применение).",
                )
            )

        instr_low = instr.lower()
        for req in ["допускается", "запрещено", "обязательно"]:
            if req not in instr_low:
                issues.append(Issue("intro.instruction_text", "error", f"В «Инструкции» отсутствует слово «{req}»."))

        return issues

    @staticmethod
    def _find_h3_section(document: ReadmeDocument, title: str) -> ReadmeSection | None:
        """Find an exact H3 section title in Chapter 1, falling back to root H3 snippets."""
        normalized = title.casefold().strip()
        candidates: list[ReadmeSection] = []
        chapter = document.chapter_section(1)
        if chapter is not None:
            candidates.extend(chapter.flatten())
        candidates.extend(section for section in document.sections if section.level == 3)
        for section in candidates:
            normalized_title = section.title.casefold().strip()
            if section.level == 3 and (normalized_title == normalized or normalized_title.startswith(f"{normalized} ")):
                return section
        return None

