"""Writing parameters are bounded, scoped to the project and absent from evaluator calls."""

import asyncio

import pytest
from project_generator_lab.core.generation import GenerationSettings, active, writing
from project_generator_lab.core.llm import ModelClient
from project_generator_lab.tests.core.test_llm_contract import Provider, Tiny, reply
from pydantic import ValidationError


@pytest.mark.parametrize("values", [{"temperature": -1}, {"top_p": 0}, {"temperature": float("nan")},
                                    {"temperature": 0.8, "top_p": 0.9}, {"presence_penalty": 3}])
def test_invalid_or_ambiguous_sampling_is_rejected(values):
    with pytest.raises(ValidationError):
        GenerationSettings(**values)


def test_only_the_writing_request_carries_custom_parameters(monkeypatch):
    provider = Provider(monkeypatch, [reply('{"answer":"текст"}'), reply('{"answer":"проверка"}')])

    async def scenario():
        with writing(GenerationSettings(temperature=0.8, frequency_penalty=0.2)):
            await ModelClient().generate(Tiny, "Напиши", "Данные")
        await ModelClient().generate(Tiny, "Проверь", "Текст")

    asyncio.run(scenario())
    assert provider.requests[0]["extra_body"] == {"temperature": 0.8, "frequency_penalty": 0.2}
    assert "extra_body" not in provider.requests[1]
    assert active.get() is None


def test_concurrent_projects_do_not_share_preferences():
    async def project(value):
        with writing(GenerationSettings(temperature=value)):
            await asyncio.sleep(0)
            return active.get().temperature

    async def scenario():
        return await asyncio.gather(project(0.2), project(1.1))

    assert asyncio.run(scenario()) == [0.2, 1.1]
