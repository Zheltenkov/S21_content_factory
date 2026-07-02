"""Endpoints для перевода произвольных документов и видео (субтитры).

Модуль реализует сервисы «Перевод документа» и «Перевод субтитров по видео».
POST /translate/readme, POST /translate/document или POST /translate/video возвращают request_id;
клиент опрашивает GET /translate/status/{request_id} до status=completed или failed.
"""

import asyncio
import json
import re
import os
import tempfile
import time
import uuid
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from content_factory.api.db.logging_db import write_log_async
from content_factory.api.db.user_runs_db import upsert_user_run
from content_factory.api.dependencies import get_current_user
from content_factory.api.utils.file_validation import FORBIDDEN_FILENAMES, MAX_VIDEO_SIZE, read_upload_limited, validate_video_file
from content_factory.api.utils.logger import get_logger
from content_factory.api.utils.logging_context import set_request_id, set_user_id
from content_factory.api.utils.result_cache import (
    get_translation_job,
    get_translation_job_owner,
    set_translation_job,
    set_translation_phase,
)
from content_factory.generation.agents.translator import TranslatorAgent
from content_factory.platform.llm.factory import create_llm_client
from content_factory.generation.agents.base.llm_client import LLMClientProtocol
from content_factory.generation.models.schemas import ProjectSeed
from content_factory.generation.subtitles.burned_pipeline import run_burned_subs_pipeline
from content_factory.generation.utils.translation_languages import get_translation_language_profile

logger = get_logger("readme-translate")
router = APIRouter()

SUPPORTED_LANGUAGES = {"ru", "en", "kg", "uz", "tg"}
STORAGE_DIR = os.getenv("STORAGE_DIR", os.path.join(tempfile.gettempdir(), "content_generator_translations"))
MAX_TRANSLATION_DOCUMENT_SIZE = int(os.getenv("MAX_TRANSLATION_DOCUMENT_SIZE_BYTES", 25 * 1024 * 1024))
TRANSLATION_DOCUMENT_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf"}
MARKDOWN_DOCUMENT_EXTENSIONS = {".md", ".markdown"}
TEXT_DOCUMENT_EXTENSIONS = {".md", ".markdown", ".txt"}
HTML_DOCUMENT_EXTENSIONS = {".html", ".htm"}
DOCX_WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
DOCX_TRANSLATABLE_XML_RE = re.compile(
    r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$",
)
MAX_DOCX_TRANSLATION_BATCH_CHARS = int(os.getenv("MAX_DOCX_TRANSLATION_BATCH_CHARS", "9000"))

ElementTree.register_namespace("w", DOCX_WORD_NAMESPACE)
ElementTree.register_namespace("xml", XML_NAMESPACE)


def _strip_invalid_xml_chars(text: str) -> str:
    """Удаляет символы, которые нельзя безопасно записать в XML 1.0.

    DOCX хранит текст в XML-файлах. Некоторые ответы модели могут содержать
    невидимые управляющие символы, которые ElementTree сериализует, но Word
    потом считает документ поврежденным.
    """
    if not text:
        return text
    return "".join(
        char
        for char in text
        if (
            char in "\t\n\r"
            or "\x20" <= char <= "\ud7ff"
            or "\ue000" <= char <= "\ufffd"
            or "\U00010000" <= char <= "\U0010ffff"
        )
    )


STAGE_PROGRESS = {
    "queued": 0,
    "extract_audio": 10,
    "chunk_audio": 15,
    "transcribe": 35,
    "correct_asr": 45,
    "translate": 60,
    "build_subtitles": 75,
    "render_video": 90,
    "done": 100,
}

# Ограничиваем количество одновременно обрабатываемых видео-задач, чтобы
# избежать конкурирующей загрузки ASR/ffmpeg и OOM на маленьких серверах.
VIDEO_MAX_CONCURRENT_JOBS = int(os.getenv("VIDEO_MAX_CONCURRENT_JOBS", "1"))
_video_jobs_semaphore = threading.Semaphore(max(1, VIDEO_MAX_CONCURRENT_JOBS))


def _markdown_title(markdown: str, fallback: str = "Перевод документа") -> str:
    """Extract a compact dashboard title from the first Markdown H1."""
    for line in (markdown or "").splitlines():
        clean = line.strip()
        if clean.startswith("# "):
            return clean.lstrip("#").strip()[:160] or fallback
    return fallback


@dataclass(frozen=True)
class ExtractedTranslationDocument:
    """Текстовый контракт документа после безопасного извлечения содержимого."""

    text: str
    filename: str
    extension: str
    title_seed: str
    content: bytes


