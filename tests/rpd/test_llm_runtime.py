"""Role routing and password-safe LLM telemetry for the RPD pipeline."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from content_factory.platform.llm.model_registry import ModelRegistry
from content_factory.rpd.llm_runtime import RpdLlmRouter, create_rpd_llm_router


class _Answer(BaseModel):
    value: str


class _Client:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self.name = name
        self.fail = fail
        self.calls: list[dict[str, Any]] = []
        self.provider = "test-provider"
        self.model = f"test-{name}"
        self._last_finish_reason = "stop"
        self._last_token_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        self._last_cost_usd = 0.001

    def complete(self, system: str, user: str, **kwargs: Any) -> str:
        self.calls.append({"system": system, "user": user, **kwargs})
        if self.fail:
            raise RuntimeError("provider down")
        return self.name

    def complete_structured(self, *, output_model: type[BaseModel], **kwargs: Any) -> BaseModel:
        self.calls.append(kwargs)
        return output_model.model_validate({"value": self.name})


def test_router_sends_each_role_to_its_own_client_and_records_usage() -> None:
    prose = _Client("prose")
    validator = _Client("validator")
    router = RpdLlmRouter({"rpd_prose": prose, "rpd_validate": validator})  # type: ignore[arg-type]

    assert router.complete("system", "user", llm_role="rpd_prose", prompt_version="p1") == "prose"
    assert router.complete("system", "user", llm_role="rpd_validate", prompt_version="v1") == "validator"

    assert len(prose.calls) == 1 and len(validator.calls) == 1
    assert [event.role for event in router.events] == ["rpd_prose", "rpd_validate"]
    assert router.events[0].total_tokens == 15
    assert router.events[0].cost_usd == 0.001


def test_router_records_failure_and_rethrows_for_pipeline_fallback() -> None:
    router = RpdLlmRouter({"rpd_prose": _Client("prose", fail=True)})  # type: ignore[arg-type]

    try:
        router.complete("system", "user", llm_role="rpd_prose", prompt_version="p1")
    except RuntimeError:
        pass

    assert len(router.events) == 1
    assert not router.events[0].success
    assert router.events[0].error_type == "RuntimeError"


def test_router_preserves_typed_structured_output() -> None:
    router = RpdLlmRouter({"rpd_semantic_preflight": _Client("typed")})  # type: ignore[arg-type]

    result = router.complete_structured(
        output_model=_Answer,
        system="system",
        user="user",
        llm_role="rpd_semantic_preflight",
        prompt_version="semantic-v1",
    )

    assert result == _Answer(value="typed")
    assert router.events[0].role == "rpd_semantic_preflight"


def test_model_registry_maps_rpd_nodes_to_independent_quality_cost_roles() -> None:
    registry = ModelRegistry.load()

    assert registry.canonical_role("rpd_translate") == "translator"
    assert registry.canonical_role("rpd_classify") == "planner"
    assert registry.canonical_role("rpd_interpret") == "planner"
    assert registry.canonical_role("rpd_prose") == "theory"
    assert registry.canonical_role("rpd_validate") == "critic"
    assert registry.canonical_role("rpd_repair") == "critic"
    assert registry.canonical_role("rpd_semantic_preflight") == "critic"


def test_router_supports_repetition_repair_as_a_critic_role() -> None:
    repair = _Client("repair")
    router = RpdLlmRouter({"rpd_repair": repair})  # type: ignore[arg-type]

    result = router.complete("system", "user", llm_role="rpd_repair", prompt_version="repair-v1")

    assert result == "repair"
    assert router.events[0].role == "rpd_repair"


def test_factory_applies_role_specific_model_timeout_retry_and_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_factory(**kwargs: Any) -> _Client:
        captured.append(kwargs)
        return _Client(str(kwargs["default_role"]))

    monkeypatch.setattr("content_factory.platform.llm.factory.create_llm_client", fake_factory)
    monkeypatch.setenv("RPD_LLM_PROSE_MODEL", "cheap-prose-model")
    monkeypatch.setenv("RPD_LLM_PROSE_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("RPD_LLM_VALIDATE_MAX_RETRIES", "3")

    router = create_rpd_llm_router()

    assert router is not None
    by_role = {call["default_role"]: call for call in captured}
    assert by_role["rpd_prose"]["model"] == "cheap-prose-model"
    assert by_role["rpd_prose"]["timeout_seconds"] == 45.0
    assert by_role["rpd_prose"]["enable_cache"] is True
    assert by_role["rpd_validate"]["max_retries"] == 3
    assert by_role["rpd_validate"]["enable_cache"] is False
