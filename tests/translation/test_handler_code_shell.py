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
