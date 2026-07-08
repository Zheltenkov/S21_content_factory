from __future__ import annotations

import pytest

from content_factory.catalog.viewer._common import UploadedFile
from content_factory.catalog.viewer.intake_brief_io import extract_brief_text_from_bytes, load_brief_text


def test_extract_csv_brief_text_normalizes_rows() -> None:
    payload = "Навык;Описание\nPython;Бэкенд\nFastAPI;API".encode()

    text = extract_brief_text_from_bytes(payload, ".csv")

    assert "Навык: Описание" in text
    assert "Python: Бэкенд" in text
    assert "FastAPI: API" in text


def test_load_brief_text_prefers_uploaded_file_over_pasted_text() -> None:
    files = {
        "brief_file": UploadedFile(
            filename="brief.txt",
            content_type="text/plain",
            data="Файловый бриф".encode(),
        )
    }

    brief_text, source_name, source_kind, file_path = load_brief_text({"brief": "Ручной текст"}, files)

    assert brief_text == "Файловый бриф"
    assert source_name == "brief.txt"
    assert source_kind == "file"
    assert file_path is None


def test_extract_brief_text_rejects_unsupported_suffix() -> None:
    with pytest.raises(ValueError, match="Поддерживаются только файлы"):
        extract_brief_text_from_bytes(b"data", ".pdf")
