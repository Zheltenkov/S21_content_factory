"""Recovery poller: reclaim + claim + dispatch loop, and the lease-holding wrapper."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from content_factory.api.db import generation_worker
from content_factory.api.db.models import Base, GenerationWorkflowState, utc_now_naive
from content_factory.api.services import generation_recovery


def _session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _seed(session_factory, **rows):
    db = session_factory()
    for request_id, kwargs in rows.items():
        db.add(GenerationWorkflowState(request_id=request_id, user_id="u1", **kwargs))
    db.commit()
    db.close()


def _get(session_factory, request_id) -> GenerationWorkflowState:
    db = session_factory()
    try:
        return db.query(GenerationWorkflowState).filter_by(request_id=request_id).one()
    finally:
        db.close()


def test_recover_claims_and_dispatches_recoverable(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, a={"status": "interrupted"}, b={"status": "created"})

    dispatched: list[tuple[str, str | None, str]] = []
    stats = generation_recovery.recover_interrupted_generation_workflows(
        dispatch=lambda rid, uid, owner: dispatched.append((rid, uid, owner)),
        worker="worker-x",
    )

    assert stats["claimed"] == 2
    assert stats["dispatched"] == 2
    assert {d[0] for d in dispatched} == {"a", "b"}
    assert all(d[2] == "worker-x" for d in dispatched)
    # Both moved to resuming and owned.
    assert _get(sf, "a").status == "resuming"
    assert _get(sf, "a").lease_owner == "worker-x"


def test_recover_respects_limit(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, a={"status": "interrupted"}, b={"status": "interrupted"}, c={"status": "interrupted"})

    dispatched: list[str] = []
    stats = generation_recovery.recover_interrupted_generation_workflows(
        dispatch=lambda rid, uid, owner: dispatched.append(rid),
        worker="w",
        limit=2,
    )
    assert stats["dispatched"] == 2
    assert len(dispatched) == 2


def test_recover_includes_reclaim_stats(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    past = utc_now_naive() - timedelta(seconds=10)
    _seed(sf, dead={"status": "running", "lease_owner": "gone", "lease_expires_at": past, "attempt_count": 1})

    stats = generation_recovery.recover_interrupted_generation_workflows(
        dispatch=lambda rid, uid, owner: None,
        worker="w",
    )
    # The dead lease is requeued to interrupted, then claimed+dispatched in the same sweep.
    assert stats["reclaimed_requeued"] == 1
    assert stats["dispatched"] == 1


def test_recover_dispatch_failure_leaves_lease(monkeypatch) -> None:
    sf = _session_factory()
    monkeypatch.setattr(generation_worker, "SessionLocal", sf)
    _seed(sf, a={"status": "interrupted"}, b={"status": "interrupted"})

    def _dispatch(rid, uid, owner):
        if rid == "a":
            raise RuntimeError("boom")

    stats = generation_recovery.recover_interrupted_generation_workflows(dispatch=_dispatch, worker="w")

    # Both claimed; only the good one dispatched; the sweep continued past the failure.
    assert stats["claimed"] == 2
    assert stats["dispatched"] == 1
    assert _get(sf, "a").lease_owner == "w"  # lease held; will expire → reclaimed next cycle


def test_run_with_lease_releases_on_success(monkeypatch) -> None:
    beats: list[str] = []
    released: list[str] = []
    monkeypatch.setattr(generation_recovery, "heartbeat_generation_workflow", lambda rid, owner: beats.append(rid))
    monkeypatch.setattr(generation_recovery, "release_generation_workflow", lambda rid, owner: released.append(rid))

    async def _run() -> str:
        await asyncio.sleep(0.03)
        return "done"

    result = asyncio.run(
        generation_recovery.run_with_generation_lease("r", "w", _run, heartbeat_interval=0.01)
    )
    assert result == "done"
    assert released == ["r"]
    assert len(beats) >= 1  # at least one heartbeat fired during the run


def test_run_with_lease_releases_on_exception(monkeypatch) -> None:
    released: list[str] = []
    monkeypatch.setattr(generation_recovery, "heartbeat_generation_workflow", lambda rid, owner: None)
    monkeypatch.setattr(generation_recovery, "release_generation_workflow", lambda rid, owner: released.append(rid))

    async def _run() -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        asyncio.run(generation_recovery.run_with_generation_lease("r", "w", _run, heartbeat_interval=0.01))
    assert released == ["r"]  # released even though the run raised
