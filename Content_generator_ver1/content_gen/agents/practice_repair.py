"""Deterministic repair helpers for practice task generation."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable

from ..config.active_goals import has_active_goal_verb
from ..config.thresholds import CODE_EXAMPLE_CONFIG, THRESHOLDS
from ..models.schemas import ProjectSeed
from ..utils.text_analysis import count_words


StyleRewrite = Callable[[str, str], str]


def is_programming_topic(seed: ProjectSeed) -> bool:
    """Return whether practice tasks should include code subtasks."""
    if not CODE_EXAMPLE_CONFIG["enable_code_tasks_in_practice"]:
        return False

    programming_keywords = [
        "программирование",
        "разработка программного обеспечения",
        "разработка по",
        "разработка приложений",
        "изучение языка программирования",
        "изучение python",
        "изучение javascript",
        "изучение java",
        "написание кода",
        "написание программы",
        "создание программы",
        "разработка программы",
        "python",
        "javascript",
        "java",
        "c++",
        "cpp",
        "go",
        "rust",
        "sql",
        "bash",
        "алгоритм программирования",
        "функция программирования",
        "класс программирования",
        "объектно-ориентированное программирование",
        "функциональное программирование",
        "разработка приложения",
        "разработка скрипта",
        "разработка модуля",
        "разработка библиотеки",
        "api разработка",
        "backend разработка",
        "frontend разработка",
    ]
    excluded_keywords = [
        "разработка проекта",
        "разработка решения",
        "разработка подхода",
        "алгоритм работы",
        "алгоритм процесса",
        "функция системы",
        "функция процесса",
    ]

    all_text = " ".join(
        [
            " ".join(seed.skills),
            seed.project_description,
            " ".join(seed.learning_outcomes),
        ]
    ).lower()
    if any(excluded in all_text for excluded in excluded_keywords):
        return False

    skills_text = " ".join(seed.skills).lower()
    desc_text = seed.project_description.lower()
    lo_text = " ".join(seed.learning_outcomes).lower()
    return (
        any(keyword in skills_text for keyword in programming_keywords)
        or any(keyword in desc_text for keyword in programming_keywords)
        or any(keyword in lo_text for keyword in programming_keywords)
    )


def summarize_approach_to_limit(bullets: list[str], language: str) -> list[str]:
    """Trim approach bullets to the configured word budget."""
    text = " ".join(bullets).strip()
    if count_words(text, language) <= THRESHOLDS["approach_words_max"]:
        return bullets

    new_bullets: list[str] = []
    total = 0
    for bullet in bullets:
        sentences = re.split(r"(?<=[\.!\?])\s+", bullet.strip())
        short = " ".join(sentences[:2]).strip()
        words = count_words(short, language)
        if total + words <= THRESHOLDS["approach_words_max"]:
            new_bullets.append(short)
            total += words
        else:
            break
    return new_bullets or [
        "Сформулируй рациональный план действий, опираясь на рамки проекта и доступные инструменты."
    ]


def force_active_goal(goal: str) -> str:
    """Convert vague Russian goal wording into an explicit action."""
    normalized = re.sub(r"\s+", " ", (goal or "").strip())
    if not normalized:
        return normalized

    leading_replacements = [
        (r"^формировать\b", "Сформировать"),
        (r"^сформировывать\b", "Сформировать"),
        (r"^собирать\b", "Сформировать"),
        (r"^работать\s+с\b", "Настроить работу с"),
        (r"^оформлять\b", "Подготовить"),
        (r"^делать\b", "Выполнить"),
    ]
    for pattern, replacement in leading_replacements:
        if re.search(pattern, normalized, flags=re.I):
            return re.sub(pattern, replacement, normalized, count=1, flags=re.I)

    lowered_first = normalized[0].lower() + normalized[1:] if normalized else normalized
    return f"Сформировать {lowered_first}"


def fix_goal_active_form(goal: str, language: str) -> str:
    """Normalize goal wording to an active, checkable action."""
    if language != "ru":
        return goal
    original_goal = (goal or "").strip()
    if not original_goal:
        return goal

    forbidden_verbs = [
        r"\bизуч(ить|и|ать|ение)\b",
        r"\bознаком(иться|ься|ление)\b",
        r"\bпосмотр(еть|и|ать)\b",
        r"\bрассмотр(еть|и|ать|ение)\b",
        r"\bпознаком(иться|ься|ление)\b",
        r"\bузна(ть|ть|вать)\b",
        r"\bпоня(ть|ть|вать|тие)\b",
        r"\bизучен(ие|ия)\b",
        r"\bознакомлен(ие|ия)\b",
    ]
    replacements = {
        r"\bизуч(ить|и|ать|ение)\b": "проанализировать",
        r"\bознаком(иться|ься|ление)\b": "описать",
        r"\bпосмотр(еть|и|ать)\b": "проанализировать",
        r"\bрассмотр(еть|и|ать|ение)\b": "проанализировать",
        r"\bпознаком(иться|ься|ление)\b": "описать",
        r"\bузна(ть|ть|вать)\b": "определить",
        r"\bпоня(ть|ть|вать|тие)\b": "проанализировать",
        r"\bизучен(ие|ия)\b": "анализ",
        r"\bознакомлен(ие|ия)\b": "описание",
    }

    fixed_goal = original_goal
    has_forbidden = any(re.search(verb, original_goal.lower()) for verb in forbidden_verbs)
    if has_forbidden:
        for pattern, replacement in replacements.items():
            fixed_goal = re.sub(pattern, replacement, fixed_goal, flags=re.I)

    if not has_active_goal_verb(fixed_goal):
        fixed_goal = force_active_goal(fixed_goal)

    if fixed_goal != original_goal:
        print(
            f"  ⚠️ Исправлена цель (активная форма): '{original_goal}' -> '{fixed_goal}'",
            file=sys.stderr,
            flush=True,
        )

    return fixed_goal


def fix_task_situation(
    situation: str,
    input_data: str,
    goal: str,
    language: str,
    *,
    style_rewrite: StyleRewrite,
) -> str:
    """Normalize or synthesize the public task situation block."""
    normalized = (situation or "").strip()
    if normalized:
        if language == "ru" and not normalized.endswith((".", "!", "?")):
            normalized += "."
        return style_rewrite(normalized, language)

    input_short = re.sub(r"`[^`]+`", "", input_data or "").strip()
    input_short = re.sub(r"\s+", " ", input_short).strip(" .")
    goal_short = re.sub(r"\s+", " ", goal or "").strip(" .")
    if language == "ru":
        generated = (
            f"У тебя на руках {input_short or 'рабочие материалы по проекту'}. "
            "По ним нужно принять понятное решение для команды и оформить результат так, "
            "чтобы другой участник смог его проверить. "
            f"Фокус задачи: {goal_short or 'подготовить проверяемый артефакт'}."
        )
    else:
        generated = (
            "You have project materials on hand. Use them to make a concrete working decision "
            "and produce an artifact another learner can review."
        )
    return style_rewrite(generated, language)


def fix_task_risk(
    risk_text: str,
    situation: str,
    goal: str,
    language: str,
    *,
    style_rewrite: StyleRewrite,
) -> str:
    """Normalize or synthesize the public task risk block."""
    normalized = re.sub(r"\s+", " ", (risk_text or "").strip())
    if normalized:
        if language == "ru" and not normalized.endswith((".", "!", "?")):
            normalized += "."
        return style_rewrite(normalized, language)

    situation_low = (situation or "").lower()
    if any(marker in situation_low for marker in ("срок", "дедлайн", "время", "тайм", "срочно")):
        generated = "Есть ограничение по сроку: решение нужно оформить быстро и без лишних итераций."
    elif any(marker in situation_low for marker in ("безопас", "риск", "ошиб", "конфликт", "неяс")):
        generated = "Главный риск — принять решение без достаточной ясности и получить проблемный результат на проверке."
    else:
        generated = (
            f"Важно явно зафиксировать ограничения и критерии выбора, "
            f"чтобы результат по задаче «{goal or 'проекта'}» можно было проверить."
        )
    return style_rewrite(generated, language)


def extract_theory_topics(theory_summary: str) -> list[str]:
    """Extract theory topic names from a numbered summary."""
    topics: list[str] = []
    for line in (theory_summary or "").splitlines():
        match = re.match(r"^\s*\d+\.\s+(.+?)\s*$", line.strip())
        if match:
            topics.append(match.group(1).strip())
    return topics


def token_set(text: str) -> set[str]:
    """Return normalized content tokens for deterministic overlap checks."""
    stop_words = {
        "это",
        "для",
        "как",
        "что",
        "или",
        "при",
        "над",
        "под",
        "про",
        "без",
        "его",
        "ее",
        "её",
        "они",
        "она",
        "оно",
        "так",
        "уже",
        "ещё",
        "этот",
        "эта",
        "эти",
        "тот",
        "который",
        "которая",
        "которые",
        "если",
        "только",
        "будет",
        "задача",
        "проект",
        "нужно",
        "нужен",
        "нужна",
        "нужны",
        "можно",
        "нельзя",
        "важно",
    }
    tokens = re.findall(r"[А-Яа-яЁёA-Za-z0-9]+", (text or "").lower())
    return {token for token in tokens if len(token) > 3 and token not in stop_words}


def infer_covered_outcomes(seed: ProjectSeed, *task_texts: str) -> list[str]:
    """Bind a task to 1-2 learning outcomes by token overlap."""
    blob_tokens: set[str] = set()
    for text in task_texts:
        blob_tokens |= token_set(text)
    matches: list[tuple[int, str]] = []
    for learning_outcome in seed.learning_outcomes or []:
        score = len(blob_tokens & token_set(learning_outcome))
        if score > 0:
            matches.append((score, learning_outcome))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [learning_outcome for _, learning_outcome in matches[:2]]


def infer_theory_support(theory_summary: str, *task_texts: str) -> list[str]:
    """Bind a task to theory topics by token overlap."""
    blob_tokens: set[str] = set()
    for text in task_texts:
        blob_tokens |= token_set(text)
    matches: list[tuple[int, str]] = []
    for topic in extract_theory_topics(theory_summary):
        score = len(blob_tokens & token_set(topic))
        if score > 0:
            matches.append((score, topic))
    matches.sort(key=lambda item: item[0], reverse=True)
    return [topic for _, topic in matches[:2]]
