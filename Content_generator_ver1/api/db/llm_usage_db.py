"""Persistence helpers for LLM usage, token and cost accounting."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from api.utils.logger import get_logger
from content_gen.exceptions import LLMAPIError

from .models import LLMUsageLedger
from .session import SessionLocal

logger = get_logger("db.llm_usage")


def _budget_node(node: str | None = None, role: str | None = None) -> str:
    """Normalize the budget bucket; role is accepted for backward compatibility."""
    return str(node or role or "default").strip().lower() or "default"


def _decimal_cost(value: float | int | Decimal | None) -> Decimal:
    """Convert provider cost into DB-safe Decimal without binary float artifacts."""
    if value is None:
        return Decimal("0")
    return Decimal(str(value)).quantize(Decimal("0.00000001"))


def get_llm_usage_spent(*, user_id: str, run_id: str, node: str | None = None, role: str | None = None) -> float:
    """Return aggregated USD spend for one user/run/node bucket."""
    if not user_id or not run_id:
        return 0.0
    bucket = _budget_node(node, role)
    db = SessionLocal()
    try:
        row = (
            db.query(LLMUsageLedger)
            .filter(
                LLMUsageLedger.user_id == user_id,
                LLMUsageLedger.run_id == run_id,
                LLMUsageLedger.node == bucket,
            )
            .first()
        )
        return float(row.cost_usd or 0) if row is not None else 0.0
    finally:
        db.close()


def record_llm_usage(
    *,
    user_id: str,
    run_id: str,
    node: str | None = None,
    role: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    cost_usd: float | int | Decimal | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Atomically upsert one completed LLM call into the usage ledger."""
    if not user_id or not run_id:
        return None
    bucket = _budget_node(node, role)
    now = datetime.utcnow()
    prompt = int(prompt_tokens or 0)
    completion = int(completion_tokens or 0)
    total = int(total_tokens or prompt + completion or 0)
    cost = _decimal_cost(cost_usd)

    db = SessionLocal()
    try:
        stmt = insert(LLMUsageLedger).values(
            user_id=user_id,
            run_id=run_id,
            node=bucket,
            role=role,
            provider=provider,
            model=model,
            calls_count=1,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_usd=cost,
            route_data=route,
            created_at=now,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "run_id", "node"],
            set_={
                "role": role,
                "provider": provider,
                "model": model,
                "calls_count": LLMUsageLedger.calls_count + 1,
                "prompt_tokens": LLMUsageLedger.prompt_tokens + prompt,
                "completion_tokens": LLMUsageLedger.completion_tokens + completion,
                "total_tokens": LLMUsageLedger.total_tokens + total,
                "cost_usd": LLMUsageLedger.cost_usd + cost,
                "route_data": route,
                "updated_at": now,
            },
        )
        db.execute(stmt)
        db.commit()
        row = (
            db.query(LLMUsageLedger)
            .filter(
                LLMUsageLedger.user_id == user_id,
                LLMUsageLedger.run_id == run_id,
                LLMUsageLedger.node == bucket,
            )
            .first()
        )
        return row.to_dict() if row is not None else None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class DatabaseLLMUsageBudgetTracker:
    """DB-backed budget tracker shared by all API workers using the same Postgres."""

    def __init__(self, fallback_tracker: Any | None = None) -> None:
        self._fallback = fallback_tracker

    def spent(self, *, user_id: str, run_id: str, node: str | None = None, role: str | None = None) -> float:
        """Return spend from Postgres, falling back to process-local memory if DB is unavailable."""
        try:
            return get_llm_usage_spent(user_id=user_id, run_id=run_id, node=node, role=role)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read LLM usage from DB for run=%s node=%s: %s", run_id, node or role, exc)
            if self._fallback is not None:
                return float(self._fallback.spent(user_id=user_id, run_id=run_id, node=node, role=role))
            return 0.0

    def assert_within_budget(
        self,
        *,
        user_id: str,
        run_id: str,
        node: str | None = None,
        role: str | None = None,
        budget_usd: float | None,
    ) -> None:
        """Reject a new call when the shared node budget has already been spent."""
        if budget_usd is None:
            return
        current = self.spent(user_id=user_id, run_id=run_id, node=node, role=role)
        if current >= budget_usd:
            bucket = _budget_node(node, role)
            raise LLMAPIError(
                f"LLM budget exceeded for node={bucket}: spent=${current:.6f}, budget=${budget_usd:.6f}"
            )

    def record(
        self,
        *,
        user_id: str,
        run_id: str,
        node: str | None = None,
        role: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        cost_usd: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        route: dict[str, Any] | None = None,
    ) -> None:
        """Persist completed call usage; DB failure degrades to process-local accounting."""
        try:
            record_llm_usage(
                user_id=user_id,
                run_id=run_id,
                node=node,
                role=role,
                provider=provider,
                model=model,
                cost_usd=cost_usd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                route=route,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist LLM usage for run=%s node=%s: %s", run_id, node or role, exc)
            if self._fallback is not None:
                self._fallback.record(
                    user_id=user_id,
                    run_id=run_id,
                    node=node,
                    role=role,
                    cost_usd=cost_usd,
                )
