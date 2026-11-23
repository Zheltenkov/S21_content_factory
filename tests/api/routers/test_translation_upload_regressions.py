"""Проверяем фактическое чтение загрузок, а не только список допустимых расширений."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from content_factory.api.routers import archive_translation, document_translation
from content_factory.api.utils.result_cache import _translation_jobs
from content_factory.generation.models.schemas import ProjectSeed
from content_factory.translation.archive import repack
from content_factory.translation.handlers.yaml_file import YamlHandler


def _zip(parts: Mapping[str, bytes | str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _structured_document(extension: str) -> bytes:
    if extension in ("yml", "yaml"):
        return "# Пояснение\ntitle: Проверка решения\nkind: '1'\n".encode()
    if extension in ("csv", "tsv"):
        delimiter = "," if extension == "csv" else "\t"
        return f"name{delimiter}price\nПроверка решения{delimiter}100\n".encode()
    if extension == "xlsx":
        return _zip({"xl/worksheets/sheet1.xml": (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Проверка решения</t>'
            '</is></c></row></sheetData></worksheet>'
        )})
    return _zip({"ppt/slides/slide1.xml": (
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Проверка решения</a:t>'
        '</a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'
    )})


@pytest.mark.parametrize("extension", ["yml", "yaml", "xlsx", "pptx", "csv", "tsv"])
async def test_registered_document_reaches_extraction(extension: str) -> None:
    content = _structured_document(extension)
    upload = UploadFile(BytesIO(content), filename="document." + extension)
    document = await document_translation._read_uploaded_translation_document(upload)
    assert "Проверка решения" in document.text
    assert document.content == content


class _TranslatedUnits:
    def complete(self, system: str, user: str, **kwargs: object) -> str:
        payload = json.loads(user.split("Входные данные:\n", 1)[1])
        return json.dumps({"translations": {unit["id"]: "Санҷиши натиҷа" for unit in payload["fragments"]}})


async def test_yaml_upload_to_tajik_returns_yaml_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Проходим чтение загрузки перед обработчиком: прежние тесты создавали документ вручную.
    content = _structured_document("yml")
    document = await document_translation._read_uploaded_translation_document(
        UploadFile(BytesIO(content), filename="check-list.yml")
    )
    monkeypatch.setattr(document_translation, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(document_translation, "create_llm_client", lambda **kwargs: _TranslatedUnits())
    monkeypatch.setattr(document_translation, "upsert_user_run", lambda **kwargs: None)
    request_id = "yaml-upload-regression"
    try:
        document_translation._run_document_translation(
            request_id, "test-user", document, "tg", "literal",
            ProjectSeed(language="en", project_type="individual", project_description="Проверка"),
        )
        result = _translation_jobs[request_id]
        assert result["status"] == "completed", result.get("error")
        target = tmp_path / "translations" / request_id / result["result_links"]["yml"]
        assert "Санҷиши натиҷа" in target.read_text(encoding="utf-8")
        assert "# Пояснение" in target.read_text(encoding="utf-8")
        assert "kind: '1'" in target.read_text(encoding="utf-8")
        assert YamlHandler().extract(target.read_bytes())
    finally:
        _translation_jobs.pop(request_id, None)


@pytest.mark.parametrize("content", [b"title: [broken", b"title: \xa2\xff"])
async def test_bad_yaml_upload_has_a_readable_400(content: bytes) -> None:
    with pytest.raises(HTTPException) as error:
        await document_translation._read_uploaded_translation_document(
            UploadFile(BytesIO(content), filename="check-list.yml")
        )
    assert error.value.status_code == 400
    assert "не поддерживается" not in error.value.detail


@pytest.mark.parametrize("extension", ["md", "yml", "csv", "html"])
def test_unreadable_archive_member_does_not_crash_inventory(extension: str) -> None:
    original = {"README.md": "Практическое задание".encode(), "broken." + extension: b"\xa2\xff"}
    members, entries, _ = archive_translation.inspect_archive(_zip(original))
    assert entries[0].selected
    assert not entries[1].selected
    assert "UTF-8" in (entries[1].skip_reason or "")
    with zipfile.ZipFile(BytesIO(repack(members, {}, report=""))) as archive:
        assert archive.read("broken." + extension) == original["broken." + extension]


def test_macos_metadata_is_skipped_and_preserved() -> None:
    binary = b"\x00\x05\x16\x07" + b"\x00" * 33 + b"\xa2\xff"
    original = {
        "README.md": "Практическое задание".encode(),
        "__MACOSX/._check-list.yml": binary,
        "nested/._README.md": binary,
        "nested/.DS_Store": binary,
        "._notes.md": "Обычный учебный текст".encode(),
    }
    members, entries, _ = archive_translation.inspect_archive(_zip(original))
    by_path = {entry.path: entry for entry in entries}
    assert by_path["README.md"].selected and by_path["._notes.md"].selected
    for name in list(original)[1:4]:
        assert not by_path[name].selected
        assert by_path[name].handler is None
        assert "macOS" in (by_path[name].skip_reason or "")
    with zipfile.ZipFile(BytesIO(repack(members, {}, report=""))) as archive:
        assert {name: archive.read(name) for name in original} == original
