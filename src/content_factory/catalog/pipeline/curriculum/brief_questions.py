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

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

#: category -> keyword hints. Editorial is the only non-blocking category; everything else
#: (and anything unmatched) blocks publication.
_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("audience", ("аудитори", "для кого", "уровень слушат", "новичк", "начинающ")),
    ("practice_format", ("формат", "как проверя", "как оцен", "оценивать задан", "формат сдач")),
    ("demo_metric", ("demo", "демо", "метрик", "kpi", "как измер", "измерить", "критери успех", "measur")),
    ("target_role", ("роль", "должност", "профессия", "кем работа", "выпускник станет")),
    ("prerequisites", ("пререквизит", "входн требован", "что должен знать", "до начала")),
    (
        "tools_access",
        ("доступ", "лицензи", "стенд", "аккаунт", "инструмент", "впн", "vpn", "ииагент"),
    ),
    ("product_scope", ("границ", "scope", "объём продукт", "масштаб", "что входит")),
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

    @property
    def key(self) -> str:
        return question_key(self.text)

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
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


def question_key(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text).strip().casefold().replace("ё", "е"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def classify_questions(
    open_questions: tuple[str, ...] | list[str],
    *,
    answers: Mapping[str, str] | None = None,
) -> tuple[BriefQuestion, ...]:
    resolved_answers = answers or {}
    questions: list[BriefQuestion] = []
    for raw_text in open_questions:
        text = str(raw_text).strip()
        if not text:
            continue
        question = classify_question(text)
        answer = str(resolved_answers.get(question.key) or "").strip()
        if answer:
            question = BriefQuestion(
                text=question.text,
                category=question.category,
                blocking=question.blocking,
                source=question.source,
                status="answered",
                answer=answer,
            )
        questions.append(question)
    return tuple(questions)


def count_blocking(questions: tuple[BriefQuestion, ...]) -> int:
    return sum(1 for question in questions if question.blocking and question.status == "open")
