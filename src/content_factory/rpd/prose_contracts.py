"""Deterministic format contracts for methodical prose sections.

The LLM owns wording, while this module owns measurable requirements such as length and paragraph
count. Keeping them outside prompts lets generation, repair and the final quality gate enforce the
same product rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .up_models import UpPlan

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+(?:[-/][0-9A-Za-zА-Яа-яЁё]+)*")
_GENERATED_PLACEHOLDER = "[Будет сгенерировано]"
_OBSERVABLE_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("выявляет", re.compile(r"\bвыяв\w+", re.IGNORECASE)),
    ("формулирует", re.compile(r"\bформулир\w+", re.IGNORECASE)),
    ("определяет", re.compile(r"\bопредел\w+", re.IGNORECASE)),
    ("проверяет", re.compile(r"\bпровер\w+", re.IGNORECASE)),
    ("анализирует", re.compile(r"\bанализир\w+", re.IGNORECASE)),
    ("обосновывает", re.compile(r"\bобоснов\w+", re.IGNORECASE)),
    ("применяет", re.compile(r"\bпримен\w+", re.IGNORECASE)),
    ("создаёт", re.compile(r"\bсозда\w+", re.IGNORECASE)),
    ("разрабатывает", re.compile(r"\bразрабат\w+|\bразработ\w+", re.IGNORECASE)),
    ("проектирует", re.compile(r"\bпроектир\w+", re.IGNORECASE)),
    ("оценивает", re.compile(r"\bоцени\w+", re.IGNORECASE)),
    ("планирует", re.compile(r"\bпланир\w+", re.IGNORECASE)),
    ("организует", re.compile(r"\bорганиз\w+", re.IGNORECASE)),
    ("настраивает", re.compile(r"\bнастраив\w+", re.IGNORECASE)),
    ("демонстрирует", re.compile(r"\bдемонстрир\w+", re.IGNORECASE)),
    ("учитывает", re.compile(r"\bучитыва\w+", re.IGNORECASE)),
    ("связывает", re.compile(r"\bсвязыва\w+", re.IGNORECASE)),
    ("готовит", re.compile(r"\bготов\w+", re.IGNORECASE)),
)
_SOURCE_TERM_STOPWORDS = {
    "академических",
    "данные",
    "задача",
    "задачи",
    "модуль",
    "модуля",
    "обучение",
    "обучения",
    "практический",
    "практической",
    "программа",
    "программы",
    "проект",
    "результат",
    "результаты",
    "слушатель",
    "учебного",
}


@dataclass(frozen=True, slots=True)
class ProseContract:
    """Measurable and semantic constraints for one family of prose sections."""

    name: str
    min_words: int
    target_words_min: int
    target_words_max: int
    min_paragraphs: int
    max_paragraphs: int
    outline: tuple[str, ...]
    min_observable_actions: int = 0
    min_source_anchors: int = 0

    def prompt_payload(self) -> dict[str, object]:
        """Return a JSON-safe contract embedded into generation and repair prompts."""
        payload: dict[str, object] = {
            "target_words": f"{self.target_words_min}-{self.target_words_max}",
            "minimum_words": self.min_words,
            "paragraphs": f"{self.min_paragraphs}-{self.max_paragraphs}",
            "required_structure": list(self.outline),
        }
        if self.min_observable_actions:
            payload["minimum_observable_actions"] = self.min_observable_actions
        if self.min_source_anchors:
            payload["minimum_source_anchors"] = self.min_source_anchors
        return payload


@dataclass(frozen=True, slots=True)
class ProseContractViolation:
    """Typed evidence for a deterministic quality finding."""

    message: str
    word_count: int
    paragraph_count: int


@dataclass(frozen=True, slots=True)
class ProgramGoalsSignals:
    """Deterministic evidence that section 2.1 is actionable and grounded in its UP."""

    observable_actions: tuple[str, ...]
    source_anchors: tuple[str, ...]
    paragraph_count: int

    @property
    def sufficient(self) -> bool:
        """Return whether a specificity warning from the subjective critic is redundant."""
        return (
            len(self.observable_actions) >= _PROGRAM_IMPLEMENTATION_GOALS_CONTRACT.min_observable_actions
            and len(self.source_anchors) >= _PROGRAM_IMPLEMENTATION_GOALS_CONTRACT.min_source_anchors
            and self.paragraph_count >= _PROGRAM_IMPLEMENTATION_GOALS_CONTRACT.min_paragraphs
        )


_RELEVANCE_CONTRACT = ProseContract(
    name="Актуальность программы",
    min_words=180,
    target_words_min=220,
    target_words_max=280,
    min_paragraphs=4,
    max_paragraphs=5,
    outline=(
        "контекст и профессиональная потребность, из-за которой программа нужна сейчас",
        "факторы востребованности темы, подтверждаемые содержанием учебного плана",
        "проблема или дефицит практических компетенций целевой аудитории без выдуманной статистики",
        "связь потребности с модулями, технологиями и результатами именно этой программы",
        "итоговое обоснование актуальности без повторения раздела о целях и задачах",
    ),
)

_PROGRAM_IMPLEMENTATION_GOALS_CONTRACT = ProseContract(
    name="Цели реализации программы",
    min_words=90,
    target_words_min=120,
    target_words_max=170,
    min_paragraphs=2,
    max_paragraphs=3,
    outline=(
        "интегральный практический результат программы, выведенный из целей и содержания модулей УП",
        "не менее шести разных наблюдаемых действий слушателя после обучения, сформулированных активными глаголами",
        "связь действий с конкретными результатами, компетенциями и учебными продуктами из УП",
        "не менее двух проверяемых результатов или артефактов, которые слушатель создаёт, обосновывает или защищает",
        "без повторения списка задач раздела 1.4 и без последовательного пересказа модулей",
    ),
    min_observable_actions=6,
    min_source_anchors=5,
)


def prose_contract_for(section_key_or_title: str) -> ProseContract | None:
    """Resolve a prose contract by semantic title marker, independent of section numbering."""
    normalized = section_key_or_title.casefold()
    if "цели реализации программы" in normalized:
        return _PROGRAM_IMPLEMENTATION_GOALS_CONTRACT
    if "актуальн" in normalized:
        return _RELEVANCE_CONTRACT
    return None


def prose_contracts_for(section_keys: set[str]) -> dict[str, dict[str, object]]:
    """Return prompt payloads only for sections with an explicit product contract."""
    result: dict[str, dict[str, object]] = {}
    for key in sorted(section_keys):
        contract = prose_contract_for(key)
        if contract is not None:
            result[key] = contract.prompt_payload()
    return result


def relevance_generation_instruction() -> str:
    """Build the single-section fallback prompt from the same authoritative contract."""
    contract = _RELEVANCE_CONTRACT
    structure = "; ".join(contract.outline)
    return (
        f"Напиши развёрнутый раздел «{contract.name}»: {contract.target_words_min}-{contract.target_words_max} "
        f"слов, {contract.min_paragraphs}-{contract.max_paragraphs} содержательных абзацев. "
        f"Раскрой последовательно: {structure}. Опирайся только на данные программы, не добавляй "
        "неподтверждённую статистику, ссылки на исследования или внешние факты."
    )


def program_implementation_goals_generation_instruction() -> str:
    """Build the fallback prompt for section 2.1 from its authoritative contract."""
    contract = _PROGRAM_IMPLEMENTATION_GOALS_CONTRACT
    structure = "; ".join(contract.outline)
    return (
        f"Напиши раздел «{contract.name}» по результатам анализа учебного плана: "
        f"{contract.target_words_min}-{contract.target_words_max} слов, "
        f"{contract.min_paragraphs}-{contract.max_paragraphs} содержательных абзаца. "
        f"Раскрой: {structure}. Используй цели модулей, результаты обучения и компетенции из УП. "
        "Не ограничивайся словами «освоить», «сформировать» или «подготовить»: называй действия, "
        "которые можно наблюдать и проверить, а также конкретные учебные продукты из УП. "
        "Не выдумывай новые результаты и не оформляй текст как список задач."
    )


def assess_program_implementation_goals(paragraphs: list[str], up: UpPlan) -> ProgramGoalsSignals:
    """Measure observable actions and lexical grounding against the source UP."""
    nonempty = [paragraph.strip() for paragraph in paragraphs if paragraph.strip()]
    text = " ".join(nonempty)
    observable_actions = tuple(
        label
        for label, pattern in _OBSERVABLE_ACTION_PATTERNS
        if pattern.search(text) is not None
    )

    # Grounding uses only source fields, never model-produced briefs. Common RPD words are removed
    # so a generic paragraph cannot pass merely by saying "program", "module" and "result".
    source_parts = [up.title]
    for module in up.modules:
        source_parts.extend((module.title, module.goals))
        for row in module.rows:
            source_parts.extend(
                (
                    row.name,
                    row.description,
                    row.outcomes_know,
                    row.outcomes_can,
                    row.outcomes_skills,
                    " ".join(row.skills),
                )
            )
    source_tokens = _significant_tokens(" ".join(source_parts))
    text_tokens = _significant_tokens(text)
    source_anchors = tuple(sorted(source_tokens & text_tokens))
    return ProgramGoalsSignals(
        observable_actions=observable_actions,
        source_anchors=source_anchors,
        paragraph_count=len(nonempty),
    )


def _significant_tokens(text: str) -> set[str]:
    """Return stable lexical anchors suitable for a conservative grounding check."""
    return {
        token
        for token in (match.group(0).casefold() for match in _WORD_RE.finditer(text))
        if len(token) >= 5 and token not in _SOURCE_TERM_STOPWORDS
    }


def validate_prose_contract(
    section_key_or_title: str,
    paragraphs: list[str],
) -> ProseContractViolation | None:
    """Return deterministic evidence when generated prose is shorter than its minimum contract."""
    contract = prose_contract_for(section_key_or_title)
    nonempty = [paragraph.strip() for paragraph in paragraphs if paragraph.strip()]
    if contract is None or _GENERATED_PLACEHOLDER in nonempty:
        return None
    word_count = len(_WORD_RE.findall(" ".join(nonempty)))
    paragraph_count = len(nonempty)
    if word_count >= contract.min_words and paragraph_count >= contract.min_paragraphs:
        return None
    return ProseContractViolation(
        message=(
            f"Раздел должен быть развёрнутым: целевой объём {contract.target_words_min}-{contract.target_words_max} "
            f"слов и {contract.min_paragraphs}-{contract.max_paragraphs} абзацев; сейчас "
            f"{word_count} слов и {paragraph_count} абзац(а)."
        ),
        word_count=word_count,
        paragraph_count=paragraph_count,
    )
