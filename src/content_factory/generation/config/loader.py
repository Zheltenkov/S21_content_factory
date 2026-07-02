"""
content_gen/config/loader.py

Загрузчик конфигов агентов.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

CONTENT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(__file__).resolve().parent
REPO_ROOT = CONFIG_ROOT.parents[1]


_prompt_cache: dict[Path, str] = {}
_agent_config_cache: dict[str, AgentConfig] = {}
_agent_versions: dict[str, str] = {}


class PromptSource(BaseModel):
    """Описывает источник промпта (inline текст или путь к файлу)."""

    type: Literal["inline", "file"] = "inline"
    value: str | None = None
    path: str | None = None
    prompt_id: str | None = None
    version: str | None = None
    owner: str | None = None
    input_schema: str | dict[str, Any] | list[Any] | None = None
    output_schema: str | dict[str, Any] | list[Any] | None = None

    def load(self) -> str:
        if self.type == "inline":
            if self.value is None:
                raise ValueError("Для inline промпта требуется поле 'value'")
            return self.value

        if self.type == "file":
            if not self.path:
                raise ValueError("Для file промпта требуется поле 'path'")
            prompt_path = (REPO_ROOT / self.path).resolve()
            if prompt_path not in _prompt_cache:
                _prompt_cache[prompt_path] = prompt_path.read_text(encoding="utf-8")
            return _prompt_cache[prompt_path]

        raise ValueError(f"Неизвестный тип промпта: {self.type}")


class PromptRegistryRecord(BaseModel):
    """Versioned prompt metadata used by tracing and offline evaluation."""

    prompt_id: str
    version: str
    owner: str
    input_schema: str | dict[str, Any] | list[Any] | None = None
    output_schema: str | dict[str, Any] | list[Any] | None = None
    prompt_hash: str
    source: str
    agent_name: str
    prompt_key: str


class LLMConfig(BaseModel):
    """Параметры LLM вызова."""

    temperature: float | None = None
    max_tokens: int | None = Field(default=None, alias="max_tokens")
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None

    def to_kwargs(self, **overrides: Any) -> dict[str, Any]:
        data = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "presence_penalty": self.presence_penalty,
            "frequency_penalty": self.frequency_penalty,
        }
        data.update({k: v for k, v in overrides.items() if v is not None})
        return {k: v for k, v in data.items() if v is not None}


class AgentConfig(BaseModel):
    """Конфиг агента."""

    name: str
    version: str
    owner: str = "content-generation"
    updated_at: str | None = None
    input_schema: str | dict[str, Any] | list[Any] | None = None
    output_schema: str | dict[str, Any] | list[Any] | None = None
    llm: LLMConfig | None = None
    prompts: dict[str, PromptSource] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    def get_prompt(self, key: str) -> str:
        if key not in self.prompts:
            raise KeyError(f"Промпт '{key}' не найден в конфиге агента {self.name}")
        return self.prompts[key].load()

    def get_prompt_record(self, key: str) -> PromptRegistryRecord:
        """Return stable prompt registry metadata without exposing raw prompt text."""
        if key not in self.prompts:
            raise KeyError(f"Промпт '{key}' не найден в конфиге агента {self.name}")
        source = self.prompts[key]
        prompt_text = source.load()
        return PromptRegistryRecord(
            prompt_id=source.prompt_id or f"{self.name}.{key}",
            version=source.version or self.version,
            owner=source.owner or self.owner,
            input_schema=(
                source.input_schema
                if source.input_schema is not None
                else self.input_schema or f"{self.name}.{key}.input"
            ),
            output_schema=(
                source.output_schema
                if source.output_schema is not None
                else self.output_schema or f"{self.name}.{key}.output"
            ),
            prompt_hash=_hash_text(prompt_text),
            source=source.path or f"agent:{self.name}:{key}",
            agent_name=self.name,
            prompt_key=key,
        )

    def prompt_trace_kwargs(
        self,
        *keys: str,
        output_schema: str | dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any]:
        """Build trace-only kwargs for LLM calls that use one or more prompt templates."""
        records = [self.get_prompt_record(key) for key in keys]
        if not records:
            return {}

        prompt_ids = _unique(record.prompt_id for record in records)
        versions = _unique(record.version for record in records)
        owners = _unique(record.owner for record in records if record.owner)
        input_schemas = _unique_schema(record.input_schema for record in records if record.input_schema is not None)
        output_schemas = _unique_schema(record.output_schema for record in records if record.output_schema is not None)
        if output_schema is not None:
            output_schemas = [output_schema]

        payload: dict[str, Any] = {
            "prompt_id": "+".join(prompt_ids),
            "prompt_version": versions[0] if len(versions) == 1 else "+".join(versions),
            "prompt_hash": _hash_text("|".join(record.prompt_hash for record in records)),
            "prompt_owner": owners[0] if len(owners) == 1 else "multiple",
            "prompt_source": "+".join(_unique(record.source for record in records)),
        }
        if input_schemas:
            payload["prompt_input_schema"] = input_schemas[0] if len(input_schemas) == 1 else input_schemas
        if output_schemas:
            payload["prompt_output_schema"] = output_schemas[0] if len(output_schemas) == 1 else output_schemas
        return payload


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _unique_schema(values: Any) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        marker = repr(value)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def get_agent_config(agent_name: str) -> AgentConfig:
    """Возвращает конфиг агента."""
    if agent_name not in _agent_config_cache:
        cfg_path = CONFIG_ROOT / "agents" / f"{agent_name}.yaml"
        data = _load_yaml(cfg_path)
        data["name"] = agent_name
        config = AgentConfig(**data)
        _agent_config_cache[agent_name] = config
        _agent_versions[agent_name] = config.version
    return _agent_config_cache[agent_name]


def get_loaded_agent_versions() -> dict[str, str]:
    """Возвращает версии уже загруженных агентских конфигов."""
    return dict(_agent_versions)


def build_prompt_registry(agent_names: list[str] | None = None) -> dict[str, PromptRegistryRecord]:
    """Load prompt metadata for configured agents without calling the model."""
    if agent_names is None:
        agents_dir = CONFIG_ROOT / "agents"
        agent_names = sorted(path.stem for path in agents_dir.glob("*.yaml"))

    registry: dict[str, PromptRegistryRecord] = {}
    for agent_name in agent_names:
        config = get_agent_config(agent_name)
        for prompt_key in sorted(config.prompts):
            record = config.get_prompt_record(prompt_key)
            registry[record.prompt_id] = record
    return registry


def prompt_trace_kwargs(
    config: Any,
    *keys: str,
    output_schema: str | dict[str, Any] | list[Any] | None = None,
) -> dict[str, Any]:
    """Return trace-only prompt metadata for configs that implement the registry contract."""
    trace_builder = getattr(config, "prompt_trace_kwargs", None)
    if not callable(trace_builder):
        return {}
    return trace_builder(*keys, output_schema=output_schema)
