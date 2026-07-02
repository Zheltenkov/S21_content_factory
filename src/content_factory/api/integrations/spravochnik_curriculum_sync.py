"""Mirror Spravochnik curriculum plans into the shared generator catalog."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy.orm import Session

from content_factory.api.db.models import SpravochnikCatalogEntity, utc_now_naive
from content_factory.api.db.session import SessionLocal
from content_factory.api.integrations.project_paths import ensure_import_path, spravochnik_root, spravochnik_sqlite_path
from content_factory.generation.models.curriculum import CurriculumPlan, CurriculumProject, ThematicBlock

logger = logging.getLogger("content_factory.api.integrations.spravochnik_curriculum_sync")

CURRICULUM_PLAN_ENTITY_TYPE = "curriculum_plan"
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

    order = _parse_int(row.get("project_index_in_block")) or _parse_int(row.get("row_number")) or fallback_order
    completion = row.get("completion_percent")
    completion_value = _parse_float(completion)
    completion_text = f"{completion_value:g}%" if completion_value is not None else _as_text(completion) or None

    return CurriculumProject(
        block_name=block_name,
        block_goals=block_goals,
        order=order,
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


def extract_generator_curriculum(entity_payload: Mapping[str, object]) -> dict[str, Any] | None:
    """Extract or rebuild the generator curriculum payload stored in the common DB."""

    current_payload = entity_payload.get("generator_curriculum")
    if isinstance(current_payload, Mapping):
        return dict(current_payload)

    spravochnik_payload = entity_payload.get("spravochnik_payload")
    if isinstance(spravochnik_payload, Mapping):
        return convert_spravochnik_plan_to_generator_curriculum(spravochnik_payload)

    if "blocks" in entity_payload and isinstance(entity_payload.get("blocks"), list):
        return dict(entity_payload)
    return None


def _parse_source_datetime(value: object) -> datetime | None:
    """Parse SQLite timestamp strings for source_updated_at."""

    text = _as_text(value)
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _json_safe(value: object) -> object:
    """Recursively convert mapping/sequence payloads into JSON-safe primitives."""

    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _upsert_curriculum_entity(db: Session, plan_payload: Mapping[str, object]) -> bool:
    """Upsert one curriculum plan mirror row."""

    source_id = _as_text(plan_payload.get("id") or plan_payload.get("plan_id"))
    if not source_id:
        return False

    generator_curriculum = convert_spravochnik_plan_to_generator_curriculum(plan_payload)
    mirror_payload = {
        "source": "spravochnik_sqlite",
        "source_plan_id": source_id,
        "summary": plan_payload.get("summary") if isinstance(plan_payload.get("summary"), Mapping) else {},
        "spravochnik_payload": _json_safe(plan_payload),
        "generator_curriculum": generator_curriculum,
    }
    now = utc_now_naive()
    entity = (
        db.query(SpravochnikCatalogEntity)
        .filter(
            SpravochnikCatalogEntity.entity_type == CURRICULUM_PLAN_ENTITY_TYPE,
            SpravochnikCatalogEntity.source_id == source_id,
        )
        .first()
    )
    if entity is None:
        db.add(
            SpravochnikCatalogEntity(
                entity_type=CURRICULUM_PLAN_ENTITY_TYPE,
                source_id=source_id,
                title=_as_text(plan_payload.get("title")) or None,
                status=_as_text(plan_payload.get("status")) or "draft",
                payload=cast(dict[str, Any], mirror_payload),
                source_updated_at=_parse_source_datetime(plan_payload.get("updated_at")),
                updated_at=now,
            )
        )
        return True

    entity.title = _as_text(plan_payload.get("title")) or None
    entity.status = _as_text(plan_payload.get("status")) or "draft"
    entity.payload = cast(dict[str, Any], mirror_payload)
    entity.source_updated_at = _parse_source_datetime(plan_payload.get("updated_at"))
    entity.updated_at = now
    return True


def _load_spravochnik_plan_payloads(sqlite_path: Path, limit: int) -> list[Mapping[str, object]]:
    """Load full UP payloads from the Spravochnik runtime SQLite database."""

    ensure_import_path(spravochnik_root())
    from viewer.app import ensure_intake_runtime_schema, get_curriculum_plan, list_curriculum_plans

    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        ensure_intake_runtime_schema(conn, sqlite_path)
        plan_refs = list_curriculum_plans(conn, limit=limit)
        payloads: list[Mapping[str, object]] = []
        for plan_ref in plan_refs:
            plan_id = _parse_int(plan_ref.get("id"))
            if plan_id is None:
                continue
            plan_payload = get_curriculum_plan(conn, plan_id)
            if plan_payload:
                payloads.append(plan_payload)
        return payloads


def _archive_stale_curriculum_entities(db: Session, active_source_ids: Iterable[str]) -> int:
    """Mark deleted Spravochnik plans as archived in the shared mirror."""

    active = set(active_source_ids)
    stale_entities = (
        db.query(SpravochnikCatalogEntity)
        .filter(SpravochnikCatalogEntity.entity_type == CURRICULUM_PLAN_ENTITY_TYPE)
        .all()
    )
    archived = 0
    for entity in stale_entities:
        if entity.source_id in active or entity.status == "archived":
            continue
        entity.status = "archived"
        entity.updated_at = utc_now_naive()
        archived += 1
    return archived


def sync_spravochnik_curriculum_plans(
    db: Session | None = None,
    sqlite_path: Path | None = None,
    *,
    limit: int = 500,
) -> dict[str, object]:
    """Synchronize Spravochnik UP rows to the common catalog database."""

    if db is None:
        with SessionLocal() as session:
            return sync_spravochnik_curriculum_plans(session, sqlite_path, limit=limit)

    source_path = sqlite_path or spravochnik_sqlite_path()
    result: dict[str, object] = {
        "source": str(source_path),
        "source_exists": source_path.exists(),
        "seen": 0,
        "synced": 0,
        "archived": 0,
    }
    if not source_path.exists():
        return result

    try:
        payloads = _load_spravochnik_plan_payloads(source_path, limit)
    except Exception as exc:
        logger.exception("Failed to load Spravochnik curriculum plans from %s", source_path)
        result["error"] = str(exc)
        return result

    active_source_ids: list[str] = []
    synced = 0
    for plan_payload in payloads:
        source_id = _as_text(plan_payload.get("id") or plan_payload.get("plan_id"))
        if source_id:
            active_source_ids.append(source_id)
        if _upsert_curriculum_entity(db, plan_payload):
            synced += 1

    result["seen"] = len(payloads)
    result["synced"] = synced
    result["archived"] = _archive_stale_curriculum_entities(db, active_source_ids)
    db.commit()
    return result
