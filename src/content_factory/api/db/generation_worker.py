"""Durable DB-lease primitives for a background generation worker.

The generation workflow state (checkpoints, status) is already durable; what is not
yet durable is *execution* — a request is generated in the web process via
``asyncio.create_task``. These primitives lay the groundwork for moving execution to a
restart-safe, multi-worker background worker, mirroring the intake worker
(``catalog/viewer/intake_worker.py``) but on the SQLAlchemy ORM:

- **claim** — atomically take a time-boxed lease on a *recoverable* workflow
  (``created`` or ``interrupted``, or a worker-owned lease that expired), moving it to
  ``resuming``. A conditional single-row ``UPDATE`` is exclusive under READ COMMITTED,
  so exactly one worker wins.
- **heartbeat** — extend the lease while the worker runs; only the owner can.
- **release** — drop the lease when the workflow reaches a terminal/paused state.
- **reclaim** — a *worker-owned* lease that expired (worker crashed) is requeued to
  ``interrupted`` while attempts remain, else ``failed``.

Safety during the hybrid transition: legacy in-process runs set ``status='running'``
with ``lease_owner`` NULL. Every claim/reclaim guard requires ``lease_owner IS NOT
NULL`` for the running/resuming branch, so the worker never touches a live in-process
generation. Timestamps are computed in Python (portable across the sqlite test DB and
production Postgres).
"""

from __future__ import annotations

import os
import socket
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, or_

from content_factory.api.utils.logger import get_logger

from .models import GenerationWorkflowState, utc_now_naive
from .session import SessionLocal

logger = get_logger("db.generation_worker")

# Lease held for this long; a worker must heartbeat within it or lose the workflow.
# Generation nodes are longer-running than intake stages, so the TTL is wider.
LEASE_TTL_SECONDS = 180
# How often a running worker should refresh its lease (well under the TTL).
HEARTBEAT_INTERVAL_SECONDS = 45
# A workflow reclaimed this many times without finishing is failed instead of requeued.
MAX_GENERATION_ATTEMPTS = 3

# Statuses a worker may pick up and resume.
CLAIMABLE_STATUSES: tuple[str, ...] = ("created", "interrupted")
# Statuses a worker holds while running; only these (with a non-NULL lease_owner) are
# eligible for reclaim, so NULL-lease in-process runs are never disturbed.
LEASED_ACTIVE_STATUSES: tuple[str, ...] = ("resuming", "running")


def worker_identity() -> str:
    """Stable-per-process worker id: ``host:pid:rand`` (rand disambiguates reuse)."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def claim_generation_workflow(
    request_id: str,
    owner: str,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> bool:
    """Atomically claim a recoverable workflow. Return True iff now owned by ``owner``."""
    if not request_id or not owner:
        return False
    now = utc_now_naive()
    expires = now + timedelta(seconds=ttl_seconds)
    db = SessionLocal()
    try:
        updated = (
            db.query(GenerationWorkflowState)
            .filter(
                GenerationWorkflowState.request_id == request_id,
                or_(
                    GenerationWorkflowState.status.in_(CLAIMABLE_STATUSES),
                    and_(
                        GenerationWorkflowState.status.in_(LEASED_ACTIVE_STATUSES),
                        GenerationWorkflowState.lease_owner.isnot(None),
                        or_(
                            GenerationWorkflowState.lease_expires_at.is_(None),
                            GenerationWorkflowState.lease_expires_at < now,
                        ),
                    ),
                ),
            )
            .update(
                {
                    GenerationWorkflowState.status: "resuming",
                    GenerationWorkflowState.lease_owner: owner,
                    GenerationWorkflowState.lease_expires_at: expires,
                    GenerationWorkflowState.heartbeat_at: now,
                    GenerationWorkflowState.attempt_count: GenerationWorkflowState.attempt_count + 1,
                    GenerationWorkflowState.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return bool(updated > 0)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to claim generation workflow %s: %s", request_id, exc)
        return False
    finally:
        db.close()


def heartbeat_generation_workflow(
    request_id: str,
    owner: str,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> bool:
    """Extend the lease of a workflow this worker owns. Return True iff extended."""
    now = utc_now_naive()
    expires = now + timedelta(seconds=ttl_seconds)
    db = SessionLocal()
    try:
        updated = (
            db.query(GenerationWorkflowState)
            .filter(
                GenerationWorkflowState.request_id == request_id,
                GenerationWorkflowState.lease_owner == owner,
                GenerationWorkflowState.status.in_(LEASED_ACTIVE_STATUSES),
            )
            .update(
                {
                    GenerationWorkflowState.lease_expires_at: expires,
                    GenerationWorkflowState.heartbeat_at: now,
                    GenerationWorkflowState.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return bool(updated > 0)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to heartbeat generation workflow %s: %s", request_id, exc)
        return False
    finally:
        db.close()


def release_generation_workflow(request_id: str, owner: str) -> None:
    """Drop the lease (e.g. on terminal/paused status). Only the owner clears it."""
    now = utc_now_naive()
    db = SessionLocal()
    try:
        db.query(GenerationWorkflowState).filter(
            GenerationWorkflowState.request_id == request_id,
            GenerationWorkflowState.lease_owner == owner,
        ).update(
            {
                GenerationWorkflowState.lease_owner: None,
                GenerationWorkflowState.lease_expires_at: None,
                GenerationWorkflowState.updated_at: now,
            },
            synchronize_session=False,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to release generation workflow %s: %s", request_id, exc)
    finally:
        db.close()


def reclaim_expired_generation_workflows(
    max_attempts: int = MAX_GENERATION_ATTEMPTS,
    error: str = "Generation worker потерян; запуск возвращён на восстановление.",
) -> dict[str, int]:
    """Requeue worker-owned workflows with a dead lease; fail those out of retries.

    Only touches rows with a non-NULL ``lease_owner`` (i.e. previously claimed by a
    worker), so live in-process generations (NULL lease) are never disturbed. Returns
    ``{"requeued": n, "failed": m}``.
    """
    now = utc_now_naive()
    db = SessionLocal()
    try:
        expired_owned = and_(
            GenerationWorkflowState.status.in_(LEASED_ACTIVE_STATUSES),
            GenerationWorkflowState.lease_owner.isnot(None),
            or_(
                GenerationWorkflowState.lease_expires_at.is_(None),
                GenerationWorkflowState.lease_expires_at < now,
            ),
        )
        requeued = (
            db.query(GenerationWorkflowState)
            .filter(expired_owned, GenerationWorkflowState.attempt_count < max_attempts)
            .update(
                {
                    GenerationWorkflowState.status: "interrupted",
                    GenerationWorkflowState.lease_owner: None,
                    GenerationWorkflowState.lease_expires_at: None,
                    GenerationWorkflowState.current_node: None,
                    GenerationWorkflowState.error: error,
                    GenerationWorkflowState.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        failed = (
            db.query(GenerationWorkflowState)
            .filter(expired_owned, GenerationWorkflowState.attempt_count >= max_attempts)
            .update(
                {
                    GenerationWorkflowState.status: "failed",
                    GenerationWorkflowState.lease_owner: None,
                    GenerationWorkflowState.lease_expires_at: None,
                    GenerationWorkflowState.current_node: None,
                    GenerationWorkflowState.error: "Generation worker потерян и исчерпаны попытки повтора.",
                    GenerationWorkflowState.updated_at: now,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        return {"requeued": int(requeued), "failed": int(failed)}
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Failed to reclaim expired generation workflows: %s", exc)
        return {"requeued": 0, "failed": 0}
    finally:
        db.close()
