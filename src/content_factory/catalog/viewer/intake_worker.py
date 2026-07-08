"""Durable DB-lease primitives for background intake jobs.

Replaces the process-local ``ACTIVE_INTAKE_JOB_IDS`` set + fixed stale-timeout with
a lease held in ``catalog.intake_job`` so intake survives process restart and is safe
across multiple workers:

- **claim** — atomically take a time-boxed lease on a specific job if it is ``pending``
  or its previous lease has expired. A conditional single-row ``UPDATE`` is exclusive
  under READ COMMITTED: concurrent claimers block on the row lock, then re-evaluate the
  guard against the now-``running`` row and match nothing, so exactly one wins.
- **heartbeat** — extend the lease while the job runs; only the owner can.
- **release** — drop the lease when the job reaches a terminal state.
- **reclaim** — a job whose lease expired (worker crashed / restarted) is requeued for
  retry while attempts remain, else failed. This *resumes* lost work instead of
  silently dropping it.

Slice 1 ships these primitives + the ``018_intake_lease`` columns; wiring them into the
executor (claim on start, heartbeat thread, reclaim on startup) is slice 2.
"""

from __future__ import annotations

import os
import socket
from uuid import uuid4

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import utc_now_iso

# Lease held for this long; a worker must heartbeat within it or lose the job.
LEASE_TTL_SECONDS = 120
# How often a running worker should refresh its lease (well under the TTL).
HEARTBEAT_INTERVAL_SECONDS = 30
# A job reclaimed this many times without finishing is failed instead of requeued.
MAX_INTAKE_ATTEMPTS = 3


def worker_identity() -> str:
    """Stable-per-process worker id: ``host:pid:rand`` (rand disambiguates threads/reuse)."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"


def claim_intake_job(
    conn: CatalogConnection,
    job_id: int,
    owner: str,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> bool:
    """Atomically claim a pending or lease-expired job. Return True iff now owned."""
    now_iso = utc_now_iso()
    cursor = conn.execute(
        """
        UPDATE intake_job
        SET status = 'running',
            lease_owner = ?,
            lease_expires_at = now() + (interval '1 second' * ?),
            heartbeat_at = now(),
            attempt_count = attempt_count + 1,
            current_stage = 'starting',
            started_at = COALESCE(started_at, ?),
            updated_at = ?
        WHERE id = ?
          AND (
                status = 'pending'
                OR (status = 'running'
                    AND (lease_expires_at IS NULL OR lease_expires_at < now()))
              )
        """,
        (owner, ttl_seconds, now_iso, now_iso, job_id),
    )
    conn.commit()
    return bool(cursor.rowcount > 0)


def heartbeat_intake_job(
    conn: CatalogConnection,
    job_id: int,
    owner: str,
    ttl_seconds: int = LEASE_TTL_SECONDS,
) -> bool:
    """Extend the lease of a running job this worker owns. Return True iff extended."""
    cursor = conn.execute(
        """
        UPDATE intake_job
        SET lease_expires_at = now() + (interval '1 second' * ?),
            heartbeat_at = now(),
            updated_at = ?
        WHERE id = ? AND lease_owner = ? AND status = 'running'
        """,
        (ttl_seconds, utc_now_iso(), job_id, owner),
    )
    conn.commit()
    return bool(cursor.rowcount > 0)


def release_intake_job(conn: CatalogConnection, job_id: int, owner: str) -> None:
    """Drop the lease (e.g. on terminal status). Only the owner clears it."""
    conn.execute(
        """
        UPDATE intake_job
        SET lease_owner = NULL,
            lease_expires_at = NULL,
            updated_at = ?
        WHERE id = ? AND lease_owner = ?
        """,
        (utc_now_iso(), job_id, owner),
    )
    conn.commit()


def reclaim_expired_intake_jobs(
    conn: CatalogConnection,
    max_attempts: int = MAX_INTAKE_ATTEMPTS,
) -> dict[str, int]:
    """Requeue jobs with a dead lease (retries left) and fail those out of retries.

    Returns ``{"requeued": n, "failed": m}``.
    """
    now_iso = utc_now_iso()
    requeued = conn.execute(
        """
        UPDATE intake_job
        SET status = 'pending',
            lease_owner = NULL,
            lease_expires_at = NULL,
            current_stage = 'queued',
            progress_note = 'Worker потерян; задача возвращена в очередь для повтора.',
            updated_at = ?
        WHERE status = 'running'
          AND (lease_expires_at IS NULL OR lease_expires_at < now())
          AND attempt_count < ?
        """,
        (now_iso, max_attempts),
    ).rowcount
    failed = conn.execute(
        """
        UPDATE intake_job
        SET status = 'failed',
            lease_owner = NULL,
            lease_expires_at = NULL,
            current_stage = 'failed',
            progress_note = 'Обработка прервана.',
            error_text = 'Worker потерян и исчерпаны попытки повтора.',
            updated_at = ?,
            finished_at = ?
        WHERE status = 'running'
          AND (lease_expires_at IS NULL OR lease_expires_at < now())
          AND attempt_count >= ?
        """,
        (now_iso, now_iso, max_attempts),
    ).rowcount
    conn.commit()
    return {"requeued": int(requeued), "failed": int(failed)}
