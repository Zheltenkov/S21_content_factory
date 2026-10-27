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


# -------------------------------------------------------------- оглавление после перевода

def _translate(handler: PlainTextHandler, source: str, table: dict[str, str]) -> str:
    units = handler.extract(source.encode())
    translations = {u.unit_id: table.get(u.text, u.text) for u in units}
    return handler.apply(source.encode(), translations).decode("utf-8")


def test_a_table_of_contents_still_points_at_its_headings() -> None:
    """Боевой прогон перевёл заголовки и оставил якоря русскими — оглавление умерло.

    Якорь выводится из заголовка, поэтому после перевода его можно пересчитать без модели.
    """
    nl = chr(10)
    source = (
        "## Содержание" + nl + nl
        + "1. [Глава I](#глава-i)" + nl
        + "2. [Предисловие](#предисловие)" + nl + nl
        + "## Глава I" + nl + nl
        + "### Предисловие" + nl
    )
    table = {
        "## Содержание": "## Contents",
        "1. [Глава I](#глава-i)" + nl + "2. [Предисловие](#предисловие)":
            "1. [Chapter I](#глава-i)" + nl + "2. [Foreword](#предисловие)",
        "## Глава I": "## Chapter I",
        "### Предисловие": "### Foreword",
    }

    result = _translate(PlainTextHandler(), source, table)

    assert "(#chapter-i)" in result, result
    assert "(#foreword)" in result, result
    assert "глава-i" not in result


def test_an_anchor_that_is_not_a_heading_is_left_alone() -> None:
    """Ссылка на чужой якорь — не наше дело: пересчитывать нечего."""
    nl = chr(10)
    source = "[к форме](#feedback-form)" + nl + nl + "## Глава I" + nl
    table = {"[к форме](#feedback-form)": "[to the form](#feedback-form)", "## Глава I": "## Chapter I"}

    result = _translate(PlainTextHandler(), source, table)

    assert "(#feedback-form)" in result


def test_headings_that_do_not_line_up_leave_the_anchors_as_they_are() -> None:
    """Модель склеила или разбила заголовки — соответствие потеряно, и гадать нельзя."""
    nl = chr(10)
    source = "[Глава I](#глава-i)" + nl + nl + "## Глава I" + nl + nl + "## Глава II" + nl
    table = {
        "[Глава I](#глава-i)": "[Chapter I](#глава-i)",
        "## Глава I": "## Chapter I" + nl + nl + "## Extra",
        "## Глава II": "## Chapter II",
    }

    result = _translate(PlainTextHandler(), source, table)

    assert "(#глава-i)" in result, "при расхождении числа заголовков ничего не трогаем"


def test_without_translations_the_anchors_are_untouched() -> None:
    nl = chr(10)
    source = "[Глава I](#глава-i)" + nl + nl + "## Глава I" + nl
    data = source.encode()

    assert PlainTextHandler().apply(data, {}) == data
