"""Embeddings must use the configured provider (Polza in prod), not demand OPENAI_API_KEY."""

from __future__ import annotations

from typing import Any

import pytest

from content_factory.generation import embeddings as emb

_ALL = ["OPENAI_API_KEY", "OPENAI_BASE_URL", "POLZA_AI_API_KEY", "POLZA_AI_BASE_URL", "OPENROUTER_API_KEY"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(emb, "OPENAI_AVAILABLE", True)


class _FakeEmbeddings:
    def create(self, input: list[str], **kwargs: Any) -> Any:  # noqa: A002 - mirrors SDK
        return type("R", (), {"data": [type("E", (), {"embedding": [0.1]})() for _ in input]})()


class _FakeOpenAI:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeOpenAI.last_kwargs = kwargs
        self.embeddings = _FakeEmbeddings()


def test_uses_polza_key_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    monkeypatch.setenv("POLZA_AI_BASE_URL", "https://polza.ai/api/v1")
    monkeypatch.setattr(emb, "OpenAI", _FakeOpenAI)

    assert emb.create_openai_embeddings(["привет"]) == [[0.1]]
    assert _FakeOpenAI.last_kwargs["api_key"] == "polza-key"
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://polza.ai/api/v1"


def test_explicit_api_key_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    monkeypatch.setattr(emb, "OpenAI", _FakeOpenAI)

    emb.create_openai_embeddings(["x"], api_key="explicit")
    assert _FakeOpenAI.last_kwargs["api_key"] == "explicit"


def test_missing_key_error_names_polza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emb, "OpenAI", _FakeOpenAI)
    with pytest.raises(RuntimeError) as exc:  # type preserved for existing callers
        emb.create_openai_embeddings(["x"])
    assert "POLZA_AI_API_KEY" in str(exc.value)
