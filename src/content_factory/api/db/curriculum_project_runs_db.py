"""Persistence helpers for the Учебные проекты operational layer."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from content_factory.api.utils.logger import get_logger

from .models import CurriculumProjectGenerationRun, CurriculumProjectSnapshot, utc_now_naive
from .session import SessionLocal

logger = get_logger("db.curriculum_project_runs")

TERMINAL_CURRICULUM_PROJECT_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


def record_curriculum_project_snapshot(
    *,
    user_id: str | None,
    context_payload: Mapping[str, Any] | None = None,
    seed_payload: Mapping[str, Any] | None = None,
    readiness: Mapping[str, Any] | None = None,
    db: Session | None = None,
) -> dict[str, Any] | None:
    """Persist a frozen project snapshot for a persisted UP selection.

    The canonical UP remains unchanged. This row captures the exact plan hash,
    row identity and generation context used by the runtime workflow.
    """

    origin = _origin_from_payload(context_payload=context_payload, seed_payload=seed_payload)
    if origin is None:
        return None

    owns_session = db is None
    session = db or SessionLocal()
    try:
        row = _ensure_snapshot_row(
            session=session,
            user_id=user_id,
            origin=origin,
            context_payload=context_payload,
            seed_payload=seed_payload,
            readiness=readiness,
        )
        session.commit()
        session.refresh(row)
        return row.to_dict()
    except Exception:
        session.rollback()
        logger.exception("Failed to persist curriculum project snapshot")
        raise
    finally:
        if owns_session:
            session.close()


def record_curriculum_project_generation_run(
    *,
    request_id: str,
    user_id: str | None,
    seed_payload: Mapping[str, Any],
    status: str = "pending",
    stage: str | None = "queued",
) -> dict[str, Any] | None:
    """Create or update the operational run row before background generation starts."""

    origin = _origin_from_payload(seed_payload=seed_payload)
    if origin is None:
        return None

    session = SessionLocal()
    try:
        snapshot = _ensure_snapshot_row(
            session=session,
            user_id=user_id,
            origin=origin,
            context_payload=_mapping_or_none(seed_payload.get("curriculum_context")),
            seed_payload=seed_payload,
            readiness=None,
        )
        run = _find_run(session, request_id=request_id, pipeline_run_id=str(origin["pipeline_run_id"]))
        if run is None:
            run = CurriculumProjectGenerationRun(
                pipeline_run_id=str(origin["pipeline_run_id"]),
                created_at=utc_now_naive(),
            )
            session.add(run)

        _apply_run_identity(run, origin)
        run.snapshot_id = snapshot.snapshot_id
        run.request_id = request_id
        run.user_id = user_id
        run.status = status
        run.stage = stage
        run.result_url = run.result_url or f"/api/v1/download/{request_id}"
        run.updated_at = utc_now_naive()
        session.commit()
        session.refresh(run)
        return run.to_dict()
    except Exception:
        session.rollback()
        logger.exception("Failed to persist curriculum project generation run %s", request_id)
        raise
    finally:
        session.close()


def mark_curriculum_project_generation_run(
    *,
    request_id: str | None = None,
    pipeline_run_id: str | None = None,
    status: str,
    stage: str | None = None,
    error: str | None = None,
    result_url: str | None = None,
    score: Mapping[str, Any] | None = None,
    review: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update the operational run status without touching canonical UP data."""

    if not request_id and not pipeline_run_id:
        return None

    session = SessionLocal()
    try:
        run = _find_run(session, request_id=request_id, pipeline_run_id=pipeline_run_id)
        if run is None:
            return None
        run.status = status
        if stage is not None:
            run.stage = stage
        if error is not None:
            run.error = error
        if result_url is not None:
            run.result_url = result_url
        if score is not None:
            run.score_data = dict(score)
        if review is not None:
            run.review_data = dict(review)
        if metadata is not None:
            current_meta = run.meta_data if isinstance(run.meta_data, dict) else {}
            run.meta_data = {**current_meta, **dict(metadata)}
        run.updated_at = utc_now_naive()
        if status in TERMINAL_CURRICULUM_PROJECT_STATUSES:
            run.completed_at = run.completed_at or utc_now_naive()
        session.commit()
        session.refresh(run)
        return run.to_dict()
    except Exception:
        session.rollback()
        logger.exception(
            "Failed to update curriculum project generation run request_id=%s pipeline_run_id=%s",
            request_id,
            pipeline_run_id,
        )
        raise
    finally:
        session.close()