@dataclass(frozen=True)
class DocxTextUnit:
    """Один переводимый текстовый блок внутри DOCX."""

    unit_id: str
    xml_path: str
    paragraph_index: int
    text: str


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
        from bs4 import BeautifulSoup  # type: ignore

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
            root = ElementTree.fromstring(archive.read(path))
        except KeyError:
            continue
        except ElementTree.ParseError as exc:
            raise HTTPException(status_code=400, detail="Не удалось разобрать XML внутри DOCX") from exc

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


def _is_docx_translatable_xml(path: str) -> bool:
    """Возвращает True для XML-частей Word, где реально хранится пользовательский текст."""
    return bool(DOCX_TRANSLATABLE_XML_RE.match(path))


def _iter_docx_text_units(content: bytes) -> list[DocxTextUnit]:
    """Собирает переводимые абзацы DOCX без изменения исходного архива."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            xml_paths = [name for name in archive.namelist() if _is_docx_translatable_xml(name)]
            units: list[DocxTextUnit] = []
            unit_index = 1
            for xml_path in xml_paths:
                try:
                    root = ElementTree.fromstring(archive.read(xml_path))
                except ElementTree.ParseError as exc:
                    raise HTTPException(status_code=400, detail=f"Не удалось разобрать {xml_path} внутри DOCX") from exc

                for paragraph_index, paragraph in enumerate(
                    root.findall(f".//{{{DOCX_WORD_NAMESPACE}}}p")
                ):
                    text_nodes = [
                        node
                        for node in paragraph.iter()
                        if node.tag == f"{{{DOCX_WORD_NAMESPACE}}}t"
                    ]
                    text = "".join(node.text or "" for node in text_nodes)
                    if not text.strip():
                        continue
                    units.append(
                        DocxTextUnit(
                            unit_id=f"{unit_index:04d}",
                            xml_path=xml_path,
                            paragraph_index=paragraph_index,
                            text=text,
                        )
                    )
                    unit_index += 1
            return units
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="DOCX-файл поврежден или имеет неверный формат") from exc


def _extract_json_object(raw_text: str) -> dict:
    """Достаёт JSON-объект из ответа модели, даже если вокруг появились служебные фразы."""
    raw = (raw_text or "").strip()
    if not raw:
        raise ValueError("Пустой ответ модели")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("Ответ модели не является JSON-объектом")
    return parsed


def _docx_translation_batches(units: list[DocxTextUnit]) -> list[list[DocxTextUnit]]:
    """Группирует абзацы DOCX так, чтобы один запрос к LLM оставался управляемым."""
    batches: list[list[DocxTextUnit]] = []
    current: list[DocxTextUnit] = []
    current_chars = 0
    for unit in units:
        unit_cost = len(unit.text) + 80
        if current and current_chars + unit_cost > MAX_DOCX_TRANSLATION_BATCH_CHARS:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(unit)
        current_chars += unit_cost
    if current:
        batches.append(current)
    return batches


def _translate_docx_units(
    llm_client: LLMClientProtocol,
    units: list[DocxTextUnit],
    target_language: str,
    progress_callback: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Переводит DOCX блоками через строгий JSON-контракт id -> translated_text."""
    if not units:
        return {}

    target_profile = get_translation_language_profile(target_language)
    system_prompt = (
        "Ты переводишь текстовые фрагменты DOCX-документа. "
        "Нужно вернуть только JSON без Markdown и пояснений. "
        "Сохраняй смысл, числа, имена файлов, пути, технические термины, формулы и сокращения. "
        f"Язык перевода: {target_profile.prompt_label}. "
        f"Письменность: {target_profile.script_instruction}."
    )
    translations: dict[str, str] = {}
    batches = _docx_translation_batches(units)

    for batch_index, batch in enumerate(batches, 1):
        if progress_callback:
            progress_callback("translate")
        payload = {
            "target_language": target_profile.prompt_label,
            "script_instruction": target_profile.script_instruction,
            "fragments": [{"id": unit.unit_id, "text": unit.text} for unit in batch],
        }
        user_prompt = (
            "Переведи каждый фрагмент отдельно и верни JSON строго такого вида:\n"
            '{"translations":{"0001":"перевод фрагмента"}}\n'
            "Не объединяй фрагменты, не меняй id, не добавляй новые ключи.\n\n"
            f"Входные данные:\n{json.dumps(payload, ensure_ascii=False)}"
        )

        raw_response = ""
        try:
            raw_response = llm_client.complete(
                system=system_prompt,
                user=user_prompt,
                response_format="json_object",
                temperature=0.1,
            )
        except Exception:
            raw_response = llm_client.complete(
                system=system_prompt,
                user=user_prompt,
                temperature=0.1,
            )

        try:
            parsed = _extract_json_object(raw_response)
        except Exception:
            raw_response = llm_client.complete(
                system=system_prompt,
                user=user_prompt,
                temperature=0.1,
            )
            parsed = _extract_json_object(raw_response)
        batch_translations = parsed.get("translations")
        if not isinstance(batch_translations, dict):
            raise RuntimeError("Модель вернула DOCX-перевод без объекта translations")

        missing_ids: list[str] = []
        for unit in batch:
            translated = batch_translations.get(unit.unit_id)
            if translated is None:
                missing_ids.append(unit.unit_id)
                continue
            translations[unit.unit_id] = str(translated).strip()
        if missing_ids:
            raise RuntimeError(
                "Модель не вернула перевод для DOCX-фрагментов: " + ", ".join(missing_ids[:10])
            )

        logger.info(
            "DOCX translation batch done: batch=%s/%s units=%s",
            batch_index,
            len(batches),
            len(batch),
        )

    return translations


