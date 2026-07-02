"""Shared subtitle primitives: audio extraction, Whisper transcription and SRT/VTT rendering."""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

# Коды языков для Whisper (ISO 639-1)
WHISPER_LANGUAGE_MAP = {
    "ru": "ru",
    "en": "en",
    "kg": "ky",  # киргизский
    "uz": "uz",
    "tg": "tg",  # таджикский может не быть, fallback на auto
}


def _seg_field(seg, key: str, default=None):
    """
    Безопасно достаёт поле сегмента из OpenAI Whisper ответа.
    Поддерживает как dict, так и объекты TranscriptionSegment (атрибуты).
    """
    if isinstance(seg, dict):
        return seg.get(key, default)
    return getattr(seg, key, default)


def _get_openai_client():
    """Возвращает клиент OpenAI для Whisper (тот же api_key/base_url что и LLM)."""
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    if not api_key:
        raise ValueError("OPENAI_API_KEY не задан")
    return OpenAI(api_key=api_key, base_url=base_url or None)


def _format_timestamp_srt(seconds: float) -> str:
    """Форматирует секунды в SRT-таймкод 00:00:00,000."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_timestamp_vtt(seconds: float) -> str:
    """Форматирует секунды в VTT-таймкод 00:00:00.000."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def build_srt(segments: list[dict]) -> str:
    """Собирает строку SRT из списка сегментов {start, end, text}."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(str(i))
        lines.append(f"{_format_timestamp_srt(start)} --> {_format_timestamp_srt(end)}")
        lines.append(text.replace("\n", " "))
        lines.append("")
    return "\n".join(lines).strip()


def build_vtt(segments: list[dict]) -> str:
    """Собирает строку WebVTT из списка сегментов {start, end, text}."""
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"{_format_timestamp_vtt(start)} --> {_format_timestamp_vtt(end)}")
        lines.append(text.replace("\n", " "))
        lines.append("")
    return "\n".join(lines).strip()


def extract_audio(video_path: str | Path, progress_callback: Callable[[str], None] | None = None) -> str:
    """
    Извлекает аудиодорожку из видео в временный mp3 через ffmpeg.

    Returns:
        Путь к созданному аудиофайлу.
    """
    if progress_callback:
        progress_callback("extract_audio")
    video_path = Path(video_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Видео не найдено: {video_path}")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg не найден. Установите ffmpeg (например: apt install ffmpeg) для обработки видео."
        )
    suffix = ".mp3"
    fd, out_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        cmd = [
            ffmpeg,
            "-y",
            "-i", str(video_path),
            "-vn",
            "-acodec", "libmp3lame",
            "-ac", "1",
            "-q:a", "4",
            "-ar", "16000",
            out_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "")[-1000:]
            raise RuntimeError(f"ffmpeg завершился с ошибкой: {stderr}")
        return out_path
    except Exception:
        if os.path.exists(out_path):
            try:
                os.unlink(out_path)
            except OSError:
                pass
        raise


def transcribe(
    audio_path: str,
    source_language: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Транскрибирует аудио через OpenAI Whisper API.

    Args:
        audio_path: путь к аудиофайлу
        source_language: код языка речи (ru, en, kg, uz, tg) или None для авто
        progress_callback: вызывается с фазой "transcribe"

    Returns:
        Список сегментов [{"start": float, "end": float, "text": str}, ...]
    """
    if progress_callback:
        progress_callback("transcribe")
    client = _get_openai_client()
    lang = None
    if source_language:
        lang = WHISPER_LANGUAGE_MAP.get(source_language.lower(), source_language.lower())
        if lang == "tg":
            lang = None
    with open(audio_path, "rb") as f:
        create_kwargs = {
            "model": "whisper-1",
            "file": f,
            "response_format": "verbose_json",
        }
        if lang:
            create_kwargs["language"] = lang
        response = client.audio.transcriptions.create(**create_kwargs)
    segments: list[dict] = []
    for seg in getattr(response, "segments", []):
        start = float(_seg_field(seg, "start", 0.0) or 0.0)
        end = float(_seg_field(seg, "end", 0.0) or 0.0)
        text_val = _seg_field(seg, "text", "") or ""
        text = str(text_val).strip()
        segments.append({"start": start, "end": end, "text": text})
    if not segments and getattr(response, "text", None):
        segments = [{"start": 0.0, "end": 0.0, "text": str(getattr(response, "text", "") or "").strip()}]
    return segments
