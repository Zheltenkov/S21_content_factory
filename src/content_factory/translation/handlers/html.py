"""HTML: перевод текстовых узлов с сохранением разметки.

Прежде модуль превращал HTML в текст и текстом же отдавал — разметка пропадала. Теперь файл
остаётся HTML: подменяется только то, что лежит между тегами.

Разбор идёт не по дереву, а по исходнику: находятся куски, которые трогать нельзя — теги,
комментарии, ``<script>``, ``<style>``, объявления, — а промежутки между ними и есть текст. Такой
способ не требует правильности разметки (а она в учебных материалах бывает всякая) и даёт
побайтное совпадение, когда переводить нечего.

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
MAX_UNITS = 20_000
MAX_CHARS = 2_000_000

_SKIP_RE = re.compile(
    r"<!--.*?-->"  # комментарий
    r"|<!\[CDATA\[.*?\]\]>"
    r"|<!.*?>"  # DOCTYPE и прочие объявления
    r"|<script\b.*?</script\s*>"
    r"|<style\b.*?</style\s*>"
    r"|<[^>]*>",  # обычный тег
    re.IGNORECASE | re.DOTALL,
)


class HtmlHandler:
    """Переводит текст между тегами, не трогая саму разметку."""

    extensions = frozenset({".html", ".htm"})

    def extract(self, content: bytes) -> list[TextUnit]:
        text = content.decode("utf-8")
        return [
            TextUnit(unit_id=unit_id, text=value, hint="текст страницы")
            for unit_id, _start, _end, value in _spans(text)
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        text = content.decode("utf-8")
        for unit_id, start, end, _value in reversed(_spans(text)):
            translated = translations.get(unit_id)
            if translated is None:
                continue
            original = text[start:end]
            head = original[: len(original) - len(original.lstrip())]
            tail = original[len(original.rstrip()) :]
            text = text[:start] + head + html.escape(translated, quote=False) + tail + text[end:]
        return text.encode("utf-8")


def _spans(text: str) -> list[tuple[str, int, int, str]]:
    """Текстовые промежутки: ``(ключ, начало, конец, текст без сущностей)``."""
    found: list[tuple[str, int, int, str]] = []
    counter = 0
    characters = 0
    cursor = 0

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

    for match in _SKIP_RE.finditer(text):
        take(cursor, match.start())
        cursor = match.end()
    take(cursor, len(text))
    return found
