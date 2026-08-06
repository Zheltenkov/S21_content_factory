"""Русификация англоязычных названий УП через LLM (термины остаются)."""

from __future__ import annotations

import json

from content_factory.rpd.translate import russify_up
from content_factory.rpd.up_models import UpModule, UpPlan, UpRow


def _up() -> UpPlan:
    return UpPlan(
        title="Курс",
        modules=[
            UpModule(
                title="self-introduction and professional role",
                rows=[UpRow(block_title="self-introduction and professional role", name="Проект", outcomes_can="умеет применять", hours="4", skills=["Навык"])],
            )
        ],
    )


class _FakeClient:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls = 0
        self.roles: list[str] = []

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        del system, user
        self.calls += 1
        self.roles.append(str(kwargs["llm_role"]))
        return json.dumps(self.mapping, ensure_ascii=False)


def test_russify_translates_english_titles() -> None:
    client = _FakeClient({"self-introduction and professional role": "Самопрезентация и профессиональная роль"})
    out = russify_up(_up(), client)
    assert out.modules[0].title == "Самопрезентация и профессиональная роль"
    assert out.modules[0].rows[0].block_title == "Самопрезентация и профессиональная роль"
    assert client.calls == 1
    assert client.roles == ["rpd_translate"]


def test_russify_noop_without_client_or_latin() -> None:
    up = _up()
    assert russify_up(up, None) is up  # без клиента — как есть
    russian = UpPlan(title="Курс", modules=[UpModule(title="Модуль первый", rows=[])])
    # нет латиницы — LLM не зовём
    client = _FakeClient({})
    russify_up(russian, client)
    assert client.calls == 0


def test_russify_survives_bad_llm_response() -> None:
    class _Bad:
        def complete(self, system: str, user: str, **kwargs: object) -> str:
            return "не json"

    out = russify_up(_up(), _Bad())
    assert out.modules[0].title == "self-introduction and professional role"  # оставили оригинал