def _nearest_space_cut(text: str, target: int, start: int) -> int:
    """Выбирает границу чанка около пробела, чтобы не резать слова при распределении по run."""
    if target <= start:
        return start
    if target >= len(text):
        return len(text)
    window = max(20, min(80, len(text) // 8))
    left = max(start, target - window)
    right = min(len(text), target + window)
    candidates = [m.end() for m in re.finditer(r"\s+", text[left:right])]
    if not candidates:
        return target
    absolute = [left + candidate for candidate in candidates]
    return min(absolute, key=lambda cut: abs(cut - target))


def _split_text_for_docx_runs(translated_text: str, original_parts: list[str]) -> list[str]:
    """Распределяет перевод по исходным run примерно пропорционально, сохраняя inline-стили."""
    if not original_parts:
        return []
    if len(original_parts) == 1:
        return [translated_text]

    weights = [max(len(part or ""), 1) for part in original_parts]
    total_weight = sum(weights) or len(original_parts)
    chunks: list[str] = []
    cursor = 0
    cumulative_weight = 0
    for index, weight in enumerate(weights[:-1]):
        cumulative_weight += weight
        target = round(len(translated_text) * cumulative_weight / total_weight)
        cut = _nearest_space_cut(translated_text, target, cursor)
        chunks.append(translated_text[cursor:cut])
        cursor = cut
    chunks.append(translated_text[cursor:])
    return chunks


def _apply_text_to_docx_paragraph(paragraph: ElementTree.Element, translated_text: str) -> None:
    """Заменяет текст абзаца, не трогая стили, таблицы, списки и другие OOXML-узлы."""
    translated_text = _strip_invalid_xml_chars(translated_text)
    text_nodes = [
        node
        for node in paragraph.iter()
        if node.tag == f"{{{DOCX_WORD_NAMESPACE}}}t"
    ]
    if not text_nodes:
        return
    original_parts = [node.text or "" for node in text_nodes]
    chunks = _split_text_for_docx_runs(translated_text, original_parts)
    for node, chunk in zip(text_nodes, chunks, strict=False):
        node.text = chunk
        if chunk[:1].isspace() or chunk[-1:].isspace():
            node.set(f"{{{XML_NAMESPACE}}}space", "preserve")
    for node in text_nodes[len(chunks):]:
        node.text = ""


def _build_translated_docx(
    content: bytes,
    units: list[DocxTextUnit],
    translations: dict[str, str],
) -> bytes:
    """Создаёт DOCX-копию с переведёнными текстовыми узлами и исходным форматированием."""
    lookup = {
        (unit.xml_path, unit.paragraph_index): translations[unit.unit_id]
        for unit in units
        if unit.unit_id in translations
    }
    input_buffer = BytesIO(content)
    output_buffer = BytesIO()
    try:
        with zipfile.ZipFile(input_buffer, "r") as source_archive:
            with zipfile.ZipFile(output_buffer, "w") as target_archive:
                for info in source_archive.infolist():
                    file_bytes = source_archive.read(info.filename)
                    if not _is_docx_translatable_xml(info.filename):
                        target_archive.writestr(info, file_bytes)
                        continue

                    root = ElementTree.fromstring(file_bytes)
                    for paragraph_index, paragraph in enumerate(
                        root.findall(f".//{{{DOCX_WORD_NAMESPACE}}}p")
                    ):
                        translated = lookup.get((info.filename, paragraph_index))
                        if translated is not None:
                            _apply_text_to_docx_paragraph(paragraph, translated)
                    target_archive.writestr(
                        info,
                        ElementTree.tostring(root, encoding="utf-8", xml_declaration=True),
                    )
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="DOCX-файл поврежден или имеет неверный формат") from exc
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=400, detail="Не удалось разобрать XML внутри DOCX") from exc
    return output_buffer.getvalue()


