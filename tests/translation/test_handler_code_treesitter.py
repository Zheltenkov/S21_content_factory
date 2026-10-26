"""Комментарии в коде через настоящие грамматики языков.

Эти проверки заменяют тесты шести ручных сканеров. Половина прежних закрепляла обходные пути —
«такую строку не трогаем, потому что разбор не уверен». Здесь разбирает сам язык, поэтому те же
случаи проверяются на правильный ответ, а не на отказ.
"""

from __future__ import annotations

import pytest

from content_factory.translation.handlers.code_treesitter import (
    c_handler,
    cpp_handler,
    go_handler,
    java_handler,
    javascript_handler,
    shell_handler,
    sql_handler,
)


def _texts(handler, source: str) -> list[str]:
    return [u.text for u in handler.extract(source.encode("utf-8"))]


# ----------------------------------------------------------------- то, что ручной разбор не брал

def test_bash_knows_a_hash_inside_parameter_expansion() -> None:
    source = "echo ${x:- # это не комментарий}  # а это комментарий\n"

    assert _texts(shell_handler(), source) == ["а это комментарий"]


def test_bash_knows_backticks_and_command_substitution() -> None:
    """Ручной разбор пропускал такие строки целиком — грамматика их читает."""
    source = "X=`date`  # после обратных кавычек\nY=$(date)  # после подстановки\n"

    assert _texts(shell_handler(), source) == ["после обратных кавычек", "после подстановки"]


def test_javascript_tells_a_regex_from_a_comment() -> None:
    source = "const re = /\\/\\//;  // после регулярного выражения\n"

    assert _texts(javascript_handler(), source) == ["после регулярного выражения"]


def test_javascript_knows_a_template_literal() -> None:
    source = "const t = `текст // не комментарий`;\n// настоящее\n"

    assert _texts(javascript_handler(), source) == ["настоящее"]


def test_c_knows_a_digit_separator() -> None:
    source = "int total = 1'000'000;  // после разделителя разрядов\n"

    assert _texts(cpp_handler(), source) == ["после разделителя разрядов"]


def test_cpp_knows_a_raw_string() -> None:
    source = 'auto s = R"(a " // не комментарий)";  // настоящее\n'

    assert _texts(cpp_handler(), source) == ["настоящее"]


def test_java_with_unicode_escapes_is_left_alone() -> None:
    """Компилятор Java разворачивает uXXXX до разбора, грамматика — нет.

    Такой файл может значить не то, что видно, поэтому мы за него не беремся. Это
    единственный язык здесь, где грамматика расходится с компилятором по замыслу.
    """
    slash = chr(92)
    broken = f'class A {{ String s = "{slash}u0022"; // пояснение' + chr(10) + '}' + chr(10)
    ordinary = f'class A {{ String s = "caf{slash}u00e9"; // пояснение' + chr(10) + '}' + chr(10)

    assert _texts(java_handler(), broken) == []
    assert _texts(java_handler(), ordinary) == [], 'отказ по признаку, а не по разбору'


def test_java_without_unicode_escapes_works() -> None:
    source = 'class A {' + chr(10) + '  int x = 1; // настоящее' + chr(10) + '}' + chr(10)

    assert _texts(java_handler(), source) == ["настоящее"]


def test_go_knows_a_raw_string() -> None:
    source = "package main\n\nvar p = `C:\\path\\`\n\n// настоящее\nfunc main() {}\n"

    assert _texts(go_handler(), source) == ["настоящее"]


def test_sql_knows_a_doubled_quote() -> None:
    source = "SELECT 'it''s -- не комментарий' AS x;  -- настоящее\n"

    assert _texts(sql_handler(), source) == ["настоящее"]


# ----------------------------------------------------------------------------- общее поведение

def test_the_shebang_is_not_a_comment() -> None:
    source = "#!/bin/bash\n# Сборка\n"

    assert _texts(shell_handler(), source) == ["Сборка"]


def test_block_comments_are_units() -> None:
    source = "/* Блок пояснения */\nint a = 1;\n"

    assert _texts(c_handler(), source) == ["Блок пояснения"]


@pytest.mark.parametrize(
    ("handler", "source"),
    [
        (shell_handler(), "#!/bin/bash\n# Сборка\nX=${y:-1}\necho 'a'  # печать\n"),
        (javascript_handler(), "// Модуль\nconst a = 1;  // хвост\n/* блок */\n"),
        (c_handler(), "/* блок */\nint a = 1;  // хвост\n"),
        (sql_handler(), "-- Выборка\nSELECT 1;  -- хвост\n"),
    ],
)
def test_without_translations_the_file_is_byte_identical(handler, source: str) -> None:
    data = source.encode("utf-8")

    assert handler.apply(data, {}) == data


def test_a_translated_comment_replaces_only_itself() -> None:
    handler = shell_handler()
    source = "# Сборка проекта\nNAME=\"project\"  # имя\n".encode()
    unit = next(u for u in handler.extract(source) if u.text == "имя")

    result = handler.apply(source, {unit.unit_id: "name"}).decode("utf-8")

    assert '# Сборка проекта\nNAME="project"  # name\n' == result


def test_a_file_that_does_not_parse_yields_nothing() -> None:
    assert shell_handler().extract(b"echo 'unclosed\n# comment\n") == []


# ------------------------------------------------- проверка целостности после подмены

def test_a_translation_that_would_change_the_code_is_refused() -> None:
    """Проверка настоящая: результат разбирается заново и сличаются все листья, кроме пояснений."""
    handler = c_handler()
    source = b"/* note */\nint a = 1;\n"
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: "конец */ int b = 2; /*"})

    assert result == source, "перевод, закрывающий блок, не принят"


def test_a_newline_in_a_translation_cannot_leak_into_code() -> None:
    handler = shell_handler()
    source = "# Сборка\n".encode()
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: "build\nrm -rf /"}).decode("utf-8")

    assert "\nrm -rf /" not in result
    assert result == "# build rm -rf /\n"


def test_a_harmless_metacharacter_now_goes_through() -> None:
    """Точка с запятой внутри пояснения остаётся пояснением — проверка это доказывает."""
    handler = shell_handler()
    source = "# Сборка\n".encode()
    unit = handler.extract(source)[0]

    assert handler.apply(source, {unit.unit_id: "build; then test"}).decode() == "# build; then test\n"
