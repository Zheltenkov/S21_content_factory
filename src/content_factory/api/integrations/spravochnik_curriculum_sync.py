"""Convert Spravochnik curriculum plans to the generator contract (Postgres-native catalog).

The former SQLite→Postgres mirror is gone: curriculum plans are authored directly in the
Postgres ``catalog`` schema, so ``sync_spravochnik_curriculum_plans`` is now a no-op.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from content_factory.generation.models.curriculum import CurriculumPlan, CurriculumProject, ThematicBlock

_DIRECTION_NAMES = {
    "BSA": "Бизнес аналитика",
    "Cb": "Кибербезопасность",
    "DO": "DevOps",
    "PjM": "Проектный менеджмент",
    "QA": "Тестирование и обеспечение качества",
    "DS": "Машинное обучение",
    "UNK": "Учебный план",
}


def _as_text(value: object) -> str:
    """Normalize optional DB values to stripped text."""

    if value is None:
        return ""
    return str(value).strip()


def _jsonish_items(value: object) -> list[object]:
    """Return list-like values from raw SQLite payload fields."""

    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _split_multivalue(value: object) -> list[str]:
    """Split newline/comma/semicolon separated fields while preserving order."""

    items = _jsonish_items(value)
    if items:
        values: list[str] = []
        for item in items:
            if isinstance(item, Mapping):
                text = _as_text(item.get("name") or item.get("title") or item.get("skill") or item.get("label"))
                if text:
                    values.append(text)
                continue
            values.extend(_split_multivalue(item))
        return list(dict.fromkeys(values))

    text = _as_text(value)
    if not text:
        return []

    # UP fields from Spravochnik use either multiline bullets or comma-separated skills/tools.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\n" in normalized:
        raw_parts = normalized.split("\n")
    else:
        raw_parts = re.split(r"[;,]", normalized)

    parts = []
    for raw_part in raw_parts:
        part = raw_part.strip().strip("-•*").strip()
        if part:
            parts.append(part.rstrip("."))
    return list(dict.fromkeys(parts))


def _parse_int(value: object) -> int | None:
    """Parse an integer from direct values and ranges like 3-4."""

    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = _as_text(value)
    if not text:
        return None
    numbers = [int(item) for item in re.findall(r"\d+", text)]
    if not numbers:
        return None
    return max(numbers)


def _parse_float(value: object) -> float | None:
    """Parse localized numeric values from Spravochnik rows."""

    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    text = _as_text(value).replace(",", ".")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


def _detect_direction_code(plan_payload: Mapping[str, object]) -> str:
    """Infer the generator direction code from platform names and plan metadata."""

    candidates: list[str] = []
    for block in _iter_plan_blocks(plan_payload):
        for row in _iter_block_rows(block):
            candidates.append(_as_text(row.get("platform_project_name") or row.get("platform_name")))
            candidates.append(_as_text(row.get("project_name")))
    candidates.extend(
        _as_text(plan_payload.get(key))
        for key in ("title", "brief_role", "brief_domain")
    )

    joined = " ".join(candidates).casefold()
    if "pjm" in joined or "проект" in joined or "менедж" in joined:
        return "PjM"
    if "bsa" in joined or "бизнес" in joined or "аналит" in joined:
        return "BSA"
    if "ds" in joined or "data" in joined or "машин" in joined or "ml" in joined:
        return "DS"
    if "devops" in joined or "do" in joined:
        return "DO"
    if "qa" in joined or "тест" in joined or "качест" in joined:
        return "QA"
    if "cb" in joined or "кибер" in joined or "безопас" in joined:
        return "Cb"
    return "UNK"


def _delivery_format(value: object) -> str:
    """Map Spravochnik delivery format to the generator enum."""

    text = _as_text(value).casefold()
    if "group" in text or "груп" in text or "команд" in text:
        return "group"
    return "individual"


def _iter_plan_blocks(plan_payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return block payloads, rebuilding them from rows if only rows are stored."""

    blocks = plan_payload.get("blocks")
    if isinstance(blocks, list):
        return [block for block in blocks if isinstance(block, Mapping)]

    rows = plan_payload.get("rows")
    if not isinstance(rows, list):
        return []

    grouped: dict[int, list[Mapping[str, object]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        grouped.setdefault(_parse_int(row.get("block_index")) or 0, []).append(row)

    rebuilt_blocks: list[Mapping[str, object]] = []
    for block_index in sorted(grouped):
        block_rows = grouped[block_index]
        first_row = block_rows[0]
        rebuilt_blocks.append(
            {
                "block_index": block_index,
                "title": first_row.get("block_title") or f"Блок {block_index or 1}",
                "goal": first_row.get("block_goal") or "",
                "rows": block_rows,
            }
        )
    return rebuilt_blocks


def _iter_block_rows(block: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return normalized row mappings from one Spravochnik UP block."""

    rows = block.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, Mapping)]
    return []


def _row_learning_outcomes(row: Mapping[str, object]) -> list[str]:
    """Merge all outcome fields from a Spravochnik UP row."""

    outcomes: list[str] = []
    for key in ("learning_outcomes", "outcomes_know", "outcomes_can", "outcomes_skills"):
        outcomes.extend(_split_multivalue(row.get(key)))
    return list(dict.fromkeys(outcomes))


def _row_skills(row: Mapping[str, object]) -> list[str]:
    """Merge canonical and weighted skill fields from a Spravochnik UP row."""

    skills = _split_multivalue(row.get("skills_list"))
    weighted_skills = row.get("weighted_skills")
    if isinstance(weighted_skills, list):
        for skill in weighted_skills:
            if isinstance(skill, Mapping):
                skill_name = _as_text(skill.get("name") or skill.get("skill") or skill.get("title"))
                if skill_name:
                    skills.append(skill_name)
            else:
                skills.extend(_split_multivalue(skill))
    return list(dict.fromkeys(skills))


def _build_project(block_name: str, block_goals: list[str], row: Mapping[str, object], fallback_order: int) -> CurriculumProject | None:
    """Build one generator project from a Spravochnik UP row."""

    title = _as_text(row.get("project_name") or row.get("title"))
    if not title:
        return None

    # ``fallback_order`` is the 1-based position within the block's already-ordered
    # rows, so it is unique per project. It is preferred over the raw
    # ``project_index_in_block`` (which is 0-based — index 0 was previously coerced
    # away by ``or``, colliding two projects onto the same order and breaking the
    # generator's block→project dropdown selection).
    order = fallback_order
    completion = row.get("completion_percent")
    completion_value = _parse_float(completion)
    completion_text = f"{completion_value:g}%" if completion_value is not None else _as_text(completion) or None

    return CurriculumProject(
        block_name=block_name,
        block_goals=block_goals,
        order=order,
        plan_row_id=_parse_int(row.get("id")),
        block_index=_parse_int(row.get("block_index")),
        row_number=_parse_int(row.get("row_number")),
        project_index=_parse_int(row.get("project_index_in_block")) or order,
        title=title,
        description=_as_text(row.get("project_summary") or row.get("description")),
        learning_outcomes=_row_learning_outcomes(row),
        skills=_row_skills(row),
        audience_level=_as_text(row.get("audience_level")) or None,
        required_tools=_split_multivalue(row.get("required_tools")),
        format=_delivery_format(row.get("delivery_format")),
        group_size=_parse_int(row.get("group_size")),
        required_software=_as_text(row.get("required_software")) or None,
        workload_hours=_parse_float(row.get("effort_hours")),
        workload_days=_parse_float(row.get("effort_days")),
        total_workload_days=_parse_float(row.get("cumulative_days")),
        xp=_parse_int(row.get("xp")),
        passing_threshold=completion_text,
        storytelling_type="sjm" if _as_text(row.get("storytelling")) else None,
        sjm=_as_text(row.get("storytelling")) or None,
        expert_notes=_as_text(row.get("validation_criteria")) or None,
        additional_materials=_as_text(row.get("materials")) or None,
        platform_name=_as_text(row.get("platform_project_name")) or title,
        gitlab_link=_as_text(row.get("artifact_links")) or None,
    )


def convert_spravochnik_plan_to_generator_curriculum(plan_payload: Mapping[str, object]) -> dict[str, Any]:
    """Convert a Spravochnik UP payload into the generator curriculum contract."""

    direction_code = _detect_direction_code(plan_payload)
    blocks: list[ThematicBlock] = []
    for block_index, block in enumerate(_iter_plan_blocks(plan_payload), start=1):
        block_name = _as_text(block.get("title") or block.get("block_title") or f"Блок {block_index}")
        block_goals = _split_multivalue(block.get("goal") or block.get("block_goal"))
        projects: list[CurriculumProject] = []
        for fallback_order, row in enumerate(_iter_block_rows(block), start=1):
            project = _build_project(block_name, block_goals, row, fallback_order)
            if project:
                projects.append(project)
        blocks.append(
            ThematicBlock(
                name=block_name,
                code=direction_code,
                goals=block_goals,
                projects=projects,
            )
        )

    plan = CurriculumPlan(
        direction=_as_text(plan_payload.get("title")) or _DIRECTION_NAMES.get(direction_code, "Учебный план"),
        direction_code=direction_code,
        blocks=blocks,
    )
    frontend_payload = plan.to_dict_for_frontend()
    frontend_payload["source_plan_id"] = plan_payload.get("id") or plan_payload.get("plan_id")
    return frontend_payload


# --------------------------------------------------------------------------- #
# Curriculum plans now live natively in Postgres (``catalog.curriculum_plan(+_row)``).
# The intake pipeline writes them directly, so the former SQLite→PG mirror is obsolete.
# --------------------------------------------------------------------------- #


def sync_spravochnik_curriculum_plans(
    db: Session | None = None,
    sqlite_path: Path | None = None,
    *,
    limit: int = 500,
) -> dict[str, object]:
    """No-op: the catalog is Postgres-native, so no SQLite→PG mirror step is needed.

    Historically this copied the SQLite catalog's curriculum plans into
    ``catalog.curriculum_plan(+_row)``. After the full-PG cutover the intake pipeline writes
    those tables directly and the generator reads them in place — nothing to sync. Kept as a
    stable no-op so the existing curriculum router endpoints keep their response shape.
    """
    return {"source": "catalog.curriculum_plan", "seen": 0, "synced": 0, "archived": 0, "native": True}
