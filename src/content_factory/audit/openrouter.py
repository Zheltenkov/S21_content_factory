"""Клиент OpenRouter для модельных проверок."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from content_factory.platform.llm import transport

class OpenRouterError(RuntimeError):
    """Ошибка обращения к OpenRouter."""


#: Default LLM gateway for the audit module — the unified content_factory endpoint.
DEFAULT_BASE_URL = transport.POLZA_CHAT_COMPLETIONS_URL


class OpenRouterClient:
    """Тонкий OpenAI-совместимый клиент. По умолчанию ходит через Polza-гейтвей."""

    def __init__(self, api_key: str, model: str, timeout_seconds: float = 60.0,
                 base_url: str = DEFAULT_BASE_URL) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.base_url = base_url
        self.last_call_usage: dict[str, int | float] = {}

    def complete_json(self, system_prompt: str, user_prompt: str, max_retries: int = 2, max_tokens: int | None = None) -> dict[str, Any]:
        """Запрашиваем у модели JSON и разбираем ответ в словарь."""

        payload = transport.build_chat_payload(
            self.model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            json_mode=True,
            max_tokens=max_tokens,
        )

        last_error: str | None = None
        for attempt in range(max_retries + 1):
            try:
                response = transport.post_chat_completion(
                    self.base_url,
                    api_key=self.api_key,
                    payload=payload,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                response_payload = response.json()
                self.last_call_usage = transport.extract_usage(response_payload)
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
