"""Tests for document upload extraction in translation router."""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException

from content_factory.api.routers.document_translation import (
    TRANSLATION_DOCUMENT_EXTENSIONS,
    _extract_translation_document_text,
    _safe_translation_filename,
)
from content_factory.api.routers.readme_translate import _translation_job_for_user
from content_factory.api.utils.result_cache import _translation_jobs, set_translation_job


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
    text = _extract_translation_document_text("notes.txt", "Привет\n\nмир".encode())

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


# ---------------------------------------------- перевод через реестр обработчиков


def test_accepted_extensions_follow_the_handler_registry() -> None:
    """Список допустимых расширений ведёт реестр: добавили обработчик — формат принимается."""
    from content_factory.translation.registry import supported_extensions

    assert supported_extensions() <= TRANSLATION_DOCUMENT_EXTENSIONS
    for extension in (".yml", ".yaml", ".xlsx", ".pptx"):
        assert extension in TRANSLATION_DOCUMENT_EXTENSIONS


class _FakeUnitsLlm:
    """Отвечает по контракту движка: каждому id — перевод с пометкой."""

    def complete(self, system: str, user: str, **kwargs) -> str:  # noqa: ARG002
        import json

        marker = "Входные данные:\n"
        payload = json.loads(user[user.index(marker) + len(marker) :])
        return json.dumps(
            {"translations": {f["id"]: f"[ru] {f['text']}" for f in payload["fragments"]}},
            ensure_ascii=False,
        )


def _run_translation(
    monkeypatch, tmp_path, filename: str, content: bytes, target_language: str = "en"
) -> dict:
    from content_factory.api.routers import document_translation as module
    from content_factory.api.utils.result_cache import _translation_jobs

    monkeypatch.setattr(module, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(module, "create_llm_client", lambda **kwargs: _FakeUnitsLlm())
    monkeypatch.setattr(module, "upsert_user_run", lambda **kwargs: None)

    document = module.ExtractedTranslationDocument(
        text="",
        filename=filename,
        extension="." + filename.rsplit(".", 1)[1],
        title_seed=filename,
        content=content,
    )
    module._run_document_translation(
        request_id="req-1",
        user_id="user-1",
        document=document,
        target_language=target_language,
        translation_mode="direct",
        seed=module.ProjectSeed(language="en", project_type="individual", project_description="тест"),
    )
    return _translation_jobs["req-1"]


def test_a_pdf_comes_back_as_a_pdf_artifact(monkeypatch, tmp_path) -> None:
    """Ради этого всё и затевалось: PDF перестаёт превращаться в голый текст."""
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 500, 150), "Intro to Machine Learning", fontsize=11)
    job = _run_translation(monkeypatch, tmp_path, "lecture.pdf", document.tobytes())

    assert job["status"] == "completed", job.get("error")
    assert "pdf" in (job.get("result_links") or {}), "должен появиться файл того же формата"
    stored = (tmp_path / "translations" / "req-1" / job["result_links"]["pdf"]).read_bytes()
    assert stored.startswith(b"%PDF"), "это настоящий PDF, а не текст"
    assert "[ru] Intro to Machine Learning" in " ".join(
        p.get_text() for p in pymupdf.open("pdf", stored)
    )


def test_a_docx_keeps_its_artifact_key(monkeypatch, tmp_path) -> None:
    """Прежние задачи и страница читают ключ docx — его нельзя переименовывать молча."""
    job = _run_translation(monkeypatch, tmp_path, "notes.docx", _minimal_docx(["Hello world"]))

    assert job["status"] == "completed", job.get("error")
    assert "docx" in (job.get("result_links") or {})


def test_any_produced_artifact_kind_can_be_downloaded(monkeypatch, tmp_path) -> None:
    """Скачивание больше не знает про один docx: отдаётся то, что задача действительно собрала."""
    import asyncio

    from content_factory.api.routers import readme_translate as route

    job = _run_translation(monkeypatch, tmp_path, "sheet.xlsx", _minimal_xlsx())
    assert "xlsx" in (job.get("result_links") or {}), job.get("error")

    monkeypatch.setattr(route, "STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(route, "_translation_job_for_user", lambda request_id, user: job)
    response = asyncio.run(
        route.download_translation_artifact(request_id="req-1", type="xlsx", user={"id": "user-1"})
    )

    assert response.status_code == 200


def test_an_artifact_kind_the_job_never_made_is_refused(monkeypatch, tmp_path) -> None:
    import asyncio

    from fastapi import HTTPException as FastApiHTTPException

    from content_factory.api.routers import readme_translate as route

    job = _run_translation(monkeypatch, tmp_path, "sheet.xlsx", _minimal_xlsx())
    monkeypatch.setattr(route, "_translation_job_for_user", lambda request_id, user: job)

    with pytest.raises(FastApiHTTPException):
        asyncio.run(
            route.download_translation_artifact(request_id="req-1", type="docx", user={"id": "u"})
        )


def _minimal_xlsx() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.active["A1"] = "Project name"
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_translating_to_russian_is_a_deliberate_no_op(monkeypatch, tmp_path) -> None:
    """Исходники фабрики всегда на русском, поэтому «перевести на русский» ничего не меняет.

    Выглядит как недосмотр, но это принятый порядок: файл пересобирается, текст остаётся.
    """
    from content_factory.api.routers import document_translation as module

    assert module.SOURCE_LANGUAGE == "ru"

    source = _minimal_docx(["Первый абзац", "Второй абзац"])
    job = _run_translation(monkeypatch, tmp_path, "notes.docx", source, target_language="ru")

    assert job["status"] == "completed", job.get("error")
    assert "Первый абзац" in job["translated_markdown"], "текст остался исходным"
