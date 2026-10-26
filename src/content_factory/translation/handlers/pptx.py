"""PPTX: перевод текста слайдов правкой XML внутри презентации.

Зависимость ``python-pptx``, заложенная в замысле, не понадобилась: презентация — такой же
OOXML-архив, как DOCX и XLSX. Текст лежит в ``<a:t>`` внутри абзацев ``<a:p>``, и правится он тем
же способом — всё, кроме переводимых частей, копируется байт в байт, поэтому картинки, переходы,
анимация и оформление остаются нетронутыми.

Переводим слайды и заметки докладчика. Образцы и макеты (``slideMasters``, ``slideLayouts``) не
трогаем: там лежат служебные подписи вроде «Щёлкните, чтобы добавить заголовок», которые на
готовых слайдах не видны, а перевод их только путает.
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

DRAWING_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
TRANSLATABLE_XML_RE = re.compile(r"^ppt/(?:slides/slide\d+|notesSlides/notesSlide\d+)\.xml$")
_PARAGRAPH_TAG = f".//{{{DRAWING_NAMESPACE}}}p"
_TEXT_TAG = f"{{{DRAWING_NAMESPACE}}}t"


class PptxHandler:
    """Переводит текст слайдов и заметок, не трогая оформление."""

    extensions = frozenset({".pptx"})

    def extract(self, content: bytes) -> list[TextUnit]:
        return [
            TextUnit(unit_id=unit_id, text=text, hint="слайд")
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
            raise DocumentFormatError("PPTX-файл поврежден или имеет неверный формат") from exc
        return output.getvalue()


def _walk(content: bytes) -> list[tuple[str, str, int, str]]:
    """Абзацы слайдов: ``(ключ, часть архива, номер, текст)`` в устойчивом порядке."""
    found: list[tuple[str, str, int, str]] = []
    try:
        with open_checked(content) as archive:
            parts = [name for name in archive.namelist() if TRANSLATABLE_XML_RE.match(name)]
            counter = 1
            for part in sorted(parts):
                root = parse_xml_without_dtd(read_member(archive, part), part)
                for index, paragraph in enumerate(root.findall(_PARAGRAPH_TAG)):
                    text = "".join(
                        node.text or "" for node in paragraph.iter() if node.tag == _TEXT_TAG
                    )
                    if not text.strip():
                        continue
                    if counter > MAX_UNITS:
                        raise DocumentFormatError("В PPTX больше " + str(MAX_UNITS) + " переводимых кусков")
                    found.append((f"{counter:04d}", part, index, text))
                    counter += 1
    except zipfile.BadZipFile as exc:
        raise DocumentFormatError("PPTX-файл поврежден или имеет неверный формат") from exc
    return found


def _apply_to_paragraph(paragraph: ElementTree.Element, translated: str) -> None:
    """Заменяет текст абзаца, раскладывая перевод по исходным run."""
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
