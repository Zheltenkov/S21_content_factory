"""GenerationStartService dispatch: legacy in-process vs durable-worker lease path."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import Mock

from content_factory.api.services.generation_start_service import GenerationStartService


def _service(*, lease_dispatch=None) -> tuple[GenerationStartService, list[tuple]]:
    runner_calls: list[tuple] = []

    async def _runner(request_id, user_id, seed, tracks, extra) -> None:
        runner_calls.append((request_id, user_id, seed))

    async def _log(*args: Any, **kwargs: Any) -> None:
        return None

    service = GenerationStartService(
        status_setter=Mock(),
        error_store=Mock(),
        task_registrar=Mock(),
        background_runner=_runner,
        log_writer=_log,
        logger=Mock(),
        workflow_service=Mock(),
        lease_dispatch=lease_dispatch,
    )
    return service, runner_calls


def test_legacy_path_calls_background_runner_directly() -> None:
    service, runner_calls = _service(lease_dispatch=None)
    asyncio.run(service._start_after_response("r1", "u1", {"seed": 1}))
    assert runner_calls == [("r1", "u1", {"seed": 1})]


def test_worker_path_routes_through_lease_dispatch() -> None:
    dispatched: list[tuple[str, str]] = []

    async def _lease_dispatch(request_id: str, user_id: str, run: Callable[[], Awaitable[None]]) -> None:
        dispatched.append((request_id, user_id))
        # The dispatch is handed a runnable that, when awaited, runs the generation.
        await run()

    service, runner_calls = _service(lease_dispatch=_lease_dispatch)
    asyncio.run(service._start_after_response("r2", "u2", {"seed": 2}))

    assert dispatched == [("r2", "u2")]
    # run() delegated to the same background runner (generation logic unchanged).
    assert runner_calls == [("r2", "u2", {"seed": 2})]


def test_worker_path_can_skip_run_when_claim_lost() -> None:
    """If the lease dispatch declines (e.g. claim lost), generation does not run."""

    async def _lease_dispatch(request_id: str, user_id: str, run: Callable[[], Awaitable[None]]) -> None:
        return  # never awaits run -> duplicate/lost-claim start is a no-op

    service, runner_calls = _service(lease_dispatch=_lease_dispatch)
    asyncio.run(service._start_after_response("r3", "u3", {"seed": 3}))
    assert runner_calls == []
