"""Model registry for role-based LLM routing.

The registry is intentionally data-only: it describes which model routes may be
used for a pipeline role, while :mod:`content_gen.llm.gateway` owns execution,
fallback, budgets and rate limits.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "model_registry.yaml"

PROVIDER_ALIASES = {
    "gpt": "polza",
    "openai": "openai",
    "polza": "polza",
    "polza_ai": "polza",
    "openrouter": "polza",
    "open_router": "polza",
    "azure": "azure",
    "azure_openai": "azure",
    "deepseek": "deepseek",
    "giga": "gigachat",
    "gigachat": "gigachat",
}
SUPPORTED_PROVIDERS = {"polza", "openai", "azure", "deepseek", "gigachat"}

DEFAULT_MODEL_BY_PROVIDER = {
    "polza": "openai/gpt-5.4-mini",
    "openai": "gpt-5.4-mini",
    "azure": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "gigachat": "GigaChat-2-Pro",
}
DEFAULT_POLZA_BASE_URL = "https://polza.ai/api/v1"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_GIGACHAT_BASE_URL = "https://gigachat.devices.sberbank.ru/api/v1"
DEFAULT_MODEL_ENV_BY_PROVIDER = {
    "polza": "POLZA_AI_MODEL",
    "openai": "OPENAI_MODEL",
    "azure": "AZURE_OPENAI_DEPLOYMENT_NAME",
    "deepseek": "DEEPSEEK_MODEL",
    "gigachat": "GIGACHAT_MODEL",
}
DEFAULT_API_KEY_ENV_BY_PROVIDER = {
    "polza": "POLZA_AI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gigachat": "GIGACHAT_CREDENTIALS",
}
DEFAULT_BASE_URL_ENV_BY_PROVIDER = {
    "polza": "POLZA_AI_BASE_URL",
    "openai": "OPENAI_BASE_URL",
    "azure": "AZURE_OPENAI_ENDPOINT",
    "deepseek": "DEEPSEEK_BASE_URL",
    "gigachat": "GIGACHAT_BASE_URL",
}


def _polza_env_aliases(env_name: str | None) -> list[str]:
    """Return env aliases for Polza settings and legacy OpenRouter model overrides."""
    if not env_name:
        return []
    names = [env_name]
    if env_name == "POLZA_AI_MODEL":
        names.extend(["OPEN_ROUTER_MODEL", "OPENROUTER_MODEL"])
    elif env_name == "POLZA_AI_BASE_URL":
        names.append("POLZA_BASE_URL")
    elif env_name == "POLZA_AI_TEMPERATURE":
        names.append("POLZA_TEMPERATURE")
    elif env_name.startswith("POLZA_AI_") and env_name.endswith("_MODEL"):
        role = env_name.removeprefix("POLZA_AI_")
        names.extend([f"OPEN_ROUTER_{role}", f"OPENROUTER_{role}"])
    return list(dict.fromkeys(names))


def _first_env(names: list[str]) -> str:
    """Read the first non-empty env value from a list of candidate names."""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def normalize_provider(provider: str | None) -> str:
    """Return a canonical provider name or raise a clear configuration error."""
    raw = (provider or "polza").strip().lower()
    normalized = PROVIDER_ALIASES.get(raw, raw)
    if normalized not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"Неизвестный LLM provider '{raw}'. Поддерживаются: {supported}")
    return normalized


def resolve_configured_provider(provider: str | None = None) -> str:
    """Resolve explicit provider or LLM_PROVIDER to a canonical registry provider."""
    return normalize_provider(provider or os.getenv("LLM_PROVIDER") or "polza")


def get_llm_provider_summary(provider: str | None = None) -> dict[str, Any]:
    """Return password-safe diagnostics for the currently configured provider."""
    resolved = resolve_configured_provider(provider)
    if resolved == "polza":
        model_env = _first_env(_polza_env_aliases("POLZA_AI_MODEL"))
        base_url = _first_env(_polza_env_aliases("POLZA_AI_BASE_URL")) or DEFAULT_POLZA_BASE_URL
        return {
            "provider": resolved,
            "available": bool(_first_env(["POLZA_AI_API_KEY"])),
            "model": model_env or DEFAULT_MODEL_BY_PROVIDER["polza"],
            "base_url": base_url,
            "credential_env": "POLZA_AI_API_KEY",
        }
    if resolved == "openai":
        return {
            "provider": resolved,
            "available": bool(os.getenv("OPENAI_API_KEY")),
            "model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL_BY_PROVIDER["openai"]),
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "credential_env": "OPENAI_API_KEY",
        }
    if resolved == "azure":
        return {
            "provider": resolved,
            "available": bool(os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT")),
            "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", DEFAULT_MODEL_BY_PROVIDER["azure"]),
            "base_url": os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            "credential_env": "AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT",
        }
    if resolved == "deepseek":
        return {
            "provider": resolved,
            "available": bool(os.getenv("DEEPSEEK_API_KEY")),
            "model": os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL_BY_PROVIDER["deepseek"]),
            "base_url": os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL,
            "credential_env": "DEEPSEEK_API_KEY",
        }
    return {
        "provider": resolved,
        "available": bool(os.getenv("GIGACHAT_CREDENTIALS") or os.getenv("GIGACHAT_API_KEY")),
        "model": os.getenv("GIGACHAT_MODEL", DEFAULT_MODEL_BY_PROVIDER["gigachat"]),
        "base_url": os.getenv("GIGACHAT_BASE_URL") or DEFAULT_GIGACHAT_BASE_URL,
        "credential_env": "GIGACHAT_CREDENTIALS",
    }


class ModelRoute(BaseModel):
    """One concrete model deployment that can satisfy a role."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str | None = None
    model_env: str | None = None
    litellm_model: str | None = None
    api_key_env: str | None = None
    base_url: str | None = None
    base_url_env: str | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    max_retries: int | None = None
    retry_delay_seconds: float | None = None
    rate_limit_rpm: int | None = None
    input_cost_per_1m: float | None = None
    output_cost_per_1m: float | None = None
    enabled: bool = True

    @field_validator("provider")
    @classmethod
    def _normalize_provider(cls, value: str) -> str:
        return normalize_provider(value)

    def resolved_model(self) -> str:
        """Resolve model name using env override first, then configured fallback."""
        env_name = self.model_env or DEFAULT_MODEL_ENV_BY_PROVIDER.get(self.provider)
        if self.provider == "polza":
            env_value = _first_env(_polza_env_aliases(env_name))
        else:
            env_value = os.getenv(env_name or "", "").strip()
        return env_value or self.model or DEFAULT_MODEL_BY_PROVIDER[self.provider]

    def resolved_api_key_env(self) -> str:
        """Return the env var used for this route credential."""
        return self.api_key_env or DEFAULT_API_KEY_ENV_BY_PROVIDER[self.provider]

    def resolved_api_key(self) -> str | None:
        """Read this route credential without exposing it in summaries."""
        if self.provider == "polza":
            return os.getenv(self.api_key_env or "POLZA_AI_API_KEY") or None
        if self.provider == "gigachat":
            return (
                os.getenv(self.api_key_env or "GIGACHAT_CREDENTIALS")
                or os.getenv("GIGACHAT_API_KEY")
            )
        return os.getenv(self.resolved_api_key_env())

    def resolved_base_url(self) -> str | None:
        """Read base URL from route config or env."""
        env_name = self.base_url_env or DEFAULT_BASE_URL_ENV_BY_PROVIDER.get(self.provider)
        if self.provider == "polza":
            return (
                self.base_url
                or _first_env(_polza_env_aliases(env_name))
                or DEFAULT_POLZA_BASE_URL
            )
        return self.base_url or os.getenv(env_name or "", "").strip() or None

    def is_configured(self) -> bool:
        """Return whether this route has enough credentials to be attempted."""
        if not self.enabled:
            return False
        if self.provider == "azure":
            return bool(self.resolved_api_key() and self.resolved_base_url())
        return bool(self.resolved_api_key())

    def litellm_name(self) -> str:
        """Return the LiteLLM model identifier for providers handled by LiteLLM."""
        if self.litellm_model:
            return self.litellm_model
        model = self.resolved_model()
        if self.provider == "deepseek":
            return f"deepseek/{model}"
        if self.provider == "polza":
            return model if model.startswith("openai/openai/") else f"openai/{model}"
        if self.provider == "azure":
            return f"azure/{model}"
        if self.provider == "gigachat":
            return f"gigachat/{model}"
        return model

    def route_key(self) -> tuple[str, str]:
        """Stable key for de-duplicating fallback chains."""
        return self.provider, self.resolved_model()


