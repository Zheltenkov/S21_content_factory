"""Разбор XML из пользовательских файлов без риска раздувания сущностей.

``xml.etree`` с Python 3.7.1 не подтягивает внешние сущности — ни файлы, ни сеть. Но внутренние
он разворачивает: часть ``word/document.xml`` с десятком вложенных объявлений разрастается в
гигабайты и кладёт процесс. DOCX приходит от пользователя, а в переводе архива — и вовсе из
архива, загруженного снаружи.

Две редакции этой защиты оказались неверными, и обе ошибки стоит знать, чтобы не повторить.

Первой был поиск ``<!DOCTYPE`` по сырым байтам. Он обходится кодировкой: в UTF-16 те же байты
выглядят как ``<\\x00!\\x00D\\x00…``, поиск подстроки их не узнаёт, а expat кодировку определяет
по метке порядка байтов и сущности разворачивает. Проверяющий и разбирающий читали файл по-разному.

Второй была попытка повесить обработчики на ``ElementTree.XMLParser().parser``. У ускоренной
реализации в CPython такого атрибута нет — он был только у чистой, — поэтому защита молча не
включалась.

Работает третья: файл сначала читает сам expat, то есть тот же разборщик и с тем же пониманием
кодировки. На объявлении DTD или сущности он отказывает, а если дошёл до корневого элемента —
объявлений не было, и чтение прекращается. DTD по стандарту стоит до корня, поэтому дальше
пролога заглядывать незачем.

Новая зависимость не нужна: ``defusedxml`` делает ровно это же через те же обработчики pyexpat.
"""

from __future__ import annotations

from xml.etree import ElementTree
from xml.parsers import expat

from content_factory.translation.errors import DocumentFormatError

_DTD_MESSAGE = (
    "объявлен DTD — такой файл не обрабатываем: "
    "объявленные сущности способны развернуться в гигабайты текста"
)


class _StopScan(Exception):
    """Пролог прочитан, дальше смотреть незачем."""


def parse_xml_without_dtd(payload: bytes, part_name: str) -> ElementTree.Element:
    """Разбирает часть документа, отказывая файлам с объявлением DTD или сущностей."""
    if _declares_dtd(payload):
        raise DocumentFormatError(f"В части {part_name} {_DTD_MESSAGE}")
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise DocumentFormatError(f"Не удалось разобрать {part_name}") from exc


def _declares_dtd(payload: bytes) -> bool:
    """Есть ли в прологе объявление DTD или сущности. Кодировку определяет expat, а не мы."""
    parser = expat.ParserCreate()
    found = False

    def on_declaration(*_args: object) -> None:
        nonlocal found
        found = True
        raise _StopScan

    def on_root_element(*_args: object) -> None:
        raise _StopScan  # дошли до корня: объявлений не было

    def on_external_reference(*_args: object) -> int:
        """Отдельно от прочих: expat ждёт здесь код возврата, хотя мы до него не доходим."""
        on_declaration()
        return 0

    parser.StartDoctypeDeclHandler = on_declaration
    parser.EntityDeclHandler = on_declaration
    parser.UnparsedEntityDeclHandler = on_declaration
    parser.NotationDeclHandler = on_declaration
    parser.ExternalEntityRefHandler = on_external_reference
    parser.StartElementHandler = on_root_element

    try:
        parser.Parse(payload, True)
    except _StopScan:
        pass
    except expat.ExpatError:
        # Файл неразбираем в принципе: пусть об этом скажет основной разбор, внятным сообщением.
        return False
    return found
