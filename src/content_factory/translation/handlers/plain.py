"""Простой текст: md, markdown, txt.

Текст режется на блоки по пустым строкам. Целиком файл одной единицей не отдать — пакетирование
перестанет работать на больших README; построчно тоже нельзя — модель потеряет связность абзаца.
Блок между пустыми строками сохраняет и то, и другое.

Разделители запоминаются отдельно от блоков, поэтому файл без переводов собирается побайтно
таким же: это проверяется тестом и служит защитой от тихой порчи переносов строк.
"""

from __future__ import annotations

import re

from content_factory.translation.units import TextUnit

_BOM = "﻿"
# Разделитель блоков — пустая строка, возможно с пробелами и в любом стиле переносов.
_BLOCK_SPLIT_RE = re.compile(r"((?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n))")


class PlainTextHandler:
    """Переводит текст целиком, блоками между пустыми строками."""

    extensions = frozenset({".md", ".markdown", ".txt"})

    def extract(self, content: bytes) -> list[TextUnit]:
        text, _, _ = _decode(content)
        units: list[TextUnit] = []
        for index, piece in enumerate(_split_blocks(text)):
            if index % 2 == 1:  # разделитель
                continue
            if piece.strip():
                units.append(TextUnit(unit_id=f"{index // 2:04d}", text=piece.strip()))
        return units

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        text, encoding, had_bom = _decode(content)
        pieces = _split_blocks(text)
        for index, piece in enumerate(pieces):
            if index % 2 == 1 or not piece.strip():
                continue
            translated = translations.get(f"{index // 2:04d}")
            if translated is None:
                continue
            pieces[index] = _keep_padding(piece, translated)
        rebuilt = "".join(pieces)
        return (_BOM + rebuilt if had_bom else rebuilt).encode(encoding)


def _decode(content: bytes) -> tuple[str, str, bool]:
    """Текст без метки порядка байтов плюс то, что нужно, чтобы собрать байты обратно."""
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8"), "utf-8", True
    return content.decode("utf-8"), "utf-8", False


def _split_blocks(text: str) -> list[str]:
    """Чередование: блок, разделитель, блок… Склейка списка даёт исходный текст."""
    return _BLOCK_SPLIT_RE.split(text) if text else []


def _keep_padding(original: str, translated: str) -> str:
    """Сохраняет отступ и хвостовые пробелы блока — иначе поедет вложенность списков markdown."""
    stripped = original.strip()
    head = original[: len(original) - len(original.lstrip())]
    tail = original[len(original.rstrip()) :] if stripped else ""
    return f"{head}{translated}{tail}"
