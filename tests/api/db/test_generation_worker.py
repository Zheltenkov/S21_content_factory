"""Durable-lease primitives for the background generation worker.

Claim/heartbeat/release/reclaim over generation_workflow_states, with the critical
invariant that legacy in-process runs (status='running', lease_owner NULL) are never
disturbed by the worker's claim/reclaim scans.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from content_factory.api.db import generation_worker
from content_factory.api.db.models import Base, GenerationWorkflowState, utc_now_naive


def _session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(session_factory, **rows_kwargs):
    db = session_factory()
    for request_id, kwargs in rows_kwargs.items():
        db.add(GenerationWorkflowState(request_id=request_id, user_id="u1", **kwargs))
    db.commit()
    db.close()


def _get(session_factory, request_id) -> GenerationWorkflowState:
    db = session_factory()
    try:
        return db.query(GenerationWorkflowState).filter_by(request_id=request_id).one()
    finally:
        db.close()


def test_claim_interrupted_takes_lease(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, r={"status": "interrupted"})

    assert generation_worker.claim_generation_workflow("r", "worker-a") is True
    row = _get(sf, "r")
    assert row.status == "resuming"
    assert row.lease_owner == "worker-a"
    assert row.lease_expires_at is not None
    assert row.attempt_count == 1


def test_claim_created_takes_lease(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, r={"status": "created"})

    assert generation_worker.claim_generation_workflow("r", "worker-a") is True
    assert _get(sf, "r").status == "resuming"


def test_second_claim_on_live_lease_fails(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, r={"status": "interrupted"})

    assert generation_worker.claim_generation_workflow("r", "worker-a") is True
    # A second worker cannot steal a fresh, unexpired lease.
    assert generation_worker.claim_generation_workflow("r", "worker-b") is False
    assert _get(sf, "r").lease_owner == "worker-a"


def test_claim_does_not_touch_in_process_running(monkeypatch) -> None:
    """A live in-process run (running, lease_owner NULL) must be invisible to claim."""
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, r={"status": "running", "lease_owner": None})

    assert generation_worker.claim_generation_workflow("r", "worker-a") is False
    row = _get(sf, "r")
    assert row.status == "running"
    assert row.lease_owner is None


def test_expired_worker_owned_lease_is_reclaimable_by_claim(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    past = utc_now_naive() - timedelta(seconds=10)
    _seed(sf, r={"status": "resuming", "lease_owner": "dead", "lease_expires_at": past, "attempt_count": 1})

    assert generation_worker.claim_generation_workflow("r", "worker-b") is True
    row = _get(sf, "r")
    assert row.lease_owner == "worker-b"
    assert row.attempt_count == 2


def test_heartbeat_only_for_owner(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, r={"status": "resuming", "lease_owner": "worker-a", "lease_expires_at": utc_now_naive()})

    assert generation_worker.heartbeat_generation_workflow("r", "worker-b") is False
    assert generation_worker.heartbeat_generation_workflow("r", "worker-a") is True
    assert _get(sf, "r").lease_expires_at > utc_now_naive()


def test_release_clears_lease(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, r={"status": "resuming", "lease_owner": "worker-a", "lease_expires_at": utc_now_naive()})

    generation_worker.release_generation_workflow("r", "worker-a")
    row = _get(sf, "r")
    assert row.lease_owner is None
    assert row.lease_expires_at is None


def test_reclaim_requeues_and_fails(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    past = utc_now_naive() - timedelta(seconds=10)
    _seed(
        sf,
        retry={"status": "resuming", "lease_owner": "dead", "lease_expires_at": past, "attempt_count": 1},
        exhausted={"status": "running", "lease_owner": "dead", "lease_expires_at": past, "attempt_count": 3},
        live={"status": "running", "lease_owner": None},
    )

    result = generation_worker.reclaim_expired_generation_workflows(max_attempts=3)

    assert result == {"requeued": 1, "failed": 1}
    assert _get(sf, "retry").status == "interrupted"
    assert _get(sf, "exhausted").status == "failed"
    # NULL-lease in-process run untouched.
    assert _get(sf, "live").status == "running"


def test_reclaim_ignores_null_lease_running(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, live={"status": "running", "lease_owner": None, "lease_expires_at": None})

    assert generation_worker.reclaim_expired_generation_workflows() == {"requeued": 0, "failed": 0}
    assert _get(sf, "live").status == "running"
