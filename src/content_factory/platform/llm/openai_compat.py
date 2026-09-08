"""Креды для OpenAI-совместимых эндпоинтов (Whisper ASR, embeddings).

Прод работает через Polza, а она реализует OpenAI-совместимый API (chat,
``/audio/transcriptions``, ``/embeddings``). Поэтому требовать именно ``OPENAI_API_KEY``
нельзя — на Polza-конфиге такие вызовы падали («OPENAI_API_KEY не задан»). Единая точка
резолва, чтобы приоритет провайдеров не расползался копиями по модулям.
"""

from __future__ import annotations

import os

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def resolve_openai_compatible_credentials() -> tuple[str, str | None]:
    """(api_key, base_url) для OpenAI-SDK: явный OpenAI -> Polza -> OpenRouter.

    ``base_url is None`` означает дефолтный эндпоинт SDK (api.openai.com).
    """
    from content_factory.platform.llm.model_registry import DEFAULT_POLZA_BASE_URL

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        return openai_key, os.getenv("OPENAI_BASE_URL") or None
    polza_key = os.getenv("POLZA_AI_API_KEY")
    if polza_key:
        base_url = os.getenv("POLZA_AI_BASE_URL") or os.getenv("POLZA_BASE_URL") or DEFAULT_POLZA_BASE_URL
        return polza_key, base_url
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        return openrouter_key, os.getenv("OPENROUTER_BASE_URL") or DEFAULT_OPENROUTER_BASE_URL
    raise ValueError(
        "Ключ LLM-провайдера не задан: нужен POLZA_AI_API_KEY "
        "(либо OPENAI_API_KEY / OPENROUTER_API_KEY)"
    )
