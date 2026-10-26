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


# --------------------------------------------------------- устойчивость разбора

def test_many_unterminated_comments_do_not_blow_up_the_scan(handler: HtmlHandler) -> None:
    """Незакрытая конструкция обязана дочитываться один раз, а не с каждой позиции заново.

    Потолки на число кусков тут не помогают: текста между тегами нет, счётчик не растёт, а
    разбор уже занят перебором. Поэтому проверяем временем.
    """
    import time

    source = ("<!--" * 60_000 + "<p>текст</p>").encode("utf-8")

    started = time.monotonic()
    handler.extract(source)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"разбор занял {elapsed:.1f} с — похоже на перебор с каждой позиции"


def test_an_unclosed_script_does_not_blow_up_the_scan(handler: HtmlHandler) -> None:
    import time

    source = ("<script>" * 40_000 + "<p>текст</p>").encode("utf-8")

    started = time.monotonic()
    handler.extract(source)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"разбор занял {elapsed:.1f} с"


def test_an_oversized_file_is_refused_before_parsing(handler: HtmlHandler) -> None:
    from content_factory.translation.errors import DocumentFormatError
    from content_factory.translation.handlers.html import MAX_BYTES

    with pytest.raises(DocumentFormatError, match="велик"):
        handler.extract(b"<p>a</p>" * (MAX_BYTES // 8 + 10))


def test_many_translations_are_applied_without_quadratic_rebuilding(handler: HtmlHandler) -> None:
    """Сборка результата срезами строки переписывает весь файл на каждую замену."""
    import time

    source = ("<p>text</p>" * 5_000).encode("utf-8")
    units = handler.extract(source)
    translations = {unit.unit_id: "текст" for unit in units}

    started = time.monotonic()
    result = handler.apply(source, translations)
    elapsed = time.monotonic() - started

    assert result.count("текст".encode()) == 5_000
    assert elapsed < 5, f"сборка заняла {elapsed:.1f} с"


def test_many_closed_script_tags_do_not_copy_the_file_each_time(handler: HtmlHandler) -> None:
    """Поиск закрывающего тега не должен приводить файл к нижнему регистру целиком.

    Незакрытые теги обрывают разбор сразу, поэтому промах прячется именно за закрытыми парами:
    каждая копия строки — проход по всему файлу, и вместе они дают квадрат.
    """
    import time

    source = ("<script>var x = 1;</script><p>текст</p>" * 20_000).encode("utf-8")

    started = time.monotonic()
    units = handler.extract(source)
    elapsed = time.monotonic() - started

    assert len(units) == 20_000, "текст между парами нашёлся"
    assert elapsed < 5, f"разбор занял {elapsed:.1f} с — похоже на копирование файла на каждый тег"


def test_a_closing_tag_in_a_different_case_is_found(handler: HtmlHandler) -> None:
    source = b"<SCRIPT>var x = 1;</SCRIPT><p>text</p>"

    texts = [u.text for u in handler.extract(source)]

    assert texts == ["text"], "регистр тега не должен мешать"
