"""Разбор XML из пользовательских файлов без риска раздувания сущностей.

``xml.etree`` с Python 3.7.1 не подтягивает внешние сущности — ни файлы, ни сеть. Но внутренние
он разворачивает: часть ``word/document.xml`` с десятком вложенных объявлений разрастается в
гигабайты и кладёт процесс. DOCX приходит от пользователя, а в переводе архива — и вовсе из
архива, загруженного снаружи.

Защита точная, без новой зависимости: OOXML по формату не содержит DTD, а без объявления сущность
не определена — ``ElementTree`` просто сообщит об ошибке. Значит отказ любой части с DTD закрывает
и «миллиард смешков», и квадратичное раздувание, не меняя ни разбор, ни сериализацию.

Искать подстроку в сырых байтах безопасно: внутри текста документа такая строка приедет
экранированной (``&lt;!DOCTYPE``), поэтому ложного отказа на статью про XML не будет.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

from content_factory.translation.errors import DocumentFormatError

_DTD_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


def parse_xml_without_dtd(payload: bytes, part_name: str) -> ElementTree.Element:
    """Разбирает часть документа, отказывая файлам с объявлением DTD или сущностей."""
    if _DTD_RE.search(payload):
        raise DocumentFormatError(
            f"В части {part_name} объявлен DTD — такой файл не обрабатываем: "
            "объявленные сущности способны развернуться в гигабайты текста"
        )
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise DocumentFormatError(f"Не удалось разобрать {part_name}") from exc
