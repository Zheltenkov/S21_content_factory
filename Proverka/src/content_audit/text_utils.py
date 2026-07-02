"""Вспомогательные функции для работы с текстом."""

from __future__ import annotations

import re


def line_for_offset(text: str, offset: int) -> int:
    """Возвращаем номер строки по смещению, начиная с единицы."""

    return text.count("\n", 0, offset) + 1


def line_end_for_match(text: str, start: int, end: int) -> int:
    """Возвращаем последнюю строку совпадения."""

    return line_for_offset(text, max(start, end - 1))


def quote_around(text: str, start: int, end: int, limit: int = 320) -> str:
    """Берём короткую цитату вокруг найденного фрагмента."""

    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    quote = re.sub(r"\s+", " ", text[line_start:line_end].strip())
    if len(quote) <= limit:
        return quote
    return f"{quote[: limit - 1]}…"


def context_around(text: str, start: int, end: int, radius: int = 500) -> str:
    """Берём контекст для модельной проверки без отправки всего файла."""

    left = max(0, start - radius)
    right = min(len(text), end + radius)
    return text[left:right].strip()


def normalize_for_match(value: str) -> str:
    """Нормализуем текст для грубого сопоставления пунктов README и чек-листа."""

    lowered = value.lower().replace("_", " ").replace("-", " ")
    return re.sub(r"[^a-zа-яё0-9]+", " ", lowered).strip()
