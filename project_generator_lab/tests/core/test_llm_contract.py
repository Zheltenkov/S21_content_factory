"""What the lab sends to the provider and how it reports failures — pinned field by field.

Written against mutants that survived: the older fakes answered but never looked at the request.
"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError
from project_generator_lab.core import llm
from pydantic import BaseModel, Field, create_model


class Tiny(BaseModel):
    answer: str = Field(description="Ответ по-русски")


def reply(content, finish="stop", usage=(10, 20)):
    counted = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]) if usage else None
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))],
                           usage=counted)


class Provider:
    """Records the client settings and every request; answers from a script."""

    def __init__(self, monkeypatch, answers, key="secret-key", base="https://gateway.test/v1", model="configured/model"):
        self.answers, self.requests, self.client_kwargs = list(answers), [], {}
        monkeypatch.setattr(llm, "model_configuration", lambda: (key, base, model))
        monkeypatch.setattr(llm, "AsyncOpenAI", self.open)

    def open(self, **kwargs):
        self.client_kwargs = kwargs
        provider = self

        class Session:
            async def __aenter__(self):
                async def create(**request):
                    provider.requests.append(request)
                    answer = provider.answers.pop(0)
                    if isinstance(answer, BaseException):
                        raise answer
                    return answer

                return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

            async def __aexit__(self, *exc):
                return False

        return Session()


def run(client, schema=Tiny, system="Правила", payload="Входы"):
    return asyncio.run(client.generate(schema, system, payload))


def test_request_carries_model_schema_limits_and_both_messages(monkeypatch):
    monkeypatch.delenv("PROJECT_LAB_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("PROJECT_LAB_MAX_OUTPUT_TOKENS", raising=False)
    provider = Provider(monkeypatch, [reply('{"answer": "да"}')])
    result, metrics = run(llm.ModelClient())
    assert result.answer == "да"
    assert provider.client_kwargs == {"api_key": "secret-key", "base_url": "https://gateway.test/v1", "timeout": 150.0, "max_retries": 1}
    request = provider.requests[0]
    assert set(request) == {"model", "messages", "response_format", "max_completion_tokens"}
    assert (request["model"], request["response_format"], request["max_completion_tokens"]) == ("configured/model", {"type": "json_object"}, 11000)
    system, user = request["messages"]
    assert (system["role"], user["role"], user["content"]) == ("system", "user", "Входы")
    schema = json.dumps(Tiny.model_json_schema(), ensure_ascii=False)
    assert system["content"] == "Правила\nВерни только JSON по схеме:\n" + schema and "Ответ по-русски" in system["content"]
    assert (metrics.model, metrics.input_tokens, metrics.output_tokens, metrics.schema_repairs) == ("configured/model", 10, 20, 0)


def test_explicit_model_and_limits_from_the_environment(monkeypatch):
    monkeypatch.setenv("PROJECT_LAB_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("PROJECT_LAB_MAX_OUTPUT_TOKENS", "32000")
    provider = Provider(monkeypatch, [reply('{"answer": "да"}')])
    _, metrics = run(llm.ModelClient(model="anthropic/claude-opus-5.5"))
    assert provider.client_kwargs["timeout"] == 600.0
    assert provider.requests[0]["max_completion_tokens"] == 32000
    assert provider.requests[0]["model"] == metrics.model == "anthropic/claude-opus-5.5"


def test_elapsed_time_is_measured_in_milliseconds(monkeypatch):
    clock = iter([100.0, 101.25])
    # Only the module's own clock: the event loop keeps the real one.
    monkeypatch.setattr(llm, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    Provider(monkeypatch, [reply('{"answer": "да"}', usage=None)])
    _, metrics = run(llm.ModelClient())
    assert (metrics.elapsed_ms, metrics.input_tokens, metrics.output_tokens) == (1250, 0, 0)


def test_repair_turn_shows_the_model_its_answer_and_only_paths_of_errors(monkeypatch):
    many = create_model("Many", **{f"f{i}": (str, ...) for i in range(35)})  # type: ignore[call-overload]
    provider = Provider(monkeypatch, [reply("{}"), reply(json.dumps({f"f{i}": "x" for i in range(35)}))])
    _, metrics = run(llm.ModelClient(), schema=many)
    assert metrics.schema_repairs == 1 and (metrics.input_tokens, metrics.output_tokens) == (20, 40)
    assistant, fix = provider.requests[1]["messages"][2:]
    assert assistant == {"role": "assistant", "content": "{}"}
    assert fix["role"] == "user"
    prefix = "Исправь только формат JSON по исходной схеме. Сохрани требования, найденные проблемы и статусы. "
    assert fix["content"].startswith(prefix) and "Верни полный JSON. Ошибки: " in fix["content"]
    errors = json.loads(fix["content"].split("Ошибки: ", 1)[1])
    assert len(errors) == 30 and errors[0] == {"path": ["f0"], "type": "missing"}


def test_missing_key_is_reported_before_any_request(monkeypatch):
    provider = Provider(monkeypatch, [], key="")
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert str(error.value) == "На сервере не настроен POLZA_AI_API_KEY" and provider.requests == []


def test_failures_name_their_cause(monkeypatch):
    response = httpx.Response(402, request=httpx.Request("POST", "https://gateway.test"))
    status = APIStatusError("limit", response=response, body=None)  # type: ignore[arg-type]
    Provider(monkeypatch, [status])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert str(error.value) == "Шлюз модели ответил ошибкой 402. Попробуйте повторить запуск."
    Provider(monkeypatch, [TimeoutError("slow")])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert str(error.value) == "Не удалось получить корректный ответ модели (TimeoutError). Попробуйте повторить запуск."
    Provider(monkeypatch, [reply("[]"), reply("[]")])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert str(error.value) == "Ответ модели не прошёл схему после исправления (ответ: model_type). Попробуйте повторить запуск."
    many = create_model("Many", **{f"f{i}": (str, ...) for i in range(8)})  # type: ignore[call-overload]
    Provider(monkeypatch, [reply("{}"), reply("{}")])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient(), schema=many)
    assert "(f0: missing; f1: missing; f2: missing; f3: missing; f4: missing)" in str(error.value)
    Provider(monkeypatch, [reply("{}", finish="length")])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert str(error.value) == "Модель не завершила ответ (length). Повторите подготовку с меньшим объёмом материалов."


def test_configuration_reads_its_own_variables(monkeypatch):
    for name in ("POLZA_AI_API_KEY", "POLZA_AI_BASE_URL", "PROJECT_LAB_MODEL", "PROJECT_LAB_REVIEWER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert llm.model_configuration() == ("", "https://polza.ai/api/v1", "openai/gpt-6-sol")
    assert llm.reviewer_model() == ""
    monkeypatch.setenv("POLZA_AI_BASE_URL", "https://other.test/v1")
    monkeypatch.setenv("PROJECT_LAB_REVIEWER_MODEL", "  anthropic/claude-opus-5.5 ")
    assert llm.model_configuration()[1] == "https://other.test/v1"
    assert llm.reviewer_model() == "anthropic/claude-opus-5.5"
    assert llm.ModelClient().model is None and llm.ModelClient("m").model == "m"


def test_json_is_cut_to_the_outer_object():
    assert llm.json_text('{"a": 1} хвост') == '{"a": 1}'
    assert llm.json_text('  {"a": {"b": 2}}  ') == '{"a": {"b": 2}}'


def test_every_call_leaves_one_log_line_without_the_prompt_or_the_answer(monkeypatch, caplog):
    # Audit 30.09.2026: model calls left no trace — a slow, failing or costly call was seen only through complaints.
    records: list[str] = []
    monkeypatch.setattr(llm.calls, "info", lambda fmt, *args: records.append(fmt % args))
    Provider(monkeypatch, [reply('{"answer": "секретный текст методолога"}')])
    run(llm.ModelClient())
    Provider(monkeypatch, [reply("{}"), reply("{}")])
    with pytest.raises(llm.ModelUnavailable):
        run(llm.ModelClient())
    assert len(records) == 2
    assert records[0].startswith("model_call schema=Tiny model=") and "outcome=ok owner=local" in records[0] and " in=10 out=20 " in records[0]
    assert "model=None" not in records[0]
    assert "repairs=1 outcome=failed:schema" in records[1]
    assert not any("секретный" in r for r in records)


def test_a_failure_says_what_the_call_had_taken(monkeypatch):
    """A call that failed after the model answered was paid for: the error carries the tokens, so that a run can count them."""
    # Twice an answer of the wrong form: two completions were paid for.
    Provider(monkeypatch, [reply("[]", usage=(100, 7)), reply("[]", usage=(130, 9))])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert (error.value.input_tokens, error.value.output_tokens) == (230, 16)
    # An answer cut short was paid for too.
    Provider(monkeypatch, [reply('{"answer": "да', finish="length", usage=(50, 11000))])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert (error.value.input_tokens, error.value.output_tokens) == (50, 11000)
    # The first answer came and the request to mend it did not: the first was paid for.
    response = httpx.Response(502, request=httpx.Request("POST", "https://gateway.test"))
    Provider(monkeypatch, [reply("[]", usage=(100, 7)), APIStatusError("bad gateway", response=response, body=None)])  # type: ignore[arg-type]
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert (error.value.input_tokens, error.value.output_tokens) == (100, 7)
    Provider(monkeypatch, [reply("[]", usage=(100, 7)), TimeoutError("slow")])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert (error.value.input_tokens, error.value.output_tokens) == (100, 7)
    # Nothing came at all, or there was no key to ask with: nothing was taken.
    Provider(monkeypatch, [TimeoutError("slow")])
    with pytest.raises(llm.ModelUnavailable) as error:
        run(llm.ModelClient())
    assert (error.value.input_tokens, error.value.output_tokens) == (0, 0) == (llm.ModelUnavailable("x").input_tokens, llm.ModelUnavailable("x").output_tokens)
