"""Observability wrapper for LLM clients."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from content_gen.observability import FallbackTraceEvent, LLMCallTraceEvent, LLMTraceRecorder, TokenUsage


TRACE_ONLY_KWARGS = {
    "trace_node",
    "trace_agent",
    "prompt_version",
    "repair_attempts",
    "prompt_id",
    "prompt_hash",
    "prompt_owner",
    "prompt_input_schema",
    "prompt_output_schema",
    "prompt_source",
}


class ObservedLLMClient:
    """Proxy that records every LLM call while preserving the wrapped client API."""

    def __init__(
        self,
        inner: Any,
        recorder: LLMTraceRecorder,
        *,
        node: str = "generation",
        agent: str = "unknown",
        prompt_version: str | None = None,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._node = node
        self._agent = agent
        self._prompt_version = prompt_version

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def model(self) -> str | None:
        return getattr(self._inner, "model", None)

    def scoped(
        self,
        *,
        node: str | None = None,
        agent: str | None = None,
        prompt_version: str | None = None,
    ) -> "ObservedLLMClient":
        """Return a view with more specific trace metadata."""
        return ObservedLLMClient(
            self._inner,
            self._recorder,
            node=node or self._node,
            agent=agent or self._agent,
            prompt_version=prompt_version or self._prompt_version,
        )

    def complete(
        self,
        system: str,
        user: str,
        response_format: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Call wrapped client and record latency/status/schema metadata."""
        started = time.perf_counter()
        call_kwargs, trace_kwargs = _split_trace_kwargs(kwargs)
        if getattr(self._inner, "supports_llm_roles", False):
            call_kwargs.setdefault("llm_role", self._node)
        try:
            response = self._inner.complete(system=system, user=user, response_format=response_format, **call_kwargs)
        except Exception as exc:
            self._record(
                system=system,
                user=user,
                response_format=response_format,
                started=started,
                status="error",
                error=str(exc),
                kwargs={**call_kwargs, **trace_kwargs},
            )
            raise
        self._record(
            system=system,
            user=user,
            response_format=response_format,
            started=started,
            status="success",
            error=None,
            kwargs={**call_kwargs, **trace_kwargs},
        )
        return response

    def complete_structured(
        self,
        *,
        output_model: type[BaseModel],
        system: str,
        user: str,
        retries: int | None = None,
        **kwargs: Any,
    ) -> BaseModel:
        """Call wrapped structured-output client and record schema validation metadata."""
        structured_call = getattr(self._inner, "complete_structured", None)
        if not callable(structured_call):
            raise AttributeError("Wrapped LLM client does not support complete_structured")

        started = time.perf_counter()
        call_kwargs, trace_kwargs = _split_trace_kwargs(kwargs)
        if getattr(self._inner, "supports_llm_roles", False):
            call_kwargs.setdefault("llm_role", self._node)
        try:
            response = structured_call(
                output_model=output_model,
                system=system,
                user=user,
                retries=retries,
                **call_kwargs,
            )
        except Exception as exc:
            self._record(
                system=system,
                user=user,
                response_format={"type": "response_model", "json_schema": {"name": output_model.__name__}},
                started=started,
                status="error",
                error=str(exc),
                kwargs={**call_kwargs, **trace_kwargs},
            )
            raise
        self._record(
            system=system,
            user=user,
            response_format={"type": "response_model", "json_schema": {"name": output_model.__name__}},
            started=started,
            status="success",
            error=None,
            kwargs={**call_kwargs, **trace_kwargs},
        )
        return response

    def complete_batch(
        self,
        requests: list[tuple[str, str, str | dict[str, Any] | None, dict[str, Any]]],
    ) -> list[str]:
        """Run a batch through the wrapped client and record one trace per item."""
        routed_requests = []
        trace_requests: list[tuple[str, str, str | dict[str, Any] | None, dict[str, Any]]] = []
        for system, user, response_format, kwargs in requests:
            routed_kwargs, trace_kwargs = _split_trace_kwargs(dict(kwargs or {}))
            if getattr(self._inner, "supports_llm_roles", False):
                routed_kwargs.setdefault("llm_role", self._node)
            routed_requests.append((system, user, response_format, routed_kwargs))
            trace_requests.append((system, user, response_format, {**routed_kwargs, **trace_kwargs}))
        if not hasattr(self._inner, "complete_batch"):
            return [
                self.complete(system=system, user=user, response_format=response_format, **(kwargs or {}))
                for system, user, response_format, kwargs in trace_requests
            ]
        started = time.perf_counter()
        try:
            results = self._inner.complete_batch(routed_requests)
        except Exception as exc:
            for system, user, response_format, kwargs in trace_requests:
                self._record(
                    system=system,
                    user=user,
                    response_format=response_format,
                    started=started,
                    status="error",
                    error=str(exc),
                    kwargs=dict(kwargs or {}),
                    include_last_usage=False,
                )
            raise
        for system, user, response_format, kwargs in trace_requests:
            self._record(
                system=system,
                user=user,
                response_format=response_format,
                started=started,
                status="success",
                error=None,
                kwargs=dict(kwargs or {}),
                include_last_usage=False,
            )
        return results

    def _record(
        self,
        *,
        system: str,
        user: str,
        response_format: Any,
        started: float,
        status: str,
        error: str | None,
        kwargs: dict[str, Any],
        include_last_usage: bool = True,
    ) -> None:
        latency_ms = (time.perf_counter() - started) * 1000
        prompt_metadata = {
            key: kwargs.pop(key)
            for key in (
                "prompt_id",
                "prompt_hash",
                "prompt_owner",
                "prompt_input_schema",
                "prompt_output_schema",
                "prompt_source",
            )
            if key in kwargs and kwargs.get(key) is not None
        }
        node = str(kwargs.pop("trace_node", self._node) or self._node)
        agent = str(kwargs.pop("trace_agent", self._agent) or self._agent)
        event = LLMCallTraceEvent.from_llm_call(
            node=node,
            agent=agent,
            system=system,
            user=user,
            response_format=response_format,
            model=self.model,
            latency_ms=latency_ms,
            status=status,
            error=error,
            prompt_version=str(kwargs.pop("prompt_version", self._prompt_version) or self._prompt_version or "unversioned"),
            repair_attempts=int(kwargs.pop("repair_attempts", 0) or 0),
            tokens=self._token_usage_snapshot() if include_last_usage and status == "success" else None,
            metadata={
                "finish_reason": getattr(self._inner, "_last_finish_reason", None)
                if include_last_usage and status == "success"
                else None,
                "provider": getattr(self._inner, "_last_provider", None) or getattr(self._inner, "provider", None),
                "route": getattr(self._inner, "_last_route", None),
                "cost_usd": getattr(self._inner, "_last_cost_usd", None),
                "budget_spent_usd": getattr(self._inner, "_last_budget_spent_usd", None),
                "llm_role": kwargs.get("llm_role"),
                "batch": not include_last_usage,
                **prompt_metadata,
            },
        )
        self._recorder.append(event)
        if status == "success":
            self._record_provider_route_fallback(node=node, system=system, user=user)

    def _record_provider_route_fallback(self, *, node: str, system: str, user: str) -> None:
        """Record provider/model route fallback when the gateway succeeded on a later route."""
        route = getattr(self._inner, "_last_route", None) or {}
        fallback_errors = list(route.get("fallback_errors") or [])
        if not fallback_errors or self._recorder.sink is None:
            return
        self._recorder.sink.record_fallback_trace(
            FallbackTraceEvent.from_fallback(
                node=node,
                fallback_type="llm_provider_route_fallback",
                reason="; ".join(str(error) for error in fallback_errors),
                quality_risk="low",
                visible_to_user=False,
                inputs={"system": system, "user": user, "route": route},
                trace={
                    "selected_provider": route.get("provider"),
                    "selected_model": route.get("model"),
                    "failed_routes": fallback_errors,
                },
                metadata={"role": route.get("role"), "agent": self._agent},
            )
        )

    def _token_usage_snapshot(self) -> TokenUsage | None:
        """Read token usage from clients that expose provider usage on the last call."""
        usage = getattr(self._inner, "_last_token_usage", None)
        if usage is None:
            return None
        if isinstance(usage, TokenUsage):
            return usage
        if isinstance(usage, dict):
            return TokenUsage.model_validate(usage)
        return TokenUsage(
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
        )


def _split_trace_kwargs(kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate provider kwargs from observability-only prompt metadata."""
    provider_kwargs: dict[str, Any] = {}
    trace_kwargs: dict[str, Any] = {}
    for key, value in dict(kwargs).items():
        if key in TRACE_ONLY_KWARGS:
            trace_kwargs[key] = value
        else:
            provider_kwargs[key] = value
    return provider_kwargs, trace_kwargs
