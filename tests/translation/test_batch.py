"""Перевод набора файлов: что переводится, что переносится, что попадает в отчёт.

Главная проверка здесь — частичный успех. Прогон не должен падать из-за одного файла, и
каждый из четырёх исходов должен быть виден в отчёте, а не скрыт за «архив скачался».
"""

from __future__ import annotations

import json

import pytest

from content_factory.translation.archive import ArchiveMember, take_inventory
from content_factory.translation.batch import translate_files
from content_factory.translation.report import FAILED, SKIPPED, TRANSLATED, UNCHANGED


class FakeLlm:
    """Переводит, приписывая ``[en]`` к каждому фрагменту."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        self.calls += 1
        marker = "Входные данные:\n"
        payload = json.loads(user[user.index(marker) + len(marker) :])
        return json.dumps(
            {"translations": {f["id"]: f"[en] {f['text']}" for f in payload["fragments"]}},
            ensure_ascii=False,
        )


class AngryLlm:
    """Падает на любом запросе."""

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        raise RuntimeError("шлюз недоступен")


def _run(members: list[ArchiveMember], llm: object = None, **kwargs: object):
    entries = take_inventory(members)
    return translate_files(
        llm or FakeLlm(), members, entries, target_language="en", **kwargs
    )


def test_a_selected_file_comes_back_translated() -> None:
    members = [ArchiveMember("README.md", "Привет мир".encode())]

    translations, outcomes = _run(members)

    assert translations["README.md"].decode("utf-8") == "[en] Привет мир"
    assert [(o.path, o.status) for o in outcomes] == [("README.md", TRANSLATED)]


def test_an_unselected_file_is_not_sent_to_the_model() -> None:
    llm = FakeLlm()
    members = [ArchiveMember("logo.png", b"\x89PNG")]

    translations, outcomes = _run(members, llm)

    assert translations == {}
    assert llm.calls == 0
    assert outcomes[0].status == SKIPPED
    assert outcomes[0].detail == "формат не переводится"


def test_one_failing_file_does_not_stop_the_run() -> None:
    """Иначе один битый файл посреди архива стоил бы всего прогона."""
    members = [
        ArchiveMember("a.md", "Первый".encode()),
        ArchiveMember("b.md", "Второй".encode()),
    ]
    entries = take_inventory(members)

    class HalfAngry(FakeLlm):
        def complete(self, system: str, user: str, **kwargs: object) -> str:
            if "Второй" in user:
                raise RuntimeError("шлюз недоступен")
            return super().complete(system, user, **kwargs)

    translations, outcomes = translate_files(
        HalfAngry(), members, entries, target_language="en"
    )

    assert "a.md" in translations
    assert "b.md" not in translations, "упавший файл остаётся исходным"
    by_path = {o.path: o for o in outcomes}
    assert by_path["a.md"].status == TRANSLATED
    assert by_path["b.md"].status == FAILED
    assert "шлюз недоступен" in by_path["b.md"].detail


def test_a_handler_that_refuses_the_translation_is_called_out() -> None:
    """Обработчик кода возвращает исходные байты, когда перевод сломал бы файл."""
    members = [ArchiveMember("src/t.sh", "# Сборка\n".encode())]
    entries = take_inventory(members)

    class BackslashLlm(FakeLlm):
        def complete(self, system: str, user: str, **kwargs: object) -> str:
            marker = "Входные данные:\n"
            payload = json.loads(user[user.index(marker) + len(marker) :])
            return json.dumps(
                {"translations": {f["id"]: "build " + chr(92) + "n rm -rf /"
                                  for f in payload["fragments"]}},
                ensure_ascii=False,
            )

    translations, outcomes = translate_files(
        BackslashLlm(), members, entries, target_language="en"
    )

    assert translations == {}, "нетронутый файл не нужно подменять в архиве"
    assert outcomes[0].status == UNCHANGED
    assert outcomes[0].detail


def test_progress_is_reported_per_file() -> None:
    seen: list[str] = []
    members = [
        ArchiveMember("a.md", "Первый".encode()),
        ArchiveMember("b.md", "Второй".encode()),
    ]

    _run(members, progress_callback=seen.append)

    assert any("a.md" in note for note in seen)
    assert any("b.md" in note for note in seen)


def test_everything_down_is_still_a_finished_run() -> None:
    """Шлюз лежит целиком: прогон доходит до конца, а отчёт называет каждый файл."""
    members = [ArchiveMember("a.md", "Первый".encode())]

    translations, outcomes = _run(members, AngryLlm())

    assert translations == {}
    assert outcomes[0].status == FAILED


def test_translating_to_russian_asks_the_model_for_nothing() -> None:
    """Исходный язык русский, поэтому ru → ru это не работа, а проверка раскладки."""
    llm = FakeLlm()
    members = [ArchiveMember("README.md", "Привет".encode())]
    entries = take_inventory(members)

    translations, outcomes = translate_files(
        llm, members, entries, target_language="ru"
    )

    assert llm.calls == 0
    assert translations == {}
    assert outcomes[0].status == UNCHANGED


@pytest.mark.parametrize("selected", [["a.md"], []])
def test_only_the_named_paths_are_translated(selected: list[str]) -> None:
    """Методист снимает галочки, и снятое не должно стоить ни запроса, ни денег."""
    llm = FakeLlm()
    members = [
        ArchiveMember("a.md", "Первый".encode()),
        ArchiveMember("b.md", "Второй".encode()),
    ]
    entries = take_inventory(members)

    translations, outcomes = translate_files(
        llm, members, entries, target_language="en", only=selected
    )

    assert set(translations) == set(selected)
    assert llm.calls == len(selected)
    skipped = {o.path for o in outcomes if o.status == SKIPPED}
    assert skipped == {"a.md", "b.md"} - set(selected)


def test_an_unchanged_plain_file_is_not_called_a_failed_integrity_check() -> None:
    """Боевой прогон сказал про .txt «не прошёл проверку целостности». Проверки там нет.

    Отказаться от перевода умеет только обработчик кода, который разбирает результат заново.
    У остальных те же байты значат ровно одно: перевод совпал с исходником.
    """
    members = [ArchiveMember("notes.txt", "Привет".encode())]
    entries = take_inventory(members)

    class EchoLlm(FakeLlm):
        def complete(self, system: str, user: str, **kwargs: object) -> str:
            marker = "Входные данные:" + chr(10)
            payload = json.loads(user[user.index(marker) + len(marker) :])
            return json.dumps(
                {"translations": {f["id"]: f["text"] for f in payload["fragments"]}},
                ensure_ascii=False,
            )

    _translations, outcomes = translate_files(
        EchoLlm(), members, entries, target_language="en"
    )

    assert outcomes[0].status == UNCHANGED
    assert "целостност" not in outcomes[0].detail, outcomes[0].detail
    assert "совпал" in outcomes[0].detail


def test_a_refused_code_translation_still_names_the_integrity_check() -> None:
    """У обработчика кода та же картина значит другое, и отчёт обязан их различать."""
    members = [ArchiveMember("t.sh", "# Сборка".encode() + chr(10).encode())]
    entries = take_inventory(members)

    class BackslashLlm(FakeLlm):
        def complete(self, system: str, user: str, **kwargs: object) -> str:
            marker = "Входные данные:" + chr(10)
            payload = json.loads(user[user.index(marker) + len(marker) :])
            return json.dumps(
                {"translations": {f["id"]: "build " + chr(92) + "n x" for f in payload["fragments"]}},
                ensure_ascii=False,
            )

    _translations, outcomes = translate_files(
        BackslashLlm(), members, entries, target_language="en"
    )

    assert outcomes[0].status == UNCHANGED
    assert "целостност" in outcomes[0].detail
