"""Document translation service — extraction, DOCX rebuild, and the job runner.

Extracted from ``api/routers/readme_translate.py``: plain/HTML/DOCX/PDF text
extraction, DOCX re-assembly with translated runs, batched DOCX unit translation,
and the ``_run_document_translation`` job runner. The thin ``/translate/document``
route in ``readme_translate.py`` re-imports the entry points from here.
"""

import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import cast

from fastapi import HTTPException, UploadFile

from content_factory.api.db.user_runs_db import upsert_user_run
from content_factory.api.utils.file_validation import FORBIDDEN_FILENAMES, read_upload_limited
from content_factory.api.utils.logger import get_logger
from content_factory.api.utils.result_cache import set_translation_job, set_translation_phase
from content_factory.generation.agents.base.llm_client import LLMClientProtocol
from content_factory.generation.agents.translator import TranslatorAgent
from content_factory.generation.models.schemas import ProjectSeed
from content_factory.platform.llm.factory import create_llm_client
from content_factory.translation.engine import translate_units
from content_factory.translation.errors import DocumentFormatError
from content_factory.translation.registry import handler_for, supported_extensions
from content_factory.translation.xml_safety import parse_xml_without_dtd

logger = get_logger("translate-document")

STORAGE_DIR = os.getenv("STORAGE_DIR", os.path.join(tempfile.gettempdir(), "content_generator_translations"))
MAX_TRANSLATION_DOCUMENT_SIZE = int(os.getenv("MAX_TRANSLATION_DOCUMENT_SIZE_BYTES", 25 * 1024 * 1024))
# Допустимые расширения ведёт реестр: добавили обработчик — формат принимается на загрузку.
TRANSLATION_DOCUMENT_EXTENSIONS = set(supported_extensions())
# Язык исходников фабрики. Перевод всегда идёт с него, поэтому запрос «перевести на русский» —
# холостой ход, а не ошибка пользователя.
SOURCE_LANGUAGE = "ru"
TEXT_DOCUMENT_EXTENSIONS = {".md", ".markdown", ".txt"}
HTML_DOCUMENT_EXTENSIONS = {".html", ".htm"}


@dataclass(frozen=True)
class ExtractedTranslationDocument:
    """Текстовый контракт документа после безопасного извлечения содержимого."""

    text: str
    filename: str
    extension: str
    title_seed: str
    content: bytes


class _PlainHtmlTextExtractor(HTMLParser):
    """Минимальный HTML-to-text fallback без внешних зависимостей."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag in {"p", "div", "section", "article", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        clean = data.strip()
        if clean:
            self.parts.append(clean + " ")

    def text(self) -> str:
        return _normalize_extracted_text("".join(self.parts))


def _normalize_extracted_text(text: str) -> str:
    """Нормализует извлеченный текст без агрессивного форматирования."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    return normalized.strip()


def _decode_text_bytes(content: bytes) -> str:
    """Декодирует пользовательские текстовые документы с частыми кодировками."""
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _extract_text_from_html(content: bytes) -> str:
    """Извлекает видимый текст HTML, удаляя script/style/noscript."""
    html = _decode_text_bytes(content)
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for node in soup(["script", "style", "noscript"]):
            node.decompose()
        return _normalize_extracted_text(soup.get_text(separator="\n"))
    except Exception:
        parser = _PlainHtmlTextExtractor()
        parser.feed(html)
        return parser.text()


def _extract_text_from_docx(content: bytes) -> str:
    """Извлекает текст из DOCX через WordprocessingML без runtime-зависимости от python-docx."""
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="DOCX-файл поврежден или имеет неверный формат") from exc

    xml_paths = ["word/document.xml"]
    xml_paths.extend(
        name for name in archive.namelist()
        if re.match(r"word/(header|footer)\d+\.xml$", name)
    )
    paragraphs: list[str] = []
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    for path in xml_paths:
        try:
            root = parse_xml_without_dtd(archive.read(path), path)
        except KeyError:
            continue
        except DocumentFormatError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        for paragraph in root.findall(".//w:p", namespace):
            pieces: list[str] = []
            for node in paragraph.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if tag == "t" and node.text:
                    pieces.append(node.text)
                elif tag == "tab":
                    pieces.append("\t")
                elif tag in {"br", "cr"}:
                    pieces.append("\n")
            line = "".join(pieces).strip()
            if line:
                paragraphs.append(line)

    return _normalize_extracted_text("\n".join(paragraphs))


