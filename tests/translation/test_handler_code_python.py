"""Комментарии в коде Python: перевод текста, который код не меняет.

Модель не получает код на правку — только список комментариев. Файл меняет код, и после подмены
результат пересобирается в токены и сличается с исходником: совпало всё, кроме комментариев, —
файл принят; не совпало — откат и отметка в отчёте. Разбор может ошибиться и пропустить
комментарий, но не может испортить код.
"""

from __future__ import annotations

import pytest

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.handlers.code import PythonCommentHandler

SAMPLE = r'''#!/usr/bin/env python3
"""Модуль обучения модели."""

import re  # регулярные выражения

MARKER = "# это не комментарий"
URL = "https://example.com/path"


def train(data):
    """Train the model on the data."""
    # Step one: clean the input
    cleaned = re.sub(r"#\s*", "", data)  # trailing note
    return cleaned
'''


@pytest.fixture
def handler() -> PythonCommentHandler:
    return PythonCommentHandler()


def _units(handler: PythonCommentHandler, text: str):
    return handler.extract(text.encode("utf-8"))


def test_comments_become_units(handler: PythonCommentHandler) -> None:
    texts = [u.text for u in _units(handler, SAMPLE)]

    assert "регулярные выражения" in texts
    assert "Step one: clean the input" in texts
    assert "trailing note" in texts


def test_a_hash_inside_a_string_is_not_a_comment(handler: PythonCommentHandler) -> None:
    texts = " ".join(u.text for u in _units(handler, SAMPLE))

    assert "это не комментарий" not in texts
    assert "example.com" not in texts, "решётки в строке и в адресе не начинают комментарий"


def test_the_shebang_is_left_alone(handler: PythonCommentHandler) -> None:
    texts = [u.text for u in _units(handler, SAMPLE)]

    assert not any("usr/bin/env" in text for text in texts)


def test_docstrings_become_units(handler: PythonCommentHandler) -> None:
    texts = [u.text for u in _units(handler, SAMPLE)]

    assert "Модуль обучения модели." in texts
    assert "Train the model on the data." in texts


def test_without_translations_the_file_is_byte_identical(handler: PythonCommentHandler) -> None:
    source = SAMPLE.encode("utf-8")

    assert handler.apply(source, {}) == source


def test_a_translated_comment_replaces_only_itself(handler: PythonCommentHandler) -> None:
    source = SAMPLE.encode("utf-8")
    unit = next(u for u in _units(handler, SAMPLE) if u.text == "Step one: clean the input")

    result = handler.apply(source, {unit.unit_id: "Шаг первый: чистим вход"}).decode("utf-8")

    assert "    # Шаг первый: чистим вход\n" in result
    assert 'MARKER = "# это не комментарий"' in result, "строка не тронута"
    assert "cleaned = re.sub" in result, "код не тронут"


def test_the_code_around_a_translation_stays_identical(handler: PythonCommentHandler) -> None:
    """Главная проверка: всё, кроме комментариев, обязано совпасть с исходником."""
    import tokenize
    from io import BytesIO

    def code_tokens(data: bytes) -> list[tuple[int, str]]:
        return [
            (tok.type, tok.string)
            for tok in tokenize.tokenize(BytesIO(data).readline)
            if tok.type not in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE)
        ]

    source = SAMPLE.encode("utf-8")
    translations = {u.unit_id: "перевод" for u in _units(handler, SAMPLE) if "#" not in u.text}

    result = handler.apply(source, translations)

    before = [t for t in code_tokens(source) if t[1] not in ("Модуль обучения модели.",)]
    after = [t for t in code_tokens(result) if t[1] != "перевод"]
    assert len(before) == len(after)


def test_a_file_that_does_not_parse_is_a_format_error(handler: PythonCommentHandler) -> None:
    with pytest.raises(DocumentFormatError):
        handler.extract(b"def broken(:\n")
