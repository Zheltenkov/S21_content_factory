"""Database maintenance helpers for runtime artifacts."""

from __future__ import annotations

from datetime import timedelta
from sqlalchemy.orm import Session

from .models import GenerationWorkflowCheckpoint, GenerationWorkflowState, PausedGenerationSession, utc_now_naive

TERMINAL_WORKFLOW_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_PAUSED_STATUSES = {"completed", "approved", "failed", "cancelled"}


def cleanup_old_runtime_state(
    db: Session,
    *,
    workflow_days_to_keep: int,
    paused_days_to_keep: int,
    batch_size: int = 500,
) -> dict[str, int]:
    """Delete terminal runtime state while preserving active/reviewable sessions."""
    deleted_workflows = _cleanup_workflow_state(
        db=db,
        days_to_keep=workflow_days_to_keep,
        batch_size=batch_size,
    )
    deleted_paused = _cleanup_paused_generation_sessions(
        db=db,
        days_to_keep=paused_days_to_keep,
        batch_size=batch_size,
    )
    return {
        "generation_workflow_states": deleted_workflows["states"],
        "generation_workflow_checkpoints": deleted_workflows["checkpoints"],
        "paused_generation_sessions": deleted_paused,
    }


def _cleanup_workflow_state(db: Session, *, days_to_keep: int, batch_size: int) -> dict[str, int]:
    if days_to_keep <= 0:
        return {"states": 0, "checkpoints": 0}

    cutoff_date = utc_now_naive() - timedelta(days=days_to_keep)
    deleted_states = 0
    deleted_checkpoints = 0

    while True:
        rows = (
            db.query(GenerationWorkflowState.id, GenerationWorkflowState.request_id)
            .filter(
                GenerationWorkflowState.status.in_(TERMINAL_WORKFLOW_STATUSES),
                GenerationWorkflowState.updated_at < cutoff_date,
            )
            .order_by(GenerationWorkflowState.id)
            .limit(batch_size)
            .all()
        )
        if not rows:
            break

        state_ids = [row_id for row_id, _ in rows]
        request_ids = [request_id for _, request_id in rows]
        deleted_checkpoints += int(
            db.query(GenerationWorkflowCheckpoint)
            .filter(GenerationWorkflowCheckpoint.request_id.in_(request_ids))
            .delete(synchronize_session=False)
            or 0
        )
        deleted_states += int(
            db.query(GenerationWorkflowState)
            .filter(GenerationWorkflowState.id.in_(state_ids))
            .delete(synchronize_session=False)
            or 0
        )
        db.commit()

        if len(rows) < batch_size:
            break

    return {"states": deleted_states, "checkpoints": deleted_checkpoints}


def _cleanup_paused_generation_sessions(db: Session, *, days_to_keep: int, batch_size: int) -> int:
    if days_to_keep <= 0:
        return 0

    cutoff_date = utc_now_naive() - timedelta(days=days_to_keep)
    total_deleted = 0

    while True:
        row_ids = [
            row_id
            for (row_id,) in (
                db.query(PausedGenerationSession.id)
                .filter(
                    PausedGenerationSession.status.in_(TERMINAL_PAUSED_STATUSES),
                    PausedGenerationSession.updated_at < cutoff_date,
                )
                .order_by(PausedGenerationSession.id)
                .limit(batch_size)
                .all()
            )
        ]
        if not row_ids:
            break

        total_deleted += int(
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.id.in_(row_ids))
            .delete(synchronize_session=False)
            or 0
        )
        db.commit()

        if len(row_ids) < batch_size:
            break

    return total_deleted


async def cleanup_old_runtime_state_async(
    *,
    workflow_days_to_keep: int,
    paused_days_to_keep: int,
    batch_size: int = 500,
) -> dict[str, int]:
    """Run runtime state cleanup in the shared DB executor."""
    import asyncio

    from .logging_db import _ensure_executor
    from .session import SessionLocal, is_database_available

    if is_database_available() is False:
        return {}

    def _cleanup_sync() -> dict[str, int]:
        db = SessionLocal()
        try:
            return cleanup_old_runtime_state(
                db,
                workflow_days_to_keep=workflow_days_to_keep,
                paused_days_to_keep=paused_days_to_keep,
                batch_size=batch_size,
            )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_ensure_executor(), _cleanup_sync)
