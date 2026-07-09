from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_catalog_intake_recovery_startup_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    from content_factory.api import main

    calls: list[tuple[Callable[..., object], Path]] = []
    warnings: list[tuple[str, tuple[object, ...]]] = []

    async def fake_to_thread(func: Callable[..., object], db_path: Path) -> dict[str, int]:
        calls.append((func, db_path))
        return {"recovered": 1, "dispatched": 2}

    monkeypatch.setenv("INTAKE_WORKER_RECOVERY_ON_STARTUP", "true")
    monkeypatch.setattr(main, "catalog_db_path", lambda: Path("catalog.sqlite"))
    monkeypatch.setattr(main.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(main.logger, "warning", lambda message, *args, **_kwargs: warnings.append((message, args)))

    await main._recover_catalog_intake_jobs_on_startup()

    assert calls == [(main.resume_pending_intake_jobs, Path("catalog.sqlite"))]
    assert warnings == [("♻️ Intake recovery: recovered=%s dispatched=%s", (1, 2))]


@pytest.mark.asyncio
async def test_catalog_intake_recovery_startup_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from content_factory.api import main

    async def fail_to_thread(_func: Callable[..., object], _db_path: Path) -> dict[str, int]:
        raise AssertionError("recovery should not run")

    monkeypatch.setenv("INTAKE_WORKER_RECOVERY_ON_STARTUP", "false")
    monkeypatch.setattr(main.asyncio, "to_thread", fail_to_thread)

    await main._recover_catalog_intake_jobs_on_startup()
