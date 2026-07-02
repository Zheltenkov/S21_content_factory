"""Сопоставление пунктов чек-листа с заданиями README."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from content_audit.text_utils import normalize_for_match


MatchStrength = Literal["strong", "weak"]


CHECKLIST_STOP_TOKENS = {
    "part",
    "task",
    "step",
    "section",
    "chapter",
    "module",
    "exercise",
    "project",
    "qism",
    "часть",
    "раздел",
    "задание",
}
ACCEPTANCE_RE = re.compile(
    r"\b(must|should|check(?:s|ed|ing)?|criteria|criterion|expected|verify|validate|valid|invalid|"
    r"error|incorrect|accepted|rejected)\b|"
    r"(долж|следует|провер|критери|ожида|ошиб|валид|некоррект|принима)",
    re.IGNORECASE,
)
ARTIFACT_RE = re.compile(
    r"`[^`]+`|"
    r"\b[\w./-]+\.(?:c|h|cpp|cc|py|js|ts|java|go|rs|rb|sh|sql|md|yml|yaml|json|txt|csv|png|jpg|jpeg|svg)\b|"
    r"\b(makefile|make|target|file|folder|directory|artifact|input|output|src|tests?)\b|"
    r"(файл|папк|каталог|артефакт|команд|цель|ввод|вывод|исходник|тест)",
    re.IGNORECASE,
)
EXAMPLE_RE = re.compile(
    r"```|\b(example|sample|template|stdin|stdout|input|output|expected output)\b|"
    r"(пример|шаблон|образец|входн|выходн)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ChecklistQuestion:
    """Один пункт чек-листа с текстом, который можно оценивать по полноте."""

    name: str
    description_text: str


@dataclass(frozen=True)
class ChecklistMatchResult:
    """Итог сопоставления чек-листа с README."""

    total: int
    matched: int
    ratio: float
    strong_matched: int
    weak_matched: int
    strong_ratio: float
    weak_ratio: float
    matched_names: tuple[str, ...]
    strong_matched_names: tuple[str, ...]
    weak_matched_names: tuple[str, ...]
    unmatched_names: tuple[str, ...]


@dataclass(frozen=True)
class ChecklistQuestionQuality:
    """Оценка полноты одного пункта чек-листа."""

    name: str
    has_expanded_description: bool
    has_acceptance_criteria: bool
    has_expected_artifact: bool
    has_example_or_template: bool
    is_complete: bool


@dataclass(frozen=True)
class ChecklistDescriptionResult:
    """Итог проверки развёрнутости пунктов чек-листа."""

    total: int
    complete: int
    ratio: float
    complete_names: tuple[str, ...]
    incomplete_names: tuple[str, ...]
    assessments: tuple[ChecklistQuestionQuality, ...]


def extract_checklist_questions(payload: object) -> list[ChecklistQuestion]:
    """Достаёт пункты проверки и содержательный текст из YAML-чек-листа."""

    if not isinstance(payload, dict):
        return []
    questions: list[ChecklistQuestion] = []
    for section in payload.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        for question in section.get("questions", []) or []:
            if isinstance(question, dict) and question.get("name"):
                questions.append(
                    ChecklistQuestion(
                        name=str(question["name"]),
                        description_text=_collect_question_text(question),
                    )
                )
    return questions


def extract_checklist_question_names(payload: object) -> list[str]:
    """Достаём имена вопросов из YAML-чек-листа."""

    return [question.name for question in extract_checklist_questions(payload)]


def match_checklist_to_readme(question_names: list[str], readme_text: str) -> ChecklistMatchResult:
    """Сопоставляет пункты чек-листа с README и возвращает объяснимый результат."""

    normalized_readme = _normalize_readme_for_checklist(readme_text)
    strong_matched_names: list[str] = []
    weak_matched_names: list[str] = []
    unmatched_names: list[str] = []
    for name in question_names:
        strength = checklist_name_match_strength(name, normalized_readme)
        if strength == "strong":
            strong_matched_names.append(name)
        elif strength == "weak":
            weak_matched_names.append(name)
        else:
            unmatched_names.append(name)
    total = len(question_names)
    matched_names = [*strong_matched_names, *weak_matched_names]
    matched = len(matched_names)
    strong_matched = len(strong_matched_names)
    weak_matched = len(weak_matched_names)
    ratio = matched / total if total else 0.0
    return ChecklistMatchResult(
        total=total,
        matched=matched,
        ratio=ratio,
        strong_matched=strong_matched,
        weak_matched=weak_matched,
        strong_ratio=strong_matched / total if total else 0.0,
        weak_ratio=weak_matched / total if total else 0.0,
        matched_names=tuple(matched_names),
        strong_matched_names=tuple(strong_matched_names),
        weak_matched_names=tuple(weak_matched_names),
        unmatched_names=tuple(unmatched_names),
    )


def checklist_name_matches_readme(name: str, normalized_readme: str) -> bool:
    """Сопоставляет техническое имя пункта с нормализованным текстом README."""

    return checklist_name_match_strength(name, normalized_readme) is not None


def checklist_name_match_strength(name: str, normalized_readme: str) -> MatchStrength | None:
    """Возвращает силу совпадения: смысловой маркер или только номер части."""

    normalized = normalize_for_match(name)
    if normalized and normalized in normalized_readme:
        return "strong"

    numbers = re.findall(r"\d+", normalized)
    tokens = [
        token
        for token in re.findall(r"[a-zа-яё0-9]+", normalized)
        if token not in CHECKLIST_STOP_TOKENS and not token.isdigit() and len(token) >= 2
    ]
    token_hits = sum(1 for token in tokens if re.search(rf"\b{re.escape(token)}\b", normalized_readme))
    number_hits = sum(1 for number in numbers if re.search(rf"\b{re.escape(number)}\b", normalized_readme))

    if not tokens:
        return "weak" if number_hits > 0 else None
    if numbers:
        if token_hits == len(tokens) and number_hits > 0:
            return "strong"
        if number_hits > 0:
            return "weak"
        return None
    return "strong" if token_hits == len(tokens) else None


def assess_checklist_description_quality(questions: list[ChecklistQuestion]) -> ChecklistDescriptionResult:
    """Считает долю пунктов с развёрнутыми описаниями, артефактами и приёмкой."""

    assessments = tuple(_assess_question_quality(question) for question in questions)
    complete_names = tuple(item.name for item in assessments if item.is_complete)
    incomplete_names = tuple(item.name for item in assessments if not item.is_complete)
    total = len(assessments)
    complete = len(complete_names)
    return ChecklistDescriptionResult(
        total=total,
        complete=complete,
        ratio=complete / total if total else 0.0,
        complete_names=complete_names,
        incomplete_names=incomplete_names,
        assessments=assessments,
    )


def _assess_question_quality(question: ChecklistQuestion) -> ChecklistQuestionQuality:
    text = question.description_text
    has_expanded_description = len(text) >= 60 or len(re.findall(r"\w+", text, flags=re.UNICODE)) >= 8
    has_acceptance_criteria = bool(ACCEPTANCE_RE.search(text))
    has_expected_artifact = bool(ARTIFACT_RE.search(text))
    has_example_or_template = bool(EXAMPLE_RE.search(text))
    supporting_signals = sum((has_acceptance_criteria, has_expected_artifact, has_example_or_template))
    return ChecklistQuestionQuality(
        name=question.name,
        has_expanded_description=has_expanded_description,
        has_acceptance_criteria=has_acceptance_criteria,
        has_expected_artifact=has_expected_artifact,
        has_example_or_template=has_example_or_template,
        is_complete=has_expanded_description and supporting_signals >= 2,
    )


def _collect_question_text(value: object) -> str:
    fragments: list[str] = []
    _collect_text_fragments(value, fragments)
    return " ".join(fragment for fragment in fragments if fragment).strip()


def _collect_text_fragments(value: object, fragments: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"name", "id", "uuid"}:
                continue
            _collect_text_fragments(item, fragments)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text_fragments(item, fragments)
        return
    if isinstance(value, str):
        fragments.append(value.strip())


def _normalize_readme_for_checklist(readme_text: str) -> str:
    """Нормализует README без markdown-якорей, чтобы они не давали ложный матч."""

    without_link_targets = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", readme_text)
    without_raw_anchors = re.sub(r"\(#[^)]+\)", " ", without_link_targets)
    return normalize_for_match(without_raw_anchors)
