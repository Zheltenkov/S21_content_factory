"""Обработчик простого текста: md, markdown, txt.

Текст режется на блоки по пустым строкам — так пакетирование работает, а структура markdown
переживает перевод. Главная проверка — невмешательство: без переводов файл обязан выйти
побайтно таким же, каким вошёл.
"""

from __future__ import annotations

import pytest

from content_factory.translation.handlers.plain import PlainTextHandler

SAMPLE = (
    "# Intro to Machine Learning\n"
    "\n"
    "Summary: Today we will help you.\n"
    "\n"
    "## Contents\n"
    "\n"
    "1. [Chapter I](#chapter-i)\n"
)


@pytest.fixture
def handler() -> PlainTextHandler:
    return PlainTextHandler()


def test_blocks_become_separate_units(handler: PlainTextHandler) -> None:
    units = handler.extract(SAMPLE.encode("utf-8"))

    assert [u.text for u in units] == [
        "# Intro to Machine Learning",
        "Summary: Today we will help you.",
        "## Contents",
        "1. [Chapter I](#chapter-i)",
    ]


def test_without_translations_the_file_is_byte_identical(handler: PlainTextHandler) -> None:
    source = SAMPLE.encode("utf-8")

    assert handler.apply(source, {}) == source


def test_a_translated_block_replaces_only_itself(handler: PlainTextHandler) -> None:
    source = SAMPLE.encode("utf-8")
    units = handler.extract(source)

    result = handler.apply(source, {units[0].unit_id: "# Введение в машинное обучение"}).decode("utf-8")

    assert result.startswith("# Введение в машинное обучение\n\n")
    assert "Summary: Today we will help you." in result, "непереведённый блок остаётся как был"


def test_the_byte_order_mark_survives(handler: PlainTextHandler) -> None:
    source = "\ufeffПривет\n".encode("utf-8")

    assert handler.apply(source, {}) == source
    assert handler.extract(source)[0].text == "Привет", "метка порядка байтов не часть текста"


def test_windows_line_endings_survive(handler: PlainTextHandler) -> None:
    source = b"first\r\n\r\nsecond\r\n"

    assert handler.apply(source, {}) == source


def test_an_empty_file_has_nothing_to_translate(handler: PlainTextHandler) -> None:
    assert handler.extract(b"") == []
    assert handler.apply(b"", {}) == b""
