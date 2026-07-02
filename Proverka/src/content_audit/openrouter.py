"""Клиент OpenRouter для модельных проверок."""

from __future__ import annotations

import json
import time
from typing import Any

import requests


class OpenRouterError(RuntimeError):
    """Ошибка обращения к OpenRouter."""


class OpenRouterClient:
    """Тонкий клиент для запросов к модели через OpenRouter."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 60.0,
                 base_url: str = "https://openrouter.ai/api/v1/chat/completions") -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url
        self.last_call_usage: dict[str, int | float] = {}

    def complete_json(self, system_prompt: str, user_prompt: str, max_retries: int = 2, max_tokens: int | None = None) -> dict[str, Any]:
        """Запрашиваем у модели JSON и разбираем ответ в словарь."""

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: str | None = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                response_payload = response.json()
                self.last_call_usage = _extract_usage(response_payload)
                content = response_payload["choices"][0]["message"]["content"]
                return _parse_json_content(content)
            except requests.HTTPError as exc:
                last_error = _format_http_error(exc)
                if _can_retry_without_json_mode(exc, payload):
                    payload.pop("response_format", None)
                    continue
            except Exception as exc:  # noqa: BLE001 - сохраняем любую ошибку провайдера.
                last_error = str(exc)

            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))

        raise OpenRouterError(f"Не удалось получить JSON от OpenRouter: {last_error}")


def _can_retry_without_json_mode(exc: requests.HTTPError, payload: dict[str, Any]) -> bool:
    """Некоторые модели OpenRouter не принимают response_format=json_object."""

    response = exc.response
    return response is not None and response.status_code == 400 and "response_format" in payload


def _format_http_error(exc: requests.HTTPError) -> str:
    """Делаем ошибку провайдера понятной без раскрытия заголовков запроса."""

    response = exc.response
    if response is None:
        return str(exc)
    body = (response.text or "").strip()
    if len(body) > 600:
        body = f"{body[:600]}..."
    if body:
        return f"OpenRouter вернул HTTP {response.status_code}: {body}"
    return f"OpenRouter вернул HTTP {response.status_code}."


def _parse_json_content(content: object) -> dict[str, Any]:
    """Разбираем JSON-ответ, включая текст с fenced-блоком или пояснением вокруг JSON."""

    if not isinstance(content, str):
        raise ValueError("Ответ модели не является строкой.")
    text = content.strip()
    if text.startswith("```"):
        text = _strip_fenced_json(text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = json.loads(_extract_json_fragment(text))
    if not isinstance(payload, dict):
        raise ValueError("Ответ модели не является JSON-объектом.")
    return payload


def _strip_fenced_json(text: str) -> str:
    """Убираем Markdown-обёртку вокруг JSON, если модель её добавила."""

    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return text


def _extract_json_fragment(text: str) -> str:
    """Достаём первый JSON-объект из ответа с лишним текстом."""

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("В ответе модели нет JSON-объекта.")
    return text[start : end + 1]


def _extract_usage(payload: dict[str, Any]) -> dict[str, int | float]:
    """Достаём статистику токенов и стоимости из ответа провайдера, если она есть."""

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
