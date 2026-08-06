"""Role-aware LLM runtime and telemetry for RPD generation."""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

RpdLlmRole = Literal[
    "rpd_translate",
    "rpd_classify",
    "rpd_interpret",
    "rpd_prose",
    "rpd_validate",
    "rpd_repair",
    "rpd_semantic_preflight",
]
T = TypeVar("T", bound=BaseModel)


class RpdLlmEvent(BaseModel):
    """Password-safe telemetry for one model invocation."""

    sequence: int
    role: RpdLlmRole
    prompt_version: str = ""
    started_at: str
    elapsed_ms: int
    success: bool
    provider: str = ""
    model: str = ""
    cache_hit: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    error_type: str = ""


@dataclass(frozen=True)
class _RolePolicy:
    role: RpdLlmRole
    env_prefix: str
    timeout_seconds: float
    max_retries: int
    enable_cache: bool


_POLICIES = (
    _RolePolicy("rpd_translate", "RPD_LLM_TRANSLATE", 90.0, 2, True),
    _RolePolicy("rpd_classify", "RPD_LLM_CLASSIFY", 60.0, 2, True),
    _RolePolicy("rpd_interpret", "RPD_LLM_INTERPRET", 60.0, 2, True),
    _RolePolicy("rpd_prose", "RPD_LLM_PROSE", 120.0, 2, True),
    _RolePolicy("rpd_validate", "RPD_LLM_VALIDATE", 60.0, 1, False),
    _RolePolicy("rpd_repair", "RPD_LLM_REPAIR", 90.0, 1, False),
    _RolePolicy("rpd_semantic_preflight", "RPD_LLM_SEMANTIC_PREFLIGHT", 60.0, 1, True),
)


class RpdLlmRouter:
    """Route calls to independently configured clients and retain eval-ready telemetry."""

    def __init__(self, clients: Mapping[RpdLlmRole, Any]) -> None:
        self._clients = dict(clients)
        self._events: list[RpdLlmEvent] = []

    @property
    def events(self) -> list[RpdLlmEvent]:
        return [event.model_copy(deep=True) for event in self._events]

    def complete(self, system: str, user: str, **kwargs: Any) -> str:
        role, prompt_version, client = self._resolve_call(kwargs)
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        try:
            response = str(
                client.complete(
                    system,
                    user,
                    llm_role=role,
                    prompt_version=prompt_version,
                    **kwargs,
                )
            )
        except Exception as exc:
            self._record(client, role, prompt_version, started_at, started, False, type(exc).__name__)
            raise
        self._record(client, role, prompt_version, started_at, started, True)
        return response

    def complete_structured(
        self,
        *,
        output_model: type[T],
        system: str,
        user: str,
        **kwargs: Any,
    ) -> T:
        role, prompt_version, client = self._resolve_call(kwargs)
        started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        try:
            response = client.complete_structured(
                output_model=output_model,
                system=system,
                user=user,
                llm_role=role,
                prompt_version=prompt_version,
                **kwargs,
            )
        except Exception as exc:
            self._record(client, role, prompt_version, started_at, started, False, type(exc).__name__)
            raise
        self._record(client, role, prompt_version, started_at, started, True)
        return output_model.model_validate(response)

    def record_cache_hit(self, *, role: str, prompt_version: str) -> None:
        """Record a logical stage served by the outer consilium cache without invoking a provider."""
        validated_role = _validate_role(role)
        client = self._clients.get(validated_role)
        if client is None:
            raise RuntimeError(f"RPD LLM role is unavailable: {validated_role}")
        self._events.append(
            RpdLlmEvent(
                sequence=len(self._events) + 1,
                role=validated_role,
                prompt_version=prompt_version,
                started_at=datetime.now(UTC).isoformat(),
                elapsed_ms=0,
                success=True,
                provider=str(getattr(client, "provider", "") or getattr(client, "_last_provider", "") or ""),
                model=str(getattr(client, "model", "") or ""),
                cache_hit=True,
            )
        )

    def _resolve_call(self, kwargs: dict[str, Any]) -> tuple[RpdLlmRole, str, Any]:
        raw_role = str(kwargs.pop("llm_role", "rpd_prose"))
        role = _validate_role(raw_role)
        prompt_version = str(kwargs.pop("prompt_version", ""))
        client = self._clients.get(role)
        if client is None:
            raise RuntimeError(f"RPD LLM role is unavailable: {role}")
        return role, prompt_version, client

    def _record(
        self,
        client: Any,
        role: RpdLlmRole,
        prompt_version: str,
        started_at: str,
        started: float,
        success: bool,
        error_type: str = "",
    ) -> None:
        usage = getattr(client, "_last_token_usage", None) or {}
        self._events.append(
            RpdLlmEvent(
                sequence=len(self._events) + 1,
                role=role,
                prompt_version=prompt_version,
                started_at=started_at,
                elapsed_ms=round((time.perf_counter() - started) * 1000),
                success=success,
                provider=str(getattr(client, "provider", "") or getattr(client, "_last_provider", "") or ""),
                model=str(getattr(client, "model", "") or ""),
                cache_hit=getattr(client, "_last_finish_reason", None) == "cache_hit",
                prompt_tokens=_optional_int(usage.get("prompt_tokens")),
                completion_tokens=_optional_int(usage.get("completion_tokens")),
                total_tokens=_optional_int(usage.get("total_tokens")),
                cost_usd=_optional_float(getattr(client, "_last_cost_usd", None)),
                error_type=error_type,
            )
        )


def create_rpd_llm_router() -> RpdLlmRouter | None:
    """Create isolated role clients; one failed role does not disable the others."""
    try:
        from content_factory.platform.llm.factory import create_llm_client
    except Exception:
        return None

    clients: dict[RpdLlmRole, Any] = {}
    for policy in _POLICIES:
        try:
            clients[policy.role] = create_llm_client(
                provider=os.getenv(f"{policy.env_prefix}_PROVIDER") or None,
                model=os.getenv(f"{policy.env_prefix}_MODEL") or None,
                default_role=policy.role,
                enable_cache=policy.enable_cache,
                max_retries=_env_int(f"{policy.env_prefix}_MAX_RETRIES", policy.max_retries),
                timeout_seconds=_env_float(f"{policy.env_prefix}_TIMEOUT_SECONDS", policy.timeout_seconds),
            )
        except Exception:
            continue
    return RpdLlmRouter(clients) if clients else None


def collect_llm_events(client: Any) -> list[RpdLlmEvent]:
    """Return telemetry when the supplied client supports it; legacy clients yield no events."""
    events = getattr(client, "events", [])
    return [RpdLlmEvent.model_validate(event) for event in events]


def _validate_role(value: str) -> RpdLlmRole:
    allowed = {policy.role for policy in _POLICIES}
    if value not in allowed:
        raise ValueError(f"Unknown RPD LLM role: {value}")
    return value


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


__all__ = ["RpdLlmEvent", "RpdLlmRole", "RpdLlmRouter", "collect_llm_events", "create_rpd_llm_router"]
