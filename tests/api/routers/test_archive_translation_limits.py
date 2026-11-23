"""Проверки размера ZIP: загрузка, распаковка и реальный multipart-запрос без LLM."""

from __future__ import annotations

import importlib
import zipfile
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO, cast

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from content_factory.api.dependencies import get_current_user
from content_factory.api.main import app
from content_factory.api.routers import archive_translation as module
from content_factory.translation.archive import MAX_MEMBER_BYTES, MAX_UNPACKED_BYTES, _check_shape
from content_factory.translation.errors import DocumentFormatError

MIB = 1024 * 1024


@pytest.fixture
def default_archive_limit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Изолируем настройку окружения и восстанавливаем модуль после проверки."""
    with monkeypatch.context() as patch:
        patch.delenv("MAX_TRANSLATION_ARCHIVE_SIZE_BYTES", raising=False)
        importlib.reload(module)
        try:
            yield
        finally:
            module._PLANS.clear()
    importlib.reload(module)


@pytest.mark.parametrize("extra_bytes", [0, 1])
async def test_upload_boundary_is_70_mib(default_archive_limit: None, extra_bytes: int) -> None:
    # Разреженный временный файл проверяет реальное потоковое чтение, не держит две копии входа.
    with TemporaryFile() as stream:
        stream.truncate(70 * MIB + extra_bytes)
        upload = UploadFile(cast(BinaryIO, stream), filename="course.zip")
        if extra_bytes:
            with pytest.raises(HTTPException) as error:
                await module.read_uploaded_archive(upload)
            assert error.value.status_code == 413
            assert "70MB" in error.value.detail
        else:
            name, content = await module.read_uploaded_archive(upload)
            assert name == "course.zip"
            assert len(content) == 70 * MIB


def test_environment_can_override_archive_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as patch:
        patch.setenv("MAX_TRANSLATION_ARCHIVE_SIZE_BYTES", str(80 * MIB))
        importlib.reload(module)
        try:
            assert module.MAX_ARCHIVE_SIZE == 80 * MIB
        finally:
            module._PLANS.clear()
    importlib.reload(module)


def test_a_69_mib_zip_can_be_inspected_over_http(
    default_archive_limit: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проходит обе прежние границы — 25 МБ на входе и 64 МБ распакованного."""
    archive_path = tmp_path / "course.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("README.md", "Практическое задание: проверьте решение.")
        # Бинарные ресурсы не переводятся, но должны присутствовать в описи без изменений.
        chunk = b"\0" * (14 * MIB)
        for index in range(4):
            archive.writestr(f"assets/data-{index}.bin", chunk)
        archive.writestr("assets/data-4.bin", b"\0" * (13 * MIB))
    assert 69 * MIB < archive_path.stat().st_size < 70 * MIB

    def forbidden_model(**kwargs: object) -> None:
        raise AssertionError("Опись не должна обращаться к модели")

    monkeypatch.setattr(module, "create_llm_client", forbidden_model)
    previous_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_current_user] = lambda: {"id": "archive-size-test", "username": "test"}
    try:
        with archive_path.open("rb") as stream:
            response = TestClient(app).post(
                "/api/v1/translate/archive/inspect",
                files={"file": ("course.zip", stream, "application/zip")},
            )
        assert response.status_code == 200, response.text
        result = response.json()
        assert len(result["entries"]) == 6
        assert result["totals"]["files"] == 1
        plan = module.plan_for_user(result["plan_id"], "archive-size-test")
        assert sum(len(member.content) for member in plan["members"]) > 69 * MIB
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)


@pytest.mark.parametrize("extra_bytes", [0, 1])
def test_unpacked_total_boundary_is_140_mib(extra_bytes: int) -> None:
    # Проверяем суммарный предел отдельно: каждый файл меньше индивидуального лимита.
    infos = []
    for index in range(10):
        info = zipfile.ZipInfo(f"data-{index}.bin")
        info.file_size = 14 * MIB + (extra_bytes if index == 9 else 0)
        infos.append(info)
    assert MAX_UNPACKED_BYTES == 140 * MIB
    if extra_bytes:
        with pytest.raises(DocumentFormatError, match="в сумме больше"):
            _check_shape(infos)
    else:
        _check_shape(infos)


def test_single_member_limit_is_still_16_mib() -> None:
    info = zipfile.ZipInfo("oversized.bin")
    info.file_size = 16 * MIB + 1
    assert MAX_MEMBER_BYTES == 16 * MIB
    with pytest.raises(DocumentFormatError, match="oversized.bin"):
        _check_shape([info])