def _extract_text_from_pdf(content: bytes) -> str:
    """Извлекает текстовый слой PDF через pypdf."""
    try:
        from pypdf import PdfReader  # type: ignore
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


async def _save_uploaded_video_to_temp(file: UploadFile, *, suffix: str) -> str:
    """Сохраняет видео потоково, прерывая чтение сразу после превышения лимита."""
    fd, video_path = tempfile.mkstemp(suffix=suffix)
    total_size = 0
    try:
        with os.fdopen(fd, "wb") as target:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_VIDEO_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Видео слишком большое. Максимум: {MAX_VIDEO_SIZE // (1024 * 1024)} MB",
                    )
                target.write(chunk)
    except Exception:
        if os.path.exists(video_path):
            try:
                os.unlink(video_path)
            except OSError:
                pass
        raise
    return video_path


def _translation_job_for_user(request_id: str, user: dict) -> dict:
    """Возвращает задачу перевода только её владельцу."""
    job = get_translation_job(request_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача перевода не найдена")

    current_user_id = user.get("id")
    owner_id = get_translation_job_owner(request_id)
    if owner_id and current_user_id and owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к задаче перевода другого пользователя")
    if not owner_id:
        raise HTTPException(status_code=403, detail="Владелец задачи перевода не определен")
    return job


class TranslateReadmeRequest(BaseModel):
    """Запрос на перевод произвольного текстового документа."""

    markdown: str
    target_language: str
    llm_provider: Literal["polza", "openrouter", "openai", "deepseek", "gigachat"] | None = None
    translation_mode: str | None = "literal"  # "literal" | "combined"
    thematic_block: str | None = None
    title_seed: str | None = None


class TranslateReadmeStartResponse(BaseModel):
    """Ответ при старте перевода (асинхронный режим)."""

    request_id: str


class TranslateReadmeStatusResponse(BaseModel):
    """Ответ при опросе статуса перевода."""

    request_id: str
    status: str  # pending | in_progress | completed | failed
    phase: str | None = None
    original_markdown: str | None = None
    translated_markdown: str | None = None
    target_language: str | None = None
    error: str | None = None
    job_type: str | None = None
    translated_subtitles: str | None = None
    original_transcript: str | None = None
    progress: float | None = None
    error_code: str | None = None
    result_links: dict[str, str] | None = None
    source_filename: str | None = None
    source_format: str | None = None


def _build_translation_seed(
    *,
    llm_provider: str | None,
    thematic_block: str | None,
    title_seed: str | None,
    project_description: str,
) -> ProjectSeed:
    """Собирает минимальный ProjectSeed для переводческого LLM-контекста."""
    return ProjectSeed(
        language="ru",
        llm_provider=llm_provider,
        project_type="individual",
        thematic_block=thematic_block or "GEN",
        audience_level="base",
        required_tools=[],
        title_seed=title_seed or "",
        project_description=project_description[:1000],
        learning_outcomes=[],
        skills=[],
        tasks_count=None,
        task_complexity=None,
        bonus_wish=None,
        context_track_dir=None,
        last_known_order=None,
        group_size=None,
        repo_base_url=None,
        repo_path_template=None,
        is_programming_project=None,
        target_languages=None,
        zun=None,
    )


def _run_translation(
    request_id: str,
    user_id: str,
    markdown: str,
    target_language: str,
    translation_mode: str,
    seed: ProjectSeed,
) -> None:
    """Синхронный запуск перевода в отдельном потоке; обновляет кэш по завершении."""
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
    translator = TranslatorAgent(llm_client)
    try:
        translated_md = translator.translate(
            markdown,
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
            phase="combine" if translation_mode == "combined" else "translate",
            original_markdown=markdown,
            translated_markdown=translated_md,
            target_language=target_language,
        )
        upsert_user_run(
            request_id=request_id,
            user_id=user_id,
            kind="translation",
            status="completed",
            title=_markdown_title(markdown),
            result_url=f"/api/v1/translate/status/{request_id}",
            metadata={"target_language": target_language, "translation_mode": translation_mode},
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Ошибка при переводе README: %s", e, exc_info=True)
        set_translation_job(
            request_id=request_id,
            status="failed",
            user_id=user_id,
            original_markdown=markdown,
            target_language=target_language,
            error=str(e),
        )
        upsert_user_run(
            request_id=request_id,
            user_id=user_id,
            kind="translation",
            status="failed",
            title=_markdown_title(markdown),
            result_url=f"/api/v1/translate/status/{request_id}",
            metadata={"target_language": target_language, "translation_mode": translation_mode, "error": str(e)},
        )


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
        if document.extension == ".docx":
            units = _iter_docx_text_units(document.content)
            if target_language == "ru":
                translations = {unit.unit_id: unit.text for unit in units}
            else:
                translations = _translate_docx_units(
                    llm_client,
                    units,
                    target_language,
                    progress_callback=progress_callback,
                )
            translated_md = _normalize_extracted_text(
                "\n".join(translations.get(unit.unit_id, unit.text) for unit in units)
            )
            progress_callback("build_docx")
            translated_docx = _build_translated_docx(document.content, units, translations)
            docx_filename = f"{_safe_download_stem(document.filename)}_{target_language}.docx"
            stored_filename = _write_translation_artifact(request_id, docx_filename, translated_docx)
            result_links = {"docx": stored_filename}
        else:
            translator = TranslatorAgent(llm_client)
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
            phase="build_docx" if document.extension == ".docx" else ("combine" if translation_mode == "combined" else "translate"),
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


def _run_burned_video_translation(
    request_id: str,
    user_id: str,
    video_path: str,
    target_language: str,
    output_mode: str,
    subtitle_style: str,
    llm_provider: str | None = None,
) -> None:
    """Запуск пайплайна с транскрипцией RU, переводом по id и опционально рендером видео с субтитрами."""
    output_dir = os.path.join(STORAGE_DIR, "translations", request_id)
    os.makedirs(output_dir, exist_ok=True)

    start_ts = time.monotonic()
    last_phase = "queued"
    last_ts = start_ts

    def progress_callback(phase: str) -> None:
        nonlocal last_phase, last_ts
        now = time.monotonic()
        elapsed = now - start_ts
        delta = now - last_ts
        logger.info(
            "Video translation progress: request_id=%s user_id=%s phase=%s prev_phase=%s elapsed=%.2fs delta=%.2fs",
            request_id,
            user_id,
            phase,
            last_phase,
            elapsed,
            delta,
        )
        last_phase = phase
        last_ts = now
        progress = STAGE_PROGRESS.get(phase)
        set_translation_phase(request_id, phase, progress)

    llm_client = create_llm_client(
        provider=llm_provider,
        default_role="translator",
        enable_cache=True,
        enable_batching=True,
        user_id=user_id,
        run_id=request_id,
    )
    # Ограничиваем количество одновременных тяжёлых задач перевода видео.
    with _video_jobs_semaphore:
        try:
            result = run_burned_subs_pipeline(
                video_path=video_path,
                target_lang=target_language,
                output_mode=output_mode,
                subtitle_style=subtitle_style,
                output_dir=output_dir,
                progress_callback=progress_callback,
                llm_client=llm_client,
            )
            result_links = {}
            if result.get("vtt_path") and os.path.exists(result["vtt_path"]):
                result_links["vtt"] = "subtitles.vtt"
            if result.get("srt_path") and os.path.exists(result["srt_path"]):
                result_links["srt"] = "subtitles.srt"
            if result.get("ass_path") and os.path.exists(result["ass_path"]):
                result_links["ass"] = "subtitles.ass"
            if result.get("transcript_path") and os.path.exists(result["transcript_path"]):
                result_links["transcript"] = "transcript_ru.json"
            if result.get("video_path") and os.path.exists(result["video_path"]):
                result_links["video"] = "output_with_subs.mp4"

            total_elapsed = time.monotonic() - start_ts
            segments_count = len(result.get("segments") or [])
            logger.info(
                "Video translation done: request_id=%s user_id=%s target_language=%s output_mode=%s segments=%d elapsed=%.2fs",
                request_id,
                user_id,
                target_language,
                output_mode,
                segments_count,
                total_elapsed,
            )

            set_translation_job(
                request_id=request_id,
                status="completed",
                user_id=user_id,
                phase="done",
                target_language=target_language,
                job_type="video",
                progress=100.0,
                result_links=result_links,
            )
            upsert_user_run(
                request_id=request_id,
                user_id=user_id,
                kind="video_translation",
                status="completed",
                title="Перевод видео",
                result_url=f"/api/v1/translate/status/{request_id}",
                metadata={
                    "target_language": target_language,
                    "output_mode": output_mode,
                    "segments_count": segments_count,
                },
            )
        except Exception as e:  # noqa: BLE001
            elapsed = time.monotonic() - start_ts
            logger.error(
                "Ошибка пайплайна перевода видео с субтитрами (request_id=%s, user_id=%s, target_language=%s, output_mode=%s, elapsed=%.2fs): %s",
                request_id,
                user_id,
                target_language,
                output_mode,
                elapsed,
                e,
                exc_info=True,
            )
            set_translation_job(
                request_id=request_id,
                status="failed",
                user_id=user_id,
                target_language=target_language,
                job_type="video",
                error=str(e),
                error_code="pipeline_error",
            )
            upsert_user_run(
                request_id=request_id,
                user_id=user_id,
                kind="video_translation",
                status="failed",
                title="Перевод видео",
                result_url=f"/api/v1/translate/status/{request_id}",
                metadata={"target_language": target_language, "output_mode": output_mode, "error": str(e)},
            )
        finally:
            if os.path.exists(video_path):
                try:
                    os.unlink(video_path)
                except OSError:
                    pass


@router.post("/translate/readme", response_model=TranslateReadmeStartResponse)
async def translate_readme_start(
    payload: TranslateReadmeRequest,
    user: dict = Depends(get_current_user),
) -> TranslateReadmeStartResponse:
    """Запускает перевод в фоне и сразу возвращает request_id. Статус опрашивать через GET /translate/status/{request_id}."""
    markdown = (payload.markdown or "").strip()
    if not markdown:
        raise HTTPException(status_code=400, detail="Исходный документ пуст")

    target_language = (payload.target_language or "").lower().strip()
    if target_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый язык перевода: {target_language!r}",
        )

    translation_mode = (payload.translation_mode or "literal").lower().strip()
    if translation_mode not in ("literal", "combined"):
        translation_mode = "literal"

    detected_lang = TranslatorAgent._detect_source_language(markdown)
    if detected_lang and detected_lang == target_language:
        language_names = {"en": "английский", "kg": "киргизский", "uz": "узбекский", "tg": "таджикский"}
        lang_name = language_names.get(target_language, target_language)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Документ уже на целевом языке ({lang_name}). "
                f"Подайте оригинальный документ на русском языке."
            ),
        )

    request_id = str(uuid.uuid4())
    user_id = user.get("id", "anonymous")

    set_request_id(request_id)
    set_user_id(user_id)

    await write_log_async(
        request_id=request_id,
        level="INFO",
        message="Старт перевода README (асинхронный режим)",
        user_id=user_id,
        phase="translate_readme_start",
        metadata={
            "target_language": target_language,
            "llm_provider": payload.llm_provider,
            "translation_mode": translation_mode,
            "markdown_chars": len(markdown),
        },
    )

    try:
        seed = _build_translation_seed(
            llm_provider=payload.llm_provider,
            thematic_block=payload.thematic_block,
            title_seed=payload.title_seed,
            project_description=markdown,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Ошибка валидации ProjectSeed для перевода: %s", e, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=f"Ошибка подготовки контекста для перевода: {e}",
        )

    set_translation_job(
        request_id=request_id,
        status="in_progress",
        user_id=user_id,
        phase="translate",
        original_markdown=markdown,
        target_language=target_language,
    )
    await asyncio.to_thread(
        upsert_user_run,
        request_id=request_id,
        user_id=user_id,
        kind="translation",
        status="in_progress",
        title=payload.title_seed or _markdown_title(markdown),
        result_url=f"/api/v1/translate/status/{request_id}",
        metadata={"target_language": target_language, "translation_mode": translation_mode},
    )

    asyncio.create_task(
        asyncio.to_thread(
            _run_translation,
            request_id,
            user_id,
            markdown,
            target_language,
            translation_mode,
            seed,
        )
    )

    logger.info(
        "🌐 Перевод документа запущен в фоне (request_id=%s, target_language=%s)",
        request_id,
        target_language,
    )
    return TranslateReadmeStartResponse(request_id=request_id)


