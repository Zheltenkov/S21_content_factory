"""Large presentations use their own upload budget without weakening document limits."""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from fastapi import HTTPException, UploadFile

from content_factory.api.routers import document_translation


def _presentation() -> bytes:
    """Build a small presentation whose media exceeds the test document budget."""
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Текст слайда</a:t>"
            "</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>",
        )
        archive.writestr("ppt/media/image1.png", b"media" * 400)
    return output.getvalue()


@pytest.mark.parametrize("known_size", [False, True])
@pytest.mark.parametrize("filename", ["meeting.pptx", "meeting.PPTX"])
async def test_presentation_at_its_limit_reaches_text_extraction(
    monkeypatch: pytest.MonkeyPatch, known_size: bool, filename: str,
) -> None:
    content = _presentation()
    # Separate budgets keep the boundary test small while exercising real ZIP extraction.
    monkeypatch.setattr(document_translation, "MAX_TRANSLATION_DOCUMENT_SIZE", 1024)
    monkeypatch.setattr(document_translation, "MAX_TRANSLATION_PRESENTATION_SIZE", len(content))
    upload = UploadFile(BytesIO(content), filename=filename, size=len(content) if known_size else None)

    document = await document_translation._read_uploaded_translation_document(upload)

    assert document.text == "Текст слайда"
    assert document.content == content
    assert document.extension == ".pptx"


@pytest.mark.parametrize("declared_size", [None, 1, "actual"])
async def test_presentation_over_its_limit_is_rejected(
    monkeypatch: pytest.MonkeyPatch, declared_size: int | str | None,
) -> None:
    content = _presentation()
    monkeypatch.setattr(document_translation, "MAX_TRANSLATION_DOCUMENT_SIZE", 1024)
    monkeypatch.setattr(document_translation, "MAX_TRANSLATION_PRESENTATION_SIZE", len(content) - 1)
    size = len(content) if declared_size == "actual" else declared_size
    assert isinstance(size, int) or size is None
    upload = UploadFile(BytesIO(content), filename="meeting.pptx", size=size)

    # Both declared and actual bytes must respect the presentation budget.
    with pytest.raises(HTTPException) as error:
        await document_translation._read_uploaded_translation_document(upload)

    assert error.value.status_code == 413


@pytest.mark.parametrize("known_size", [False, True])
@pytest.mark.parametrize("extension", ["txt", "docx", "pdf", "xlsx"])
async def test_other_documents_keep_the_document_limit(
    monkeypatch: pytest.MonkeyPatch, known_size: bool, extension: str,
) -> None:
    content = b"x" * 1025
    monkeypatch.setattr(document_translation, "MAX_TRANSLATION_DOCUMENT_SIZE", 1024)
    monkeypatch.setattr(document_translation, "MAX_TRANSLATION_PRESENTATION_SIZE", 4096)
    upload = UploadFile(BytesIO(content), filename=f"document.{extension}", size=len(content) if known_size else None)

    with pytest.raises(HTTPException) as error:
        await document_translation._read_uploaded_translation_document(upload)

    assert error.value.status_code == 413
