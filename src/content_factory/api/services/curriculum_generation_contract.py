"""Server-side contract for generating projects from persisted curriculum plans.

The generator may still accept manually filled forms, but persisted UP flows need
stronger guarantees: readiness checks, immutable content hashes and stable row
identity. This module keeps that boundary outside the HTTP router so both
``/curriculum/build-context`` and ``/generate`` enforce the same rules.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from content_factory.api.db.session import SessionLocal
from content_factory.api.integrations.spravochnik_curriculum_sync import (
    convert_spravochnik_plan_to_generator_curriculum,
)
from content_factory.generation.models.curriculum import CurriculumPlan, CurriculumProject, ThematicBlock


class CurriculumContractError(Exception):
    """HTTP-adapter friendly error for curriculum-to-generation contract failures."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


_PLAN_HASH_KEYS = (
    "id",
    "brief_id",
    "source_policy",
    "status",
    "title",
    "audience_level",
    "total_blocks",
    "total_projects",
    "total_hours",
    "total_days",
    "total_xp",
    "profile_id",
    "direction",
    "version",
    "author_ref",
)

_ROW_HASH_KEYS = (
    "id",
    "plan_id",
    "block_index",
    "row_number",
    "project_index_in_block",
    "block_title",
    "block_goal",
    "project_name",
    "project_summary",
    "outcomes_know",
    "outcomes_can",
    "outcomes_skills",
    "learning_outcomes",
    "skills_list",
    "audience_level",
    "required_tools",
    "materials",
    "storytelling",
    "delivery_format",
    "group_size",
    "effort_hours",
    "effort_days",
    "cumulative_days",
    "xp",
    "platform_project_name",
    "artifact_links",
    "completion_percent",
    "p2p_checks",
    "weighted_skills",
    "validation_criteria",
)


def new_pipeline_run_id() -> str:
    """Create an opaque run id that can bind UP, generation and audit artifacts."""

    return f"pipe_{uuid.uuid4().hex}"


def load_persisted_curriculum_snapshot(db: Session, plan_id: int) -> dict[str, Any]:
    """Load a persisted UP and return generator payload + immutable snapshot metadata."""

    try:
        plan = (
            db.execute(text("SELECT * FROM catalog.curriculum_plan WHERE id = :id"), {"id": plan_id})
            .mappings()
            .first()
        )
        if plan is None:
            raise CurriculumContractError(404, "Учебный план не найден в общей базе")

        rows = (
            db.execute(
                text(
                    "SELECT * FROM catalog.curriculum_plan_row WHERE plan_id = :id "
                    "ORDER BY block_index, row_number, id"
                ),
                {"id": plan_id},
            )
            .mappings()
            .all()
        )
    except CurriculumContractError:
        raise
    except SQLAlchemyError as exc:
        raise CurriculumContractError(503, "Не удалось прочитать учебный план из базы") from exc

    plan_dict = dict(plan)
    row_dicts = [dict(row) for row in rows]
    payload = _assemble_plan_payload(plan_dict, row_dicts)
    curriculum = convert_spravochnik_plan_to_generator_curriculum(payload)
    plan_hash = _snapshot_hash(_snapshot_source(plan_dict, row_dicts))
    plan_version = _snapshot_version(plan_dict, plan_hash)
    readiness = build_curriculum_readiness(db, plan_dict, row_dicts, curriculum)

    snapshot = {
        "source": "catalog.curriculum_plan",
        "plan_id": int(plan_dict["id"]),
        "source_plan_id": int(plan_dict["id"]),
        "brief_id": _optional_int(plan_dict.get("brief_id")),
        "plan_version": plan_version,
        "plan_hash": plan_hash,
        "row_count": len(row_dicts),
        "status": plan_dict.get("status"),
    }
    curriculum["source_plan_id"] = int(plan_dict["id"])
    curriculum["plan_version"] = plan_version
    curriculum["plan_hash"] = plan_hash
    curriculum["readiness"] = readiness
    return {
        "plan": plan_dict,
        "rows": row_dicts,
        "payload": payload,
        "curriculum": curriculum,
        "snapshot": snapshot,
        "readiness": readiness,
    }