@router.post("/translate/document", response_model=TranslateReadmeStartResponse)
async def translate_document_start(
    file: UploadFile = File(...),
    target_language: str = Form(...),
    translation_mode: str = Form("literal"),
    llm_provider: Literal["polza", "openrouter", "openai", "deepseek", "gigachat"] | None = Form(None),
    user: dict = Depends(get_current_user),
) -> TranslateReadmeStartResponse:
    """Загружает TXT/Markdown/HTML/DOCX/PDF, извлекает текст и запускает перевод в фоне."""
    document = await _read_uploaded_translation_document(file)

    target_language = (target_language or "").lower().strip()
    if target_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый язык перевода: {target_language!r}",
        )

    translation_mode = (translation_mode or "literal").lower().strip()
    if translation_mode not in ("literal", "combined"):
        translation_mode = "literal"

    detected_lang = TranslatorAgent._detect_source_language(document.text)
    if detected_lang and detected_lang == target_language:
        language_names = {"en": "английский", "kg": "киргизский", "uz": "узбекский", "tg": "таджикский"}
        lang_name = language_names.get(target_language, target_language)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Документ уже на целевом языке ({lang_name}). "
                f"Подайте оригинальный документ на русском языке."
            ),
        )

    try:
        seed = _build_translation_seed(
            llm_provider=llm_provider,
            thematic_block="GEN",
            title_seed=document.title_seed,
            project_description=document.text,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Ошибка валидации ProjectSeed для перевода документа: %s", e, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail=f"Ошибка подготовки контекста для перевода: {e}",
        )

    request_id = str(uuid.uuid4())
    user_id = user.get("id", "anonymous")
    set_request_id(request_id)
    set_user_id(user_id)

    await write_log_async(
        request_id=request_id,
        level="INFO",
        message="Старт перевода документа",
        user_id=user_id,
        phase="translate_document_start",
        metadata={
            "target_language": target_language,
            "llm_provider": llm_provider,
            "translation_mode": translation_mode,
            "source_filename": document.filename,
            "source_format": document.extension.lstrip("."),
            "document_chars": len(document.text),
        },
    )

    set_translation_job(
        request_id=request_id,
        status="in_progress",
        user_id=user_id,
        phase="translate",
        original_markdown=document.text,
        target_language=target_language,
        job_type="document",
        source_filename=document.filename,
        source_format=document.extension.lstrip("."),
    )
    await asyncio.to_thread(
        upsert_user_run,
        request_id=request_id,
        user_id=user_id,
        kind="translation",
        status="in_progress",
        title=document.title_seed,
        result_url=f"/api/v1/translate/status/{request_id}",
        metadata={
            "target_language": target_language,
            "translation_mode": translation_mode,
            "source_format": document.extension.lstrip("."),
        },
    )

    asyncio.create_task(
        asyncio.to_thread(
            _run_document_translation,
            request_id,
            user_id,
            document,
            target_language,
            translation_mode,
            seed,
        )
    )

    logger.info(
        "Document translation started (request_id=%s, target_language=%s, source_format=%s)",
        request_id,
        target_language,
        document.extension,
    )
    return TranslateReadmeStartResponse(request_id=request_id)


