"""BriefQuestion entity + blocking taxonomy (contract epic, slice 7).

The review found 8 methodical questions kept as free text; readiness allowed a
``ready_with_questions`` publishable state. Fix: turn each open question into a typed
``BriefQuestion`` with a category and a blocking flag, so freeze/publication can require
``blocking_question_count == 0`` while a draft is always buildable.

Conservative rule: a question is blocking by DEFAULT; it is downgraded to non-blocking
only when it matches an explicit editorial category. Questions about audience, practice
format, demo/success metric, or target role always block.

Pure leaf: depends only on stdlib.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: category -> keyword hints. Editorial is the only non-blocking category; everything else
#: (and anything unmatched) blocks publication.
_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("audience", ("аудитори", "для кого", "уровень слушат", "новичк", "начинающ")),
    ("practice_format", ("формат", "как проверя", "как оцен", "оценивать задан", "формат сдач")),
    ("demo_metric", ("demo", "демо", "метрик", "kpi", "как измер", "измерить", "критери успех", "measur")),
    ("target_role", ("роль", "должност", "профессия", "кем работа", "выпускник станет")),
    ("editorial", ("опечат", "переформул", "стиль", "редакц", "уточнить назван", "название лучше", "кейс", "сторител", "storytell", "какие примеры", "иллюстрац")),
)

_NON_BLOCKING_CATEGORIES = frozenset({"editorial"})


@dataclass(frozen=True)
class BriefQuestion:
    """A typed open question from the brief, with an explicit blocking decision."""

    text: str
    category: str
    blocking: bool
    source: str = "brief"
    status: str = "open"
    answer: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "category": self.category,
            "blocking": self.blocking,
            "source": self.source,
            "status": self.status,
            "answer": self.answer,
        }


def _categorize(text: str) -> str:
    lowered = re.sub(r"\s+", " ", text.casefold().replace("ё", "е"))
    for category, hints in _CATEGORY_HINTS:
        if any(hint in lowered for hint in hints):
            return category
    return "uncategorized"


def classify_question(text: str) -> BriefQuestion:
    """Classify one question; blocking unless it is explicitly editorial."""
    category = _categorize(text)
    return BriefQuestion(text=text.strip(), category=category, blocking=category not in _NON_BLOCKING_CATEGORIES)


def classify_questions(open_questions: tuple[str, ...] | list[str]) -> tuple[BriefQuestion, ...]:
    return tuple(classify_question(str(text)) for text in open_questions if str(text).strip())


def count_blocking(questions: tuple[BriefQuestion, ...]) -> int:
    return sum(1 for question in questions if question.blocking and question.status == "open")
