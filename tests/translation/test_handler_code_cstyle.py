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


# ---------------------------------------------- отказ там, где язык расходится с разбором

def test_a_translation_ending_in_a_backslash_cannot_swallow_the_next_line(
    js: CStyleCommentHandler,
) -> None:
    """В C обратный слеш в конце пояснения продолжает его на следующую строку."""
    source = "// пояснение\nconst a = 1;\n".encode()
    unit = js.extract(source)[0]

    result = js.apply(source, {unit.unit_id: "note \\"}).decode()

    assert "const a = 1;" in result
    assert not result.splitlines()[0].rstrip().endswith("\\"), "перевод не должен кончаться слешем"


def test_a_translation_with_quotes_keeps_the_original(js: CStyleCommentHandler) -> None:
    source = "// пояснение\n".encode()
    unit = js.extract(source)[0]

    assert js.apply(source, {unit.unit_id: 'note "quoted"'}).decode() == "// пояснение\n"


def test_sql_does_not_treat_a_backslash_as_an_escape() -> None:
    """В SQL строка кончается на кавычке, слеш её не продолжает."""
    sql = SqlCommentHandler()
    source = "SELECT 'a\' AS x;  -- пояснение\n".encode()

    assert [u.text for u in sql.extract(source)] == ["пояснение"]


def test_sql_understands_a_doubled_quote() -> None:
    sql = SqlCommentHandler()
    source = "SELECT 'it''s -- не комментарий' AS x;  -- пояснение\n".encode()

    assert [u.text for u in sql.extract(source)] == ["пояснение"]


def test_a_go_raw_string_has_no_escapes() -> None:
    """В сырой строке Go обратный слеш — обычный знак, он не экранирует кавычку."""
    from content_factory.translation.handlers.code import GoCommentHandler

    source = "var p = `C:\\path\\`\n// пояснение\n".encode()

    assert [u.text for u in GoCommentHandler().extract(source)] == ["пояснение"]


def test_a_cpp_raw_string_stops_the_scan() -> None:
    """Сырые строки C++ разбор не моделирует — за такой файл он не берётся."""
    from content_factory.translation.handlers.code import CFamilyCommentHandler

    source = 'auto s = R"(a " // not a comment)";\n// пояснение\n'.encode()

    assert CFamilyCommentHandler().extract(source) == []


def test_a_java_text_block_stops_the_scan() -> None:
    from content_factory.translation.handlers.code import CFamilyCommentHandler

    source = 'String s = """\n  a " // not a comment\n  """;\n// пояснение\n'.encode()

    assert CFamilyCommentHandler().extract(source) == []


def test_cpp_digit_separators_stop_the_scan() -> None:
    """``1'000'000``: апостроф здесь разделитель разрядов, а не начало символьного литерала."""
    from content_factory.translation.handlers.code import CFamilyCommentHandler

    source = "int total = 1'000'000;\n// пояснение\n".encode()

    assert CFamilyCommentHandler().extract(source) == []


def test_java_unicode_escapes_stop_the_scan() -> None:
    """Java разворачивает ``\u002f`` до разбора, поэтому такой файл может значить не то, что видно."""
    from content_factory.translation.handlers.code import CFamilyCommentHandler

    source = "String s = \"\u0022\";\n// пояснение\n".encode()

    assert CFamilyCommentHandler().extract(source) == []


def test_a_backslash_inside_a_sql_string_stops_the_scan() -> None:
    """В MySQL слеш экранирует, в стандарте нет: угадывать диалект нельзя."""
    sql = SqlCommentHandler()
    source = "SELECT 'a\'b' AS x;  -- пояснение\n".encode()

    assert sql.extract(source) == []


def test_a_comment_continued_by_a_backslash_is_skipped(js: CStyleCommentHandler) -> None:
    """Такое пояснение продолжается на следующую строку, и её границы разбор не знает."""
    source = ("// пояснение \\" + "\n" + "const a = 1;\n// обычное\n").encode()

    assert [u.text for u in js.extract(source)] == ["обычное"]


def test_hex_digit_separators_stop_the_scan_too() -> None:
    """``0xA'B'C``: разделитель бывает и между шестнадцатеричными цифрами."""
    from content_factory.translation.handlers.code import CFamilyCommentHandler

    source = "int mask = 0xA'B'C;\n// пояснение\n".encode()

    assert CFamilyCommentHandler().extract(source) == []


def test_an_english_apostrophe_in_a_comment_does_not_disable_the_file() -> None:
    """Отказ должен защищать, а не выключать работу: апостроф в прозе встречается постоянно."""
    from content_factory.translation.handlers.code import CFamilyCommentHandler

    source = b"int a = 1;\n// don't touch this, it's the project's entry point\n"

    texts = [u.text for u in CFamilyCommentHandler().extract(source)]

    assert texts == ["don't touch this, it's the project's entry point"]
