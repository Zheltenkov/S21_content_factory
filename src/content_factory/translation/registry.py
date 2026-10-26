"""Расширение файла → обработчик формата.

Реестр намеренно плоский: добавить формат значит написать обработчик и дописать его сюда одной
строкой. Пересечения расширений запрещены и проверяются тестом — иначе выбор обработчика стал бы
зависеть от порядка объявления.
"""

from __future__ import annotations

from content_factory.translation.handlers.docx import DocxHandler
from content_factory.translation.handlers.plain import PlainTextHandler
from content_factory.translation.handlers.pptx import PptxHandler
from content_factory.translation.handlers.xlsx import XlsxHandler
from content_factory.translation.handlers.yaml_file import YamlHandler
from content_factory.translation.units import FormatHandler

_HANDLERS: tuple[FormatHandler, ...] = (PlainTextHandler(), DocxHandler(), YamlHandler(), XlsxHandler(), PptxHandler())


def all_handlers() -> tuple[FormatHandler, ...]:
    """Все зарегистрированные обработчики."""
    return _HANDLERS


def handler_for(extension: str) -> FormatHandler | None:
    """Обработчик для расширения (с точкой, регистр не важен) или ``None``, если формат чужой."""
    normalized = (extension or "").lower()
    if not normalized:
        return None
    for handler in _HANDLERS:
        if normalized in handler.extensions:
            return handler
    return None


def supported_extensions() -> frozenset[str]:
    """Все расширения, для которых есть обработчик."""
    return frozenset().union(*(handler.extensions for handler in _HANDLERS)) if _HANDLERS else frozenset()
