"""Explicit model adapter with strict parsing and no synthetic fallback."""

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import TypedDict, TypeVar

from openai import APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, ValidationError

from .generation import active, record_sampling

T = TypeVar("T", bound=BaseModel)


class SamplingOptions(TypedDict, total=False):
    extra_body: dict[str, float]


# One line per model call — what, which model, how long, how many tokens, how it ended — so a slow, failing or costly
# call is seen in the server log, not only through complaints (audit 30.09.2026; the book's «transparency»). Never
# the prompt or the answer: they carry methodologists' materials.
calls = logging.getLogger("project_generator_lab.model_calls")
if not calls.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    calls.addHandler(handler)
    calls.setLevel(logging.INFO)
    calls.propagate = False


def log_call(schema: str, model: str, ms: int, input_tokens: int, output_tokens: int, repairs: int, outcome: str,
             cost_rub: float | None = None) -> None:
    from . import usage as ledger
    from .access import current_owner

    calls.info("model_call schema=%s model=%s ms=%d in=%d out=%d repairs=%d outcome=%s owner=%s", schema, model,
               ms, input_tokens, output_tokens, repairs, outcome, current_owner.get())
    # The same line, kept with the project the call was made for: the log is lost with the server's window.
    ledger.record("model", schema, model, input_tokens, output_tokens, ms, repairs, outcome, cost_rub)


def paid(so_far: float | None, usage: object) -> float | None:
    """What the attempts of one call were charged, added up as the gateway names it; None while it has named none."""
    from .usage import charged

    now = charged(usage) if usage is not None else None
    return so_far if now is None else (so_far or 0.0) + now


