"""Web-search model adapter (Perplexity through the same gateway): strict JSON, links only from the provider.

The answer refers to sources by their [n] numbers; the links themselves come from the provider's citations,
never from the model's text. A number outside the returned list is dropped by the caller.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import TypeVar

from openai import APIStatusError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from . import usage as ledger
from .llm import CallMetrics, ModelUnavailable, json_text, model_configuration

T = TypeVar("T", bound=BaseModel)
# uvicorn prints this logger to the console and server.txt; the line carries no content, only where links were found.
logger = logging.getLogger("uvicorn.error")
DEFAULT_SEARCH_MODEL = "perplexity/sonar-pro"


def search_model() -> str:
    return os.getenv("PROJECT_LAB_SEARCH_MODEL", "").strip() or DEFAULT_SEARCH_MODEL


URL_IN_TEXT = re.compile(r"https?://[^\s<>()\[\]\"'«»]+")


def citations(raw: dict[str, object]) -> list[str]:
    """Links the provider says it used. Gateways differ: Perplexity puts `citations` (URLs) at the top, some pass
    `search_results` with url fields, OpenAI-style gateways put `url_citation` annotations into the message."""
    found: list[object] = []
    urls = raw.get("citations")
    if isinstance(urls, list):
        found += urls
    results = raw.get("search_results")
    if isinstance(results, list):
        found += [r.get("url") for r in results if isinstance(r, dict)]
    choices = raw.get("choices")
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    if isinstance(message, dict):
        if isinstance(message.get("citations"), list):
            found += message["citations"]
        for note in message.get("annotations") or []:
            if isinstance(note, dict):
                inner = note.get("url_citation")
                found.append((inner if isinstance(inner, dict) else note).get("url"))
    clean = [u.rstrip(".,;") for u in found if isinstance(u, str) and u.startswith(("http://", "https://"))]
    return list(dict.fromkeys(clean))


def links_in_text(text: str) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;:") for u in URL_IN_TEXT.findall(text)))


def pick(sources: list[str], numbers: list[int]) -> tuple[list[str], int]:
    """The provider's links for [n] marks, in order, without repeats; also how many marks pointed nowhere."""
    wanted = list(dict.fromkeys(numbers))
    kept = [sources[n - 1] for n in wanted if 1 <= n <= len(sources)]
    return kept, len(wanted) - len(kept)


# What a search that answers in free text is written down as in the account of a project: it has no schema to be named by.
TEXT_SEARCH = "search_text"


def spent(asked: str, model: str, started: float, usage: object, outcome: str) -> CallMetrics:
    """The metrics of a search call, written into the account of the project as every call of a model is: the
    search is paid for too, and was the part of a project's cost nothing kept (07.10.2026)."""
    metrics = CallMetrics(model, int((time.monotonic() - started) * 1000),
                          int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0))
    ledger.record("search", asked, model, metrics.input_tokens, metrics.output_tokens, metrics.elapsed_ms, 0, outcome,
                  ledger.charged(usage) if usage is not None else None)
    return metrics


