"""Provider contract recovery is bounded and includes the cost of both calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from project_generator_lab.concept.models import GroundedReview
from project_generator_lab.core import llm
from project_generator_lab.tests.concept.workflow_fixtures import grounded_review


def response(content, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20))


def mock_provider(monkeypatch, responses):
    create = AsyncMock(side_effect=responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: context)
    monkeypatch.setattr(llm, "model_configuration", lambda: ("private-test-key", "unused", "fixture"))
    return create


def test_schema_repair_keeps_problem_and_counts_both_calls(monkeypatch):
    valid = grounded_review()
    valid.outcomes[0].status = "problem"
    valid.outcomes[0].gap = "Применение не запланировано"
    broken = valid.model_copy(deep=True).model_dump()
    del broken["outcomes"][0]["required_action"]
    import json

    create = mock_provider(monkeypatch, [response(json.dumps(broken)), response(valid.model_dump_json())])
    result, metrics = asyncio.run(llm.ModelClient().generate(GroundedReview, "Исходные правила", "Исходные входы"))
    assert result.outcomes[0].status == "problem" and result.outcomes[0].gap
    assert metrics.schema_repairs == 1
    assert (metrics.input_tokens, metrics.output_tokens) == (20, 40)
    assert create.await_count == 2
    messages = create.call_args.kwargs["messages"]
    assert messages[1]["content"] == "Исходные входы"
    assert 'required_action' in messages[-1]["content"]
    assert 'private-test-key' not in str(messages)


def test_invalid_second_response_stops_without_synthetic_success(monkeypatch):
    create = mock_provider(monkeypatch, [response("{}"), response("{}")])
    with pytest.raises(llm.ModelUnavailable, match="не прошёл схему после исправления \\(outcomes: missing"):
        asyncio.run(llm.ModelClient().generate(GroundedReview, "Правила", "Входы"))
    assert create.await_count == 2


def test_truncated_response_is_not_repaired_as_complete(monkeypatch):
    create = mock_provider(monkeypatch, [response("{}", "length")])
    with pytest.raises(llm.ModelUnavailable, match="не завершила"):
        asyncio.run(llm.ModelClient().generate(GroundedReview, "Правила", "Входы"))
    assert create.await_count == 1


def test_json_is_taken_out_of_a_fence_or_a_sentence_but_not_invented():
    from project_generator_lab.core.llm import json_text

    assert json_text('{"a": 1}') == '{"a": 1}'
    assert json_text('```json\n{"a": {"b": 2}}\n```') == '{"a": {"b": 2}}'
    assert json_text('Вот ответ:\n{"a": 1}\nГотово.') == '{"a": 1}'
    assert json_text("нет JSON") == "нет JSON"
    assert json_text("} перевёрнуто {") == "} перевёрнуто {"


def test_answer_ceiling_is_configurable(monkeypatch):
    from project_generator_lab.core.llm import max_output_tokens

    monkeypatch.delenv("PROJECT_LAB_MAX_OUTPUT_TOKENS", raising=False)
    assert max_output_tokens() == 11000
    monkeypatch.setenv("PROJECT_LAB_MAX_OUTPUT_TOKENS", "32000")
    assert max_output_tokens() == 32000


def test_lab_model_is_its_own_and_defaults_to_gpt_6_sol(monkeypatch):
    # POLZA_AI_MODEL belongs to the main generator; changing it must not move the lab.
    from project_generator_lab.core.llm import model_configuration

    monkeypatch.setenv("POLZA_AI_API_KEY", "k")
    monkeypatch.setenv("POLZA_AI_MODEL", "openai/gpt-5.4-mini")
    monkeypatch.delenv("PROJECT_LAB_MODEL", raising=False)
    monkeypatch.delenv("POLZA_AI_BASE_URL", raising=False)
    assert model_configuration() == ("k", "https://polza.ai/api/v1", "openai/gpt-6-sol")
    monkeypatch.setenv("PROJECT_LAB_MODEL", "anthropic/claude-opus-5.5")
    assert model_configuration()[2] == "anthropic/claude-opus-5.5"
