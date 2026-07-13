"""Summary / review builders for methodology checkpoints.

Pure dict/list builders extracted from ``checkpoint``: seed/context/task-plan summaries,
context & planning reviews, label localizers, similar-project/story-map/practice-step/
evidence summaries, and the ``_compact_*`` value shrinkers used to bound checkpoint
artifact payloads. Depends only on ``_truncate_text`` (checkpoint_text leaf) + pydantic
``BaseModel`` + stdlib, so the checkpoint policy and requirement-matrix can share them
without a cycle. All internal helpers (no external consumer); ``checkpoint`` re-imports
whichever it still calls.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .checkpoint_text import _truncate_text


def _part_summary(part: Any) -> dict[str, Any]:
    title = _get_value(part, "title") or _get_value(part, "heading") or _get_value(part, "name")
    text = (
        _get_value(part, "text")
        or _get_value(part, "content")
        or _get_value(part, "body")
        or _get_value(part, "text_markdown")
        or ""
    )
    example = _get_value(part, "example") or ""
    combined_text = " ".join([str(text or ""), str(example or "")]).strip()
    return {
        "title": str(title or "Часть теории"),
        "words": len(combined_text.split()),
    }


def _task_summary(task: Any, *, bonus: bool = False) -> dict[str, Any]:
    title = _get_value(task, "title") or _get_value(task, "name") or _get_value(task, "task_title")
    objective = _get_value(task, "objective") or _get_value(task, "goal") or _get_value(task, "description")
    normalized_title = str(title or "Практическая задача")
    if bonus and "бонус" not in normalized_title.casefold():
        normalized_title = f"Бонусное задание: {normalized_title}"
    return {
        "title": normalized_title,
        "objective": _truncate_text(str(objective or ""), 220),
    }


def _seed_title(context: dict[str, Any]) -> str:
    seed = context.get("seed")
    return str(
        context.get("title")
        or _get_value(seed, "platform_name")
        or _get_value(seed, "title_seed")
        or _get_value(seed, "project_description")
        or ""
    ).strip()


def _context_review(seed: Any, context_meta: Any, context_analysis: Any, context_bundle: Any) -> dict[str, Any]:
    title = str(_get_value(seed, "title_seed") or _get_value(seed, "platform_name") or "").strip()
    project_description = _truncate_text(str(_get_value(seed, "project_description") or ""), 420)
    storytelling = _truncate_text(str(_get_value(seed, "sjm") or ""), 420)
    thematic_block = str(_get_value(seed, "thematic_block") or _get_value(context_meta, "thematic_block") or "").strip()
    direction = str(_get_value(seed, "direction") or _get_value(context_meta, "track") or "").strip()
    audience_level = str(_get_value(seed, "audience_level") or "").strip()
    context_text = _first_non_empty(
        _get_value(context_analysis, "context_summary"),
        _get_value(context_meta, "context_summary"),
        _get_value(context_bundle, "context_summary"),
    )
    narrative_anchor = _first_non_empty(
        _get_value(context_analysis, "narrative_anchor"),
        _get_value(context_meta, "narrative_anchor"),
        _get_value(context_bundle, "narrative_anchor"),
    )
    facts = [
        {"label": "Трек", "value": direction},
        {"label": "Блок программы", "value": thematic_block},
        {"label": "Формат проекта", "value": _project_type_label(str(_get_value(seed, "project_type") or ""))},
        {"label": "Уровень аудитории", "value": _audience_level_label(audience_level)},
        {"label": "Источник контекста", "value": str(_get_value(context_bundle, "context_source") or "").strip()},
    ]
    facts = [item for item in facts if item["value"]]
    will_use = [
        "тему и описание проекта как основной фокус README",
        "сторителлинг как связку между задачами, теорией и практикой",
        "образовательные результаты и навыки как ограничения для содержания",
        "контекст программы, чтобы проект не выпадал из соседних проектов трека",
    ]
    can_change = [
        "уточнить тему, описание проекта или акцент учебного кейса",
        "переформулировать сторителлинг и роль студента",
        "добавить или убрать образовательные результаты и навыки",
        "указать, что контекст программы интерпретирован неверно",
    ]
    return {
        "project_title": title,
        "project_description": project_description,
        "storytelling": storytelling,
        "program_context": _truncate_text(str(context_text or ""), 420),
        "narrative_anchor": _truncate_text(str(narrative_anchor or ""), 260),
        "facts": facts,
        "will_use": will_use,
        "can_change": can_change,
    }


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _project_type_label(value: str) -> str:
    labels = {
        "individual": "индивидуальный",
        "group": "групповой",
        "team": "командный",
    }
    return labels.get(value.lower(), value)


def _audience_level_label(value: str) -> str:
    labels = {
        "base": "базовый",
        "basic": "базовый",
        "middle": "средний",
        "advanced": "продвинутый",
    }
    return labels.get(value.lower(), value)


def _similar_project_summaries(value: Any, *, limit: int = 5) -> list[str]:
    items = _compact_list(value, limit=limit)
    summaries: list[str] = []
    for item in items:
        if isinstance(item, dict):
            title = item.get("title") or item.get("name") or item.get("project") or item.get("value")
            if title:
                summaries.append(str(title))
                continue
        summaries.append(str(item))
    return summaries


def _planning_review(
    task_plan: Any,
    practice_plan: Any,
    artifact_chain: Any,
    evidence_specs: Any,
    story_map: Any,
) -> dict[str, Any]:
    task_count = _get_value(task_plan, "tasks_count") or _get_value(practice_plan, "task_count")
    complexity = str(_get_value(task_plan, "complexity") or "").strip()
    explanation = _first_non_empty(
        _get_value(task_plan, "explanation"),
        _get_value(task_plan, "rationale"),
        _get_value(practice_plan, "project_goal"),
    )
    resolved_story_map = story_map or _get_value(practice_plan, "story_map")
    facts = [
        {"label": "Количество задач", "value": str(task_count) if task_count else ""},
        {"label": "Сложность", "value": _complexity_label(complexity)},
        {"label": "Основание", "value": str(_get_value(task_plan, "level_source") or "").strip()},
        {"label": "Материалы", "value": str(len(_evidence_summaries(evidence_specs or _get_value(artifact_chain, "evidence_specs"))))},
    ]
    facts = [item for item in facts if item["value"]]
    return {
        "facts": facts,
        "explanation": _truncate_text(str(explanation or ""), 520),
        "story": _story_map_summary(resolved_story_map),
        "task_flow": _practice_step_summaries(practice_plan, artifact_chain, limit=8),
        "evidence": _evidence_summaries(evidence_specs or _get_value(artifact_chain, "evidence_specs"), limit=6),
        "will_use": [
            "количество задач и уровень сложности при генерации практического блока",
            "цепочку артефактов, чтобы задания зависели друг от друга, а не были разрозненными",
            "исходные материалы как raw evidence, из которых студент должен вывести решение",
            "план задач как ограничение для теоретических разделов главы 2",
        ],
        "can_change": [
            "изменить количество задач или ожидаемую сложность",
            "перестроить последовательность задач и зависимость артефактов",
            "уточнить, какие материалы нужны студенту на входе",
            "попросить усилить или ослабить связь со сторителлингом",
        ],
    }


def _complexity_label(value: str) -> str:
    labels = {
        "low": "низкая",
        "easy": "низкая",
        "medium": "средняя",
        "middle": "средняя",
        "high": "высокая",
        "advanced": "высокая",
    }
    return labels.get(value.lower(), value)


def _story_map_summary(story_map: Any) -> dict[str, str]:
    if story_map is None:
        return {}
    return {
        "role": _truncate_text(str(_get_value(story_map, "student_role") or ""), 180),
        "case": _truncate_text(str(_get_value(story_map, "working_case") or ""), 240),
        "tension": _truncate_text(str(_get_value(story_map, "central_tension") or ""), 240),
        "completion": _truncate_text(str(_get_value(story_map, "completion") or ""), 240),
    }


def _practice_step_summaries(practice_plan: Any, artifact_chain: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    practice_steps = _as_list(_get_value(practice_plan, "steps"))
    artifact_steps = _as_list(_get_value(artifact_chain, "steps"))
    rows: list[dict[str, Any]] = []
    max_len = max(len(practice_steps), len(artifact_steps))
    for index in range(min(max_len, limit)):
        practice_step = practice_steps[index] if index < len(practice_steps) else None
        artifact_step = artifact_steps[index] if index < len(artifact_steps) else None
        task_index = _get_value(practice_step, "task_index") or _get_value(artifact_step, "task_index") or index + 1
        title = _first_non_empty(
            _get_value(practice_step, "title_hint"),
            _get_value(practice_step, "title"),
            f"Задача {task_index}",
        )
        artifact = _first_non_empty(
            _get_value(practice_step, "artifact_location"),
            _get_value(artifact_step, "artifact_location"),
        )
        depends_on = _first_non_empty(
            _get_value(practice_step, "depends_on"),
            _get_value(artifact_step, "depends_on"),
        )
        rows.append(
            {
                "index": task_index,
                "title": _truncate_text(str(title), 180),
                "artifact": _truncate_text(str(artifact), 220),
                "depends_on": _truncate_text(str(depends_on), 220),
                "focus": _truncate_text(_join_first(_get_value(practice_step, "p2p_focus"), limit=2), 220),
            }
        )
    return rows


def _evidence_summaries(value: Any, *, limit: int = 6) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in _as_list(value)[:limit]:
        contains = _join_first(_get_value(item, "contains"), limit=2)
        rows.append(
            {
                "path": str(_get_value(item, "path") or _get_value(item, "name") or item or "").strip(),
                "kind": str(_get_value(item, "evidence_type") or _get_value(item, "kind") or "").strip(),
                "contains": _truncate_text(contains, 240),
            }
        )
    return [row for row in rows if row["path"]]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _join_first(value: Any, *, limit: int = 3) -> str:
    items = _as_list(value)
    if not items:
        return ""
    return "; ".join(str(item) for item in items[:limit] if item)


def _seed_summary(seed: Any) -> dict[str, Any]:
    return {
        "language": str(_get_value(seed, "language") or ""),
        "project_type": str(_get_value(seed, "project_type") or ""),
        "direction": str(_get_value(seed, "direction") or ""),
        "thematic_block": str(_get_value(seed, "thematic_block") or ""),
        "audience_level": str(_get_value(seed, "audience_level") or ""),
        "project_description": _truncate_text(str(_get_value(seed, "project_description") or ""), 320),
        "storytelling": _truncate_text(str(_get_value(seed, "sjm") or ""), 320),
    }


def _context_summary(context_meta: Any, context_analysis: Any, context_bundle: Any) -> dict[str, Any]:
    return {
        "context_meta": _compact_object(
            context_meta,
            keys=["track", "thematic_block", "last_order", "narrative_anchor", "context_summary"],
        ),
        "context_analysis": _compact_object(
            context_analysis,
            keys=["context_summary", "narrative_anchor", "audience_level_match"],
        ),
        "context_bundle": _compact_object(
            context_bundle,
            keys=["context_source", "context_summary", "narrative_anchor", "previous_projects_count"],
        ),
    }


def _task_plan_summary(task_plan: Any) -> dict[str, Any]:
    if task_plan is None:
        return {}
    return {
        "tasks_count": _get_value(task_plan, "tasks_count"),
        "complexity": _get_value(task_plan, "complexity"),
        "level_index": _get_value(task_plan, "level_index"),
        "level_source": _get_value(task_plan, "level_source"),
        "rationale": _truncate_text(str(_get_value(task_plan, "rationale") or ""), 320),
        "explanation": _truncate_text(str(_get_value(task_plan, "explanation") or ""), 420),
    }


def _contract_summary(value: Any) -> Any:
    return _compact_value(value, text_limit=260, max_items=8)


def _compact_list(value: Any, *, limit: int = 8) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str | bytes):
        return [_truncate_text(str(value), 260)]
    if not isinstance(value, list):
        value = list(value) if isinstance(value, tuple | set) else [value]
    return [_compact_value(item, text_limit=260, max_items=6) for item in value[:limit]]


def _compact_object(value: Any, *, keys: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    payload = value.model_dump(exclude_none=True, mode="json") if isinstance(value, BaseModel) else value
    if not isinstance(payload, dict):
        return {"value": _compact_value(payload)}
    return {
        key: _compact_value(payload.get(key), text_limit=260, max_items=6)
        for key in keys
        if payload.get(key) not in (None, "", [], {})
    }


def _compact_value(value: Any, *, text_limit: int = 260, max_items: int = 8) -> Any:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return _compact_value(value.model_dump(exclude_none=True, mode="json"), text_limit=text_limit, max_items=max_items)
    if isinstance(value, dict):
        items = list(value.items())[:max_items]
        compact = {
            str(key): _compact_value(item_value, text_limit=text_limit, max_items=max_items)
            for key, item_value in items
        }
        if len(value) > len(items):
            compact["_truncated_keys"] = len(value) - len(items)
        return compact
    if isinstance(value, list):
        result = [_compact_value(item, text_limit=text_limit, max_items=max_items) for item in value[:max_items]]
        if len(value) > len(result):
            result.append({"_truncated_items": len(value) - len(result)})
        return result
    if isinstance(value, tuple | set):
        return _compact_value(list(value), text_limit=text_limit, max_items=max_items)
    if isinstance(value, int | float | bool):
        return value
    return _truncate_text(str(value), text_limit)


def _assets_count(assets: Any) -> int:
    if assets is None:
        return 0
    if isinstance(assets, dict):
        return len(assets)
    if isinstance(assets, list | tuple | set):
        return len(assets)
    return 1


def _project_spec_summary(spec: Any) -> dict[str, Any]:
    if spec is None:
        return {}
    return {
        "title": str(_get_value(spec, "title") or ""),
        "theory_count": len(_get_value(spec, "theory") or []),
        "practice_count": len(_get_value(spec, "practice") or []),
        "language": str(_get_value(spec, "language") or ""),
    }


def _rubric_item_failed(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("passed") is False:
        return True
    if "score" not in item:
        return False
    try:
        return float(item.get("score") or 0) != 1.0
    except (TypeError, ValueError):
        return True


def _get_value(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)