@router.post("/translate/video", response_model=TranslateReadmeStartResponse)
async def translate_video_start(
    file: UploadFile = File(...),
    target_language: str = Form(...),
    output_mode: str = Form("burned_video"),  # burned_video | subtitles_only | both
    subtitle_style: str = Form("boxed"),  # boxed | outline
    llm_provider: Literal["polza", "openrouter", "openai", "deepseek", "gigachat"] | None = Form(None),
    user: dict = Depends(get_current_user),
) -> TranslateReadmeStartResponse:
    """Загружает видео, транскрибирует RU (gpt-4o-transcribe), переводит, выдаёт VTT/SRT/ASS и опционально MP4 с вожёнными субтитрами."""
    validate_video_file(file)
    target_language = (target_language or "").lower().strip()
    if target_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый язык перевода: {target_language!r}",
        )
    mode = (output_mode or "burned_video").lower().strip()
    if mode not in ("burned_video", "subtitles_only", "both"):
        mode = "burned_video"
    style = (subtitle_style or "boxed").lower().strip()
    if style not in ("boxed", "outline"):
        style = "boxed"

    suffix = os.path.splitext(file.filename or "")[1] or ".mp4"
    if suffix.lower() not in {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v"}:
        suffix = ".mp4"
    try:
        video_path = await _save_uploaded_video_to_temp(file, suffix=suffix)
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить видео: {e}")

    request_id = str(uuid.uuid4())
    user_id = user.get("id", "anonymous")
    set_request_id(request_id)
    set_user_id(user_id)

    await write_log_async(
        request_id=request_id,
        level="INFO",
        message="Старт перевода видео (транскрипция RU, субтитры/видео)",
        user_id=user_id,
        phase="translate_video_start",
        metadata={
            "target_language": target_language,
            "llm_provider": llm_provider,
            "output_mode": mode,
            "subtitle_style": style,
        },
    )

    set_translation_job(
        request_id=request_id,
        status="in_progress",
        user_id=user_id,
        phase="queued",
        target_language=target_language,
        job_type="video",
        progress=0.0,
    )
    await asyncio.to_thread(
        upsert_user_run,
        request_id=request_id,
        user_id=user_id,
        kind="video_translation",
        status="in_progress",
        title=file.filename or "Перевод видео",
        result_url=f"/api/v1/translate/status/{request_id}",
        metadata={"target_language": target_language, "llm_provider": llm_provider, "output_mode": mode},
    )

    asyncio.create_task(
        asyncio.to_thread(
            _run_burned_video_translation,
            request_id,
            user_id,
            video_path,
            target_language,
            mode,
            style,
            llm_provider,
        )
    )

    logger.info(
        "Video translation started (request_id=%s, target_language=%s, output_mode=%s)",
        request_id,
        target_language,
        mode,
    )
    return TranslateReadmeStartResponse(request_id=request_id)


@router.get("/translate/subtitles/{request_id}")
async def download_translated_subtitles(
    request_id: str,
    user: dict = Depends(get_current_user),
) -> Response:
    """Скачивает файл переведённых субтитров (SRT или VTT) по request_id. Обратная совместимость для старых задач без result_links."""
    job = _translation_job_for_user(request_id, user)
    if job.get("job_type") != "video":
        raise HTTPException(status_code=400, detail="Запрос не является задачей перевода видео")
    result_links = job.get("result_links") or {}
    if result_links:
        ext = "vtt" if "vtt" in result_links else "srt"
        return await _stream_download(request_id, ext, job)
    content = job.get("translated_subtitles")
    if not content:
        raise HTTPException(status_code=404, detail="Субтитры не найдены (задача ещё не завершена или завершилась с ошибкой)")
    ext = job.get("subtitle_format") or "srt"
    if ext not in ("srt", "vtt"):
        ext = "srt"
    media_type = "text/vtt" if ext == "vtt" else "text/plain"
    lang = job.get("target_language") or "ru"
    filename = f"subtitles_{lang}.{ext}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _stream_download(request_id: str, file_type: str, job: dict):
    """Отдаёт файл из STORAGE_DIR/translations/{request_id}/ по type."""
    result_links = job.get("result_links") or {}
    filename = result_links.get(file_type)
    if not filename:
        raise HTTPException(status_code=404, detail=f"Файл типа {file_type!r} недоступен для этой задачи")
    dir_path = os.path.join(STORAGE_DIR, "translations", request_id)
    file_path = os.path.join(dir_path, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден или удалён")
    media_map = {
        "video": "video/mp4",
        "vtt": "text/vtt",
        "srt": "text/plain",
        "ass": "text/x-ssa",
        "transcript": "application/json",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return FileResponse(
        path=file_path,
        media_type=media_map.get(file_type, "application/octet-stream"),
        filename=filename,
    )


@router.get("/translate/download/{request_id}")
async def download_translation_artifact(
    request_id: str,
    type: str = Query(..., alias="type"),  # video | vtt | srt | ass | transcript | docx
    user: dict = Depends(get_current_user),
):
    """Скачивает артефакт перевода: видео-файлы или DOCX для переведённого документа."""
    job = _translation_job_for_user(request_id, user)
    kind = (type or "").lower().strip()
    if job.get("job_type") == "video":
        if kind not in ("video", "vtt", "srt", "ass", "transcript"):
            raise HTTPException(status_code=400, detail="type должен быть: video, vtt, srt, ass, transcript")
    elif job.get("job_type") == "document":
        if kind != "docx":
            raise HTTPException(status_code=400, detail="Для документа доступен только type=docx")
    else:
        raise HTTPException(status_code=400, detail="Для этой задачи нет файлов для скачивания")
    return await _stream_download(request_id, kind, job)


@router.get("/translate/status/{request_id}", response_model=TranslateReadmeStatusResponse)
async def translate_readme_status(
    request_id: str,
    user: dict = Depends(get_current_user),
) -> TranslateReadmeStatusResponse:
    """Возвращает текущий статус и результат перевода (при status=completed). stage=phase, progress, error_code, result_links для видео."""
    job = _translation_job_for_user(request_id, user)
    return TranslateReadmeStatusResponse(
        request_id=request_id,
        status=job.get("status", "pending"),
        phase=job.get("phase"),
        original_markdown=job.get("original_markdown"),
        translated_markdown=job.get("translated_markdown"),
        target_language=job.get("target_language"),
        error=job.get("error"),
        job_type=job.get("job_type"),
        translated_subtitles=job.get("translated_subtitles"),
        original_transcript=job.get("original_transcript"),
        progress=job.get("progress"),
        error_code=job.get("error_code"),
        result_links=job.get("result_links"),
        source_filename=job.get("source_filename"),
        source_format=job.get("source_format"),
    )

