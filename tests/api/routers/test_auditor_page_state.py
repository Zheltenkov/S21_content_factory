"""Regression tests for the auditor page state builder.

Guards against a class of breakage where a symbol the auditor router reaches
through the ``web_app`` module (a re-export) is dropped by dead-import cleanup.
That once turned ``GET /app/auditor`` into a 500 (``module
'content_factory.audit.web_app' has no attribute 'load_env_file'``) with no
test coverage. These tests exercise the state builder directly so the wiring
stays honest.
"""

from __future__ import annotations

from content_factory.api.routers import auditor


def test_auditor_page_state_builds_without_reexport_gap() -> None:
    """Building per-user auditor state must not raise (env/config wiring intact)."""

    user = {"id": "regression_page_state"}
    state_key, state = auditor._auditor_page_state(user)

    assert state_key == "regression_page_state"
    assert isinstance(state.env_values, dict)
    assert state.report_dir.exists()


def test_auditor_page_state_is_cached_per_user() -> None:
    """Second call for the same user returns the same cached state object."""

    user = {"id": "regression_cache"}
    _, first = auditor._auditor_page_state(user)
    _, second = auditor._auditor_page_state(user)

    assert first is second
