"""HTML: перевод текстовых узлов с сохранением разметки.

Прежде модуль превращал HTML в текст и текстом же отдавал — разметка терялась. Теперь файл
остаётся HTML: подменяется только текст между тегами. Содержимое ``<script>``, ``<style>`` и
комментариев не трогается, иначе перевод сломает код страницы.
"""

from __future__ import annotations

import pytest

from content_factory.translation.handlers.html import HtmlHandler

SAMPLE = (
    "<!DOCTYPE html>\n"
    '<html lang="en"><head><title>Intro to Machine Learning</title>\n'
    "<style>.a { color: red; }</style>\n"
    '<script>var greeting = "Hello";</script>\n'
    "</head>\n"
    "<body>\n"
    "  <!-- служебный комментарий -->\n"
    '  <h1 class="title">Chapter I</h1>\n'
    "  <p>Today we will help you with <b>basic tasks</b> in Python.</p>\n"
    "</body></html>\n"
)


@pytest.fixture
def handler() -> HtmlHandler:
    return HtmlHandler()


def test_text_between_tags_becomes_units(handler: HtmlHandler) -> None:
    texts = [u.text for u in handler.extract(SAMPLE.encode("utf-8"))]

    assert "Intro to Machine Learning" in texts
    assert "Chapter I" in texts
    assert "Today we will help you with" in texts
    assert "basic tasks" in texts


def test_script_and_style_are_not_translated(handler: HtmlHandler) -> None:
    texts = " ".join(u.text for u in handler.extract(SAMPLE.encode("utf-8")))

    assert "color: red" not in texts
    assert "var greeting" not in texts


def test_comments_are_not_translated(handler: HtmlHandler) -> None:
    texts = [u.text for u in handler.extract(SAMPLE.encode("utf-8"))]

    assert "служебный комментарий" not in texts


def test_tags_and_attributes_are_not_units(handler: HtmlHandler) -> None:
    texts = " ".join(u.text for u in handler.extract(SAMPLE.encode("utf-8")))

    assert "class" not in texts and "lang" not in texts and "html" not in texts


def test_without_translations_the_file_is_byte_identical(handler: HtmlHandler) -> None:
    source = SAMPLE.encode("utf-8")

    assert handler.apply(source, {}) == source


def test_a_translated_node_replaces_only_itself(handler: HtmlHandler) -> None:
    source = SAMPLE.encode("utf-8")
    unit = next(u for u in handler.extract(source) if u.text == "Chapter I")

    result = handler.apply(source, {unit.unit_id: "Глава I"}).decode("utf-8")

    assert '<h1 class="title">Глава I</h1>' in result
    assert "<b>basic tasks</b>" in result, "соседний узел и его теги не тронуты"
    assert '<script>var greeting = "Hello";</script>' in result


def test_entities_come_out_decoded_and_go_back_escaped(handler: HtmlHandler) -> None:
    source = b"<p>Caf&eacute; &amp; tea</p>"

    unit = handler.extract(source)[0]
    assert unit.text == "Café & tea", "модель должна видеть текст, а не сущности"

    result = handler.apply(source, {unit.unit_id: "Кофе & чай"}).decode("utf-8")
    assert result == "<p>Кофе &amp; чай</p>", "амперсанд обязан вернуться экранированным"


def test_a_translation_with_markup_characters_is_escaped(handler: HtmlHandler) -> None:
    source = b"<p>text</p>"
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: "1 < 2 и <b>не тег</b>"}).decode("utf-8")

    assert "&lt;b&gt;" in result, "разметка из перевода не должна становиться разметкой страницы"
