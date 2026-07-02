"""Tests for document upload extraction in translation router."""

from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException

from api.routers.readme_translate import (
    _build_translated_docx,
    _iter_docx_text_units,
    _extract_translation_document_text,
    _safe_translation_filename,
    _translate_docx_units,
    _translation_job_for_user,
    TRANSLATION_DOCUMENT_EXTENSIONS,
)
from api.utils.result_cache import _translation_jobs, set_translation_job


@pytest.fixture(autouse=True)
def clear_translation_jobs() -> None:
    """Keep translation status tests isolated from in-memory job cache."""
    _translation_jobs.clear()
    yield
    _translation_jobs.clear()


def _minimal_docx(paragraphs: list[str]) -> bytes:
    """Build a minimal DOCX package with document.xml only."""
    xml_paragraphs = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for text in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{xml_paragraphs}</w:body>"
        "</w:document>"
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def _read_docx_document_xml(content: bytes) -> str:
    with ZipFile(BytesIO(content), "r") as archive:
        return archive.read("word/document.xml").decode("utf-8")


class FakeDocxLlm:
    """Small fake LLM that follows the DOCX JSON translation contract."""

    def complete(self, system: str, user: str, response_format: str | None = None, **kwargs) -> str:  # noqa: ARG002
        assert "translations" in user
        return '{"translations":{"0001":"First paragraph","0002":"Second paragraph"}}'


def test_translation_document_extensions_cover_requested_formats() -> None:
    assert {".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf"} <= TRANSLATION_DOCUMENT_EXTENSIONS


def test_extract_translation_document_text_from_plain_text() -> None:
    text = _extract_translation_document_text("notes.txt", "Привет\n\nмир".encode("utf-8"))

    assert text == "Привет\n\nмир"


def test_extract_translation_document_text_from_html_strips_markup_and_scripts() -> None:
    html = b"<html><body><h1>Title</h1><script>bad()</script><p>Hello <b>world</b></p></body></html>"

    text = _extract_translation_document_text("page.html", html)

    assert "Title" in text
    assert "Hello" in text
    assert "world" in text
    assert "bad()" not in text
    assert "<h1>" not in text


def test_extract_translation_document_text_from_docx() -> None:
    content = _minimal_docx(["Первый абзац", "Второй абзац"])

    text = _extract_translation_document_text("document.docx", content)

    assert "Первый абзац" in text
    assert "Второй абзац" in text


def test_translate_docx_units_uses_json_contract() -> None:
    units = [
        *_iter_docx_text_units(_minimal_docx(["Первый абзац", "Второй абзац"]))
    ]

    translations = _translate_docx_units(FakeDocxLlm(), units, "en")

    assert translations == {
        "0001": "First paragraph",
        "0002": "Second paragraph",
    }


def test_build_translated_docx_replaces_text_and_keeps_package_parts() -> None:
    source = _minimal_docx(["Первый абзац", "Второй абзац"])
    units = _iter_docx_text_units(source)
    translated = _build_translated_docx(
        source,
        units,
        {"0001": "First paragraph", "0002": "Second paragraph"},
    )

    xml_text = _read_docx_document_xml(translated)
    assert "First paragraph" in xml_text
    assert "Second paragraph" in xml_text
    assert "Первый абзац" not in xml_text
    ElementTree.fromstring(xml_text.encode("utf-8"))


def test_build_translated_docx_strips_invalid_xml_control_chars() -> None:
    source = _minimal_docx(["В файлах .java"])
    units = _iter_docx_text_units(source)
    translated = _build_translated_docx(source, units, {"0001": "\x02java fayllarda"})

    xml_text = _read_docx_document_xml(translated)
    assert "\x02" not in xml_text
    assert "java fayllarda" in xml_text
    ElementTree.fromstring(xml_text.encode("utf-8"))


def test_safe_translation_filename_rejects_unsupported_extension() -> None:
    upload = type("Upload", (), {"filename": "payload.exe"})()

    with pytest.raises(HTTPException) as exc_info:
        _safe_translation_filename(upload)

    assert exc_info.value.status_code == 400
    assert "Формат документа не поддерживается" in exc_info.value.detail


def test_translation_job_for_user_returns_cached_status_for_owner() -> None:
    set_translation_job(
        request_id="translate-1",
        status="completed",
        user_id="user-1",
        target_language="uz",
        translated_markdown="Tarjima tayyor",
    )

    job = _translation_job_for_user("translate-1", {"id": "user-1"})

    assert job["status"] == "completed"
    assert job["target_language"] == "uz"
    assert job["translated_markdown"] == "Tarjima tayyor"


def test_translation_job_for_user_rejects_other_owner() -> None:
    set_translation_job(
        request_id="translate-2",
        status="in_progress",
        user_id="user-1",
        target_language="uz",
    )

    with pytest.raises(HTTPException) as exc_info:
        _translation_job_for_user("translate-2", {"id": "user-2"})

    assert exc_info.value.status_code == 403


def test_translation_job_for_user_returns_404_for_missing_job() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _translation_job_for_user("missing", {"id": "user-1"})

    assert exc_info.value.status_code == 404
