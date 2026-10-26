"""Комментарии в shell-скриптах.

Готового разборщика нет, поэтому свой: решётка начинает комментарий не везде. Внутри кавычек это
обычный знак, в ``${#array}`` — длина, в строке запуска — не пояснение, а внутри heredoc вообще
текст, который уйдёт в программу.

Проверка после подмены та же по смыслу, что в Python, но сличается остаток файла без
комментариев: результат сканируется заново, и если перевод сдвинул разбор — файл откатывается.
"""

from __future__ import annotations

import pytest

from content_factory.translation.handlers.code import ShellCommentHandler

SAMPLE = """#!/bin/bash
# Сборка проекта
set -e

NAME="project"            # имя артефакта
MARKER="# не комментарий"
QUOTED='# тоже не комментарий'
URL="https://example.com/path"
COUNT=${#NAME}

cat <<EOF
# это текст для программы, а не комментарий
EOF

echo "$NAME"  # печать имени
"""


@pytest.fixture
def handler() -> ShellCommentHandler:
    return ShellCommentHandler()


def _texts(handler: ShellCommentHandler, text: str = SAMPLE) -> list[str]:
    return [u.text for u in handler.extract(text.encode("utf-8"))]


def test_real_comments_become_units(handler: ShellCommentHandler) -> None:
    texts = _texts(handler)

    assert "Сборка проекта" in texts
    assert "имя артефакта" in texts
    assert "печать имени" in texts


def test_a_hash_inside_quotes_is_not_a_comment(handler: ShellCommentHandler) -> None:
    joined = " ".join(_texts(handler))

    assert "не комментарий" not in joined
    assert "example.com" not in joined


def test_the_length_expansion_is_not_a_comment(handler: ShellCommentHandler) -> None:
    joined = " ".join(_texts(handler))

    assert "NAME}" not in joined, "${#NAME} — длина, а не пояснение"


def test_the_shebang_is_left_alone(handler: ShellCommentHandler) -> None:
    assert not any("bin/bash" in text for text in _texts(handler))


def test_heredoc_content_is_left_alone(handler: ShellCommentHandler) -> None:
    joined = " ".join(_texts(handler))

    assert "текст для программы" not in joined, "это уйдёт в программу, а не пояснение"


def test_without_translations_the_file_is_byte_identical(handler: ShellCommentHandler) -> None:
    source = SAMPLE.encode("utf-8")

    assert handler.apply(source, {}) == source


def test_a_translated_comment_replaces_only_itself(handler: ShellCommentHandler) -> None:
    source = SAMPLE.encode("utf-8")
    unit = next(u for u in handler.extract(source) if u.text == "имя артефакта")

    result = handler.apply(source, {unit.unit_id: "artifact name"}).decode("utf-8")

    assert "# artifact name\n" in result
    assert 'MARKER="# не комментарий"' in result, "строка не тронута"
    assert "COUNT=${#NAME}" in result, "подстановка не тронута"


def test_a_translation_with_a_line_break_cannot_leak_into_code(handler: ShellCommentHandler) -> None:
    """Перенос строки в переводе превратил бы остаток пояснения в команду."""
    source = SAMPLE.encode("utf-8")
    unit = next(u for u in handler.extract(source) if u.text == "Сборка проекта")

    result = handler.apply(source, {unit.unit_id: "сборка\nrm -rf /"}).decode("utf-8")

    assert "\nrm -rf /" not in result
    assert "# сборка rm -rf /\n" in result, "перенос заменён пробелом, команда не появилась"


def test_a_script_with_unbalanced_quotes_still_round_trips(handler: ShellCommentHandler) -> None:
    source = b"echo 'unclosed\n# comment after\n"

    assert handler.apply(source, {}) == source


# ------------------------------------------------ отказ там, где разбор не уверен

def test_a_trailing_comment_after_a_backtick_is_skipped(handler: ShellCommentHandler) -> None:
    """Обратные кавычки разбор не моделирует, поэтому такую строку он не трогает вовсе."""
    source = b"X=`date` # not sure this is a comment\n"

    assert handler.extract(source) == []
    assert handler.apply(source, {"0001": "перевод"}) == source


def test_a_trailing_comment_after_a_command_substitution_is_skipped(
    handler: ShellCommentHandler,
) -> None:
    source = b"X=$(date) # not sure either\n"

    assert handler.extract(source) == []


def test_a_whole_line_comment_is_translated_even_near_those(handler: ShellCommentHandler) -> None:
    """Пояснение с начала строки однозначно, его разбор трогает и рядом с такими конструкциями."""
    source = "# Сборка\nX=`date`\n".encode()
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: "Build"}).decode("utf-8")

    assert result.startswith("# Build\n")


def test_a_trailing_comment_after_a_quoted_string_still_works(handler: ShellCommentHandler) -> None:
    """Кавычки разбор моделирует, поэтому обычный хвостовой комментарий остаётся в работе."""
    source = b'NAME="project"  # \xd0\xb8\xd0\xbc\xd1\x8f\n'

    units = handler.extract(source)

    assert [u.text for u in units] == ["имя"]


def test_an_unterminated_quote_disables_the_whole_file(handler: ShellCommentHandler) -> None:
    """Незакрытая кавычка означает, что разбор сбился: трогать такой файл нельзя."""
    source = b"echo 'unclosed\n# comment after\n"

    assert handler.extract(source) == []
    assert handler.apply(source, {"0001": "перевод"}) == source


def test_a_translation_that_adds_shell_syntax_is_refused(handler: ShellCommentHandler) -> None:
    """Если в переводе появились обратные кавычки, которых не было, — берём исходный текст."""
    source = b"# \xd0\xa1\xd0\xb1\xd0\xbe\xd1\x80\xd0\xba\xd0\xb0\n"
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: "Build `rm -rf /`"}).decode("utf-8")

    assert "`rm -rf /`" not in result
    assert result == "# Сборка\n", "остался исходный текст пояснения"


def test_a_hash_inside_parameter_expansion_is_not_a_comment(handler: ShellCommentHandler) -> None:
    """В ``${x:- # текст}`` решётка — часть значения по умолчанию, а не начало пояснения.

    Принять её за пояснение значит съесть и закрывающую скобку: перевод сломал бы код, а
    проверка этого не увидела бы — она сканирует тем же разборщиком.
    """
    source = "echo ${x:- # это не комментарий}\n".encode("utf-8")

    assert handler.extract(source) == []
    assert handler.apply(source, {"0001": "перевод"}) == source


def test_a_strip_expansion_still_allows_a_real_trailing_comment(
    handler: ShellCommentHandler,
) -> None:
    """``${VAR#prefix}`` — обрезка, и настоящее пояснение после неё остаётся в работе."""
    source = "X=${VAR#prefix}  # настоящее пояснение\n".encode("utf-8")

    assert [u.text for u in handler.extract(source)] == ["настоящее пояснение"]


def test_nested_expansion_is_tracked_to_its_end(handler: ShellCommentHandler) -> None:
    source = "echo ${a:-${b:- # внутри}}  # снаружи\n".encode("utf-8")

    assert [u.text for u in handler.extract(source)] == ["снаружи"]


def test_a_translation_with_shell_metacharacters_keeps_the_original(
    handler: ShellCommentHandler,
) -> None:
    source = "# Сборка\n".encode("utf-8")
    unit = handler.extract(source)[0]

    result = handler.apply(source, {unit.unit_id: "Build; rm -rf /"}).decode("utf-8")

    assert result == "# Сборка\n", "перевод с управляющими знаками не берём"
