"""Document translation service — extraction, DOCX rebuild, and the job runner.

Extracted from ``api/routers/readme_translate.py``: plain/HTML/DOCX/PDF text
extraction, DOCX re-assembly with translated runs, batched DOCX unit translation,
and the ``_run_document_translation`` job runner. The thin ``/translate/document``
route in ``readme_translate.py`` re-imports the entry points from here.
"""

import json
import os
import re
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

from fastapi import HTTPException, UploadFile

from content_factory.api.db.user_runs_db import upsert_user_run
from content_factory.api.utils.file_validation import FORBIDDEN_FILENAMES, read_upload_limited
from content_factory.api.utils.logger import get_logger
from content_factory.api.utils.result_cache import set_translation_job, set_translation_phase
from content_factory.generation.agents.base.llm_client import LLMClientProtocol
from content_factory.generation.agents.translator import TranslatorAgent
from content_factory.generation.models.schemas import ProjectSeed
from content_factory.generation.utils.translation_languages import get_translation_language_profile
from content_factory.platform.llm.factory import create_llm_client

logger = get_logger("translate-document")

STORAGE_DIR = os.getenv("STORAGE_DIR", os.path.join(tempfile.gettempdir(), "content_generator_translations"))
MAX_TRANSLATION_DOCUMENT_SIZE = int(os.getenv("MAX_TRANSLATION_DOCUMENT_SIZE_BYTES", 25 * 1024 * 1024))
TRANSLATION_DOCUMENT_EXTENSIONS = {".md", ".markdown", ".txt", ".html", ".htm", ".docx", ".pdf"}
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
    for _index, weight in enumerate(weights[:-1]):
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
        if document.extension == ".docx":
            units = _iter_docx_text_units(document.content)
            if target_language == "ru":
                translations = {unit.unit_id: unit.text for unit in units}
            else:
                translations = _translate_docx_units(
                    cast(LLMClientProtocol, llm_client),
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