async def ask(schema: type[T], system: str, payload: str, max_tokens: int = 4000) -> tuple[T, list[str], CallMetrics]:
    key, base, _ = model_configuration()
    if not key:
        raise ModelUnavailable("На сервере не настроен POLZA_AI_API_KEY")
    model = search_model()
    started = time.monotonic()
    shape = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    try:
        async with AsyncOpenAI(api_key=key, base_url=base, timeout=float(os.getenv("PROJECT_LAB_TIMEOUT_SECONDS", "150")), max_retries=1) as client:
            # No response_format: search models accept a JSON instruction but not every gateway accepts json_object.
            response = await client.chat.completions.create(
                model=model, max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": f"{system}\nСхема ответа:\n{shape}"}, {"role": "user", "content": payload}],
            )
    except asyncio.CancelledError:
        spent(schema.__name__, model, started, None, "cancelled")  # the search was asked: it is written, then the stop goes on
        raise
    except APIStatusError as exc:
        spent(schema.__name__, model, started, None, f"failed:http{exc.status_code}")
        raise ModelUnavailable(f"Шлюз поисковой модели ответил ошибкой {exc.status_code}. Проверьте, что {model} доступна в Polza.") from exc
    except Exception as exc:
        spent(schema.__name__, model, started, None, f"failed:{type(exc).__name__}")
        raise ModelUnavailable(f"Не удалось получить ответ поисковой модели ({type(exc).__name__}). Попробуйте ещё раз.") from exc
    if not response.choices:
        spent(schema.__name__, model, started, response.usage, "failed:empty")
        raise ModelUnavailable("Поисковая модель вернула пустой ответ. Попробуйте ещё раз.")
    choice = response.choices[0]
    if choice.finish_reason not in {"stop", None}:
        spent(schema.__name__, model, started, response.usage, f"failed:{choice.finish_reason}")
        raise ModelUnavailable(f"Поисковая модель не завершила ответ ({choice.finish_reason}). Попробуйте ещё раз.")
    try:
        answer = schema.model_validate_json(json_text(choice.message.content or ""))
    except ValidationError as exc:
        spent(schema.__name__, model, started, response.usage, "failed:schema")
        raise ModelUnavailable("Поисковая модель вернула ответ не по схеме. Попробуйте ещё раз.") from exc
    return answer, citations(response.model_dump()), spent(schema.__name__, model, started, response.usage, "ok")


async def ask_text(system: str, payload: str, max_tokens: int = 3000) -> tuple[str, list[str], CallMetrics]:
    """A free-text search answer and every link it carries: citations first, then addresses written in the text.
    Search models keep a JSON schema poorly, so the structure is left to the writer model and the links to code."""
    key, base, _ = model_configuration()
    if not key:
        raise ModelUnavailable("На сервере не настроен POLZA_AI_API_KEY")
    model = search_model()
    started = time.monotonic()
    try:
        async with AsyncOpenAI(api_key=key, base_url=base, timeout=float(os.getenv("PROJECT_LAB_TIMEOUT_SECONDS", "150")), max_retries=1) as client:
            response = await client.chat.completions.create(
                model=model, max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": payload}])
    except asyncio.CancelledError:
        spent(TEXT_SEARCH, model, started, None, "cancelled")
        raise
    except APIStatusError as exc:
        spent(TEXT_SEARCH, model, started, None, f"failed:http{exc.status_code}")
        raise ModelUnavailable(f"Шлюз поисковой модели ответил ошибкой {exc.status_code}. Проверьте, что {model} доступна в Polza.") from exc
    except Exception as exc:
        spent(TEXT_SEARCH, model, started, None, f"failed:{type(exc).__name__}")
        raise ModelUnavailable(f"Не удалось получить ответ поисковой модели ({type(exc).__name__}). Попробуйте ещё раз.") from exc
    if not response.choices:
        spent(TEXT_SEARCH, model, started, response.usage, "failed:empty")
        raise ModelUnavailable("Поисковая модель вернула пустой ответ. Попробуйте ещё раз.")
    text = response.choices[0].message.content or ""
    metrics = spent(TEXT_SEARCH, model, started, response.usage, "ok")
    raw = response.model_dump()
    # What the gateway returned, without content: enough to see in the server log where the links live.
    logger.info("search model=%s keys=%s citations=%d text_links=%d", model, sorted(raw), len(citations(raw)), len(links_in_text(text)))
    return text, list(dict.fromkeys([*citations(raw), *links_in_text(text)])), metrics


class SearchClient:
    """Injectable wrapper: the content stage receives it, tests replace it."""

    async def generate(self, schema: type[T], system: str, payload: str) -> tuple[T, list[str], CallMetrics]:
        return await ask(schema, system, payload)

    async def text(self, system: str, payload: str) -> tuple[str, list[str], CallMetrics]:
        return await ask_text(system, payload)
