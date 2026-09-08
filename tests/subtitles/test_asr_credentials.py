"""ASR (Whisper) credentials resolve to the same provider the rest of the LLM stack uses.

Production runs on Polza, which serves /audio/transcriptions; requiring OPENAI_API_KEY there
broke video translation with "OPENAI_API_KEY не задан".
"""

from __future__ import annotations

import pytest

from content_factory.generation.subtitles.pipeline import _resolve_asr_credentials

_ALL = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "POLZA_AI_API_KEY",
    "POLZA_AI_BASE_URL",
    "POLZA_BASE_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALL:
        monkeypatch.delenv(name, raising=False)


def test_polza_key_is_used_with_its_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    monkeypatch.setenv("POLZA_AI_BASE_URL", "https://polza.ai/api/v1")
    assert _resolve_asr_credentials() == ("polza-key", "https://polza.ai/api/v1")


def test_polza_key_alone_falls_back_to_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    key, base_url = _resolve_asr_credentials()
    assert key == "polza-key"
    assert base_url and "polza.ai" in base_url  # never falls through to api.openai.com


def test_legacy_polza_base_url_alias_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    monkeypatch.setenv("POLZA_BASE_URL", "https://legacy.polza/api/v1")
    assert _resolve_asr_credentials()[1] == "https://legacy.polza/api/v1"


def test_explicit_openai_wins_over_polza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("POLZA_AI_API_KEY", "polza-key")
    assert _resolve_asr_credentials() == ("openai-key", None)  # None -> SDK default endpoint


def test_openrouter_is_the_last_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    key, base_url = _resolve_asr_credentials()
    assert key == "or-key" and base_url and "openrouter" in base_url


def test_no_key_names_every_accepted_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError) as exc:
        _resolve_asr_credentials()
    message = str(exc.value)
    assert "POLZA_AI_API_KEY" in message and "OPENAI_API_KEY" in message