class ModelRoleConfig(BaseModel):
    """Routing config for one generation role."""

    model_config = ConfigDict(extra="forbid")

    fallback_chain: list[ModelRoute] = Field(default_factory=list)
    timeout_seconds: float | None = None
    max_retries: int | None = None
    retry_delay_seconds: float | None = None
    rate_limit_rpm: int | None = None
    budget_usd: float | None = None


class ModelRegistryDefaults(BaseModel):
    """Default route settings applied when a role or route omits values."""

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = 120.0
    max_retries: int = 2
    retry_delay_seconds: float = 1.0
    rate_limit_rpm: int | None = None
    budget_usd: float | None = None


class ModelRegistry(BaseModel):
    """Role-to-model registry with aliases and environment-aware routes."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    defaults: ModelRegistryDefaults = Field(default_factory=ModelRegistryDefaults)
    aliases: dict[str, str] = Field(default_factory=dict)
    roles: dict[str, ModelRoleConfig] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> ModelRegistry:
        """Load a registry from YAML, falling back to a safe built-in default."""
        registry_path = Path(path or os.getenv("LLM_MODEL_REGISTRY") or DEFAULT_REGISTRY_PATH)
        if not registry_path.exists():
            return cls.default()
        with registry_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        return cls.model_validate(payload)

    @classmethod
    def default(cls) -> ModelRegistry:
        """Return a minimal default that preserves old env-based behavior."""
        return cls(
            roles={
                "default": ModelRoleConfig(
                    fallback_chain=[
                        ModelRoute(provider="polza", model_env="POLZA_AI_MODEL", model="openai/gpt-5.4-mini"),
                        ModelRoute(provider="deepseek", model_env="DEEPSEEK_MODEL", model="deepseek-chat"),
                        ModelRoute(provider="gigachat", model_env="GIGACHAT_MODEL", model="GigaChat-2-Pro"),
                    ]
                )
            }
        )

    def canonical_role(self, role: str | None) -> str:
        """Map runtime node names to configured model roles."""
        raw = (role or "default").strip().lower() or "default"
        return self.aliases.get(raw, raw)

    def role_config(self, role: str | None) -> ModelRoleConfig:
        """Return config for a role, falling back to default role."""
        canonical = self.canonical_role(role)
        return self.roles.get(canonical) or self.roles.get("default") or ModelRoleConfig()

    def chain_for_role(
        self,
        role: str | None,
        *,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        strict_provider: bool = False,
    ) -> list[ModelRoute]:
        """Return configured, de-duplicated routes for a role.

        The first route preserves backward compatibility with ``LLM_PROVIDER`` and
        explicit constructor arguments. Registry routes then provide fallbacks.
        """
        role_config = self.role_config(role)
        raw_provider = preferred_provider or os.getenv("LLM_PROVIDER") or "polza"
        normalized_preferred = normalize_provider(raw_provider)
        preferred = ModelRoute(
            provider=raw_provider,
            model=preferred_model,
            model_env=DEFAULT_MODEL_ENV_BY_PROVIDER.get(normalized_preferred),
        )
        fallback_chain = role_config.fallback_chain
        if strict_provider and preferred_provider:
            fallback_chain = [route for route in fallback_chain if route.provider == normalized_preferred]

        chain = [preferred, *fallback_chain]
        seen: set[tuple[str, str]] = set()
        resolved: list[ModelRoute] = []
        for route in chain:
            if not route.enabled:
                continue
            key = route.route_key()
            if key in seen:
                continue
            seen.add(key)
            if route.is_configured():
                resolved.append(route)
        return resolved

    def summary(self) -> dict[str, Any]:
        """Return password-safe registry diagnostics."""
        roles: dict[str, Any] = {}
        for role, config in self.roles.items():
            roles[role] = [
                {
                    "provider": route.provider,
                    "model": route.resolved_model(),
                    "configured": route.is_configured(),
                }
                for route in config.fallback_chain
            ]
        return {
            "version": self.version,
            "path": str(Path(os.getenv("LLM_MODEL_REGISTRY") or DEFAULT_REGISTRY_PATH)),
            "roles": roles,
        }
