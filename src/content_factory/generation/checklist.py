"""YAML checklist generation from the final README contract."""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .agents.practice_parsing import extract_inline_from_public, parse_p2p_criteria
from .models.readme_document import ReadmeDocument, ReadmeSection
from .models.schemas import PracticeTask


class ChecklistQuestion(BaseModel):
    """One peer-review checklist question."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    rating: str = "6"


class ChecklistSection(BaseModel):
    """Top-level checklist section compatible with existing examples."""

    model_config = ConfigDict(extra="forbid")

    kind: str = "1"
    name: str = "Основная часть"
    description: str = ""
    questions: list[ChecklistQuestion] = Field(default_factory=list)


class ProjectChecklist(BaseModel):
    """Full check-list.yml payload."""

    model_config = ConfigDict(extra="forbid")

    introduction: str
    quick_actions: list[str] = Field(default_factory=lambda: ["EMPTY_WORK", "CHEAT"])
    language: str = "ru"
    guidelines: str
    name: str = ""
    comment: str = ""
    sections: list[ChecklistSection] = Field(default_factory=list)

    def to_yaml(self) -> str:
        """Serialize with multiline strings close to the repository examples."""
        payload = _literalize_multiline_strings(self.model_dump(mode="json"))
        return yaml.safe_dump(
            payload,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            width=120,
        )


class _LiteralString(str):
    """Marker for PyYAML block scalar serialization."""


def _literal_string_representer(dumper: yaml.SafeDumper, data: _LiteralString) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.SafeDumper.add_representer(_LiteralString, _literal_string_representer)


def build_project_checklist(
    *,
    project_title: str,
    language: str,
    readme_document: ReadmeDocument,
    practice_tasks: list[PracticeTask] | None = None,
) -> ProjectChecklist:
    """Build a peer-review checklist from the final README and parsed practice tasks."""
    tasks = _collect_checklist_tasks(readme_document, practice_tasks or [])
    questions = [
        ChecklistQuestion(
            name=_question_name(index, task["title"]),
            description=_question_description(task),
            rating="6",
        )
        for index, task in enumerate(tasks, 1)
    ]
    if not questions:
        questions = [
            ChecklistQuestion(
                name="Проверка итогового README",
                description=(
                    "- README открывается и содержит аннотацию, теорию, практику и заключение.\n"
                    "- Практические задания содержат проверяемые ожидаемые результаты.\n"
                    "- Итоговый проект можно проверить без устных пояснений автора."
                ),
                rating="6",
            )
        ]

    normalized_language = (language or "ru").strip().lower() or "ru"
    return ProjectChecklist(
        introduction=_checklist_introduction(normalized_language),
        language=normalized_language,
        guidelines=_checklist_guidelines(normalized_language),
        name=_safe_project_name(project_title),
        sections=[ChecklistSection(questions=questions)],
    )


def build_project_checklist_yaml(
    *,
    project_title: str,
    language: str,
    readme_document: ReadmeDocument,
    practice_tasks: list[PracticeTask] | None = None,
) -> str:
    """Build serialized check-list.yml content from the final README."""
    return build_project_checklist(
        project_title=project_title,
        language=language,
        readme_document=readme_document,
        practice_tasks=practice_tasks,
    ).to_yaml()


def _collect_checklist_tasks(
    readme_document: ReadmeDocument,
    practice_tasks: list[PracticeTask],
) -> list[dict[str, Any]]:
    typed_by_index = {index: task for index, task in enumerate(practice_tasks, 1)}
    tasks = [
        _task_payload_from_section(index, section, typed_by_index.get(index))
        for index, section in enumerate(_practice_sections(readme_document), 1)
    ]
    if tasks:
        return tasks
    return [
        _task_payload_from_practice_task(index, task)
        for index, task in enumerate(practice_tasks, 1)
    ]


def _practice_sections(readme_document: ReadmeDocument) -> list[ReadmeSection]:
    sections: list[ReadmeSection] = []
    for section in readme_document.sections:
        for item in section.flatten():
            kind = str((item.metadata or {}).get("section_kind") or "")
            if kind == "practice_task" or re.match(r"^(?:задание|задача|task)\s+\d+", item.title, flags=re.I):
                sections.append(item)
    return sections


def _task_payload_from_section(
    index: int,
    section: ReadmeSection,
    fallback: PracticeTask | None,
) -> dict[str, Any]:
    markdown = section.to_markdown()
    action_block = section.label_block("Что нужно сделать")
    result_block = section.label_block("Что должно получиться")
    submit_block = section.label_block("Формат сдачи")
    criteria = _clean_checklist_items(parse_p2p_criteria(markdown))
    if not criteria:
        criteria = _clean_checklist_items(_bullet_items(result_block))
    if not criteria and fallback is not None:
        criteria = _clean_checklist_items(fallback.p2p_criteria)

    artifact_paths = list((section.metadata or {}).get("artifact_paths") or [])
    fallback_location = getattr(fallback, "artifact_location", "") if fallback is not None else ""
    return {
        "index": index,
        "title": section.title,
        "goal": extract_inline_from_public(action_block, "Цель") or getattr(fallback, "goal", ""),
        "expected_artifact": getattr(fallback, "expected_artifact", "") if fallback is not None else "",
        "artifact_location": artifact_paths[0] if artifact_paths else fallback_location,
        "criteria": criteria,
        "submit": submit_block,
    }


def _task_payload_from_practice_task(index: int, task: PracticeTask) -> dict[str, Any]:
    return {
        "index": index,
        "title": task.title,
        "goal": task.goal,
        "expected_artifact": task.expected_artifact,
        "artifact_location": task.artifact_location,
        "criteria": _clean_checklist_items(task.p2p_criteria),
        "submit": "",
    }


def _question_name(index: int, title: str) -> str:
    title = re.sub(r"\s+", " ", (title or "").strip())
    if re.match(r"^(?:Задание|Задача|Task)\s+\d+", title, flags=re.I):
        return title
    return f"Задание {index}. {title or 'Проверяемый результат'}"


def _question_description(task: dict[str, Any]) -> str:
    lines: list[str] = []
    goal = str(task.get("goal") or "").strip()
    expected = str(task.get("expected_artifact") or "").strip()
    location = str(task.get("artifact_location") or "").strip()
    criteria = [str(item).strip() for item in task.get("criteria") or [] if str(item).strip()]
    submit = str(task.get("submit") or "").strip()

    if goal:
        lines.append(f"- Цель результата понятна: {goal}")
    if expected:
        lines.append(f"- Итоговый артефакт соответствует формулировке: {expected}")
    if location:
        lines.append(f"- Артефакт размещен или доступен по указанному в README пути `{location}`.")
    if criteria:
        lines.extend(f"- {item}" for item in criteria)
    if submit and not location:
        lines.append(f"- Формат сдачи соблюден: {submit}")
    lines.append("- Пир может объяснить ключевые решения и показать, где они отражены в артефакте.")
    return "\n".join(dict.fromkeys(lines))


def _bullet_items(markdown: str) -> list[str]:
    items: list[str] = []
    for raw_line in (markdown or "").splitlines():
        line = raw_line.strip()
        line = re.sub(r"^[-*]\s*\[[ xX]?\]\s*", "", line)
        line = re.sub(r"^[-*]\s*", "", line)
        if line:
            items.append(line)
    return items


def _clean_checklist_items(items: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        text = re.sub(r"\s+", " ", str(item or "").strip(" -\t"))
        if not text or text in {"…", "..."} or len(text) < 6:
            continue
        key = text.casefold()
        if key not in seen:
            cleaned.append(text)
            seen.add(key)
    return cleaned[:12]


def _literalize_multiline_strings(value: Any) -> Any:
    if isinstance(value, str):
        return _LiteralString(value) if "\n" in value else value
    if isinstance(value, list):
        return [_literalize_multiline_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _literalize_multiline_strings(item) for key, item in value.items()}
    return value


def _safe_project_name(project_title: str) -> str:
    return re.sub(r"\s+", " ", (project_title or "GeneratedProject").strip())[:120]


def _checklist_introduction(language: str) -> str:
    if language == "en":
        return (
            "Project review is part of learning. Use the checklist to verify the submitted artifacts, "
            "ask the peer to explain their decisions, and discuss 2-3 improvement ideas after the formal checks."
        )
    return (
        "Проверка проекта — это часть обучения. Используй чек-лист, чтобы проверить артефакты из README, "
        "попросить пира объяснить принятые решения и после формальной проверки обсудить 2–3 идеи улучшения."
    )


def _checklist_guidelines(language: str) -> str:
    if language == "en":
        return (
            "- Evaluate only the artifacts requested in the final README.\n"
            "- Make sure the reviewed work belongs to the participant or team being reviewed.\n"
            "- If the work is incomplete, still use the checklist to identify what is missing.\n"
            "- Use EMPTY_WORK for an empty or unavailable submission and CHEAT for signs of non-independent work."
        )
    return (
        "- Оценивай только те артефакты, которые указаны в финальном README.\n"
        "- Убедись, что проверяемая работа принадлежит участнику или команде, которых ты проверяешь.\n"
        "- Если работа не завершена, всё равно пройди по чек-листу и зафиксируй, чего не хватает.\n"
        "- Используй EMPTY_WORK для пустой или недоступной сдачи и CHEAT при признаках несамостоятельного выполнения."
    )
