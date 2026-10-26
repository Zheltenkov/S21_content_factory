"""PPTX: перевод текста слайдов правкой XML внутри презентации.

Зависимость python-pptx не понадобилась: презентация — такой же OOXML, как DOCX и XLSX, текст
лежит в ``<a:t>`` внутри абзацев слайда. Правятся слайды и заметки докладчика; образцы и макеты
не трогаем — там служебные подписи вроде «Щёлкните, чтобы добавить заголовок».
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.etree import ElementTree

import pytest

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.handlers.pptx import PptxHandler

_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _slide(*paragraphs: str) -> str:
    body = "".join(
        f'<a:p><a:r><a:rPr lang="en-US"/><a:t>{text}</a:t></a:r></a:p>' for text in paragraphs
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        f'xmlns:a="{_DRAWING}"><p:cSld><p:spTree><p:sp><p:txBody>'
        f"{body}"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )


def _pptx_bytes(extra: dict[str, str] | None = None) -> bytes:
    parts = {
        "[Content_Types].xml": "<Types/>",
        "ppt/slides/slide1.xml": _slide("Intro to Machine Learning", "Summary of the day"),
        "ppt/slides/slide2.xml": _slide("Decision boundaries"),
        "ppt/notesSlides/notesSlide1.xml": _slide("Remind about the deadline"),
        "ppt/slideLayouts/slideLayout1.xml": _slide("Click to edit Master title style"),
        "ppt/media/image1.png": "\x89PNG-не-трогать",
    }
    parts.update(extra or {})
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _texts_in(content: bytes, part: str) -> list[str]:
    with zipfile.ZipFile(BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read(part))
    return [node.text or "" for node in root.iter(f"{{{_DRAWING}}}t")]


@pytest.fixture
def handler() -> PptxHandler:
    return PptxHandler()


def test_slide_text_and_speaker_notes_become_units(handler: PptxHandler) -> None:
    texts = [u.text for u in handler.extract(_pptx_bytes())]

    assert "Intro to Machine Learning" in texts
    assert "Decision boundaries" in texts
    assert "Remind about the deadline" in texts, "заметки докладчика тоже переводим"


def test_layout_placeholders_are_left_alone(handler: PptxHandler) -> None:
    texts = [u.text for u in handler.extract(_pptx_bytes())]

    assert "Click to edit Master title style" not in texts


def test_without_translations_the_slides_survive(handler: PptxHandler) -> None:
    source = _pptx_bytes()

    result = handler.apply(source, {})

    assert _texts_in(result, "ppt/slides/slide1.xml") == _texts_in(source, "ppt/slides/slide1.xml")


def test_media_is_copied_untouched(handler: PptxHandler) -> None:
    source = _pptx_bytes()

    result = handler.apply(source, {})

    with zipfile.ZipFile(BytesIO(result)) as archive:
        assert archive.read("ppt/media/image1.png") == "\x89PNG-не-трогать".encode()


def test_a_translated_paragraph_replaces_only_itself(handler: PptxHandler) -> None:
    source = _pptx_bytes()
    unit = next(u for u in handler.extract(source) if u.text == "Intro to Machine Learning")

    result = handler.apply(source, {unit.unit_id: "Введение в машинное обучение"})

    texts = _texts_in(result, "ppt/slides/slide1.xml")
    assert texts[0] == "Введение в машинное обучение"
    assert texts[1] == "Summary of the day", "соседний абзац не тронут"


def test_ids_are_stable_between_extract_and_apply(handler: PptxHandler) -> None:
    source = _pptx_bytes()

    first = [u.unit_id for u in handler.extract(source)]
    second = [u.unit_id for u in handler.extract(handler.apply(source, {}))]

    assert first == second


def test_a_declared_entity_inside_a_slide_is_refused(handler: PptxHandler) -> None:
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "a">]>'
        f'<p:sld xmlns:p="p" xmlns:a="{_DRAWING}"><a:p><a:r><a:t>&a;</a:t></a:r></a:p></p:sld>'
    )
    with pytest.raises(DocumentFormatError):
        handler.extract(_pptx_bytes({"ppt/slides/slide1.xml": bomb}))


def test_a_broken_file_is_a_format_error(handler: PptxHandler) -> None:
    with pytest.raises(DocumentFormatError):
        handler.extract(b"not a presentation")
