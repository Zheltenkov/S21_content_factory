"""Persistence helpers for durable generation workflow state."""

from __future__ import annotations

from typing import Any

from api.utils.logger import get_logger
from content_gen.utils.rubric_export import convert_numpy_types

from .models import GenerationWorkflowCheckpoint, GenerationWorkflowState, utc_now_naive
from .paused_generation_codec import serialize_context
from .session import SessionLocal

logger = get_logger("db.generation_workflow")

ACTIVE_WORKFLOW_STATUSES: tuple[str, ...] = ("running", "node_completed", "resuming")


def create_generation_workflow(
    *,
    request_id: str,
    user_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create or reset the durable workflow root row for a generation request."""
    if not request_id:
        return None
    db = SessionLocal()
    try:
        row = db.query(GenerationWorkflowState).filter(GenerationWorkflowState.request_id == request_id).first()
        if row is None:
            row = GenerationWorkflowState(
                request_id=request_id,
                user_id=user_id,
                status="created",
                created_at=utc_now_naive(),
            )
            db.add(row)
        row.user_id = user_id or row.user_id
        row.status = "created"
        row.current_node = None
        row.last_completed_node = None
        row.resume_from_node = None
        row.progress_current = 0
        row.progress_total = 0
        row.error = None
        row.meta_data = convert_numpy_types(metadata or {})
        row.commands = []
        row.updated_at = utc_now_naive()
        db.commit()
        db.refresh(row)
        return row.to_dict()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to create workflow state %s: %s", request_id, exc)
        return None
    finally:
        db.close()


def transition_generation_workflow(
    *,
    request_id: str,
    status: str,
    user_id: str | None = None,
    current_node: str | None = None,
    last_completed_node: str | None = None,
    resume_from_node: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    error: str | None = None,
    metadata: dict[str, Any] | None = None,
    command: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Persist a state transition without blocking core generation if DB fails."""
    if not request_id:
        return None
    db = SessionLocal()
    try:
        row = db.query(GenerationWorkflowState).filter(GenerationWorkflowState.request_id == request_id).first()
        if row is None:
            row = GenerationWorkflowState(request_id=request_id, user_id=user_id, created_at=utc_now_naive())
            db.add(row)
        if user_id:
            row.user_id = user_id
        row.status = status
        if current_node is not None or status in {
            "node_completed",
            "completed",
            "failed",
            "cancelled",
            "needs_review",
            "resuming",
            "interrupted",
        }:
            row.current_node = current_node
        if last_completed_node is not None:
            row.last_completed_node = last_completed_node
        if resume_from_node is not None or status != "needs_review":
            row.resume_from_node = resume_from_node
        if progress_current is not None:
            row.progress_current = max(0, int(progress_current))
        if progress_total is not None:
            row.progress_total = max(0, int(progress_total))
        if error is not None or status in {"running", "node_completed", "completed", "resuming"}:
            row.error = error
        if metadata is not None:
            existing = row.meta_data if isinstance(row.meta_data, dict) else {}
            row.meta_data = {**existing, **convert_numpy_types(metadata)}
        if command is not None:
            commands = list(row.commands or [])
            commands.append(convert_numpy_types(command))
            row.commands = commands
        row.updated_at = utc_now_naive()
        db.commit()
        db.refresh(row)
        return row.to_dict()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to transition workflow %s to %s: %s", request_id, status, exc)
        return None
    finally:
        db.close()


def record_generation_workflow_checkpoint(
    *,
    request_id: str,
    user_id: str | None,
    node_id: str,
    node_name: str,
    status: str,
    input_hash: str,
    output_artifact: dict[str, Any] | None = None,
    context_snapshot: dict[str, Any] | None = None,
    validation_result: dict[str, Any] | None = None,
    retry_count: int = 0,
    duration_ms: float | None = None,
    checkpoint_index: int | None = None,
) -> dict[str, Any] | None:
    """Create or update one durable node checkpoint."""
    if not request_id or not node_id:
        return None
    db = SessionLocal()
    try:
        safe_index = checkpoint_index
        if safe_index is None:
            safe_index = db.query(GenerationWorkflowCheckpoint).filter(
                GenerationWorkflowCheckpoint.request_id == request_id
            ).count() + 1
        row = (
            db.query(GenerationWorkflowCheckpoint)
            .filter(
                GenerationWorkflowCheckpoint.request_id == request_id,
                GenerationWorkflowCheckpoint.checkpoint_index == safe_index,
            )
            .first()
        )
        if row is None:
            row = GenerationWorkflowCheckpoint(
                request_id=request_id,
                checkpoint_index=int(safe_index),
                created_at=utc_now_naive(),
            )
            db.add(row)
            existing_retry_count = 0
            is_existing_attempt = False
        else:
            existing_retry_count = int(getattr(row, "retry_count", 0) or 0)
            is_existing_attempt = bool(row.node_id)
        row.user_id = user_id or row.user_id
        row.node_id = node_id
        row.node_name = node_name
        row.status = status
        row.input_hash = input_hash
        row.output_artifact = convert_numpy_types(output_artifact or {})
        row.context_snapshot = convert_numpy_types(
            serialize_context(context_snapshot)
            if isinstance(context_snapshot, dict)
            else {}
        )
        row.validation_result = convert_numpy_types(validation_result or {})
        incoming_retry_count = max(0, int(retry_count or 0))
        row.retry_count = (
            max(incoming_retry_count, existing_retry_count + 1)
            if is_existing_attempt
            else incoming_retry_count
        )
        row.duration_ms = duration_ms
        db.commit()
        db.refresh(row)
        return row.to_dict()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to persist workflow checkpoint %s/%s: %s", request_id, node_id, exc)
        return None
    finally:
        db.close()


def get_generation_workflow(request_id: str, *, include_checkpoints: bool = True) -> dict[str, Any] | None:
    """Load a workflow state with optional checkpoint list."""
    if not request_id:
        return None
    db = SessionLocal()
    try:
        row = db.query(GenerationWorkflowState).filter(GenerationWorkflowState.request_id == request_id).first()
        if row is None:
            return None
        payload = row.to_dict()
        if include_checkpoints:
            checkpoints = (
                db.query(GenerationWorkflowCheckpoint)
                .filter(GenerationWorkflowCheckpoint.request_id == request_id)
                .order_by(GenerationWorkflowCheckpoint.checkpoint_index.asc())
                .all()
            )
            payload["checkpoints"] = [checkpoint.to_dict() for checkpoint in checkpoints]
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load workflow %s: %s", request_id, exc)
        return None
    finally:
        db.close()


def mark_interrupted_generation_workflows(
    *,
    error: str = "Процесс генерации был остановлен при перезапуске сервера. Запуск можно восстановить из checkpoint-ов.",
    statuses: tuple[str, ...] = ACTIVE_WORKFLOW_STATUSES,
    batch_size: int = 500,
) -> list[dict[str, Any]]:
    """Mark workflows left active by a dead process as explicitly recoverable."""
    db = SessionLocal()
    try:
        rows = (
            db.query(GenerationWorkflowState)
            .filter(GenerationWorkflowState.status.in_(list(statuses)))
            .order_by(GenerationWorkflowState.updated_at.asc())
            .limit(max(1, int(batch_size)))
            .all()
        )
        interrupted_at = utc_now_naive()
        snapshots: list[dict[str, Any]] = []
        for row in rows:
            previous_status = row.status
            metadata = row.meta_data if isinstance(row.meta_data, dict) else {}
            row.status = "interrupted"
            row.current_node = None
            row.error = error
            row.meta_data = {
                **metadata,
                "interrupted_from_status": previous_status,
                "interrupted_at": interrupted_at.isoformat(),
            }
            row.updated_at = interrupted_at
            snapshots.append(row.to_dict())
        db.commit()
        return snapshots
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to mark interrupted workflows: %s", exc)
        return []
    finally:
        db.close()