def build_curriculum_readiness(
    db: Session,
    plan: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    curriculum: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a blocker-oriented readiness report for generating from a UP."""

    blockers: list[dict[str, Any]] = []
    plan_id = int(plan["id"])
    brief_id = _optional_int(plan.get("brief_id"))
    status = str(plan.get("status") or "").strip().casefold()
    blocks = curriculum.get("blocks") if isinstance(curriculum, Mapping) else None
    has_blocks = isinstance(blocks, list) and any(
        isinstance(block, Mapping) and block.get("projects") for block in blocks
    )

    if status in {"invalid", "deferred"}:
        blockers.append(
            {
                "code": "plan_status_not_ready",
                "severity": "error",
                "count": 1,
                "message": f"УП имеет статус {status}; генерация разрешена только после сборки плана.",
            }
        )
    if not rows or not has_blocks:
        blockers.append(
            {
                "code": "plan_has_no_projects",
                "severity": "error",
                "count": 1,
                "message": "УП не содержит проектов для генерации.",
            }
        )

    if brief_id is not None:
        open_reviews = _safe_count(
            db,
            """
            SELECT COUNT(*)
            FROM catalog.review_queue
            WHERE status = 'open'
              AND source_ref = :source_ref
            """,
            {"source_ref": f"brief:{brief_id}"},
        )
        if open_reviews:
            blockers.append(
                {
                    "code": "open_reviews",
                    "severity": "error",
                    "count": open_reviews,
                    "message": "По брифу УП остались открытые review-блокеры.",
                }
            )

        open_templates = _safe_count(
            db,
            """
            SELECT COUNT(*)
            FROM catalog.curriculum_artifact_template_proposal
            WHERE status = 'open'
              AND (plan_id = :plan_id OR brief_id = :brief_id)
            """,
            {"plan_id": plan_id, "brief_id": brief_id},
        )
        if open_templates:
            blockers.append(
                {
                    "code": "open_template_proposals",
                    "severity": "error",
                    "count": open_templates,
                    "message": "По УП остались открытые предложения шаблонов.",
                }
            )

    return {
        "ready": not blockers,
        "blockers": blockers,
    }


def build_generation_context_from_persisted_plan(
    db: Session,
    *,
    plan_id: int,
    block_name: str,
    project_order: int,
    expected_plan_hash: str | None = None,
    pipeline_run_id: str | None = None,
) -> dict[str, Any]:
    """Build curriculum context from the current DB row and attach frozen lineage metadata."""

    snapshot = load_persisted_curriculum_snapshot(db, plan_id)
    _raise_if_not_ready(snapshot["readiness"])
    _raise_if_snapshot_changed(snapshot["snapshot"]["plan_hash"], expected_plan_hash)

    curriculum = _curriculum_model_from_payload(snapshot["curriculum"])
    context = curriculum.build_context(block_name, project_order)
    project = curriculum.get_project(block_name, project_order)
    if context is None or project is None:
        raise CurriculumContractError(404, f"Проект #{project_order} в блоке '{block_name}' не найден")

    origin = _project_origin(
        snapshot=snapshot["snapshot"],
        project=project,
        pipeline_run_id=pipeline_run_id or new_pipeline_run_id(),
    )
    context_payload = context.model_dump()
    context_payload["curriculum_origin"] = origin
    return {
        "context": context_payload,
        "origin": origin,
        "snapshot": snapshot["snapshot"],
        "readiness": snapshot["readiness"],
    }


def validate_generation_seed_curriculum_contract(seed_data: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and enrich a generation seed that originated from a persisted UP."""

    origin = _origin_from_seed(seed_data)
    if origin is None:
        return None

    plan_id = _optional_int(origin.get("plan_id") or origin.get("source_plan_id") or seed_data.get("source_plan_id"))
    if plan_id is None:
        raise CurriculumContractError(400, "В seed указан curriculum_origin без source_plan_id")

    with SessionLocal() as db:
        snapshot = load_persisted_curriculum_snapshot(db, plan_id)

    _raise_if_not_ready(snapshot["readiness"])
    expected_hash = _optional_text(origin.get("plan_hash") or seed_data.get("plan_hash"))
    _raise_if_snapshot_changed(snapshot["snapshot"]["plan_hash"], expected_hash)

    plan_row_id = _optional_int(origin.get("plan_row_id") or seed_data.get("plan_row_id"))
    if plan_row_id is not None and not any(_optional_int(row.get("id")) == plan_row_id for row in snapshot["rows"]):
        raise CurriculumContractError(409, "Строка УП для выбранного проекта больше не найдена")

    enriched_origin = {
        **origin,
        **snapshot["snapshot"],
        "pipeline_run_id": _optional_text(origin.get("pipeline_run_id") or seed_data.get("pipeline_run_id"))
        or new_pipeline_run_id(),
    }
    for key in ("plan_row_id", "project_index", "block_index", "row_number", "row_hash"):
        if origin.get(key) is not None:
            enriched_origin[key] = origin[key]

    seed_data["curriculum_origin"] = enriched_origin
    seed_data["pipeline_run_id"] = enriched_origin["pipeline_run_id"]
    seed_data["source_plan_id"] = enriched_origin["source_plan_id"]
    seed_data["plan_version"] = enriched_origin["plan_version"]
    seed_data["plan_hash"] = enriched_origin["plan_hash"]
    if enriched_origin.get("plan_row_id") is not None:
        seed_data["plan_row_id"] = enriched_origin["plan_row_id"]
    if enriched_origin.get("project_index") is not None:
        seed_data["project_index"] = enriched_origin["project_index"]

    curriculum_context = seed_data.get("curriculum_context")
    if isinstance(curriculum_context, dict):
        curriculum_context["curriculum_origin"] = enriched_origin
    return enriched_origin


def _assemble_plan_payload(plan: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    payload = dict(plan)
    payload["rows"] = [dict(row) for row in rows]
    return payload


def _curriculum_model_from_payload(data: Mapping[str, Any]) -> CurriculumPlan:
    blocks: list[ThematicBlock] = []
    for block_data in data.get("blocks", []):
        if not isinstance(block_data, Mapping):
            continue
        projects: list[CurriculumProject] = []
        for project_data in block_data.get("projects", []):
            if not isinstance(project_data, Mapping):
                continue
            payload = dict(project_data)
            payload.pop("block_name", None)
            payload.pop("block_goals", None)
            projects.append(
                CurriculumProject(
                    block_name=str(block_data.get("name") or ""),
                    block_goals=[str(item) for item in block_data.get("goals", [])],
                    **payload,
                )
            )
        blocks.append(
            ThematicBlock(
                name=str(block_data.get("name") or ""),
                code=str(block_data.get("code") or "UNK"),
                goals=[str(item) for item in block_data.get("goals", [])],
                projects=projects,
            )
        )
    return CurriculumPlan(
        direction=str(data.get("direction") or "Unknown"),
        direction_code=str(data.get("direction_code") or "UNK"),
        blocks=blocks,
    )


def _project_origin(
    *,
    snapshot: Mapping[str, Any],
    project: CurriculumProject,
    pipeline_run_id: str,
) -> dict[str, Any]:
    row_hash = _snapshot_hash(
        {
            "plan_id": snapshot.get("plan_id"),
            "plan_hash": snapshot.get("plan_hash"),
            "plan_row_id": project.plan_row_id,
            "block_index": project.block_index,
            "row_number": project.row_number,
            "project_index": project.project_index or project.order,
            "title": project.title,
            "description": project.description,
            "learning_outcomes": project.learning_outcomes,
            "skills": project.skills,
        }
    )
    return {
        **dict(snapshot),
        "pipeline_run_id": pipeline_run_id,
        "plan_row_id": project.plan_row_id,
        "block_index": project.block_index,
        "row_number": project.row_number,
        "project_index": project.project_index or project.order,
        "project_order": project.order,
        "project_title": project.title,
        "row_hash": row_hash,
    }


def _origin_from_seed(seed_data: Mapping[str, Any]) -> dict[str, Any] | None:
    origin = seed_data.get("curriculum_origin")
    if isinstance(origin, Mapping):
        return dict(origin)
    context = seed_data.get("curriculum_context")
    if isinstance(context, Mapping) and isinstance(context.get("curriculum_origin"), Mapping):
        return dict(context["curriculum_origin"])
    if seed_data.get("source_plan_id") or seed_data.get("plan_hash") or seed_data.get("plan_row_id"):
        return {
            "source_plan_id": seed_data.get("source_plan_id"),
            "plan_hash": seed_data.get("plan_hash"),
            "plan_row_id": seed_data.get("plan_row_id"),
            "project_index": seed_data.get("project_index"),
            "pipeline_run_id": seed_data.get("pipeline_run_id"),
        }
    return None


def _raise_if_not_ready(readiness: Mapping[str, Any]) -> None:
    if readiness.get("ready"):
        return
    blockers = readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else []
    raise CurriculumContractError(
        409,
        {
            "message": "УП не готов к генерации",
            "blockers": blockers,
        },
    )


def _raise_if_snapshot_changed(current_hash: str, expected_hash: str | None) -> None:
    if expected_hash and expected_hash != current_hash:
        raise CurriculumContractError(
            409,
            {
                "message": "УП изменился после подготовки контекста. Обновите УП и выберите проект заново.",
                "expected_plan_hash": expected_hash,
                "current_plan_hash": current_hash,
            },
        )


def _snapshot_source(plan: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "plan": {key: plan.get(key) for key in _PLAN_HASH_KEYS},
        "rows": [
            {key: row.get(key) for key in _ROW_HASH_KEYS}
            for row in sorted(
                rows,
                key=lambda item: (
                    _optional_int(item.get("block_index")) or 0,
                    _optional_int(item.get("row_number")) or 0,
                    _optional_int(item.get("id")) or 0,
                ),
            )
        ],
    }


def _snapshot_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _snapshot_version(plan: Mapping[str, Any], plan_hash: str) -> str:
    base_version = _optional_text(plan.get("version")) or "v1"
    return f"{base_version}:{plan_hash[:12]}"


def _safe_count(db: Session, sql: str, params: Mapping[str, Any]) -> int:
    try:
        return int(db.execute(text(sql), dict(params)).scalar() or 0)
    except SQLAlchemyError:
        # Readiness should stay compatible with older local/test schemas where a
        # particular blocker table has not been created yet.
        return 0


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str | None:
    text_value = str(value or "").strip()
    return text_value or None
