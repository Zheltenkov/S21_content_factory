"""HTML: перевод текстовых узлов с сохранением разметки.

Прежде модуль превращал HTML в текст и текстом же отдавал — разметка пропадала. Теперь файл
остаётся HTML: подменяется только то, что лежит между тегами.

Разбор идёт не по дереву, а по исходнику: находятся куски, которые трогать нельзя — теги,
комментарии, ``<script>``, ``<style>``, объявления, — а промежутки между ними и есть текст. Такой
способ не требует правильности разметки (а она в учебных материалах бывает всякая) и даёт
побайтное совпадение, когда переводить нечего.

Проход линейный, и это не украшение. Первая редакция искала пропускаемые куски одним регулярным
выражением с ``.*?``; на сорока тысячах незакрытых ``<script>`` разбор занимал 68 секунд, потому
что каждая незакрытая конструкция заставляла движок дочитывать файл до конца заново. Потолки на
число кусков от этого не защищали: текста между тегами там нет, счётчик не растёт, а время уже
потрачено. Теперь курсор всегда идёт вперёд, а незакрытая конструкция дочитывается один раз.

Модель видит текст без сущностей (``Caf&eacute;`` → ``Café``), а на обратном пути перевод
экранируется. Поэтому ответ модели не может стать разметкой: ``<b>`` из перевода попадёт на
страницу как видимый текст, а не как тег.

Ограничение: ``>`` внутри значения атрибута (``<a title="a>b">``) разорвёт тег раньше времени.
В вёрстке это редкость, а защиту от неверного разбора даёт побайтная проверка невмешательства.
"""

from __future__ import annotations

import html
import re

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.units import TextUnit

# Пределы: файл приходит из загруженного архива.
MAX_BYTES = 10 * 1024 * 1024
MAX_UNITS = 20_000
MAX_CHARS = 2_000_000

# Элементы, внутри которых переводить нечего: их содержимое — код, а не текст.
_RAW_ELEMENT_RE = re.compile(r"<(script|style)\b", re.IGNORECASE)
# Закрывающие теги ищем предкомпилированным выражением от нужной позиции. Приведение всего файла
# к нижнему регистру копировало бы строку на каждый такой тег: двадцать тысяч закрытых пар
# разбирались 106 секунд.
_RAW_CLOSERS = {
    "script": re.compile(r"</script", re.IGNORECASE),
    "style": re.compile(r"</style", re.IGNORECASE),
}


class HtmlHandler:
    """Переводит текст между тегами, не трогая саму разметку."""

    extensions = frozenset({".html", ".htm"})

    def extract(self, content: bytes) -> list[TextUnit]:
        text = _decode(content)
        return [
            TextUnit(unit_id=unit_id, text=value, hint="текст страницы")
            for unit_id, _start, _end, value in _spans(text)
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        text = _decode(content)
        # Сборка кусками: срезы строки на каждую замену переписывали бы весь файл заново.
        pieces: list[str] = []
        cursor = 0
        for unit_id, start, end, _value in _spans(text):
            translated = translations.get(unit_id)
            if translated is None:
                continue
            original = text[start:end]
            head = original[: len(original) - len(original.lstrip())]
            tail = original[len(original.rstrip()) :]
            pieces.append(text[cursor:start])
            pieces.append(head + html.escape(translated, quote=False) + tail)
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces).encode("utf-8")


def _decode(content: bytes) -> str:
    if len(content) > MAX_BYTES:
        raise DocumentFormatError(
            f"HTML-файл слишком велик: {len(content)} байт при пределе {MAX_BYTES}"
        )
    return content.decode("utf-8")


def _spans(text: str) -> list[tuple[str, int, int, str]]:
    """Текстовые промежутки: ``(ключ, начало, конец, текст без сущностей)``."""
    found: list[tuple[str, int, int, str]] = []
    counter = 0
    characters = 0

    def take(start: int, end: int) -> None:
        nonlocal counter, characters
        raw = text[start:end]
        if not raw.strip():
            return
        value = html.unescape(raw).strip()
        if not value:
            return
        counter += 1
        if counter > MAX_UNITS:
            raise DocumentFormatError(f"В HTML больше {MAX_UNITS} переводимых кусков текста")
        characters += len(value)
        if characters > MAX_CHARS:
            raise DocumentFormatError(f"В HTML больше {MAX_CHARS} знаков текста")
        found.append((f"{counter:04d}", start, end, value))

    cursor = 0
    while cursor < len(text):
        opening = text.find("<", cursor)
        if opening < 0:
            take(cursor, len(text))
            break
        take(cursor, opening)
        cursor = _skip_construct(text, opening)
    return found


def _skip_construct(text: str, opening: int) -> int:
    """Конец конструкции, начавшейся с ``<``. Незакрытая считается идущей до конца файла."""
    if text.startswith("<!--", opening):
        return _after(text, "-->", opening + 4)
    if text.startswith("<![CDATA[", opening):
        return _after(text, "]]>", opening + 9)
    raw = _RAW_ELEMENT_RE.match(text, opening)
    if raw:
        closing = _RAW_CLOSERS[raw.group(1).lower()].search(text, opening)
        return _after(text, ">", closing.start()) if closing else len(text)
    return _after(text, ">", opening + 1)


def _after(text: str, closing: str, start: int) -> int:
    """Позиция сразу за закрывающей строкой; её нет — конец файла."""
    position = text.find(closing, start)
    return position + len(closing) if position >= 0 else len(text)