def since(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class ModelUnavailable(RuntimeError):
    """Provider failed or returned an invalid structured response."""

    # What the call had taken before it failed, when the provider said: a failure is paid for all the same.
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class CallMetrics:
    model: str
    elapsed_ms: int
    input_tokens: int
    output_tokens: int
    schema_repairs: int = 0


# Chosen by the model comparison of 27–28.09.2026 (README, «Сравнение моделей»).
DEFAULT_MODEL = "openai/gpt-6-sol"


def model_configuration() -> tuple[str, str, str]:
    # Reuse the configured account, keeping credentials on the server only.
    key = os.getenv("POLZA_AI_API_KEY", "")
    base = os.getenv("POLZA_AI_BASE_URL", "https://polza.ai/api/v1")
    # The lab has its own model: POLZA_AI_MODEL belongs to the main generator and must not switch it silently.
    model = os.getenv("PROJECT_LAB_MODEL") or DEFAULT_MODEL
    return key, base, model


def reviewer_model() -> str:
    """Optional separate reviewer model, ideally another family; empty means the main model reviews."""
    return os.getenv("PROJECT_LAB_REVIEWER_MODEL", "").strip()


def max_output_tokens() -> int:
    """Answer ceiling per call. Tokenizers differ: the same Russian JSON costs Claude noticeably more
    tokens than GPT, so a ceiling that fits one family truncates another."""
    return int(os.getenv("PROJECT_LAB_MAX_OUTPUT_TOKENS", "11000"))


def json_text(content: str) -> str:
    """The JSON object inside a reply. Some providers ignore json_object mode and wrap the answer
    in a ```json fence or add a sentence around it; the schema check itself stays strict."""
    text = content.strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


class ModelClient:
    def __init__(self, model: str | None = None) -> None:
        self.model = model

    async def generate(self, schema: type[T], system: str, payload: str) -> tuple[T, CallMetrics]:
        key, base, configured = model_configuration()
        model = getattr(self, "model", None) or configured
        if not key:
            raise ModelUnavailable("На сервере не настроен POLZA_AI_API_KEY")
        started = time.monotonic()
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system + "\nВерни только JSON по схеме:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)},
            {"role": "user", "content": payload},
        ]
        input_tokens = output_tokens = repairs = 0
        cost: float | None = None  # what the gateway charged for the attempts, in roubles; None until it says
        preferences = active.get()
        sampling = preferences.sampling() if preferences else {}
        options: SamplingOptions = {"extra_body": sampling} if sampling else {}
        try:
            async with AsyncOpenAI(api_key=key, base_url=base, timeout=float(os.getenv("PROJECT_LAB_TIMEOUT_SECONDS", "150")), max_retries=1) as client:
                # Repair only an invalid response contract, once; never substitute a synthetic result.
                for attempt in range(2):
                    response = await client.chat.completions.create(
                        model=model, messages=messages,
                        response_format={"type": "json_object"}, max_completion_tokens=max_output_tokens(),
                        **options,
                    )
                    usage = response.usage
                    input_tokens += usage.prompt_tokens if usage else 0
                    output_tokens += usage.completion_tokens if usage else 0
                    cost = paid(cost, usage)
                    choice = response.choices[0]
                    if choice.finish_reason != "stop":
                        raise ModelUnavailable(f"Модель не завершила ответ ({choice.finish_reason}). Повторите подготовку с меньшим объёмом материалов.")
                    content = choice.message.content or ""
                    try:
                        result = schema.model_validate_json(json_text(content))
                    except ValidationError as invalid:
                        if attempt:
                            raise
                        # Field paths/types suffice; avoid logging provider content or credentials.
                        errors = [{"path": list(e["loc"]), "type": e["type"]} for e in invalid.errors()][:30]
                        repairs = 1
                        messages.extend([
                            {"role": "assistant", "content": content},
                            {"role": "user", "content": "Исправь только формат JSON по исходной схеме. Сохрани требования, найденные проблемы и статусы. Заполни пропущенные обязательные поля по исходным данным, не выдумывая факты. Верни полный JSON. Ошибки: " + json.dumps(errors, ensure_ascii=False)},
                        ])
                        continue
                    elapsed = since(started)
                    record_sampling(model, sampling, sampling)
                    log_call(schema.__name__, model, elapsed, input_tokens, output_tokens, attempt, "ok", cost)
                    return result, CallMetrics(model, elapsed, input_tokens, output_tokens, attempt)
            raise ModelUnavailable("Не удалось восстановить формат ответа модели.")
        except asyncio.CancelledError:
            # The job was stopped while the model was answering. The call was made — and an attempt answered before
            # is paid for: it is written as the others are, then the stop goes on.
            log_call(schema.__name__, model, since(started), input_tokens, output_tokens, repairs, "cancelled", cost)
            raise
        except ModelUnavailable as exc:
            log_call(schema.__name__, model, since(started), input_tokens, output_tokens, repairs, f"failed:{str(exc)[:60]}", cost)
            exc.input_tokens, exc.output_tokens = input_tokens, output_tokens
            raise
        except ValidationError as exc:
            log_call(schema.__name__, model, since(started), input_tokens, output_tokens, repairs, "failed:schema", cost)
            # Paths and error types only: enough to tell a format problem from a content gap.
            where = "; ".join(f"{'.'.join(str(x) for x in e['loc']) or 'ответ'}: {e['type']}" for e in exc.errors()[:5])
            failure = ModelUnavailable(f"Ответ модели не прошёл схему после исправления ({where}). Попробуйте повторить запуск.")
            failure.input_tokens, failure.output_tokens = input_tokens, output_tokens
            raise failure from exc
        except APIStatusError as exc:
            log_call(schema.__name__, model, since(started), input_tokens, output_tokens, repairs, f"failed:http{exc.status_code}", cost)
            # The HTTP status tells a missing model (404) or a limit (429) from a provider failure (5xx).
            hint = (" Пользовательские параметры генерации могут не поддерживаться этой моделью; "
                    "верните их в режим «По умолчанию» и повторите." if sampling and exc.status_code in {400, 422} else "")
            failure = ModelUnavailable(f"Шлюз модели ответил ошибкой {exc.status_code}.{hint} Попробуйте повторить запуск.")
            failure.input_tokens, failure.output_tokens = input_tokens, output_tokens
            raise failure from exc
        except Exception as exc:
            log_call(schema.__name__, model, since(started), input_tokens, output_tokens, repairs, f"failed:{type(exc).__name__}", cost)
            # Never expose provider payloads, credentials or chain-of-thought in UI/logs.
            failure = ModelUnavailable(f"Не удалось получить корректный ответ модели ({type(exc).__name__}). Попробуйте повторить запуск.")
            failure.input_tokens, failure.output_tokens = input_tokens, output_tokens
            raise failure from exc
