"""Валидатор практических задач."""

import re
from dataclasses import dataclass

from ..config.banned_phrases import BAD_GOAL_PATTERNS
from ..config.thresholds import THRESHOLDS
from ..didactics.patterns import PRACTICE_TASK_TITLE_PATTERN_STRICT
from ..models.readme_document import ReadmeDocument, ReadmeSection
from ..utils.text_analysis import count_words


@dataclass
class Issue:
    """Проблема валидации."""

    path: str
    level: str
    message: str


class PracticeValidator:
    """Валидатор Главы 3 (Практика)."""

    def __init__(self):
        pass

    def validate_markdown(self, md: str, language: str, tasks_count_expected: int | None) -> list[Issue]:
        """Validate Chapter 3 from a Markdown boundary payload."""
        return self.validate_document(
            ReadmeDocument.from_markdown(md),
            language=language,
            tasks_count_expected=tasks_count_expected,
        )

    def validate_document(
        self,
        document: ReadmeDocument,
        *,
        language: str,
        tasks_count_expected: int | None,
    ) -> list[Issue]:
        """Validate Chapter 3 against the typed README document tree."""
        issues: list[Issue] = []
        task_sections = self._practice_task_sections(document, language=language)

        n_tasks = len(task_sections)
        lo_all, hi_all = THRESHOLDS["practice_tasks_range"]
        if tasks_count_expected is not None and n_tasks != tasks_count_expected:
            issues.append(
                Issue(
                    "practice.tasks",
                    "warn",
                    f"Сгенерировано {n_tasks} практических заданий, ожидалось {tasks_count_expected}.",
                )
            )
        if not (lo_all <= n_tasks <= hi_all):
            issues.append(
                Issue(
                    "practice.tasks",
                    "error",
                    f"Количество практических заданий {n_tasks} вне допустимого диапазона {lo_all}–{hi_all}.",
                )
            )

        for i, section in enumerate(task_sections):
            canonical = (
                section.has_label("Что нужно сделать")
                and section.has_label("Что должно получиться")
                and section.has_label("Формат сдачи")
            )
            if not canonical:
                for label in ["Что нужно сделать", "Что должно получиться", "Формат сдачи"]:
                    if not section.has_label(label):
                        issues.append(Issue(f"practice.tasks[{i}].{label}", "error", f"Отсутствует блок «{label}»."))

            action_block = section.label_block("Что нужно сделать")
            m_ap = _extract_colon_field(action_block, "Подход")
            if m_ap:
                # Используем универсальную функцию подсчета слов
                words = count_words(m_ap, language)
                if words > THRESHOLDS["approach_words_max"]:
                    issues.append(
                        Issue(
                            f"practice.tasks[{i}].approach_bullets",
                            "error",
                            f"«Подход» содержит {words} слов (> {THRESHOLDS['approach_words_max']}).",
                        )
                    )

            goal = _extract_colon_field(action_block, "Цель")
            if goal:
                for pat in BAD_GOAL_PATTERNS.get(language, []):
                    if re.search(pat, goal, flags=re.I):
                        issues.append(
                            Issue(
                                f"practice.tasks[{i}].goal",
                                "error",
                                "Цель сформулирована как «изучить/ознакомиться/посмотреть» — нужно действие+результат.",
                            )
                        )

            result = section.label_block("Что должно получиться")
            if result:
                if "где найти" not in result.lower() and "repo/" not in result and "/" not in result:
                    issues.append(
                        Issue(
                            f"practice.tasks[{i}].expected_artifact",
                            "warn",
                            "Укажи, где найти артефакт (скобками или repo/… путь).",
                        )
                    )
        return issues

    @staticmethod
    def _practice_task_sections(document: ReadmeDocument, *, language: str) -> list[ReadmeSection]:
        """Return typed practice task sections from Chapter 3 or partial H3 snippets."""
        title_re = re.compile(PRACTICE_TASK_TITLE_PATTERN_STRICT.replace(r"^###\s+", r"^"), flags=re.I)
        chapter = document.chapter_section(3, language=language)
        source = chapter.children if chapter is not None else document.sections
        return [section for section in source if section.level == 3 and title_re.search(section.title)]


def _has_label(text: str, label: str) -> bool:
    return bool(re.search(rf"\*\*{re.escape(label)}:?\*\*", text, flags=re.I))


def _extract_label_block(text: str, label: str) -> str:
    match = re.search(rf"\*\*{re.escape(label)}:?\*\*\s*(.+?)(?=\n\*\*|\n###|\Z)", text, flags=re.S | re.I)
    return match.group(1).strip() if match else ""


def _extract_goal_from_canonical(text: str) -> str:
    return _extract_canonical_action_field(text, "Цель")


def _extract_canonical_action_field(text: str, label: str) -> str:
    action = _extract_label_block(text, "Что нужно сделать")
    return _extract_colon_field(action, label)


def _extract_colon_field(text: str, label: str) -> str:
    """Extract a colon-prefixed field from a canonical action block."""
    labels = ["Ситуация", "Исходные данные", "Цель", "Подход"]
    target = label.casefold()
    capturing = False
    collected: list[str] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        current_label = next(
            (item for item in labels if stripped.casefold().startswith(f"{item.casefold()}:")),
            "",
        )
        if current_label:
            if capturing:
                break
            if current_label.casefold() == target:
                capturing = True
                remainder = stripped.split(":", 1)[1].strip()
                if remainder:
                    collected.append(remainder)
            continue
        if capturing:
            collected.append(line)
    return "\n".join(collected).strip()
