import pytest
from pydantic import BaseModel

from content_factory.generation.exceptions import LLMAPIError, LLMRateLimitError
from content_factory.platform.llm.factory import create_llm_client
from content_factory.platform.llm.gateway import LLMGateway, LLMUsageBudgetTracker
from content_factory.platform.llm.model_registry import ModelRegistry, ModelRoleConfig, ModelRoute


def _registry_with_openai_deepseek(*, budget_usd: float | None = None) -> ModelRegistry:
    return ModelRegistry(
        aliases={"enhancement_plan": "planner"},
        roles={
            "planner": ModelRoleConfig(
                budget_usd=budget_usd,
                fallback_chain=[
                    ModelRoute(
                        provider="openai",
                        model="gpt-test",
                        input_cost_per_1m=1000.0,
                        output_cost_per_1m=1000.0,
                    ),
                    ModelRoute(provider="deepseek", model="deepseek-chat"),
                ],
            )
        }
    )


def test_gateway_falls_back_to_next_configured_provider(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    gateway = LLMGateway(
        registry=_registry_with_openai_deepseek(),
        provider="openai",
        model="gpt-test",
        enable_cache=False,
        budget_tracker=LLMUsageBudgetTracker(),
    )
    calls: list[str] = []

    def fake_complete_route(**kwargs):
        route = kwargs["route"]
        calls.append(route.provider)
        if route.provider == "openai":
            raise RuntimeError("provider down")
        gateway._last_token_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        return "ok"

    monkeypatch.setattr(gateway, "_complete_route", fake_complete_route)

    assert gateway.complete(system="s", user="u", llm_role="planner") == "ok"
    assert calls == ["openai", "deepseek"]
    assert gateway.provider == "deepseek"
    assert gateway._last_route["fallback_errors"]


def test_gateway_strict_provider_does_not_fall_back_to_other_providers(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GIGACHAT_CREDENTIALS", raising=False)
    monkeypatch.delenv("GIGACHAT_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    gateway = LLMGateway(
        registry=_registry_with_openai_deepseek(),
        provider="gigachat",
        strict_provider=True,
        enable_cache=False,
        budget_tracker=LLMUsageBudgetTracker(),
    )

    with pytest.raises(LLMAPIError, match="GigaChat"):
        gateway.complete(system="s", user="u", llm_role="planner")

    assert gateway.provider is None


def test_gateway_strict_openai_quota_stops_before_same_provider_fallback(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    registry = ModelRegistry(
        roles={
            "planner": ModelRoleConfig(
                fallback_chain=[
                    ModelRoute(provider="openai", model="gpt-fallback"),
                    ModelRoute(provider="deepseek", model="deepseek-chat"),
                ],
            )
        }
    )
    gateway = LLMGateway(
        registry=registry,
        provider="openai",
        model="gpt-primary",
        strict_provider=True,
        enable_cache=False,
        budget_tracker=LLMUsageBudgetTracker(),
    )
    calls: list[str] = []

    def fake_complete_route(**kwargs):
        route = kwargs["route"]
        calls.append(route.resolved_model())
        raise RuntimeError("OpenAIException - You exceeded your current quota, please check billing details.")

    monkeypatch.setattr(gateway, "_complete_route", fake_complete_route)

    with pytest.raises(LLMRateLimitError, match="OpenAI недоступен"):
        gateway.complete(system="s", user="u", llm_role="planner")

    assert calls == ["gpt-primary"]


def test_factory_makes_explicit_provider_strict_by_default(monkeypatch) -> None:
    monkeypatch.setenv("LLM_BUDGET_DB_ENABLED", "false")

    client = create_llm_client(provider="gigachat", enable_cache=False, enable_batching=False)

    assert isinstance(client, LLMGateway)
    assert client.preferred_provider == "gigachat"
    assert client.strict_provider is True


def test_gateway_tracks_budget_by_user_run_and_role(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    tracker = LLMUsageBudgetTracker()
    gateway = LLMGateway(
        registry=_registry_with_openai_deepseek(budget_usd=0.001),
        provider="openai",
        model="gpt-test",
        enable_cache=False,
        budget_tracker=tracker,
        user_id="user-1",
        run_id="run-1",
    )

    def fake_complete_route(**_kwargs):
        gateway._last_token_usage = {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}
        gateway._last_cost_usd = 0.002
        return "ok"

    monkeypatch.setattr(gateway, "_complete_route", fake_complete_route)

    assert gateway.complete(system="s", user="u", llm_role="planner") == "ok"
    assert tracker.spent(user_id="user-1", run_id="run-1", role="planner") == pytest.approx(0.002)
    with pytest.raises(LLMAPIError, match="budget exceeded"):
        gateway.complete(system="s2", user="u2", llm_role="planner")


class StructuredGatewayPayload(BaseModel):
    title: str


def test_gateway_structured_output_uses_same_route_budget_and_node(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    tracker = LLMUsageBudgetTracker()
    gateway = LLMGateway(
        registry=_registry_with_openai_deepseek(budget_usd=0.001),
        provider="openai",
        model="gpt-test",
        enable_cache=False,
        budget_tracker=tracker,
        user_id="user-1",
        run_id="run-1",
    )

    def fake_structured_route(**kwargs):
        assert kwargs["output_model"] is StructuredGatewayPayload
        gateway._last_token_usage = {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2}
        gateway._last_cost_usd = 0.002
        return StructuredGatewayPayload(title="ok")

    monkeypatch.setattr(gateway, "_complete_structured_route", fake_structured_route)

    result = gateway.complete_structured(
        output_model=StructuredGatewayPayload,
        system="s",
        user="u",
        llm_role="enhancement_plan",
    )

    assert result.title == "ok"
    assert gateway._last_route["node"] == "enhancement_plan"
    assert gateway._last_route["structured_schema"] == "StructuredGatewayPayload"
    assert tracker.spent(user_id="user-1", run_id="run-1", node="enhancement_plan") == pytest.approx(0.002)


def test_gateway_passes_gigachat_ssl_verify_from_env(monkeypatch) -> None:
    monkeypatch.setenv("GIGACHAT_API_KEY", "gigachat-key")
    monkeypatch.setenv("GIGACHAT_VERIFY_SSL_CERTS", "false")
    gateway = LLMGateway(enable_cache=False)
    route = ModelRoute(provider="gigachat", model="GigaChat-Pro")

    request_kwargs = gateway._litellm_request_kwargs(route, {})

    assert request_kwargs["api_key"] == "gigachat-key"
    assert request_kwargs["ssl_verify"] is False


def test_gateway_prefers_gigachat_ca_bundle_file(monkeypatch) -> None:
    monkeypatch.setenv("GIGACHAT_API_KEY", "gigachat-key")
    monkeypatch.setenv("GIGACHAT_VERIFY_SSL_CERTS", "true")
    monkeypatch.setenv("GIGACHAT_CA_BUNDLE_FILE", "/opt/content-generator/certs/russian-root.pem")
    gateway = LLMGateway(enable_cache=False)
    route = ModelRoute(provider="gigachat", model="GigaChat-Pro")

    request_kwargs = gateway._litellm_request_kwargs(route, {})

    assert request_kwargs["ssl_verify"] == "/opt/content-generator/certs/russian-root.pem"


def test_gateway_keeps_ssl_verify_out_of_non_gigachat_routes(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("GIGACHAT_VERIFY_SSL_CERTS", "false")
    gateway = LLMGateway(enable_cache=False)
    route = ModelRoute(provider="openai", model="gpt-test")

    request_kwargs = gateway._litellm_request_kwargs(route, {"ssl_verify": False})

    assert request_kwargs["api_key"] == "openai-key"
    assert "ssl_verify" not in request_kwargs


def test_gateway_passes_polza_credentials_base_url_and_temperature(monkeypatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    monkeypatch.setenv("POLZA_AI_BASE_URL", "https://polza.example/api/v1")
    monkeypatch.setenv("POLZA_AI_TEMPERATURE", "0.2")
    gateway = LLMGateway(enable_cache=False)
    route = ModelRoute(provider="polza", model="openai/gpt-5.4-mini")

    request_kwargs = gateway._litellm_request_kwargs(route, {})

    assert request_kwargs["api_key"] == "polza-key"
    assert request_kwargs["api_base"] == "https://polza.example/api/v1"
    assert request_kwargs["temperature"] == 0.2
    assert "extra_headers" not in request_kwargs
    assert route.litellm_name() == "openai/openai/gpt-5.4-mini"
