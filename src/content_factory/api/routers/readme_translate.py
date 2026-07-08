"""Endpoints для перевода произвольных документов и видео (субтитры).

Модуль реализует сервисы «Перевод документа» и «Перевод субтитров по видео».
POST /translate/readme, POST /translate/document или POST /translate/video возвращают request_id;
клиент опрашивает GET /translate/status/{request_id} до status=completed или failed.
"""

import asyncio
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from content_factory.api.db.logging_db import write_log_async
from content_factory.api.db.user_runs_db import upsert_user_run
from content_factory.api.dependencies import get_current_user
from content_factory.api.routers.document_translation import (
    STORAGE_DIR,
    _read_uploaded_translation_document,
    _run_document_translation,
)
from content_factory.api.utils.file_validation import (
    MAX_VIDEO_SIZE,
    validate_video_file,
)
from content_factory.api.utils.logger import get_logger
from content_factory.api.utils.logging_context import set_request_id, set_user_id
from content_factory.api.utils.result_cache import (
    get_translation_job,
    get_translation_job_owner,
    set_translation_job,
    set_translation_phase,
)
from content_factory.generation.agents.base.llm_client import LLMClientProtocol
from content_factory.generation.agents.translator import TranslatorAgent
from content_factory.generation.models.schemas import ProjectSeed
from content_factory.generation.subtitles.burned_pipeline import run_burned_subs_pipeline
from content_factory.platform.llm.factory import create_llm_client

logger = get_logger("readme-translate")
router = APIRouter()

SUPPORTED_LANGUAGES = {"ru", "en", "kg", "uz", "tg"}
MARKDOWN_DOCUMENT_EXTENSIONS = {".md", ".markdown"}





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
    translator = TranslatorAgent(cast(LLMClientProtocol, llm_client))
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
        ) from e

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
        ) from e

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
        raise HTTPException(status_code=500, detail=f"Не удалось сохранить видео: {e}") from e

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


async def _stream_download(request_id: str, file_type: str, job: dict[str, Any]) -> Response:
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
) -> Response:
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

