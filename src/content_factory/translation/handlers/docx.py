"""DOCX: подмена текстовых узлов внутри исходного архива.

Переезд механики из ``api/routers/document_translation.py`` на общий контракт без изменения
поведения. Архив не пересобирается с нуля: каждая часть, кроме переводимых XML, копируется как
есть, поэтому стили, таблицы, нумерация и картинки сохраняются сами собой.

Ключ единицы — сквозной номер абзаца в порядке обхода архива. ``extract`` и ``apply`` обходят его
одинаково, поэтому связь «номер → абзац» воспроизводится без хранения состояния между вызовами.
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
from content_factory.translation.zip_safety import open_checked, read_member

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
TRANSLATABLE_XML_RE = re.compile(
    r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$"
)
_PARAGRAPH_TAG = f".//{{{WORD_NAMESPACE}}}p"
_TEXT_TAG = f"{{{WORD_NAMESPACE}}}t"
# Управляющие символы, которые ElementTree запишет, а Word потом сочтёт документ повреждённым.
_INVALID_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class DocxHandler:
    """Переводит текст абзацев DOCX, не трогая оформление."""

    extensions = frozenset({".docx"})

    def extract(self, content: bytes) -> list[TextUnit]:
        units: list[TextUnit] = []
        for unit_id, _xml_path, _index, text in _walk(content):
            units.append(TextUnit(unit_id=unit_id, text=text))
        return units

    def apply(self, content: bytes, translations: dict[str, str]) -> bytes:
        lookup = {
            (xml_path, index): translations[unit_id]
            for unit_id, xml_path, index, _text in _walk(content)
            if unit_id in translations
        }
        output = BytesIO()
        try:
            with open_checked(content) as source:
                with zipfile.ZipFile(output, "w") as target:
                    for info in source.infolist():
                        payload = read_member(source, info.filename)
                        if not TRANSLATABLE_XML_RE.match(info.filename):
                            target.writestr(info, payload)
                            continue
                        root = parse_xml_without_dtd(payload, info.filename)
                        for index, paragraph in enumerate(root.findall(_PARAGRAPH_TAG)):
                            translated = lookup.get((info.filename, index))
                            if translated is not None:
                                _apply_to_paragraph(paragraph, translated)
                        target.writestr(
                            info, ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
                        )
        except zipfile.BadZipFile as exc:
            raise DocumentFormatError("DOCX-файл поврежден или имеет неверный формат") from exc
        return output.getvalue()


def _walk(content: bytes) -> list[tuple[str, str, int, str]]:
    """Абзацы с текстом: ``(ключ, часть архива, номер абзаца, текст)`` в устойчивом порядке."""
    found: list[tuple[str, str, int, str]] = []
    try:
        with open_checked(content) as archive:
            paths = [name for name in archive.namelist() if TRANSLATABLE_XML_RE.match(name)]
            counter = 1
            for xml_path in paths:
                root = parse_xml_without_dtd(read_member(archive, xml_path), xml_path)
                for index, paragraph in enumerate(root.findall(_PARAGRAPH_TAG)):
                    text = "".join(node.text or "" for node in paragraph.iter() if node.tag == _TEXT_TAG)
                    if not text.strip():
                        continue
                    if counter > MAX_UNITS:
                        raise DocumentFormatError("В DOCX больше " + str(MAX_UNITS) + " переводимых кусков")
                    found.append((f"{counter:04d}", xml_path, index, text))
                    counter += 1
    except zipfile.BadZipFile as exc:
        raise DocumentFormatError("DOCX-файл поврежден или имеет неверный формат") from exc
    return found


def _apply_to_paragraph(paragraph: ElementTree.Element, translated: str) -> None:
    """Заменяет текст абзаца, не трогая стили, таблицы, списки и прочие узлы OOXML."""
    translated = strip_invalid_xml(translated)
    nodes = [node for node in paragraph.iter() if node.tag == _TEXT_TAG]
    if not nodes:
        return
    chunks = spread_over_runs(translated, [node.text or "" for node in nodes])
    for node, chunk in zip(nodes, chunks, strict=False):
        node.text = chunk
        if chunk[:1].isspace() or chunk[-1:].isspace():
            node.set(f"{{{XML_NAMESPACE}}}space", "preserve")
    for node in nodes[len(chunks) :]:
        node.text = ""
