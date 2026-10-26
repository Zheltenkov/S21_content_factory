"""Данные-примеры: csv и tsv.

Правило принято пользователем и держится на одном наблюдении: код учащегося читает колонку по
имени. Поэтому английский файл не трогаем вовсе, а в русском переводим содержимое, оставляя
английские заголовки на месте — ``df['price']`` продолжает работать само собой.

Файл не пересобирается библиотекой ``csv``: она переставляет кавычки по своему усмотрению, и
побайтного совпадения на нетронутом файле не было бы. Вместо этого поля находятся вместе с их
границами в исходном тексте и подменяются на месте.
"""

from __future__ import annotations

import re

from content_factory.translation.language import dominant_language, looks_russian
from content_factory.translation.units import TextUnit

MAX_UNITS = 20_000
# Значение без букв — число, дата, код — не переводим никогда.
_HAS_LETTERS_RE = re.compile(r"[^\W\d_]", re.UNICODE)


class CsvHandler:
    """Переводит текстовые ячейки csv, если наполнение файла русское."""

    extensions = frozenset({".csv"})
    delimiter = ","

    def extract(self, content: bytes) -> list[TextUnit]:
        text = content.decode("utf-8")
        return [
            TextUnit(unit_id=unit_id, text=value, hint=hint)
            for unit_id, _start, _end, value, hint in self._fields(text)
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        text = content.decode("utf-8")
        pieces: list[str] = []
        cursor = 0
        changed = False
        for unit_id, start, end, _value, _hint in self._fields(text):
            translated = translations.get(unit_id)
            if translated is None:
                continue
            pieces.append(text[cursor:start])
            pieces.append(self._render(text[start:end], translated))
            cursor = end
            changed = True
        if not changed:
            return content
        pieces.append(text[cursor:])
        return "".join(pieces).encode("utf-8")

    def _render(self, original: str, translated: str) -> str:
        """Готовит перевод к вставке: кавычки ставятся, если без них строка развалится."""
        was_quoted = original.startswith('"') and original.endswith('"')
        body = translated.replace('"', '""')
        needs_quotes = any(mark in translated for mark in (self.delimiter, '"', "\n", "\r"))
        if was_quoted or needs_quotes:
            return f'"{body}"'
        return translated

    def _fields(self, text: str) -> list[tuple[str, int, int, str, str]]:
        """Переводимые ячейки: ``(ключ, начало, конец, значение, вид)``.

        Пустой список означает, что файл трогать не нужно: он английский, в нём нет букв или в
        нём нечего переводить.
        """
        cells = _scan(text, self.delimiter)
        if not cells:
            return []
        body_values = [value for row, _column, _start, _end, value in cells if row > 0]
        if dominant_language(body_values) != "ru":
            return []  # английский пример остаётся как есть

        found: list[tuple[str, int, int, str, str]] = []
        for row, _column, start, end, value in cells:
            if not value.strip() or not _HAS_LETTERS_RE.search(value):
                continue
            if row == 0 and not looks_russian(value):
                continue  # английский заголовок читает код учащегося
            if len(found) >= MAX_UNITS:
                break
            hint = "заголовок колонки" if row == 0 else "ячейка данных"
            found.append((f"{len(found) + 1:04d}", start, end, value, hint))
        return found


class TsvHandler(CsvHandler):
    """То же самое для файлов с табуляцией."""

    extensions = frozenset({".tsv"})
    delimiter = "\t"


def _scan(text: str, delimiter: str) -> list[tuple[int, int, int, int, str]]:
    """Ячейки с границами: ``(строка, колонка, начало, конец, значение)``.

    Границы указывают на содержимое поля вместе с кавычками, если они были: подменять нужно
    именно их, иначе кавычки останутся вокруг нового значения.
    """
    cells: list[tuple[int, int, int, int, str]] = []
    index = 0
    length = len(text)
    row = column = 0
    while index <= length:
        start = index
        if index < length and text[index] == '"':
            index += 1
            parts: list[str] = []
            while index < length:
                if text[index] == '"':
                    if index + 1 < length and text[index + 1] == '"':
                        parts.append('"')
                        index += 2
                        continue
                    index += 1
                    break
                parts.append(text[index])
                index += 1
            value = "".join(parts)
        else:
            while index < length and text[index] not in (delimiter, "\n", "\r"):
                index += 1
            value = text[start:index]

        cells.append((row, column, start, index, value))

        if index >= length:
            break
        if text[index] == delimiter:
            column += 1
            index += 1
            continue
        # перевод строки, возможно в два знака
        if text.startswith("\r\n", index):
            index += 2
        else:
            index += 1
        row += 1
        column = 0
        if index >= length:
            break
    return cells
