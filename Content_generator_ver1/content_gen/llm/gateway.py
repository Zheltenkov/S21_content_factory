"""Role-aware LLM gateway with provider fallback, budget and rate controls."""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib
import json
import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, TypeVar

from pydantic import BaseModel

from ..exceptions import LLMAPIError, LLMRateLimitError, LLMTimeoutError
from .model_registry import ModelRegistry, ModelRoute

T = TypeVar("T", bound=BaseModel)


@dataclass
class _CacheEntry:
    response: str
    timestamp: float
    ttl: float


class LLMUsageBudgetTracker:
    """In-memory spend tracker keyed by user, run and node role."""

    def __init__(self) -> None:
        self._spend: dict[tuple[str, str, str], float] = {}
        self._lock = Lock()

    def spent(self, *, user_id: str, run_id: str, node: str | None = None, role: str | None = None) -> float:
        """Return current tracked spend for a user/run/role tuple."""
        bucket = str(node or role or "default").strip().lower() or "default"
        with self._lock:
            return self._spend.get((user_id, run_id, bucket), 0.0)

    def assert_within_budget(
        self,
        *,
        user_id: str,
        run_id: str,
        node: str | None = None,
        role: str | None = None,
        budget_usd: float | None,
    ) -> None:
        """Fail before another model call when the configured budget is already spent."""
        if budget_usd is None:
            return
        bucket = str(node or role or "default").strip().lower() or "default"
        current = self.spent(user_id=user_id, run_id=run_id, node=bucket)
        if current >= budget_usd:
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
        cost_usd: float | None,
        **_kwargs: Any,
    ) -> None:
        """Record estimated or provider-reported cost for a completed call."""
        if cost_usd is None or cost_usd <= 0:
            return
        bucket = str(node or role or "default").strip().lower() or "default"
        key = (user_id, run_id, bucket)
        with self._lock:
            self._spend[key] = self._spend.get(key, 0.0) + cost_usd


_GLOBAL_BUDGET_TRACKER = LLMUsageBudgetTracker()


class _RateLimiter:
    """Simple process-local RPM limiter for provider/model/role routes."""

    def __init__(self) -> None:
        self._last_call: dict[tuple[str, str, str], float] = {}
        self._lock = Lock()

    def wait(self, *, role: str, route: ModelRoute, rpm: int | None) -> None:
        """Sleep if the route would exceed its configured requests-per-minute."""
        if not rpm or rpm <= 0:
            return
        min_interval = 60.0 / float(rpm)
        key = (role, route.provider, route.resolved_model())
        with self._lock:
            now = time.monotonic()
            previous = self._last_call.get(key)
            if previous is not None:
                delay = min_interval - (now - previous)
                if delay > 0:
                    time.sleep(delay)
                    now = time.monotonic()
            self._last_call[key] = now


