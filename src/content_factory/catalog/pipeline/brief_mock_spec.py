"""Offline (mock) brief -> spec synthesis for stage_brief_to_catalog.

Pure text heuristics used when ``config.USE_LIVE`` is off: detect a program brief,
extract neutral topic candidates, build placeholder skill names, and assemble a
normalized spec from a free-form brief without any LLM call. Extracted from
``stage_brief_to_catalog`` as a leaf (imports only sibling leaves + stdlib); the stage
module re-imports the helpers it still calls (``_mock_spec_from_brief`` in ``decompose``,
``_topic_to_mock_skill_name``/``_short_topic_label`` in offline synthesis).
"""

from __future__ import annotations

import re
from typing import Any

from .brief_bloom_workload import _extract_workload_from_text, _normalized_spec
from .skill_names import canonicalize_skill_name, has_observable_action

_BRIEF_SECTION_LABEL_RE = re.compile(
    r"^(наименование|идея|целевая аудитория|участники|результат|цель|задача|описание|требования|контекст)\s*[:\-]\s*",
    re.IGNORECASE,
)


def _is_program_brief_text(brief: str) -> bool:
    source = brief.casefold().replace("ё", "е")
    return bool(re.search(r"\b(программа|курс|обучени|учебн|ветк|паспорт|тз)\b", source))


def _brief_sentence_candidates(brief: str) -> list[str]:
    """Extract neutral topic candidates from a free-form brief for offline mode."""
    # An explicit graduate-outcome section takes precedence over the plan writer's instructions.
    topics: list[str] = []
    outcome_depth = 0
    for line in brief.splitlines():
        heading = re.match(r"^\s*(#{1,6})\s+(.+)", line)
        if heading:
            if outcome_depth and len(heading[1]) <= outcome_depth:
                outcome_depth = 0
            if re.search(r"результат\w*.*(?:обучени|выпускник)|образовательн\w* результат|learning outcomes", heading[2], re.I):
                outcome_depth = len(heading[1])
        elif outcome_depth and (bullet := re.match(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)", line)):
            topics.append(re.split(r"\.\s", bullet[1], maxsplit=1)[0].strip(" ."))
    if topics:
        return list(dict.fromkeys(topics))[:12]
    candidates: list[str] = []
    for chunk in re.split(r"[\n.;•\u2022]+", brief):
        if chunk.lstrip().startswith("#"):
            continue
        text = _BRIEF_SECTION_LABEL_RE.sub("", chunk).strip(" \t:-")
        text = re.sub(r"\s+", " ", text)
        if len(text) < 12 or len(text) > 180:
            continue
        if re.search(r"\b(телефон|email|http|www)\b", text.casefold()):
            continue
        candidates.append(text)
    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        norm = item.casefold().replace("ё", "е")
        if norm in seen:
            continue
        seen.add(norm)
        unique.append(item)
    return unique[:12]


def _short_topic_label(text: str, *, max_words: int = 8, max_chars: int = 90) -> str:
    label = re.sub(r"\s+", " ", str(text or "")).strip(" .,-:;")
    words = label.split()
    if len(words) > max_words:
        label = " ".join(words[:max_words])
    if len(label) > max_chars:
        label = label[:max_chars].rstrip(" .,-:;") + "..."
    return label or "общая тема"


def _topic_to_mock_skill_name(topic: str) -> str:
    """Build an offline skill placeholder from source text without domain-specific templates."""
    cleaned = _BRIEF_SECTION_LABEL_RE.sub("", str(topic or "")).strip(" .,-:;")
    canonical = canonicalize_skill_name(cleaned)
    if has_observable_action(canonical):
        return canonical
    return f"Работа с темой «{_short_topic_label(canonical)}»"


def _extract_mock_role(brief: str, *, is_program: bool) -> str:
    for pattern in (
        r"(?:роль(?: выпускника)?|профиль(?: выпускника)?|выпускник|специалист)\s*[:—\-]\s*([^.\n;]{3,90})",
        r"(?:подготовить|обучить|готовим|готовить)\s+((?:специалист|участник|сотрудник|студент|слушател|выпускник)\w*[^.\n;,:]{0,65})",
    ):
        match = re.search(pattern, brief, flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split()).strip(" .,-")
    program = re.search(r"\bпрограмм[ауые]\s+«([^»\n]{2,120})»", brief, re.I)
    if is_program and program:
        return f"Выпускник программы «{program[1]}»"
    return "Выпускник программы" if is_program else "Специалист"


def _extract_mock_domain(brief: str, areas: list[str]) -> str:
    explicit = re.search(r"(?:\b(?:домен|направление)\s*[:—-]\s*|выбирающ\w+ направление\s+)([^.\n;]{2,120})", brief, re.I)
    if explicit:
        return explicit[1].strip()
    if areas:
        return areas[0][:120]
    first_line = next((line.strip() for line in brief.splitlines() if line.strip()), "")
    return first_line[:120] or "Домен из брифа"


def _mock_spec_from_brief(brief: str) -> dict[str, Any]:
    is_program = _is_program_brief_text(brief)
    areas = _brief_sentence_candidates(brief)
    if not areas:
        areas = ["Ключевые задачи и навыки из брифа"]
    goal = re.search(r"(?:цель программы|результат для заказчика)\s*[:—-]\s*([^.\n]+)", brief, re.I)
    raw = {
        "artifact_type": "program_brief" if is_program else "learner_brief",
        "role": _extract_mock_role(brief, is_program=is_program),
        "seniority": "не указан",
        "domain": _extract_mock_domain(brief, areas),
        "operator_role": None,
        "program_goal": goal[1].strip() if goal else areas[0] if is_program else "",
        "must_include_areas": areas[:12],
        "sub_queries": [f"Навыки выпускника: {area}" for area in areas[:6]],
    }
    spec = _normalized_spec(raw)
    spec.update({key: value for key, value in _extract_workload_from_text(brief).items() if value is not None})
    return spec
