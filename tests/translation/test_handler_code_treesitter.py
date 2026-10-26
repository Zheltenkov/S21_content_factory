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

def test_a_translation_cannot_smuggle_a_java_escape_in() -> None:
    """Признак проверяется и на переводе: иначе файл без экранирования получит его от модели.

    Компилятор развернёт такое экранирование и прочтёт файл иначе, а грамматика этого не
    заметит — она экранирование не разворачивает.
    """
    handler = java_handler()
    slash = chr(92)
    source = ('class A {' + chr(10) + '  int x = 1; // пояснение' + chr(10) + '}' + chr(10)).encode()
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: f'note {slash}u0022 here'})

    assert result == source, 'перевод с экранированием не принят'


def test_a_trigraph_in_a_translation_is_refused() -> None:
    """``??/`` в C — та же обратная косая, только записанная триграфом.

    Препроцессор склеил бы по ней строки, затянув следующую строку кода в пояснение.
    Грамматика триграфов не знает, поэтому повторный разбор этого не поймал бы.
    """
    handler = c_handler()
    source = b'/* note */\nint a = 1;\n'
    unit = handler.extract(source)[0]

    assert handler.apply(source, {unit.unit_id: 'конец ??/'}) == source


def test_a_backslash_in_a_translation_is_refused() -> None:
    """Запрет общий для всех языков: слой, который развернёт косую, у каждого свой."""
    handler = shell_handler()
    source = '# Сборка' + chr(10)
    data = source.encode()
    unit = handler.extract(data)[0]

    assert handler.apply(data, {unit.unit_id: 'build ' + chr(92) + 'n rm -rf /'}) == data


def test_a_block_comment_keeps_a_space_after_its_marker() -> None:
    """``/*!`` у MySQL — исполняемый код. Перевод не должен начинаться сразу за меткой."""
    handler = sql_handler()
    source = '/*выборка*/\nSELECT 1;\n'.encode()
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: '!40000 DROP TABLE t'}).decode()

    assert result.startswith('/* !40000'), result


def test_sql_block_comments_are_units() -> None:
    """Грамматика SQL зовёт ``/* … */`` ``marginalia`` — проверка на подстроку их теряла."""
    source = '/*Блок выборки*/' + chr(10) + 'SELECT 1;  -- хвост' + chr(10)

    assert _texts(sql_handler(), source) == ['Блок выборки', 'хвост']


def test_an_executable_sql_comment_is_not_a_comment() -> None:
    """``/*!…*/`` у MySQL и ``/*+…*/`` у Oracle — это код, а не пояснение.

    Грамматика зовёт их так же, ``marginalia``, и ошибки не видит. Перевести их значило бы
    испортить дамп: сервер читает их как указания, а не как текст.
    """
    source = (
        '/*!40000 ALTER TABLE t DISABLE KEYS */' + chr(10)
        + '/*+ INDEX(t idx) */' + chr(10)
        + '/*Настоящее пояснение*/' + chr(10)
        + 'SELECT 1;' + chr(10)
    )

    assert _texts(sql_handler(), source) == ['Настоящее пояснение']


def test_an_opening_block_marker_in_a_translation_is_refused() -> None:
    """PostgreSQL вкладывает блочные пояснения друг в друга, а грамматика — нет.

    ``/* перевод /* */`` для грамматики один комментарий, для сервера — незакрытая
    вложенность, съедающая остаток файла. Повторный разбор этого не заметит.
    """
    handler = sql_handler()
    source = ('/* выборка */' + chr(10) + 'SELECT 1;' + chr(10)).encode()
    unit = handler.extract(source)[0]

    assert handler.apply(source, {unit.unit_id: 'см. /* ниже'}) == source