class LLMGateway:
    """Unified generation-time LLM entrypoint.

    The gateway preserves the old ``complete(system, user, response_format, **kwargs)``
    API, but it routes calls by pipeline role through a registry-backed fallback
    chain. Providers are executed through LiteLLM; route fallback is handled here,
    provider-specific transports are not duplicated in this codebase.
    """

    supports_llm_roles = True

    def __init__(
        self,
        *,
        registry: ModelRegistry | None = None,
        model: str | None = None,
        provider: str | None = None,
        strict_provider: bool = False,
        default_role: str = "default",
        enable_cache: bool | None = None,
        enable_batching: bool | None = None,
        cache_ttl: int | None = None,
        max_cache_size: int | None = None,
        max_retries: int | None = None,
        retry_delay: float | None = None,
        timeout_seconds: float | None = None,
        budget_tracker: LLMUsageBudgetTracker | None = None,
        user_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.registry = registry or ModelRegistry.load()
        self.default_role = default_role
        self.preferred_provider = provider
        self.preferred_model = model
        self.strict_provider = strict_provider
        self.enable_cache = enable_cache if enable_cache is not None else (
            os.getenv("LLM_CACHE_ENABLED", "true").lower() == "true"
        )
        self.enable_batching = enable_batching if enable_batching is not None else (
            os.getenv("LLM_BATCHING_ENABLED", "true").lower() == "true"
        )
        self._cache_ttl = cache_ttl or int(os.getenv("LLM_CACHE_TTL", "3600"))
        self._max_cache_size = max_cache_size or int(os.getenv("LLM_CACHE_MAX_SIZE", "10000"))
        self._max_retries = max_retries if max_retries is not None else self._env_int("LLM_MAX_RETRIES", None)
        self._retry_delay = retry_delay if retry_delay is not None else self._env_float("LLM_RETRY_DELAY", None)
        self._timeout_seconds = (
            timeout_seconds if timeout_seconds is not None else self._env_float("LLM_TIMEOUT_SECONDS", None)
        )
        self._cache: dict[str, _CacheEntry] = {}
        self._cache_lock = Lock()
        self._rate_limiter = _RateLimiter()
        self._budget_tracker = budget_tracker or _GLOBAL_BUDGET_TRACKER
        self._default_user_id = user_id or "anonymous"
        self._default_run_id = run_id or "adhoc"

        self.provider: str | None = None
        self.model: str | None = model
        self.temperature: float | None = None
        self._last_finish_reason: str | None = None
        self._last_token_usage: dict[str, int | None] | None = None
        self._last_provider: str | None = None
        self._last_route: dict[str, Any] | None = None
        self._last_cost_usd: float | None = None
        self._last_budget_spent_usd: float | None = None

    def configure_run_context(self, *, user_id: str | None = None, run_id: str | None = None) -> None:
        """Attach request identity for budget and trace attribution."""
        if user_id:
            self._default_user_id = str(user_id)
        if run_id:
            self._default_run_id = str(run_id)

    def complete(
        self,
        system: str,
        user: str,
        response_format: str | dict[str, Any] | None = None,
        use_cache: bool = True,
        **kwargs: Any,
    ) -> str:
        """Execute one LLM request through a role-specific fallback chain."""
        node = str(kwargs.pop("llm_role", self.default_role) or self.default_role).strip().lower() or self.default_role
        role = self.registry.canonical_role(node)
        kwargs.pop("llm_agent", None)
        for metadata_key in (
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
        ):
            kwargs.pop(metadata_key, None)
        user_id = str(kwargs.pop("llm_user_id", self._default_user_id) or "anonymous")
        run_id = str(kwargs.pop("llm_run_id", self._default_run_id) or "adhoc")
        role_config = self.registry.role_config(role)
        routes = self.registry.chain_for_role(
            role,
            preferred_provider=self.preferred_provider,
            preferred_model=self.preferred_model,
            strict_provider=self.strict_provider,
        )
        if not routes:
            if self.strict_provider and self.preferred_provider:
                raise LLMAPIError(self._selected_provider_not_configured_message(self.preferred_provider))
            raise LLMAPIError(f"No configured LLM routes for role='{role}'")

        cache_key = self._cache_key(role, system, user, response_format, kwargs)
        if self.enable_cache and use_cache:
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                self._last_finish_reason = "cache_hit"
                self._last_token_usage = None
                self._last_cost_usd = 0.0
                return cached

        budget = self._env_float("LLM_BUDGET_USD_PER_ROLE", role_config.budget_usd)
        self._budget_tracker.assert_within_budget(
            user_id=user_id,
            run_id=run_id,
            node=node,
            role=role,
            budget_usd=budget,
        )

        errors: list[str] = []
        for route in routes:
            try:
                self._last_cost_usd = None
                self._last_token_usage = None
                self._last_finish_reason = None
                self._rate_limiter.wait(
                    role=role,
                    route=route,
                    rpm=route.rate_limit_rpm or role_config.rate_limit_rpm or self.registry.defaults.rate_limit_rpm,
                )
                response = self._complete_route(
                    route=route,
                    role=role,
                    role_config=role_config,
                    system=system,
                    user=user,
                    response_format=response_format,
                    kwargs=kwargs,
                )
                self.provider = route.provider
                self.model = route.resolved_model()
                self._last_provider = route.provider
                self._last_route = {
                    "node": node,
                    "role": role,
                    "provider": route.provider,
                    "model": route.resolved_model(),
                    "fallback_errors": list(errors),
                }
                cost = self._estimate_cost(route)
                self._last_cost_usd = cost
                usage = self._last_token_usage or {}
                self._budget_tracker.record(
                    user_id=user_id,
                    run_id=run_id,
                    node=node,
                    role=role,
                    provider=route.provider,
                    model=route.resolved_model(),
                    cost_usd=cost,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    route=self._last_route,
                )
                self._last_budget_spent_usd = self._budget_tracker.spent(
                    user_id=user_id,
                    run_id=run_id,
                    node=node,
                    role=role,
                )
                if self.enable_cache and use_cache:
                    self._save_to_cache(cache_key, response)
                return response
            except Exception as exc:  # noqa: BLE001 - route fallback needs provider-agnostic errors
                if self.strict_provider and self._is_account_quota_error(str(exc)):
                    raise LLMRateLimitError(self._provider_quota_message(route.provider)) from exc
                errors.append(f"{route.provider}/{route.resolved_model()}: {exc}")
                continue

        message = "; ".join(errors) or "unknown provider error"
        if "timeout" in message.lower() or "timed out" in message.lower():
            raise LLMTimeoutError(f"All LLM routes timed out for role='{role}': {message}")
        if self._is_account_quota_error(message):
            raise LLMRateLimitError(self._provider_quota_message_from_route_errors(message))
        if self._is_rate_limit_error(message):
            raise LLMRateLimitError(f"Провайдер LLM временно ограничил частоту запросов для role='{role}'.")
        raise LLMAPIError(f"All LLM routes failed for role='{role}': {message}")

    def complete_structured(
        self,
        *,
        output_model: type[T],
        system: str,
        user: str,
        retries: int | None = None,
        **kwargs: Any,
    ) -> T:
        """Execute one structured-output request through Instructor and route fallback."""
        node = str(kwargs.pop("llm_role", self.default_role) or self.default_role).strip().lower() or self.default_role
        role = self.registry.canonical_role(node)
        kwargs.pop("llm_agent", None)
        for metadata_key in (
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
        ):
            kwargs.pop(metadata_key, None)
        user_id = str(kwargs.pop("llm_user_id", self._default_user_id) or "anonymous")
        run_id = str(kwargs.pop("llm_run_id", self._default_run_id) or "adhoc")
        kwargs.pop("_structured_use_schema", None)

        role_config = self.registry.role_config(role)
        routes = self.registry.chain_for_role(
            role,
            preferred_provider=self.preferred_provider,
            preferred_model=self.preferred_model,
            strict_provider=self.strict_provider,
        )
        if not routes:
            if self.strict_provider and self.preferred_provider:
                raise LLMAPIError(self._selected_provider_not_configured_message(self.preferred_provider))
            raise LLMAPIError(f"No configured LLM routes for role='{role}'")

        budget = self._env_float("LLM_BUDGET_USD_PER_ROLE", role_config.budget_usd)
        self._budget_tracker.assert_within_budget(
            user_id=user_id,
            run_id=run_id,
            node=node,
            role=role,
            budget_usd=budget,
        )

        errors: list[str] = []
        for route in routes:
            try:
                self._last_cost_usd = None
                self._last_token_usage = None
                self._last_finish_reason = None
                self._rate_limiter.wait(
                    role=role,
                    route=route,
                    rpm=route.rate_limit_rpm or role_config.rate_limit_rpm or self.registry.defaults.rate_limit_rpm,
                )
                result = self._complete_structured_route(
                    route=route,
                    role_config=role_config,
                    system=system,
                    user=user,
                    output_model=output_model,
                    retries=retries,
                    kwargs=kwargs,
                )
                self.provider = route.provider
                self.model = route.resolved_model()
                self._last_provider = route.provider
                self._last_route = {
                    "node": node,
                    "role": role,
                    "provider": route.provider,
                    "model": route.resolved_model(),
                    "structured_schema": output_model.__name__,
                    "fallback_errors": list(errors),
                }
                cost = self._estimate_cost(route)
                self._last_cost_usd = cost
                usage = self._last_token_usage or {}
                self._budget_tracker.record(
                    user_id=user_id,
                    run_id=run_id,
                    node=node,
                    role=role,
                    provider=route.provider,
                    model=route.resolved_model(),
                    cost_usd=cost,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    route=self._last_route,
                )
                self._last_budget_spent_usd = self._budget_tracker.spent(
                    user_id=user_id,
                    run_id=run_id,
                    node=node,
                    role=role,
                )
                return result
            except Exception as exc:  # noqa: BLE001 - route fallback needs provider-agnostic errors
                if self.strict_provider and self._is_account_quota_error(str(exc)):
                    raise LLMRateLimitError(self._provider_quota_message(route.provider)) from exc
                errors.append(f"{route.provider}/{route.resolved_model()}: {exc}")
                continue

        message = "; ".join(errors) or "unknown provider error"
        if "timeout" in message.lower() or "timed out" in message.lower():
            raise LLMTimeoutError(f"All structured LLM routes timed out for role='{role}': {message}")
        if self._is_account_quota_error(message):
            raise LLMRateLimitError(self._provider_quota_message_from_route_errors(message))
        if self._is_rate_limit_error(message):
            raise LLMRateLimitError(f"Провайдер LLM временно ограничил частоту запросов для role='{role}'.")
        raise LLMAPIError(f"All structured LLM routes failed for role='{role}': {message}")

    def complete_batch(
        self,
        requests: list[tuple[str, str, str | dict[str, Any] | None, dict[str, Any]]],
    ) -> list[str]:
        """Execute independent requests with bounded parallelism."""
        if not self.enable_batching:
            return [
                self.complete(system=system, user=user, response_format=response_format, **(kwargs or {}))
                for system, user, response_format, kwargs in requests
            ]

        results = [""] * len(requests)
        max_workers = min(len(requests), int(os.getenv("LLM_BATCH_MAX_WORKERS", "10")))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: dict[concurrent.futures.Future[str], int] = {}
            for index, (system, user, response_format, kwargs) in enumerate(requests):
                futures[
                    executor.submit(
                        self.complete,
                        system=system,
                        user=user,
                        response_format=response_format,
                        **(kwargs or {}),
                    )
                ] = index
            for future in concurrent.futures.as_completed(futures):
                index = futures[future]
                try:
                    results[index] = future.result()
                except Exception:
                    results[index] = ""
        return results

    def _complete_route(
        self,
        *,
        route: ModelRoute,
        role: str,
        role_config: Any,
        system: str,
        user: str,
        response_format: str | dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> str:
        """Call one route through LiteLLM."""
        return self._complete_with_litellm(route, role_config, system, user, response_format, kwargs)

    def _complete_with_litellm(
        self,
        route: ModelRoute,
        role_config: Any,
        system: str,
        user: str,
        response_format: str | dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> str:
        """Call OpenAI-compatible providers through LiteLLM."""
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        litellm = importlib.import_module("litellm")
        request_kwargs = self._litellm_request_kwargs(route, kwargs)
        if response_format == "json_object":
            request_kwargs["response_format"] = {"type": "json_object"}
        elif isinstance(response_format, dict):
            request_kwargs["response_format"] = response_format

        timeout = route.timeout_seconds or role_config.timeout_seconds or self._timeout_seconds or self.registry.defaults.timeout_seconds
        max_retries = route.max_retries or role_config.max_retries or self._max_retries or self.registry.defaults.max_retries
        response = litellm.completion(
            model=route.litellm_name(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            timeout=timeout,
            num_retries=max_retries,
            **request_kwargs,
        )
        self._capture_litellm_metadata(response)
        return self._extract_content(response)

    def _litellm_request_kwargs(self, route: ModelRoute, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Build provider credentials and sampling kwargs for LiteLLM calls."""
        request_kwargs = dict(kwargs)
        request_kwargs.setdefault(
            "temperature",
            route.temperature if route.temperature is not None else self._provider_temperature(route.provider),
        )
        request_kwargs = {key: value for key, value in request_kwargs.items() if value is not None}

        if route.provider == "gigachat":
            ssl_verify = self._provider_ssl_verify(route.provider)
            if ssl_verify is not None:
                request_kwargs.setdefault("ssl_verify", ssl_verify)
        else:
            request_kwargs.pop("ssl_verify", None)

        api_key = route.resolved_api_key()
        api_base = route.resolved_base_url()
        if api_key:
            request_kwargs["api_key"] = api_key
        if api_base:
            request_kwargs["api_base"] = api_base
        if route.provider == "azure":
            api_version = os.getenv("AZURE_OPENAI_API_VERSION") or os.getenv("AZURE_API_VERSION")
            if api_version:
                request_kwargs["api_version"] = api_version
        return request_kwargs

    def _complete_structured_route(
        self,
        *,
        route: ModelRoute,
        role_config: Any,
        system: str,
        user: str,
        output_model: type[T],
        retries: int | None,
        kwargs: dict[str, Any],
    ) -> T:
        """Call one route with Instructor over LiteLLM."""
        return self._complete_structured_with_instructor(
            route=route,
            role_config=role_config,
            system=system,
            user=user,
            output_model=output_model,
            retries=retries,
            kwargs=kwargs,
        )

    def _complete_structured_with_instructor(
        self,
        *,
        route: ModelRoute,
        role_config: Any,
        system: str,
        user: str,
        output_model: type[T],
        retries: int | None,
        kwargs: dict[str, Any],
    ) -> T:
        """Use Instructor over LiteLLM so response_model validation owns retries."""
        os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
        litellm = importlib.import_module("litellm")
        instructor = importlib.import_module("instructor")

        request_kwargs = self._litellm_request_kwargs(route, kwargs)
        timeout = route.timeout_seconds or role_config.timeout_seconds or self._timeout_seconds or self.registry.defaults.timeout_seconds
        max_retries = retries or route.max_retries or role_config.max_retries or self._max_retries or self.registry.defaults.max_retries
        mode = getattr(getattr(instructor, "Mode"), "JSON")
        client = instructor.from_litellm(litellm.completion, mode=mode)
        response, raw_completion = client.create_with_completion(
            model=route.litellm_name(),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_model=output_model,
            max_retries=max_retries,
            timeout=timeout,
            **request_kwargs,
        )
        self._capture_litellm_metadata(raw_completion)
        return response

    def _capture_litellm_metadata(self, response: Any) -> None:
        """Store finish reason, usage and provider-reported cost from a LiteLLM response."""
        choice = self._first_choice(response)
        self._last_finish_reason = self._value(choice, "finish_reason")
        usage = self._value(response, "usage") or {}
        self._last_token_usage = {
            "prompt_tokens": self._value(usage, "prompt_tokens"),
            "completion_tokens": self._value(usage, "completion_tokens"),
            "total_tokens": self._value(usage, "total_tokens"),
        } if usage else None
        hidden = self._value(response, "_hidden_params") or {}
        cost = self._value(hidden, "response_cost")
        self._last_cost_usd = float(cost) if cost is not None else None

    def _extract_content(self, response: Any) -> str:
        """Extract assistant content from OpenAI-compatible response shapes."""
        choice = self._first_choice(response)
        message = self._value(choice, "message") or {}
        content = self._value(message, "content")
        return str(content or "").strip()

    def _estimate_cost(self, route: ModelRoute) -> float | None:
        """Estimate call cost from usage when the provider did not report it."""
        if self._last_cost_usd is not None:
            return self._last_cost_usd
        usage = self._last_token_usage or {}
        input_cost = route.input_cost_per_1m
        output_cost = route.output_cost_per_1m
        if input_cost is None and output_cost is None:
            return None
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        return (prompt_tokens * float(input_cost or 0) + completion_tokens * float(output_cost or 0)) / 1_000_000

    def _cache_key(self, role: str, system: str, user: str, response_format: Any, kwargs: dict[str, Any]) -> str:
        """Build a stable cache key that includes role-specific routing."""
        payload = {
            "role": role,
            "system": system,
            "user": user,
            "response_format": response_format,
            "provider": self.preferred_provider,
            "model": self.preferred_model,
            "strict_provider": self.strict_provider,
            "kwargs": kwargs,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _get_from_cache(self, key: str) -> str | None:
        with self._cache_lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            if time.time() - entry.timestamp < entry.ttl:
                return entry.response
            del self._cache[key]
            return None

    def _save_to_cache(self, key: str, response: str) -> None:
        with self._cache_lock:
            if len(self._cache) >= self._max_cache_size:
                oldest = sorted(self._cache.items(), key=lambda item: item[1].timestamp)
                for stale_key, _ in oldest[: max(1, len(oldest) // 10)]:
                    del self._cache[stale_key]
            self._cache[key] = _CacheEntry(response=response, timestamp=time.time(), ttl=self._cache_ttl)

    @staticmethod
    def _value(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def _first_choice(self, response: Any) -> Any:
        choices = self._value(response, "choices") or []
        return choices[0] if choices else {}

    @staticmethod
    def _env_float(name: str, default: float | None) -> float | None:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        return float(raw)

    @staticmethod
    def _env_int(name: str, default: int | None) -> int | None:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            return default
        return int(raw)

    @staticmethod
    def _provider_temperature(provider: str) -> float | None:
        if provider == "polza":
            raw = os.getenv("POLZA_AI_TEMPERATURE", "").strip() or os.getenv("POLZA_TEMPERATURE", "").strip()
            return float(raw) if raw else None
        env_by_provider = {
            "openai": "OPENAI_TEMPERATURE",
            "deepseek": "DEEPSEEK_TEMPERATURE",
            "azure": "AZURE_OPENAI_TEMPERATURE",
            "gigachat": "GIGACHAT_TEMPERATURE",
        }
        raw = os.getenv(env_by_provider.get(provider, ""), "").strip()
        return float(raw) if raw else None

    @staticmethod
    def _selected_provider_not_configured_message(provider: str) -> str:
        normalized = str(provider or "").strip().lower()
        if normalized in {"giga", "gigachat"}:
            return (
                "Выбран GigaChat, но на сервере не настроены учетные данные. "
                "Добавьте GIGACHAT_CREDENTIALS или GIGACHAT_API_KEY в .env."
            )
        if normalized == "deepseek":
            return "Выбран DeepSeek, но на сервере не настроен DEEPSEEK_API_KEY в .env."
        if normalized in {"polza", "polza_ai", "openrouter", "open_router", "gpt"}:
            return "Выбран Polza AI, но на сервере не настроен POLZA_AI_API_KEY в .env."
        if normalized == "openai":
            return "Выбран OpenAI, но на сервере не настроен OPENAI_API_KEY в .env."
        return f"Выбранный LLM provider '{provider}' не настроен на сервере."

    @staticmethod
    def _is_account_quota_error(message: str) -> bool:
        lower = message.lower()
        return any(
            marker in lower
            for marker in (
                "exceeded your current quota",
                "insufficient_quota",
                "billing details",
                "quota exceeded",
                "you exceeded your current quota",
            )
        )

    @staticmethod
    def _is_rate_limit_error(message: str) -> bool:
        lower = message.lower()
        return "rate limit" in lower or "ratelimiterror" in lower or "429" in lower

    @staticmethod
    def _provider_quota_message(provider: str) -> str:
        normalized = str(provider or "").strip().lower()
        if normalized == "openai":
            return (
                "OpenAI недоступен: исчерпана квота или не настроен billing для текущего API ключа. "
                "Пополните баланс, обновите OPENAI_API_KEY или выберите другой ИИ-провайдер, например GigaChat."
            )
        if normalized in {"polza", "polza_ai", "openrouter", "open_router", "gpt"}:
            return (
                "Polza AI недоступен: исчерпана квота, не оплачен баланс или отклонен текущий API ключ. "
                "Проверьте POLZA_AI_API_KEY и баланс Polza AI."
            )
        if normalized in {"giga", "gigachat"}:
            return (
                "GigaChat недоступен: исчерпана квота или отклонена авторизация текущих учетных данных. "
                "Проверьте GIGACHAT_API_KEY/GIGACHAT_CREDENTIALS или выберите другой ИИ-провайдер."
            )
        if normalized == "deepseek":
            return (
                "DeepSeek недоступен: исчерпана квота или отклонена авторизация текущего API ключа. "
                "Проверьте DEEPSEEK_API_KEY или выберите другой ИИ-провайдер."
            )
        return "Выбранный LLM-провайдер недоступен из-за квоты или billing-ограничения."

    @classmethod
    def _provider_quota_message_from_route_errors(cls, message: str) -> str:
        lower = message.lower()
        if "polza/" in lower or "polza" in lower or "openrouter/" in lower or "openrouter" in lower:
            return cls._provider_quota_message("polza")
        if "openai/" in lower or "openai" in lower:
            return cls._provider_quota_message("openai")
        if "gigachat/" in lower or "gigachat" in lower:
            return cls._provider_quota_message("gigachat")
        if "deepseek/" in lower or "deepseek" in lower:
            return cls._provider_quota_message("deepseek")
        return cls._provider_quota_message("")

    @staticmethod
    def _provider_ssl_verify(provider: str) -> bool | str | None:
        """Resolve provider-specific TLS verification config for LiteLLM.

        GigaChat installations often require either a custom CA bundle or an
        explicit opt-out in local/dev environments with corporate TLS
        interception. The value intentionally stays provider-scoped because
        OpenAI-compatible clients may reject an unknown ``ssl_verify`` argument.
        """
        if provider != "gigachat":
            return None
        ca_bundle_file = os.getenv("GIGACHAT_CA_BUNDLE_FILE", "").strip()
        if ca_bundle_file:
            return ca_bundle_file
        raw = os.getenv("GIGACHAT_VERIFY_SSL_CERTS")
        if raw is None or raw.strip() == "":
            return None
        value = raw.strip()
        normalized = value.lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        return value
