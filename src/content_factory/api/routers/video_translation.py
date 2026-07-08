"""Video (burned-subtitle) translation service.

Extracted from ``api/routers/readme_translate.py``: saves the uploaded video to a
temp file and runs the ASR -> translate -> burn-subtitles pipeline as a background
job, bounded by a concurrency semaphore. The ``/translate/video`` route re-imports
the entry points from here.
"""

import os
import tempfile
import threading
import time

from fastapi import HTTPException, UploadFile

from content_factory.api.db.user_runs_db import upsert_user_run
from content_factory.api.routers.document_translation import STORAGE_DIR
from content_factory.api.utils.file_validation import MAX_VIDEO_SIZE
from content_factory.api.utils.logger import get_logger
from content_factory.api.utils.result_cache import set_translation_job, set_translation_phase
from content_factory.generation.subtitles.burned_pipeline import run_burned_subs_pipeline
from content_factory.platform.llm.factory import create_llm_client

logger = get_logger("translate-video")

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
VIDEO_MAX_CONCURRENT_JOBS = int(os.getenv("VIDEO_MAX_CONCURRENT_JOBS", "1"))
_video_jobs_semaphore = threading.Semaphore(max(1, VIDEO_MAX_CONCURRENT_JOBS))


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

