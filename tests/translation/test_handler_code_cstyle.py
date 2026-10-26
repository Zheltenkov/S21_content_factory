"""Комментарии в C-подобных языках и SQL.

Общий костяк: строки, ``//`` до конца строки и ``/* */``. У SQL вместо ``//`` — ``--``.

Неоднозначность здесь одна, зато настоящая: в JavaScript косая черта начинает и деление, и
регулярное выражение, а внутри выражения ``//`` вовсе не комментарий. Различить это без полного
разбора языка нельзя, поэтому строка с косой чертой перед предполагаемым комментарием
пропускается целиком — как в shell с обратными кавычками.
"""

from __future__ import annotations

import pytest

from content_factory.translation.handlers.code import CStyleCommentHandler, SqlCommentHandler

JS = """// Модуль обучения
const NAME = "project";  // имя
const URL = "https://example.com/path";
const TEMPLATE = `текст // не комментарий`;

/* Блок пояснения
   во вторую строку */
function train(data) {
  return data;
}
"""


@pytest.fixture
def js() -> CStyleCommentHandler:
    return CStyleCommentHandler()


@pytest.fixture
def sql() -> SqlCommentHandler:
    return SqlCommentHandler()


def _texts(handler, text: str) -> list[str]:
    return [u.text for u in handler.extract(text.encode("utf-8"))]


def test_line_and_block_comments_become_units(js: CStyleCommentHandler) -> None:
    texts = _texts(js, JS)

    assert "Модуль обучения" in texts
    assert "имя" in texts
    assert any("Блок пояснения" in text for text in texts)


def test_slashes_inside_a_string_are_not_a_comment(js: CStyleCommentHandler) -> None:
    joined = " ".join(_texts(js, JS))

    assert "example.com" not in joined


def test_a_template_literal_is_not_a_comment(js: CStyleCommentHandler) -> None:
    joined = " ".join(_texts(js, JS))

    assert "не комментарий" not in joined


def test_a_line_with_a_slash_before_the_comment_is_skipped(js: CStyleCommentHandler) -> None:
    """Деление и регулярное выражение неразличимы без полного разбора — такую строку не трогаем."""
    source = "const re = /\\/\\//;  // пояснение\nconst half = a / b;  // тоже\n".encode()

    assert _texts(js, source.decode("utf-8")) == []


def test_without_translations_the_file_is_byte_identical(js: CStyleCommentHandler) -> None:
    source = JS.encode("utf-8")

    assert js.apply(source, {}) == source


def test_a_translated_line_comment_replaces_only_itself(js: CStyleCommentHandler) -> None:
    source = JS.encode("utf-8")
    unit = next(u for u in js.extract(source) if u.text == "имя")

    result = js.apply(source, {unit.unit_id: "name"}).decode("utf-8")

    assert "// name\n" in result
    assert 'const URL = "https://example.com/path";' in result, "строка не тронута"
    assert "`текст // не комментарий`" in result, "шаблонная строка не тронута"


def test_a_block_comment_keeps_its_borders(js: CStyleCommentHandler) -> None:
    source = JS.encode("utf-8")
    unit = next(u for u in js.extract(source) if "Блок пояснения" in u.text)

    result = js.apply(source, {unit.unit_id: "Block note"}).decode("utf-8")

    assert "/* Block note */" in result or "/*Block note*/" in result


def test_a_translation_cannot_close_the_block_early(js: CStyleCommentHandler) -> None:
    """Звёздочка со слешем в переводе закрыла бы пояснение, а остаток стал бы кодом."""
    source = JS.encode("utf-8")
    unit = next(u for u in js.extract(source) if "Блок пояснения" in u.text)

    result = js.apply(source, {unit.unit_id: "конец */ alert(1) /*"}).decode("utf-8")

    assert "alert(1)" not in result or result == JS, "код из перевода не должен появиться"


def test_an_unterminated_block_disables_the_whole_file(js: CStyleCommentHandler) -> None:
    source = b"/* unterminated\nconst a = 1;\n"

    assert js.extract(source) == []


def test_sql_uses_double_dash(sql: SqlCommentHandler) -> None:
    source = "-- Выборка проектов\nSELECT name FROM projects;  -- хвост\n".encode()

    assert _texts(sql, source.decode("utf-8")) == ["Выборка проектов", "хвост"]


def test_sql_ignores_a_dash_inside_a_string(sql: SqlCommentHandler) -> None:
    source = "SELECT '-- не комментарий' AS marker;\n".encode()

    assert _texts(sql, source.decode("utf-8")) == []
