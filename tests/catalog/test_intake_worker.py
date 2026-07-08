"""Durable intake-job lease primitives (claim / heartbeat / release / reclaim)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from content_factory.catalog.viewer.intake_worker import (
    claim_intake_job,
    heartbeat_intake_job,
    reclaim_expired_intake_jobs,
    release_intake_job,
)


def _create_job(conn: Any, *, status: str = "pending") -> int:
    cur = conn.execute(
        "INSERT INTO intake_job(source_kind, brief_text, status) VALUES ('text', 'brief', ?)",
        (status,),
    )
    conn.commit()
    return int(cur.lastrowid)


def _job(conn: Any, job_id: int) -> dict[str, Any]:
    return dict(conn.execute("SELECT * FROM intake_job WHERE id = ?", (job_id,)).fetchone())


def _expire_lease(conn: Any, job_id: int) -> None:
    conn.execute(
        "UPDATE intake_job SET lease_expires_at = now() - interval '5 seconds' WHERE id = ?",
        (job_id,),
    )
    conn.commit()


def test_claim_pending_job_and_block_double_claim(catalog_conn: Any) -> None:
    job_id = _create_job(catalog_conn)

    assert claim_intake_job(catalog_conn, job_id, "worker-a") is True
    row = _job(catalog_conn, job_id)
    assert row["status"] == "running"
    assert row["lease_owner"] == "worker-a"
    assert row["attempt_count"] == 1

    # Second worker cannot claim a job with a live lease.
    assert claim_intake_job(catalog_conn, job_id, "worker-b") is False
    assert _job(catalog_conn, job_id)["lease_owner"] == "worker-a"


def test_heartbeat_only_by_owner(catalog_conn: Any) -> None:
    job_id = _create_job(catalog_conn)
    claim_intake_job(catalog_conn, job_id, "worker-a")

    assert heartbeat_intake_job(catalog_conn, job_id, "worker-a") is True
    assert heartbeat_intake_job(catalog_conn, job_id, "worker-b") is False


def test_release_clears_lease(catalog_conn: Any) -> None:
    job_id = _create_job(catalog_conn)
    claim_intake_job(catalog_conn, job_id, "worker-a")

    release_intake_job(catalog_conn, job_id, "worker-a")
    row = _job(catalog_conn, job_id)
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None


def test_expired_lease_is_reclaimable_by_another_worker(catalog_conn: Any) -> None:
    job_id = _create_job(catalog_conn)
    claim_intake_job(catalog_conn, job_id, "worker-a")
    _expire_lease(catalog_conn, job_id)

    # A different worker can take over an expired lease.
    assert claim_intake_job(catalog_conn, job_id, "worker-b") is True
    row = _job(catalog_conn, job_id)
    assert row["lease_owner"] == "worker-b"
    assert row["attempt_count"] == 2


def test_reclaim_requeues_expired_with_retries_left(catalog_conn: Any) -> None:
    job_id = _create_job(catalog_conn)
    claim_intake_job(catalog_conn, job_id, "worker-a")  # attempt_count -> 1
    _expire_lease(catalog_conn, job_id)

    result = reclaim_expired_intake_jobs(catalog_conn, max_attempts=3)
    assert result == {"requeued": 1, "failed": 0}
    row = _job(catalog_conn, job_id)
    assert row["status"] == "pending"
    assert row["lease_owner"] is None


def test_reclaim_fails_when_attempts_exhausted(catalog_conn: Any) -> None:
    job_id = _create_job(catalog_conn)
    claim_intake_job(catalog_conn, job_id, "worker-a")
    catalog_conn.execute("UPDATE intake_job SET attempt_count = 3 WHERE id = ?", (job_id,))
    catalog_conn.commit()
    _expire_lease(catalog_conn, job_id)

    result = reclaim_expired_intake_jobs(catalog_conn, max_attempts=3)
    assert result == {"requeued": 0, "failed": 1}
    assert _job(catalog_conn, job_id)["status"] == "failed"


def test_reclaim_requeues_legacy_running_job_without_lease(catalog_conn: Any) -> None:
    # A job left 'running' before the lease columns existed (or by a crash mid-claim)
    # has a NULL lease and must still be reclaimed on restart.
    job_id = _create_job(catalog_conn, status="running")
    assert _job(catalog_conn, job_id)["lease_expires_at"] is None

    result = reclaim_expired_intake_jobs(catalog_conn, max_attempts=3)
    assert result == {"requeued": 1, "failed": 0}
    assert _job(catalog_conn, job_id)["status"] == "pending"


def test_reclaim_ignores_live_leases(catalog_conn: Any) -> None:
    job_id = _create_job(catalog_conn)
    claim_intake_job(catalog_conn, job_id, "worker-a")  # live lease, not expired

    result = reclaim_expired_intake_jobs(catalog_conn, max_attempts=3)
    assert result == {"requeued": 0, "failed": 0}
    assert _job(catalog_conn, job_id)["status"] == "running"


def test_dispatch_pending_submits_only_pending_jobs(catalog_conn: Any, monkeypatch: Any) -> None:
    from content_factory.catalog.viewer import intake_ops

    submitted: list[int] = []
    monkeypatch.setattr(
        intake_ops.INTAKE_EXECUTOR,
        "submit",
        lambda _fn, _db_path, job_id: submitted.append(job_id),
    )
    pending_a = _create_job(catalog_conn, status="pending")
    _create_job(catalog_conn, status="running")  # not pending -> not dispatched
    pending_b = _create_job(catalog_conn, status="pending")

    intake_ops._dispatch_pending_intake_jobs(catalog_conn, Path("unused-on-postgres"))

    assert sorted(submitted) == sorted([pending_a, pending_b])
