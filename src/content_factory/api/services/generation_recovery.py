"""Recovery poller for interrupted generation workflows (durable worker, slice 2).

On restart the previous process's active workflows are marked ``interrupted`` (and
``created`` rows that never dispatched stay recoverable). This module reclaims dead
leases and then, for each recoverable workflow, atomically claims a lease and hands it
to an injected ``dispatch`` callback that resumes generation from the durable
checkpoints (via the existing ``run_workflow_command_background(command="resume")``).

Design:
- ``dispatch`` is INJECTED so this loop is pure and unit-testable and does not pull in
  the heavy resume-service DI. The live wiring (main.py lifespan) passes a callback
  that runs the resume inside ``run_with_generation_lease`` and is gated behind
  ``GENERATION_WORKER_ENABLED`` (default off), so enabling auto-resume is opt-in.
- ``run_with_generation_lease`` holds the lease for the duration of a resume by
  heartbeating on an interval, and always releases it on exit — so a resume longer
  than the lease TTL is not reclaimed and double-run.
- If a claim's dispatch raises, the lease is left to expire and the next reclaim cycle
  requeues it (bounded by ``MAX_GENERATION_ATTEMPTS``), so nothing is silently dropped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from content_factory.api.db.generation_worker import (
    HEARTBEAT_INTERVAL_SECONDS,
    claim_generation_workflow,
    heartbeat_generation_workflow,
    list_claimable_generation_workflows,
    reclaim_expired_generation_workflows,
    release_generation_workflow,
    worker_identity,
)
from content_factory.api.utils.logger import get_logger

logger = get_logger("services.generation_recovery")

_T = TypeVar("_T")

# dispatch(request_id, user_id, owner) -> None: start the resume for a claimed workflow.
DispatchResume = Callable[[str, str | None, str], None]


def recover_interrupted_generation_workflows(
    *,
    dispatch: DispatchResume,
    worker: str | None = None,
    limit: int = 10,
    reclaim: bool = True,
) -> dict[str, int]:
    """Reclaim dead leases, then claim and dispatch resume for recoverable workflows.

    Returns counts: ``reclaimed_requeued``, ``reclaimed_failed``, ``claimed``,
    ``dispatched``. A workflow that is claimed but whose dispatch raises is counted in
    ``claimed`` but not ``dispatched``; its lease expires and is reclaimed next cycle.
    """
    stats = {"reclaimed_requeued": 0, "reclaimed_failed": 0, "claimed": 0, "dispatched": 0}
    if reclaim:
        reclaimed = reclaim_expired_generation_workflows()
        stats["reclaimed_requeued"] = reclaimed["requeued"]
        stats["reclaimed_failed"] = reclaimed["failed"]

    owner = worker or worker_identity()
    for candidate in list_claimable_generation_workflows(limit=limit):
        request_id = candidate.get("request_id")
        if not request_id:
            continue
        if not claim_generation_workflow(request_id, owner):
            continue
        stats["claimed"] += 1
        try:
            dispatch(request_id, candidate.get("user_id"), owner)
            stats["dispatched"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad dispatch must not stop the sweep.
            logger.warning("Failed to dispatch resume for %s: %s; lease will be reclaimed.", request_id, exc)
    return stats


async def run_with_generation_lease(
    request_id: str,
    owner: str,
    run: Callable[[], Awaitable[_T]],
    *,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> _T:
    """Await ``run()`` while heartbeating the lease; always release the lease on exit.

    Holds the (already-claimed) lease for the whole resume so a run longer than the
    lease TTL is not reclaimed. The heartbeat and release run in threads because the
    DB primitives are synchronous.
    """
    stop = asyncio.Event()

    async def _heartbeat_loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=heartbeat_interval)
            except TimeoutError:
                await asyncio.to_thread(heartbeat_generation_workflow, request_id, owner)

    beat = asyncio.create_task(_heartbeat_loop())
    try:
        return await run()
    finally:
        stop.set()
        await beat
        await asyncio.to_thread(release_generation_workflow, request_id, owner)