def _extract_text_from_pdf(content: bytes) -> str:
    """Извлекает текстовый слой PDF через pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Для перевода PDF нужно установить зависимость pypdf из requirements.txt",
        ) from exc

    try:
        reader = PdfReader(BytesIO(content))
        page_text = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Не удалось извлечь текст из PDF") from exc

    return _normalize_extracted_text("\n\n".join(page_text))


def _safe_translation_filename(file: UploadFile) -> tuple[str, str]:
    """Возвращает безопасное имя и расширение документа для перевода."""
    raw_filename = file.filename or ""
    filename = Path(raw_filename).name
    if (
        not filename
        or filename in FORBIDDEN_FILENAMES
        or ".." in raw_filename
        or "/" in raw_filename
        or "\\" in raw_filename
        or filename != raw_filename
    ):
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")
    extension = Path(filename).suffix.lower()
    if extension not in TRANSLATION_DOCUMENT_EXTENSIONS:
        allowed = ", ".join(sorted(TRANSLATION_DOCUMENT_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Формат документа не поддерживается. Разрешены: {allowed}")
    return filename, extension


def _extract_translation_document_text(filename: str, content: bytes) -> str:
    """Извлекает текст из поддерживаемого документа по расширению."""
    extension = Path(filename).suffix.lower()
    if extension in TEXT_DOCUMENT_EXTENSIONS:
        return _normalize_extracted_text(_decode_text_bytes(content))
    if extension in HTML_DOCUMENT_EXTENSIONS:
        return _extract_text_from_html(content)
    if extension == ".docx":
        return _extract_text_from_docx(content)
    if extension == ".pdf":
        return _extract_text_from_pdf(content)
    raise HTTPException(status_code=400, detail="Формат документа не поддерживается")


async def _read_uploaded_translation_document(file: UploadFile) -> ExtractedTranslationDocument:
    """Валидирует upload и извлекает текст для дальнейшего LLM-перевода."""
    filename, extension = _safe_translation_filename(file)
    if getattr(file, "size", None) and file.size and file.size > MAX_TRANSLATION_DOCUMENT_SIZE:
        limit_mb = MAX_TRANSLATION_DOCUMENT_SIZE // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Документ слишком большой. Максимум: {limit_mb} MB")

    try:
        content = await read_upload_limited(file, max_size=MAX_TRANSLATION_DOCUMENT_SIZE)
    except HTTPException as exc:
        limit_mb = MAX_TRANSLATION_DOCUMENT_SIZE // (1024 * 1024)
        exc.detail = f"Документ слишком большой. Максимум: {limit_mb} MB"
        raise

    text = _extract_translation_document_text(filename, content)
    if not text:
        raise HTTPException(status_code=400, detail="Не удалось извлечь текст из документа")

    return ExtractedTranslationDocument(
        text=text,
        filename=filename,
        extension=extension,
        title_seed=Path(filename).stem[:160] or "Перевод документа",
        content=content,
    )


def _safe_download_stem(filename: str) -> str:
    """Готовит компактное имя скачиваемого файла без путей и управляющих символов."""
    stem = Path(filename or "document").stem or "document"
    stem = re.sub(r"[^\wА-Яа-яЁё.-]+", "_", stem, flags=re.UNICODE).strip("._-")
    return stem[:120] or "document"


def _write_translation_artifact(request_id: str, filename: str, content: bytes) -> str:
    """Сохраняет бинарный артефакт перевода в рабочее хранилище и возвращает имя файла."""
    safe_filename = Path(filename).name
    if not safe_filename or safe_filename in FORBIDDEN_FILENAMES:
        raise RuntimeError("Недопустимое имя файла артефакта перевода")
    output_dir = os.path.join(STORAGE_DIR, "translations", request_id)
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, safe_filename)
    with open(output_path, "wb") as target:
        target.write(content)
    return safe_filename


def _run_document_translation(
    request_id: str,
    user_id: str,
    document: ExtractedTranslationDocument,
    target_language: str,
    translation_mode: str,
    seed: ProjectSeed,
) -> None:
    """Переводит загруженный документ; для DOCX дополнительно собирает DOCX-артефакт."""

    def progress_callback(phase: str) -> None:
        set_translation_phase(request_id, phase)

    llm_client = create_llm_client(
        provider=seed.llm_provider,
        default_role="translator",
        enable_cache=True,
        enable_batching=True,
        user_id=user_id,
        run_id=request_id,
    )
    try:
        result_links: dict[str, str] | None = None
        # Формат, у которого есть обработчик, собирается обратно в себя же: вёрстка сохраняется
        # по построению. Простой текст остаётся за переводчиком-агентом — у него разбивка на
        # части, доработка и режим «combined», которыми пользуются методисты.
        handler = (
            None if document.extension in TEXT_DOCUMENT_EXTENSIONS else handler_for(document.extension)
        )
        if handler is not None:
            units = handler.extract(document.content)
            if target_language == SOURCE_LANGUAGE:
                # Исходники в фабрике всегда на русском, поэтому «перевести на русский» — это
                # холостой ход: текст возвращается как был, но файл всё равно пересобирается и
                # методист получает его в привычном виде. Не дефект, а принятый порядок.
                translations = {unit.unit_id: unit.text for unit in units}
            else:
                translations = translate_units(
                    cast(LLMClientProtocol, llm_client),
                    units,
                    target_language,
                    progress_callback=progress_callback,
                )
            translated_md = _normalize_extracted_text(
                "\n".join(translations.get(unit.unit_id, unit.text) for unit in units)
            )
            progress_callback("build_docx")
            kind = document.extension.lstrip(".")
            translated_bytes = handler.apply(document.content, translations)
            artifact_name = f"{_safe_download_stem(document.filename)}_{target_language}.{kind}"
            stored_filename = _write_translation_artifact(request_id, artifact_name, translated_bytes)
            result_links = {kind: stored_filename}
        else:
            translator = TranslatorAgent(cast(LLMClientProtocol, llm_client))
            translated_md = translator.translate(
                document.text,
                target_language,
                seed,
                translation_mode=translation_mode,
                progress_callback=progress_callback,
                strict=True,
            )

        set_translation_job(
            request_id=request_id,
            status="completed",
            user_id=user_id,
            phase="build_docx" if handler is not None else ("combine" if translation_mode == "combined" else "translate"),
            original_markdown=document.text,
            translated_markdown=translated_md,
            target_language=target_language,
            job_type="document",
            result_links=result_links,
            source_filename=document.filename,
            source_format=document.extension.lstrip("."),
        )
        upsert_user_run(
            request_id=request_id,
            user_id=user_id,
            kind="translation",
            status="completed",
            title=document.title_seed,
            result_url=f"/api/v1/translate/status/{request_id}",
            metadata={
                "target_language": target_language,
                "translation_mode": translation_mode,
                "source_format": document.extension.lstrip("."),
                "has_docx_artifact": bool(result_links and result_links.get("docx")),
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Ошибка при переводе документа: %s", e, exc_info=True)
        set_translation_job(
            request_id=request_id,
            status="failed",
            user_id=user_id,
            original_markdown=document.text,
            target_language=target_language,
            error=str(e),
            job_type="document",
            source_filename=document.filename,
            source_format=document.extension.lstrip("."),
        )
        upsert_user_run(
            request_id=request_id,
            user_id=user_id,
            kind="translation",
            status="failed",
            title=document.title_seed,
            result_url=f"/api/v1/translate/status/{request_id}",
            metadata={
                "target_language": target_language,
                "translation_mode": translation_mode,
                "source_format": document.extension.lstrip("."),
                "error": str(e),
            },
        )