def list_curriculum_project_runs_for_plan(
    *,
    source_plan_id: int,
    user_id: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return recent operational run rows for one UP plan."""

    safe_limit = max(1, min(limit, 2000))
    session = SessionLocal()
    try:
        query = session.query(CurriculumProjectGenerationRun).filter(
            CurriculumProjectGenerationRun.source_plan_id == source_plan_id
        )
        if user_id:
            query = query.filter(CurriculumProjectGenerationRun.user_id == user_id)
        rows = (
            query.order_by(
                CurriculumProjectGenerationRun.updated_at.desc(),
                CurriculumProjectGenerationRun.created_at.desc(),
            )
            .limit(safe_limit)
            .all()
        )
        return [row.to_dict() for row in rows]
    finally:
        session.close()


def _ensure_snapshot_row(
    *,
    session: Session,
    user_id: str | None,
    origin: Mapping[str, Any],
    context_payload: Mapping[str, Any] | None,
    seed_payload: Mapping[str, Any] | None,
    readiness: Mapping[str, Any] | None,
) -> CurriculumProjectSnapshot:
    pipeline_run_id = str(origin["pipeline_run_id"])
    row = (
        session.query(CurriculumProjectSnapshot)
        .filter(CurriculumProjectSnapshot.pipeline_run_id == pipeline_run_id)
        .first()
    )
    if row is None:
        row = CurriculumProjectSnapshot(
            snapshot_id=f"upsnap_{uuid.uuid4().hex}",
            pipeline_run_id=pipeline_run_id,
            created_by=user_id,
            created_at=utc_now_naive(),
        )
        session.add(row)

    _apply_snapshot_identity(row, origin)
    if context_payload is not None and row.context_data is None:
        row.context_data = dict(context_payload)
    if seed_payload is not None:
        row.seed_data = dict(seed_payload)
    if readiness is not None:
        row.readiness_data = dict(readiness)
    row.updated_at = utc_now_naive()
    return row


def _find_run(
    session: Session,
    *,
    request_id: str | None,
    pipeline_run_id: str | None,
) -> CurriculumProjectGenerationRun | None:
    filters = []
    if request_id:
        filters.append(CurriculumProjectGenerationRun.request_id == request_id)
    if pipeline_run_id:
        filters.append(CurriculumProjectGenerationRun.pipeline_run_id == pipeline_run_id)
    if not filters:
        return None
    return session.query(CurriculumProjectGenerationRun).filter(or_(*filters)).first()


def _apply_snapshot_identity(row: CurriculumProjectSnapshot, origin: Mapping[str, Any]) -> None:
    row.source_plan_id = int(origin["source_plan_id"])
    row.plan_version = str(origin["plan_version"])
    row.plan_hash = str(origin["plan_hash"])
    row.plan_row_id = _optional_int(origin.get("plan_row_id"))
    row.row_hash = _optional_text(origin.get("row_hash"))
    row.block_index = _optional_int(origin.get("block_index"))
    row.row_number = _optional_int(origin.get("row_number"))
    row.project_index = _optional_int(origin.get("project_index"))
    row.project_order = _optional_int(origin.get("project_order"))
    row.project_title = _optional_text(origin.get("project_title"))


def _apply_run_identity(row: CurriculumProjectGenerationRun, origin: Mapping[str, Any]) -> None:
    row.source_plan_id = int(origin["source_plan_id"])
    row.plan_version = str(origin["plan_version"])
    row.plan_hash = str(origin["plan_hash"])
    row.plan_row_id = _optional_int(origin.get("plan_row_id"))
    row.row_hash = _optional_text(origin.get("row_hash"))
    row.block_index = _optional_int(origin.get("block_index"))
    row.row_number = _optional_int(origin.get("row_number"))
    row.project_index = _optional_int(origin.get("project_index"))
    row.project_order = _optional_int(origin.get("project_order"))
    row.project_title = _optional_text(origin.get("project_title"))


def _origin_from_payload(
    *,
    context_payload: Mapping[str, Any] | None = None,
    seed_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    seed = seed_payload or {}
    context = context_payload or _mapping_or_none(seed.get("curriculum_context")) or {}
    origin = _mapping_or_none(seed.get("curriculum_origin")) or _mapping_or_none(context.get("curriculum_origin"))
    if origin is None and (seed.get("source_plan_id") or seed.get("plan_hash")):
        origin = {
            "source_plan_id": seed.get("source_plan_id"),
            "plan_hash": seed.get("plan_hash"),
            "plan_version": seed.get("plan_version"),
            "pipeline_run_id": seed.get("pipeline_run_id"),
            "plan_row_id": seed.get("plan_row_id"),
            "project_index": seed.get("project_index"),
        }
    if origin is None:
        return None

    normalized = dict(origin)
    source_plan_id = _optional_int(normalized.get("source_plan_id") or normalized.get("plan_id"))
    plan_hash = _optional_text(normalized.get("plan_hash"))
    plan_version = _optional_text(normalized.get("plan_version"))
    pipeline_run_id = _optional_text(normalized.get("pipeline_run_id") or seed.get("pipeline_run_id"))
    if source_plan_id is None or not plan_hash or not plan_version or not pipeline_run_id:
        return None
    normalized["source_plan_id"] = source_plan_id
    normalized["plan_version"] = plan_version
    normalized["plan_hash"] = plan_hash
    normalized["pipeline_run_id"] = pipeline_run_id
    return normalized


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


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
