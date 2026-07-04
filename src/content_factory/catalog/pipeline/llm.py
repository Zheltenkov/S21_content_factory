"""Слой доступа к OpenAI-compatible LLM API через Polza AI/OpenRouter fallback."""
from __future__ import annotations

import json
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from content_factory.platform.llm import transport

from . import config
from .prompt_versions import prompt_version_for_stage

_USAGE_CONTEXT: ContextVar[dict[str, object] | None] = ContextVar("llm_usage_context", default=None)


def set_usage_context(**kwargs: object) -> None:
    current = dict(_USAGE_CONTEXT.get() or {})
    for key, value in kwargs.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    _USAGE_CONTEXT.set(current)


def clear_usage_context() -> None:
    _USAGE_CONTEXT.set({})


def _append_usage_log(
    model: str,
    messages: list[dict],
    json_mode: bool,
    timeout: int,
    max_tokens: int | None,
    resp: dict,
    latency_ms: float,
) -> None:
    usage = resp.get("usage") or {}
    context = _USAGE_CONTEXT.get() or {}
    stage = context.get("stage")
    record = {
        "logged_at": datetime.now(UTC).isoformat(),
        "job_id": context.get("job_id"),
        "brief_id": context.get("brief_id"),
        "stage": stage,
        "prompt_version": context.get("prompt_version") or prompt_version_for_stage(str(stage) if stage else None),
        "model": model,
        "json_mode": json_mode,
        "timeout_seconds": timeout or config.REQUEST_TIMEOUT_SECONDS,
        "max_tokens": max_tokens,
        "message_count": len(messages),
        "latency_ms": round(latency_ms, 2),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
    }
    log_path = Path(config.LLM_USAGE_LOG_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def chat(model: str, messages: list[dict], json_mode: bool = False, timeout: int = 90, max_tokens: int | None = None) -> dict:
    if not config.LLM_API_KEY:
        raise RuntimeError("POLZA_AI_API_KEY не найден. Проверьте .env в корне проекта.")
    payload = transport.build_chat_payload(model, messages, json_mode=json_mode, max_tokens=max_tokens)
    extra_headers: dict[str, str] | None = None
    if config.LLM_PROVIDER == "openrouter":
        extra_headers = {
            "HTTP-Referer": config.OPENROUTER_HTTP_REFERER,
            "X-Title": config.OPENROUTER_APP_TITLE,
        }
    started_at = time.perf_counter()
    # trust_env=False keeps the catalog client from picking up proxy env vars.
    r = transport.post_chat_completion(
        config.LLM_CHAT_COMPLETIONS_URL,
        api_key=config.LLM_API_KEY,
        payload=payload,
        timeout=timeout or config.REQUEST_TIMEOUT_SECONDS,
        trust_env=False,
        extra_headers=extra_headers,
    )
    latency_ms = (time.perf_counter() - started_at) * 1000
    r.raise_for_status()
    response_json = r.json()
    _append_usage_log(model, messages, json_mode, timeout, max_tokens, response_json, latency_ms)
    return response_json


def content(resp: dict) -> str:
    return resp["choices"][0]["message"]["content"]


def citations(resp: dict) -> list[str]:
    cits = resp.get("citations") or []
    if not cits:
        ann = resp["choices"][0]["message"].get("annotations") or []
        cits = [a.get("url_citation", {}).get("url", "") for a in ann]
    return [c for c in cits if c]
