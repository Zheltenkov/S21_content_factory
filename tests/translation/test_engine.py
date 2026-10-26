"""Движок перевода: список единиц → словарь переводов, пакетами и с одним повтором.

Вынут из _translate_docx_units: тот же контракт id → текст, но без привязки к DOCX,
потому что по нему теперь работают все форматы.
"""

from __future__ import annotations

import json

import pytest

from content_factory.translation.engine import translate_units
from content_factory.translation.units import TextUnit


class FakeLlm:
    """Отвечает переводом вида «[ru] исходный текст» и запоминает запросы."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.replies = replies or []

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        self.calls.append({"system": system, "user": user, **kwargs})
        if self.replies:
            return self.replies.pop(0)
        marker = "Входные данные:\n"
        payload = json.loads(user[user.index(marker) + len(marker) :])
        return json.dumps(
            {"translations": {f["id"]: f"[ru] {f['text']}" for f in payload["fragments"]}},
            ensure_ascii=False,
        )


def _units(count: int, text: str = "Hello") -> list[TextUnit]:
    return [TextUnit(unit_id=f"{i:04d}", text=f"{text} {i}") for i in range(count)]


def test_every_unit_comes_back_translated() -> None:
    llm = FakeLlm()

    result = translate_units(llm, _units(3), "russian")

    assert result == {"0000": "[ru] Hello 0", "0001": "[ru] Hello 1", "0002": "[ru] Hello 2"}
    assert len(llm.calls) == 1


def test_without_units_the_model_is_not_called_at_all() -> None:
    llm = FakeLlm()

    assert translate_units(llm, [], "russian") == {}
    assert llm.calls == []


def test_long_input_is_split_into_batches() -> None:
    llm = FakeLlm()
    units = [TextUnit(unit_id=f"{i:04d}", text="я" * 3000) for i in range(4)]

    result = translate_units(llm, units, "russian", batch_chars=5000)

    assert len(result) == 4
    assert len(llm.calls) == 4, "каждая единица по 3000 знаков не умещается с соседом"


def test_a_garbled_answer_is_retried_once() -> None:
    good = json.dumps({"translations": {"0000": "перевод"}}, ensure_ascii=False)
    llm = FakeLlm(replies=["это не json", good])

    result = translate_units(llm, _units(1), "russian")

    assert result == {"0000": "перевод"}
    assert len(llm.calls) == 2


def test_a_missing_fragment_is_an_error_naming_it() -> None:
    llm = FakeLlm(replies=[json.dumps({"translations": {"0000": "перевод"}})] * 3)

    with pytest.raises(RuntimeError, match="0001"):
        translate_units(llm, _units(2), "russian")


def test_the_hint_reaches_the_model() -> None:
    llm = FakeLlm()

    translate_units(llm, [TextUnit(unit_id="0000", text="Price", hint="заголовок колонки")], "russian")

    assert "заголовок колонки" in str(llm.calls[0]["user"])


def test_progress_is_reported_per_batch() -> None:
    llm = FakeLlm()
    seen: list[str] = []
    units = [TextUnit(unit_id=f"{i:04d}", text="я" * 3000) for i in range(3)]

    translate_units(llm, units, "russian", batch_chars=5000, progress_callback=seen.append)

    assert len(seen) == 3
