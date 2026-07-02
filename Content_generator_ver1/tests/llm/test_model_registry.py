from content_gen.llm.model_registry import (
    ModelRegistry,
    ModelRoleConfig,
    ModelRoute,
    get_llm_provider_summary,
    normalize_provider,
    resolve_configured_provider,
)


def test_polza_is_default_provider(monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    assert resolve_configured_provider() == "polza"
    assert normalize_provider("gpt") == "polza"
    assert normalize_provider("openrouter") == "polza"


def test_polza_route_uses_polza_env(monkeypatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    monkeypatch.setenv("POLZA_AI_MODEL", "openai/gpt-5.4-mini")
    monkeypatch.setenv("POLZA_AI_BASE_URL", "https://polza.example/api/v1")

    route = ModelRoute(provider="polza")

    assert route.provider == "polza"
    assert route.resolved_api_key() == "polza-key"
    assert route.resolved_model() == "openai/gpt-5.4-mini"
    assert route.resolved_base_url() == "https://polza.example/api/v1"
    assert route.litellm_name() == "openai/openai/gpt-5.4-mini"


def test_polza_summary_is_password_safe(monkeypatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    monkeypatch.setenv("POLZA_AI_MODEL", "openai/gpt-5.4-mini")

    summary = get_llm_provider_summary("polza")

    assert summary["provider"] == "polza"
    assert summary["available"] is True
    assert summary["model"] == "openai/gpt-5.4-mini"
    assert "polza-key" not in str(summary)


def test_registry_prefers_requested_provider_and_skips_unconfigured_routes(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")

    registry = ModelRegistry(
        roles={
            "planner": ModelRoleConfig(
                fallback_chain=[
                    ModelRoute(provider="openai", model="gpt-test"),
                    ModelRoute(provider="deepseek", model="deepseek-chat"),
                ]
            )
        }
    )

    chain = registry.chain_for_role("planner")

    assert [route.provider for route in chain] == ["deepseek"]
    assert chain[0].resolved_model() == "deepseek-chat"


def test_registry_maps_node_alias_to_role() -> None:
    registry = ModelRegistry(
        aliases={"title_annotation": "planner"},
        roles={"planner": ModelRoleConfig(fallback_chain=[])},
    )

    assert registry.canonical_role("title_annotation") == "planner"
    assert registry.role_config("title_annotation") is registry.roles["planner"]
