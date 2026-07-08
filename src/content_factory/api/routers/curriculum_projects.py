"""Operational API for generating projects from persisted curriculum plans."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from content_factory.api.db.curriculum_project_runs_db import list_curriculum_project_runs_for_plan
from content_factory.api.db.session import get_db_session
from content_factory.api.dependencies import get_current_user
from content_factory.api.integrations.spravochnik_curriculum_sync import sync_spravochnik_curriculum_plans
from content_factory.api.services.curriculum_generation_contract import (
    CurriculumContractError,
    load_persisted_curriculum_snapshot,
)

router = APIRouter(prefix="/curriculum-projects", tags=["curriculum-projects"])

ACTIVE_RUN_STATUSES = {"pending", "in_progress", "needs_review", "resuming"}


@router.get("/plans", response_model=dict[str, Any])
async def list_learning_project_plans(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return persisted UP plans available for the Учебные проекты cockpit."""

    sync_result = sync_spravochnik_curriculum_plans(db)
    plans = (
        db.execute(text("SELECT * FROM catalog.curriculum_plan ORDER BY updated_at DESC, id DESC"))
        .mappings()
        .all()
    )
    return {
        "user_id": user.get("id"),
        "plans": [_plan_summary(dict(plan)) for plan in plans],
        "sync": sync_result,
    }


@router.get("/plans/{source_id}", response_model=dict[str, Any])
async def get_learning_project_plan(
    source_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Return one UP with generation-status overlay for every project row."""

    sync_spravochnik_curriculum_plans(db)
    try:
        plan_id = int(source_id)
    except (TypeError, ValueError):
        raise HTTPException(404, "Учебный план не найден в общей базе") from None

    try:
        snapshot = load_persisted_curriculum_snapshot(db, plan_id)
    except CurriculumContractError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc

    user_id = str(user.get("id") or "")
    runs = list_curriculum_project_runs_for_plan(source_plan_id=plan_id, user_id=user_id or None)
    run_history = _runs_by_project(runs)
    projects = _project_items(
        curriculum=snapshot["curriculum"],
        plan_snapshot=snapshot["snapshot"],
        readiness=snapshot["readiness"],
        run_history=run_history,
    )
    status_counts = Counter(str(project["generation_status"]) for project in projects)
    return {
        "user_id": user.get("id"),
        "plan": _plan_summary(snapshot["plan"]),
        "snapshot": snapshot["snapshot"],
        "readiness": snapshot["readiness"],
        "projects": projects,
        "stats": {
            "total_projects": len(projects),
            "ready": bool(snapshot["readiness"].get("ready")),
            "generated": status_counts.get("completed", 0),
            "in_progress": sum(status_counts.get(status, 0) for status in ACTIVE_RUN_STATUSES),
            "needs_review": status_counts.get("needs_review", 0),
            "failed": status_counts.get("failed", 0),
            "cancelled": status_counts.get("cancelled", 0),
            "not_started": status_counts.get("not_started", 0),
            "generation_runs": len(runs),
        },
    }


def _plan_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    plan_id = int(plan["id"])
    return {
        "id": plan_id,
        "source_id": str(plan_id),
        "title": plan.get("title") or plan.get("direction") or f"УП #{plan_id}",
        "status": plan.get("status"),
        "direction": plan.get("direction") or None,
        "blocks": int(plan.get("total_blocks") or 0),
        "projects": int(plan.get("total_projects") or 0),
        "updated_at": plan.get("updated_at"),
        "source_updated_at": plan.get("updated_at"),
    }


def _project_items(
    *,
    curriculum: Mapping[str, Any],
    plan_snapshot: Mapping[str, Any],
    readiness: Mapping[str, Any],
    run_history: Mapping[tuple[Any, ...], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    ready = bool(readiness.get("ready"))
    blockers = readiness.get("blockers") if isinstance(readiness.get("blockers"), list) else []
    items: list[dict[str, Any]] = []
    blocks = curriculum.get("blocks")
    if not isinstance(blocks, list):
        return items
    for block_position, block in enumerate(blocks, start=1):
        if not isinstance(block, Mapping):
            continue
        block_name = str(block.get("name") or "")
        block_index = _optional_int(block.get("block_index")) or block_position
        projects = block.get("projects")
        if not isinstance(projects, list):
            continue
        for project_position, project in enumerate(projects, start=1):
            if not isinstance(project, Mapping):
                continue
            identity = _project_identity(
                project=project,
                plan_snapshot=plan_snapshot,
                block_index=block_index,
                project_position=project_position,
            )
            project_run_history = run_history.get(_identity_key(identity), [])
            latest_run = project_run_history[0] if project_run_history else None
            generation_status = str(latest_run.get("status") if latest_run else "not_started")
            active = generation_status in ACTIVE_RUN_STATUSES
            project_order = _optional_int(project.get("order")) or project_position
            items.append(
                {
                    "identity": identity,
                    "block_name": block_name,
                    "block_index": identity.get("block_index"),
                    "row_number": identity.get("row_number"),
                    "project_order": project_order,
                    "project_index": identity.get("project_index"),
                    "title": project.get("title") or project.get("platform_name") or f"Проект {project_order}",
                    "platform_name": project.get("platform_name"),
                    "description": project.get("description"),
                    "learning_outcomes": project.get("learning_outcomes") or [],
                    "skills": project.get("skills") or [],
                    "generation_status": generation_status,
                    "generation": latest_run,
                    "generation_history": project_run_history[:5],
                    "generation_runs_count": len(project_run_history),
                    "can_generate": ready and not active,
                    "blockers": blockers if not ready else [],
                }
            )
    return items


def _project_identity(
    *,
    project: Mapping[str, Any],
    plan_snapshot: Mapping[str, Any],
    block_index: int,
    project_position: int,
) -> dict[str, Any]:
    project_order = _optional_int(project.get("order")) or project_position
    return {
        "source_plan_id": int(plan_snapshot["source_plan_id"]),
        "plan_version": plan_snapshot.get("plan_version"),
        "plan_hash": plan_snapshot.get("plan_hash"),
        "plan_row_id": _optional_int(project.get("plan_row_id")),
        "row_hash": project.get("row_hash"),
        "block_index": _optional_int(project.get("block_index")) or block_index,
        "row_number": _optional_int(project.get("row_number")),
        "project_index": _optional_int(project.get("project_index")) or project_order,
        "project_order": project_order,
        "project_title": project.get("title") or project.get("platform_name"),
    }


def _runs_by_project(runs: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    history: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for run in runs:
        key = _identity_key(run)
        history.setdefault(key, []).append(run)
    return history


def _identity_key(value: Mapping[str, Any]) -> tuple[Any, ...]:
    plan_row_id = _optional_int(value.get("plan_row_id"))
    if plan_row_id is not None:
        return ("row", plan_row_id)
    return (
        "position",
        _optional_int(value.get("source_plan_id")),
        _optional_int(value.get("block_index")),
        _optional_int(value.get("project_index") or value.get("project_order")),
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
