"""Factory for production LLM clients."""

from __future__ import annotations

import os

from .gateway import LLMGateway, LLMUsageBudgetTracker


_MEMORY_BUDGET_TRACKER = LLMUsageBudgetTracker()


def create_llm_client(
    *,
    model: str | None = None,
    provider: str | None = None,
    strict_provider: bool | None = None,
    default_role: str = "default",
    enable_cache: bool | None = None,
    enable_batching: bool | None = None,
    max_retries: int | None = None,
    retry_delay: float | None = None,
    timeout_seconds: float | None = None,
    user_id: str | None = None,
    run_id: str | None = None,
) -> object:
    """Create the role-aware production LLM gateway."""
    return LLMGateway(
        model=model,
        provider=provider,
        strict_provider=(provider is not None if strict_provider is None else strict_provider),
        default_role=default_role,
        enable_cache=enable_cache,
        enable_batching=enable_batching,
        max_retries=max_retries,
        retry_delay=retry_delay,
        timeout_seconds=timeout_seconds,
        budget_tracker=_create_budget_tracker(),
        user_id=user_id,
        run_id=run_id,
    )


def _create_budget_tracker() -> object:
    """Create DB-backed budget tracker when the API database layer is available."""
    if os.getenv("LLM_BUDGET_DB_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        return _MEMORY_BUDGET_TRACKER
    try:
        from api.db.llm_usage_db import DatabaseLLMUsageBudgetTracker

        return DatabaseLLMUsageBudgetTracker(fallback_tracker=_MEMORY_BUDGET_TRACKER)
    except Exception:
        return _MEMORY_BUDGET_TRACKER
