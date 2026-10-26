"""XLSX: перевод строковых ячеек правкой XML внутри книги.

openpyxl здесь не используется намеренно. Он умеет читать и писать книгу, но пересборка теряет
диаграммы, картинки, условное оформление и часть настроек листа. Вместо этого правятся те же
части архива, что и в DOCX: всё остальное копируется байт в байт.

Текст в XLSX живёт в двух местах. Основное — общая таблица строк ``xl/sharedStrings.xml``: Excel
хранит каждую уникальную строку один раз, а ячейки ссылаются на неё номером. Реже встречается
встроенный текст прямо в листе (``<is>``), и он тоже поддержан.

Числа, даты и формулы в этих частях не лежат вовсе, поэтому не трогаются сами собой — отдельной
проверки для них не нужно.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.ooxml import MAX_UNITS, spread_over_runs, strip_invalid_xml
from content_factory.translation.units import TextUnit
from content_factory.translation.xml_safety import parse_xml_without_dtd
from content_factory.translation.zip_safety import open_checked, read_member, read_xml_member

SHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
TRANSLATABLE_XML_RE = re.compile(r"^xl/(?:sharedStrings\.xml|worksheets/sheet\d+\.xml)$")
_TEXT_TAG = f"{{{SHEET_NAMESPACE}}}t"
# Общая строка (`si`) и встроенный текст ячейки (`is`) — две обёртки одного и того же.
_HOLDER_TAGS = (f".//{{{SHEET_NAMESPACE}}}si", f".//{{{SHEET_NAMESPACE}}}is")


class XlsxHandler:
    """Переводит текст ячеек, не трогая числа, формулы и оформление."""

    extensions = frozenset({".xlsx"})

    def extract(self, content: bytes) -> list[TextUnit]:
        return [
            TextUnit(unit_id=unit_id, text=text, hint="ячейка")
            for unit_id, _part, _index, text in _walk(content)
        ]

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        lookup = {
            (part, index): translations[unit_id]
            for unit_id, part, index, _text in _walk(content)
            if unit_id in translations
        }
        output = BytesIO()
        try:
            with open_checked(content) as source:
                with zipfile.ZipFile(output, "w") as target:
                    for info in source.infolist():
                        if not TRANSLATABLE_XML_RE.match(info.filename):
                            target.writestr(info, read_member(source, info.filename))
                            continue
                        payload = read_xml_member(source, info.filename)
                        root = parse_xml_without_dtd(payload, info.filename)
                        for index, holder in enumerate(_holders(root)):
                            translated = lookup.get((info.filename, index))
                            if translated is not None:
                                _apply_to_holder(holder, translated)
                        target.writestr(
                            info, ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
                        )
        except zipfile.BadZipFile as exc:
            raise DocumentFormatError("XLSX-файл поврежден или имеет неверный формат") from exc
        return output.getvalue()


def _walk(content: bytes) -> list[tuple[str, str, int, str]]:
    """Тексты ячеек: ``(ключ, часть архива, номер, текст)`` в устойчивом порядке."""
    found: list[tuple[str, str, int, str]] = []
    try:
        with open_checked(content) as archive:
            parts = [name for name in archive.namelist() if TRANSLATABLE_XML_RE.match(name)]
            counter = 1
            for part in sorted(parts):
                root = parse_xml_without_dtd(read_xml_member(archive, part), part)
                for index, holder in enumerate(_holders(root)):
                    text = "".join(node.text or "" for node in holder.iter() if node.tag == _TEXT_TAG)
                    if not text.strip():
                        continue
                    if counter > MAX_UNITS:
                        raise DocumentFormatError("В XLSX больше " + str(MAX_UNITS) + " переводимых кусков")
                    found.append((f"{counter:04d}", part, index, text))
                    counter += 1
    except zipfile.BadZipFile as exc:
        raise DocumentFormatError("XLSX-файл поврежден или имеет неверный формат") from exc
    return found


def _holders(root: ElementTree.Element) -> list[ElementTree.Element]:
    """Обёртки текста в порядке появления — и общие строки, и встроенные."""
    holders: list[ElementTree.Element] = []
    for tag in _HOLDER_TAGS:
        holders.extend(root.findall(tag))
    return holders


def _apply_to_holder(holder: ElementTree.Element, translated: str) -> None:
    """Заменяет текст, раскладывая перевод по исходным run — так остаётся inline-оформление."""
    translated = strip_invalid_xml(translated)
    nodes = [node for node in holder.iter() if node.tag == _TEXT_TAG]
    if not nodes:
        return
    chunks = spread_over_runs(translated, [node.text or "" for node in nodes])
    for node, chunk in zip(nodes, chunks, strict=False):
        node.text = chunk
        if chunk[:1].isspace() or chunk[-1:].isspace():
            node.set(f"{{{XML_NAMESPACE}}}space", "preserve")
    for node in nodes[len(chunks) :]:
        node.text = ""
