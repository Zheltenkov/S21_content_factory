"""Persistence helpers for the user-scoped recent runs dashboard."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from api.utils.logger import get_logger

from .models import UserRun, utc_now_naive
from .session import SessionLocal

logger = get_logger("db.user_runs")

ACTIVE_RUN_STATUSES = {"pending", "in_progress", "needs_review", "resuming"}
STALE_RECONCILABLE_STATUSES = {"pending", "in_progress", "resuming"}
RECOVERABLE_RUN_KINDS = {"generation", "regeneration", "readme_improvement"}
DEFAULT_STALE_ACTIVE_RUN_SECONDS = 6 * 60 * 60


def stale_active_run_seconds() -> int:
    """Return the inactivity window after which active dashboard rows are reconciled."""
    raw_seconds = os.getenv("USER_RUN_STALE_AFTER_SECONDS")
    raw_hours = os.getenv("USER_RUN_STALE_AFTER_HOURS")
    raw_value = raw_seconds if raw_seconds is not None else raw_hours
    multiplier = 1 if raw_seconds is not None else 60 * 60
    if raw_value is None:
        return DEFAULT_STALE_ACTIVE_RUN_SECONDS
    try:
        value = int(float(raw_value) * multiplier)
    except (TypeError, ValueError):
        logger.warning("Invalid stale user-run TTL %r; using default", raw_value)
        return DEFAULT_STALE_ACTIVE_RUN_SECONDS
    return max(60, value)


def upsert_user_run(
    *,
    request_id: str,
    user_id: str,
    kind: str,
    status: str,
    title: str | None = None,
    score: dict[str, Any] | None = None,
    result_url: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create or update one dashboard activity row without blocking core work."""
    if not request_id or not user_id:
        return None
    db = SessionLocal()
    try:
        row = db.query(UserRun).filter(UserRun.request_id == request_id).first()
        if row is None:
            row = UserRun(
                request_id=request_id,
                user_id=user_id,
                kind=kind,
                status=status,
                created_at=utc_now_naive(),
            )
            db.add(row)
        row.user_id = user_id
        row.kind = kind
        row.status = status
        if title is not None:
            row.title = title[:500]
        if score is not None:
            row.score = score
        if result_url is not None:
            row.result_url = result_url
        if metadata is not None:
            row.meta_data = metadata
        row.updated_at = utc_now_naive()
        db.commit()
        db.refresh(row)
        return row.to_dict()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to persist user run %s: %s", request_id, exc)
        return None
    finally:
        db.close()


def list_recent_user_runs_for_user(user_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """Return recent product activity rows for one user as detached dictionaries."""
    safe_limit = max(1, min(limit, 50))
    db = SessionLocal()
    try:
        rows = (
            db.query(UserRun)
            .filter(UserRun.user_id == user_id)
            .order_by(UserRun.updated_at.desc(), UserRun.created_at.desc())
            .limit(safe_limit)
            .all()
        )
        return [row.to_dict() for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list user runs for %s: %s", user_id, exc)
        return []
    finally:
        db.close()


def count_active_user_runs(user_id: str) -> int:
    """Count active dashboard jobs for the current user."""
    db = SessionLocal()
    try:
        return (
            db.query(UserRun)
            .filter(UserRun.user_id == user_id, UserRun.status.in_(ACTIVE_RUN_STATUSES))
            .count()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to count active user runs for %s: %s", user_id, exc)
        return 0
    finally:
        db.close()


def mark_user_run_cancelled(
    *,
    request_id: str,
    user_id: str,
    reason: str = "user_cancelled_from_dashboard",
) -> dict[str, Any] | None:
    """Mark one user-owned dashboard run as cancelled even if runtime state is gone."""
    if not request_id or not user_id:
        return None
    db = SessionLocal()
    try:
        row = (
            db.query(UserRun)
            .filter(UserRun.request_id == request_id, UserRun.user_id == user_id)
            .first()
        )
        if row is None:
            return None

        previous_status = row.status
        metadata = row.meta_data if isinstance(row.meta_data, dict) else {}
        row.status = "cancelled"
        row.meta_data = {
            **metadata,
            "cancelled_from_status": previous_status,
            "cancelled_reason": reason,
            "cancelled_at": utc_now_naive().isoformat(),
        }
        row.updated_at = utc_now_naive()
        db.commit()
        db.refresh(row)
        return row.to_dict()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to cancel user run %s for %s: %s", request_id, user_id, exc)
        return None
    finally:
        db.close()


def _stale_terminal_status(row: UserRun) -> str:
    """Resolve the terminal status for a stale active dashboard row."""
    kind = str(row.kind or "").lower()
    if kind in RECOVERABLE_RUN_KINDS:
        return "interrupted"
    return "failed"


def reconcile_stale_active_user_runs(
    *,
    user_id: str | None = None,
    stale_after_seconds: int | None = None,
    now: datetime | None = None,
    batch_size: int = 500,
) -> list[dict[str, Any]]:
    """Mark stale active dashboard rows as terminal so counters cannot stay stuck."""
    effective_now = now or utc_now_naive()
    ttl_seconds = stale_after_seconds if stale_after_seconds is not None else stale_active_run_seconds()
    cutoff = effective_now - timedelta(seconds=max(60, int(ttl_seconds)))
    db = SessionLocal()
    try:
        query = db.query(UserRun).filter(
            UserRun.status.in_(STALE_RECONCILABLE_STATUSES),
            UserRun.updated_at < cutoff,
        )
        if user_id is not None:
            query = query.filter(UserRun.user_id == user_id)

        rows = (
            query.order_by(UserRun.updated_at.asc(), UserRun.created_at.asc())
            .limit(max(1, int(batch_size)))
            .all()
        )
        reconciled: list[dict[str, Any]] = []
        for row in rows:
            previous_status = row.status
            metadata = row.meta_data if isinstance(row.meta_data, dict) else {}
            row.status = _stale_terminal_status(row)
            row.meta_data = {
                **metadata,
                "stale_reconciled_at": effective_now.isoformat(),
                "stale_from_status": previous_status,
                "stale_after_seconds": max(60, int(ttl_seconds)),
            }
            row.updated_at = effective_now
            reconciled.append(row.to_dict())
        db.commit()
        return reconciled
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to reconcile stale user runs for %s: %s", user_id or "*", exc)
        return []
    finally:
        db.close()
