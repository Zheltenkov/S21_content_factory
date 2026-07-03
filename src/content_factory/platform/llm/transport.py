"""Shared low-level transport for OpenAI-compatible chat-completions.

Extracted from the audit (`audit/openrouter.py`) and catalog
(`catalog/pipeline/llm.py`) clients so the payload/header construction, the HTTP
POST, and the usage parsing are not triplicated. Higher-level concerns stay in
each caller: retry / JSON-content parsing (audit), citations + usage logging
(catalog), and budgets / roles / structured output (the generation gateway).

Polza is the unified default provider across content_factory; it is
OpenAI-compatible and proxies the same models (openai/gpt-5.4-mini,
perplexity/sonar, qwen/qwen3-coder).
"""

from __future__ import annotations

from typing import Any

import requests

#: Unified default chat-completions endpoint. Both the audit and catalog clients
#: default here (catalog can override via ``LLM_CHAT_COMPLETIONS_URL``).
POLZA_CHAT_COMPLETIONS_URL = "https://polza.ai/api/v1/chat/completions"


def build_chat_payload(
    model: str,
    messages: list[dict[str, Any]],
    *,
    json_mode: bool = False,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Assemble the OpenAI-compatible chat-completions request body."""

    payload: dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    return payload


def auth_headers(api_key: str, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Bearer auth + JSON content-type, plus any provider-specific extras."""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def post_chat_completion(
    url: str,
    *,
    api_key: str,
    payload: dict[str, Any],
    timeout: float,
    trust_env: bool = True,
    extra_headers: dict[str, str] | None = None,
) -> requests.Response:
    """POST a prepared chat-completions payload and return the raw response.

    Does not call ``raise_for_status`` — callers keep their own error handling
    (audit retries without json-mode on 400; catalog measures latency first).
    ``trust_env=False`` ignores proxy env vars (catalog's behaviour).
    """

    session = requests.Session()
    session.trust_env = trust_env
    return session.post(
        url,
        headers=auth_headers(api_key, extra=extra_headers),
        json=payload,
        timeout=timeout,
    )


def extract_usage(payload: dict[str, Any]) -> dict[str, int | float]:
    """Normalise the provider's token/cost usage block, if present."""

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return {}

    result: dict[str, int | float] = {}
    for source_key, target_key in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
        ("cost", "cost_usd"),
        ("cost_usd", "cost_usd"),
    ):
        value = usage.get(source_key)
        if isinstance(value, int | float):
            result[target_key] = value
        elif isinstance(value, str):
            try:
                result[target_key] = float(value)
            except ValueError:
                continue
    return result
