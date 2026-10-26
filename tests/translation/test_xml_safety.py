"""Защита от раздувания сущностей в XML внутри DOCX.

Внешние сущности xml.etree не подтягивает с Python 3.7.1, а вот внутренние раздувает: часть
`word/document.xml` с объявлением сущностей разворачивается в гигабайты и кладёт процесс. Файл
приходит от пользователя, а скоро будет приходить и из архива, поэтому проверка обязательна.

OOXML по формату не содержит DTD, а без объявления сущность не определена — значит отказ любой
части с DTD закрывает и «миллиард смешков», и квадратичное раздувание.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.handlers.docx import DocxHandler
from content_factory.translation.xml_safety import parse_xml_without_dtd

_BOMB = (
    '<?xml version="1.0"?>\n'
    "<!DOCTYPE lolz [\n"
    '  <!ENTITY lol "lol">\n'
    '  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
    '  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">\n'
    "]>\n"
    "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
    "<w:body><w:p><w:r><w:t>&lol3;</w:t></w:r></w:p></w:body></w:document>"
)


def _docx_with_part(document_xml: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_a_declared_entity_is_refused_outright() -> None:
    with pytest.raises(DocumentFormatError, match="DTD"):
        parse_xml_without_dtd(_BOMB.encode("utf-8"), "word/document.xml")


def test_ordinary_xml_still_parses() -> None:
    root = parse_xml_without_dtd(b'<?xml version="1.0"?><root><a>text</a></root>', "part.xml")

    assert root.find("a") is not None


def test_extracting_from_a_bomb_fails_instead_of_expanding() -> None:
    with pytest.raises(DocumentFormatError):
        DocxHandler().extract(_docx_with_part(_BOMB))


def test_applying_to_a_bomb_fails_instead_of_expanding() -> None:
    with pytest.raises(DocumentFormatError):
        DocxHandler().apply(_docx_with_part(_BOMB), {})


def test_the_word_doctype_inside_visible_text_is_not_a_dtd() -> None:
    """В тексте документа такая строка приедет экранированной и разбору не мешает."""
    safe = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&lt;!DOCTYPE в тексте статьи</w:t></w:r></w:p></w:body></w:document>"
    )

    units = DocxHandler().extract(_docx_with_part(safe))

    assert units[0].text == "<!DOCTYPE в тексте статьи"


def test_the_same_bomb_in_utf16_is_refused_too() -> None:
    """Проверка по сырым байтам обходится кодировкой, поэтому отказывать должен сам разборщик.

    В UTF-16 те же байты выглядят как ``<\x00!\x00D\x00…``: поиск подстроки их не узнаёт, а
    expat кодировку определяет по метке порядка байтов и сущности разворачивает.
    """
    with pytest.raises(DocumentFormatError):
        parse_xml_without_dtd(_BOMB.encode("utf-16"), "word/document.xml")


def test_utf16_without_a_dtd_still_parses() -> None:
    root = parse_xml_without_dtd('<root><a>текст</a></root>'.encode("utf-16"), "part.xml")

    assert root.find("a") is not None
    assert root.find("a").text == "текст"


def test_an_external_entity_is_refused_as_well() -> None:
    external = (
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        "<root>&xxe;</root>"
    )

    with pytest.raises(DocumentFormatError):
        parse_xml_without_dtd(external.encode("utf-8"), "part.xml")
