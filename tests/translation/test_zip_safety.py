"""Защита от разворачивания сжатых документов.

DOCX, XLSX и PPTX — zip, пришедший от пользователя. Загрузка ограничена двадцатью пятью
мегабайтами в сжатом виде, и этого с запасом хватает, чтобы развернуться в сотни гигабайт:
килобайт нулей сжимается почти до нуля. Поэтому проверяется не только размер архива.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.zip_safety import MAX_MEMBER_BYTES, open_checked, read_member


def _zip_with(name: str, payload: bytes) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, payload)
    return buffer.getvalue()


def test_an_ordinary_archive_opens() -> None:
    content = _zip_with("word/document.xml", b"<w:document/>")

    with open_checked(content) as archive:
        assert read_member(archive, "word/document.xml") == b"<w:document/>"


def test_a_member_that_unpacks_too_large_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.translation.zip_safety.MAX_MEMBER_BYTES", 1024)
    content = _zip_with("word/document.xml", b"\0" * 200_000)

    with pytest.raises(DocumentFormatError, match="разворачивается"):
        with open_checked(content):
            pass


def test_the_whole_archive_is_measured_not_just_one_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("content_factory.translation.zip_safety.MAX_TOTAL_BYTES", 4096)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for index in range(10):
            archive.writestr(f"part{index}.xml", b"\0" * 1000)

    with pytest.raises(DocumentFormatError, match="разворачивается"):
        with open_checked(buffer.getvalue()):
            pass


def test_a_lying_header_does_not_get_to_fill_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Объявленный размер — слова архива о себе; чтение всё равно ограничено."""
    monkeypatch.setattr("content_factory.translation.zip_safety.MAX_MEMBER_BYTES", 1024)
    content = _zip_with("part.xml", b"\0" * 200_000)

    with zipfile.ZipFile(BytesIO(content)) as archive:
        for info in archive.infolist():
            info.file_size = 10  # архив утверждает, что внутри десять байт
        # Отказ приходит либо по пределу, либо по несошедшейся контрольной сумме — важно, что
        # он приходит, а память не наполняется.
        with pytest.raises(DocumentFormatError):
            read_member(archive, "part.xml")


def test_a_broken_archive_is_a_format_error() -> None:
    with pytest.raises(DocumentFormatError):
        with open_checked(b"not a zip"):
            pass


def test_the_limits_are_sane() -> None:
    assert MAX_MEMBER_BYTES >= 50 * 1024 * 1024, "обычный DOCX с картинками должен проходить"
