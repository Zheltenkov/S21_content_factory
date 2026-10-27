"""Простой текст: md, markdown, txt.

Текст режется на блоки по пустым строкам. Целиком файл одной единицей не отдать — пакетирование
перестанет работать на больших README; построчно тоже нельзя — модель потеряет связность абзаца.
Блок между пустыми строками сохраняет и то, и другое.

Разделители запоминаются отдельно от блоков, поэтому файл без переводов собирается побайтно
таким же: это проверяется тестом и служит защитой от тихой порчи переносов строк.

Отдельная забота — оглавление. Боевой прогон курсового README перевёл и заголовки, и подписи
ссылок, а якоря оставил русскими: ``[Chapter I](#глава-i)`` не ведёт никуда. Просить об этом
модель бессмысленно — якорь выводится из заголовка по правилу, значит его можно пересчитать
самим, не тратя ни вызова. Если число заголовков до и после перевода разошлось, соответствия
нет и якоря остаются как были: лучше прежняя ссылка, чем наугад переставленная.
"""

from __future__ import annotations

import re

from content_factory.translation.units import TextUnit

_BOM = "﻿"
# Разделитель блоков — пустая строка, возможно с пробелами и в любом стиле переносов.
_BLOCK_SPLIT_RE = re.compile(r"((?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n))")
# Ссылка на якорь внутри того же документа.
_ANCHOR_LINK_RE = re.compile(r"\]\(#([^)\s]+)\)")


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
        changed = False
        for index, piece in enumerate(pieces):
            if index % 2 == 1 or not piece.strip():
                continue
            translated = translations.get(f"{index // 2:04d}")
            if translated is None:
                continue
            pieces[index] = _keep_padding(piece, translated)
            changed = True
        rebuilt = "".join(pieces)
        if changed:
            rebuilt = _retarget_anchors(text, rebuilt)
        return (_BOM + rebuilt if had_bom else rebuilt).encode(encoding)


def _decode(content: bytes) -> tuple[str, str, bool]:
    """Текст без метки порядка байтов плюс то, что нужно, чтобы собрать байты обратно."""
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8"), "utf-8", True
    return content.decode("utf-8"), "utf-8", False


def _split_blocks(text: str) -> list[str]:
    """Чередование: блок, разделитель, блок… Склейка списка даёт исходный текст."""
    return _BLOCK_SPLIT_RE.split(text) if text else []


def _retarget_anchors(before: str, after: str) -> str:
    """Переставляет ссылки оглавления на переведённые заголовки.

    Соответствие — по порядку заголовков: первый заголовок стал первым, второй вторым. Если
    их число разошлось, соответствия нет, и файл возвращается как есть. Якорь, которого среди
    заголовков исходника не было, не трогаем: он ведёт не сюда, и это не наша забота.
    """
    old_headings = _headings(before)
    new_headings = _headings(after)
    if not old_headings or len(old_headings) != len(new_headings):
        return after

    mapping: dict[str, str] = {}
    seen: set[str] = set()
    for old, new in zip(old_headings, new_headings, strict=True):
        slug = _anchor_slug(old)
        if slug in seen:
            # Два заголовка дали один якорь: Markdown различает их суффиксом, которого мы
            # не считаем. Ни тот, ни другой не переставляем.
            mapping.pop(slug, None)
            continue
        seen.add(slug)
        mapping[slug] = _anchor_slug(new)

    def replace(match: re.Match[str]) -> str:
        target = mapping.get(match.group(1))
        return match.group(0) if target is None else f"](#{target})"

    return _ANCHOR_LINK_RE.sub(replace, after)


def _headings(text: str) -> list[str]:
    """Заголовки Markdown по порядку.

    Разбор построчный, а не выражением: ``(.+?)[ \\t]*#*[ \\t]*$`` даёт три перекрывающихся
    квантификатора над одними и теми же знаками, и на строке, где конец недостижим, перебирает
    разбиения кубически — две тысячи знаков занимали три четверти минуты. Файл внутри архива
    весит до шестнадцати мегабайт, так что это остановка прогона, а не неудобство.

    Пустой заголовок тоже считается: соответствие держится на порядке, и пропуск сдвинул бы
    весь список.
    """
    found: list[str] = []
    for line in text.splitlines():
        body = line.lstrip(" \t")
        if len(line) - len(body) > 3:
            continue  # четыре пробела — это блок кода, а не заголовок
        hashes = len(body) - len(body.lstrip("#"))
        if not 1 <= hashes <= 6:
            continue
        rest = body[hashes:]
        if rest and rest[0] not in " \t":
            continue  # ``#тема`` — не заголовок
        found.append(rest.strip())
    return found


def _anchor_slug(heading: str) -> str:
    """Якорь заголовка по правилу Markdown: строчные буквы, пробелы в дефисы, знаки прочь."""
    text = heading.strip().lower()
    text = "".join(char for char in text if char.isalnum() or char in " -_")
    return "-".join(text.split())


def _keep_padding(original: str, translated: str) -> str:
    """Сохраняет отступ и хвостовые пробелы блока — иначе поедет вложенность списков markdown."""
    stripped = original.strip()
    head = original[: len(original) - len(original.lstrip())]
    tail = original[len(original.rstrip()) :] if stripped else ""
    return f"{head}{translated}{tail}"
