"""
Пайплайн: видео -> аудио -> транскрипция RU (Whisper medium) -> пост-коррекция ASR -> перевод по id ->
сборка VTT/SRT/ASS -> рендер MP4 с вжёженными субтитрами.

Исходный язык всегда RU. Сегменты переводятся строго 1:1 по id с валидацией и retry/fallback.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
import logging

from content_factory.platform.llm.model_registry import resolve_configured_provider
from content_factory.generation.subtitles.pipeline import build_srt, build_vtt, extract_audio
from content_factory.generation.subtitles.pipeline import transcribe as openai_whisper_transcribe
from content_factory.generation.utils.translation_languages import get_translation_language_profile

WHISPER_MODEL = os.getenv("WHISPER_ASR_MODEL", "large-v3-turbo")
TRANSLATE_BATCH_SIZE = int(os.getenv("TRANSLATE_BATCH_SIZE", "60"))
SUBTITLE_CONTEXT_WINDOW = max(0, int(os.getenv("SUBTITLE_CONTEXT_WINDOW", "1")))
TRANSLATE_BATCH_MIN_RETRY = 5
DEBUG_SUBTITLES_LOG = os.getenv("SUBTITLES_DEBUG_LOG", "0") in {"1", "true", "True"}
VIDEO_ENCODER = os.getenv("VIDEO_ENCODER", "libx264")
logger = logging.getLogger(__name__)
CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")

_whisper_model_cache = None


def _resolve_subtitle_translate_model() -> str | None:
    """Resolve optional subtitle translation model without leaking OpenAI defaults to other providers."""
    provider = resolve_configured_provider()
    if provider == "polza":
        provider_override = (
            os.getenv("POLZA_AI_TRANSLATE_SUBTITLES_MODEL", "").strip()
            or os.getenv("POLZA_TRANSLATE_SUBTITLES_MODEL", "").strip()
            or os.getenv("OPEN_ROUTER_TRANSLATE_SUBTITLES_MODEL", "").strip()
            or os.getenv("OPENROUTER_TRANSLATE_SUBTITLES_MODEL", "").strip()
        )
    else:
        provider_override = os.getenv(f"{provider.upper()}_TRANSLATE_SUBTITLES_MODEL", "").strip()
    if provider_override:
        return provider_override
    generic_override = os.getenv("TRANSLATE_SUBTITLES_MODEL", "").strip()
    return generic_override if provider == "openai" and generic_override else None


def _format_ts_ass(seconds: float) -> str:
    """ASS time: H:mm:ss.cc (centiseconds)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = int((s % 1) * 100)
    sec = int(s)
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"

def get_audio_duration_seconds(audio_path: str) -> float:
    """Возвращает длительность аудио в секундах через ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe не найден")
    cmd = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", audio_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe error: {r.stderr}")
    return float(r.stdout.strip() or 0)


def chunk_audio(
    audio_path: str,
    progress_callback: Callable[[str], None] | None = None,
) -> list[tuple[str, float]]:
    """
    Если аудио > 25 MB, нарезает на чанки с перекрытием 1 сек.
    Возвращает список (путь_к_чанку, смещение_в_секундах_в_глобальной_шкале).
    """
    if progress_callback:
        progress_callback("chunk_audio")
    size = os.path.getsize(audio_path)
    if size <= TRANSCRIBE_MAX_AUDIO_BYTES:
        return [(audio_path, 0.0)]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден")
    duration = get_audio_duration_seconds(audio_path)
    if duration <= 0:
        return [(audio_path, 0.0)]
    bytes_per_sec = size / duration
    chunk_duration_sec = (TRANSCRIBE_MAX_AUDIO_BYTES * 0.9) / bytes_per_sec if bytes_per_sec > 0 else 600
    chunk_duration_sec = max(60, min(chunk_duration_sec, duration))
    step = max(1.0, chunk_duration_sec - OVERLAP_SECONDS)
    chunks: list[tuple[str, float]] = []
    start = 0.0
    idx = 0
    try:
        while start < duration:
            fd, out_chunk = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            cmd = [
                ffmpeg, "-y", "-i", audio_path,
                "-ss", str(start), "-t", str(chunk_duration_sec + OVERLAP_SECONDS),
                "-acodec", "copy", out_chunk,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(out_chunk):
                if os.path.exists(out_chunk):
                    try:
                        os.unlink(out_chunk)
                    except OSError:
                        pass
                break
            chunks.append((out_chunk, start))
            start += step
            idx += 1
        if not chunks:
            chunks = [(audio_path, 0.0)]
        return chunks
    except Exception:
        for c, _ in chunks:
            if os.path.exists(c):
                try:
                    os.unlink(c)
                except OSError:
                    pass
        raise


def transcribe_ru_whisper(
    audio_path: str,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Транскрипция аудио через OpenAI Whisper API (whisper-1), язык ru.
    Возвращает сегменты с полями id, start, end, text.
    """
    if progress_callback:
        progress_callback("transcribe")
    # Используем общий OpenAI Whisper пайплайн, чтобы не держать большую
    # локальную модель в памяти и снизить риск OOM.
    segments = openai_whisper_transcribe(
        audio_path,
        source_language="ru",
        progress_callback=None,
    )
    if not segments:
        duration = get_audio_duration_seconds(audio_path)
        segments = [
            {
                "start": 0.0,
                "end": max(0.1, duration),
                "text": "",
            }
        ]
    if DEBUG_SUBTITLES_LOG and segments:
        logger.info(
            "ASR segments (whisper-1, count=%d): %s",
            len(segments),
            json.dumps(segments[:15], ensure_ascii=False),
        )
    merged = _deduplicate_segments(segments)
    # Сдвигаем только первый сегмент на 4 секунды от начала видео,
    # остальные оставляем как есть, корректно смещая и конец.
    if merged:
        first = merged[0]
        first_start = float(first.get("start", 0.0))
        delay = 4.0 - first_start
        if delay > 0:
            first["start"] = first_start + delay
            first["end"] = float(first.get("end", first_start))
    for i, m in enumerate(merged, 1):
        m["id"] = i
    return merged


def correct_asr_segments(
    segments: list[dict],
    llm_client,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Прогоняет распознанный русский текст через агента: исправление ошибок ASR (омонимы, пропуски, пунктуация).
    Сохраняет структуру: id, start, end; меняется только text.
    """
    if progress_callback:
        progress_callback("correct_asr")
    if not segments:
        return segments
    system = (
        "Ты агент пост-коррекции распознанной речи (ASR). Исправляй типичные ошибки: омонимы, пропущенные слова, "
        "пунктуацию, слитные/раздельные написания. Язык — русский. Не меняй смысл и стиль. "
        "Верни валидный JSON: массив объектов с полями \"id\" (number), \"start\" (number), \"end\" (number), \"text\" (string). "
        "Количество элементов и id должны совпадать с входом. start/end — в секундах, не меняй."
    )
    batch_size = 25
    result = [None] * len(segments)
    for i in range(0, len(segments), batch_size):
        batch = segments[i : i + batch_size]
        payload = [{"id": s["id"], "start": s["start"], "end": s["end"], "text": s.get("text", "")} for s in batch]
        user = "Исправь ошибки распознавания в сегментах. Верни только JSON-массив:\n" + json.dumps(payload, ensure_ascii=False)
        try:
            raw = llm_client.complete(system=system, user=user, response_format="json_object", temperature=0)
            data = json.loads(raw)
            if not isinstance(data, list):
                data = data.get("items", data.get("segments", [data]))
            by_id = {int(item["id"]): item for item in data if "id" in item}
            for j, seg in enumerate(batch):
                idx = i + j
                if result[idx] is not None:
                    continue
                sid = seg.get("id", idx + 1)
                if sid in by_id:
                    result[idx] = {
                        **seg,
                        "text": str(by_id[sid].get("text", "")).strip(),
                    }
                else:
                    result[idx] = dict(seg)
        except Exception:
            for j, seg in enumerate(batch):
                idx = i + j
                if result[idx] is None:
                    result[idx] = dict(seg)
    for idx in range(len(segments)):
        if result[idx] is None:
            result[idx] = dict(segments[idx])
    return result


def _deduplicate_segments(segments: list[dict]) -> list[dict]:
    """Убирает дубликаты по перекрытию времени и схожести текста."""
    if not segments:
        return []
    out = [segments[0]]
    for s in segments[1:]:
        prev = out[-1]
        if s["start"] < prev["end"] - 0.3 and (s["text"] == prev["text"] or not s["text"].strip()):
            continue
        if s["text"].strip():
            out.append(s)
    return out


def _build_context_window_payload(
    segments: list[dict],
    indices: list[int],
    window_size: int,
) -> list[dict]:
    """
    Формирует payload для перевода с контекстным окном.
    Переводится только text_ru, а context_before/context_after даются для понимания смысла.
    """
    payload: list[dict] = []
    for idx in indices:
        seg = segments[idx]
        item = {
            "id": seg.get("id", idx + 1),
            "text_ru": (seg.get("text") or "").strip(),
            "context_before": [],
            "context_after": [],
        }
        for offset in range(window_size, 0, -1):
            prev_idx = idx - offset
            if prev_idx >= 0:
                prev_text = (segments[prev_idx].get("text") or "").strip()
                if prev_text:
                    item["context_before"].append(prev_text)
        for offset in range(1, window_size + 1):
            next_idx = idx + offset
            if next_idx < len(segments):
                next_text = (segments[next_idx].get("text") or "").strip()
                if next_text:
                    item["context_after"].append(next_text)
        payload.append(item)
    return payload


def translate_segments_llm(
    segments: list[dict],
    target_lang: str,
    llm_client,
    progress_callback: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Переводит сегменты на target_lang. Вход/выход с полем id. Строгий JSON, валидация, retry, fallback по одному.
    segments: [{"id", "start", "end", "text"}] (text = text_ru). Возвращает те же сегменты с полем "text" = переведённый текст.
    """
    if progress_callback:
        progress_callback("translate")
    if target_lang == "ru":
        return [{"id": s.get("id", i), "start": s["start"], "end": s["end"], "text": (s.get("text") or "").strip()} for i, s in enumerate(segments, 1)]
    profile = get_translation_language_profile(target_lang)
    target_lang_name = profile.prompt_label
    style_hints = {
        "en": "Пиши естественным современным английским, простыми и ясными предложениями, без кальки и тяжеловесных конструкций.",
        "kg": "Пиши естественно и понятно для носителя языка, простыми фразами, избегай кальки с русского.",
        "uz": "Пиши естественно и понятно для носителя языка, простыми фразами, избегай кальки с русского и излишне сложных конструкций.",
        "tg": "Пиши естественно и понятно для носителя языка, простыми фразами, избегай кальки с русского.",
        "ru": "Пиши естественно и ясно, без избыточных канцеляризмов.",
    }
    extra_hint = " ".join(
        part for part in (profile.script_instruction, style_hints.get(target_lang, "")) if part
    )
    system = (
        "Ты профессиональный переводчик субтитров. "
        "Перевод должен быть точным по смыслу, естественным для носителя языка, простым и удобным для чтения. "
        "Нельзя добавлять новый смысл, опускать важный смысл или делать вольный пересказ. "
        "Избегай кальки с русского и неестественного порядка слов. "
        f"Письменность: {profile.script_instruction}. "
        "Сохраняй структуру: НЕЛЬЗЯ менять количество элементов, объединять или разбивать сегменты, менять id. "
        f"Переводишь с русского на {target_lang_name}. {extra_hint} "
        "Ответ ДОЛЖЕН быть строго JSON-массивом длины N, где N = количеству сегментов во входе. "
        "Каждый элемент массива — объект вида {\"id\": <number>, \"text\": <string>}. "
        "Поле \"id\" в ответе должно совпадать с id соответствующего сегмента во входе. "
        "Переводи только поле text_ru, поля context_before/context_after используй только как контекст. "
        "Никаких дополнительных полей, заголовков или комментариев."
    )
    result = [None] * len(segments)
    batch_size = TRANSLATE_BATCH_SIZE
    while batch_size >= 1:
        indices: list[int] = []
        for i, seg in enumerate(segments):
            if result[i] is not None:
                continue
            indices.append(i)
            if len(indices) >= batch_size:
                break
        if not indices:
            break
        batch = _build_context_window_payload(
            segments=segments,
            indices=indices,
            window_size=SUBTITLE_CONTEXT_WINDOW,
        )
        batch_json = json.dumps(batch, ensure_ascii=False)
        if DEBUG_SUBTITLES_LOG:
            logger.info(
                "Translate batch (size=%d, target_lang=%s, context_window=%d): %s",
                len(batch),
                target_lang,
                SUBTITLE_CONTEXT_WINDOW,
                batch_json,
            )
        user = (
            f"Переведи субтитры на {target_lang_name}. "
            f"Соблюдай письменность целевого языка: {profile.script_instruction}. "
            "Верни ТОЛЬКО JSON-массив длины N, где N = количеству сегментов во входе. "
            "Каждый элемент массива: {\"id\": <number>, \"text\": \"...\"}. "
            "Количество элементов и id должны в точности совпадать с входными сегментами. "
            "Переводи только поле text_ru; context_before/context_after не переводи, используй только для понимания смысла.\n\n"
            "Входные сегменты с контекстом:\n" + batch_json
        )
        # Грубая оценка длины промпта в токенах (~4 символа на токен)
        approx_prompt_tokens = max(1, (len(system) + len(user)) // 4)
        if approx_prompt_tokens > 12000 and DEBUG_SUBTITLES_LOG:
            logger.warning(
                "Translate batch prompt is large (~%d tokens, batch_size=%d, target_lang=%s). "
                "Возможен выход за лимит токенов модели, рассмотрите уменьшение TRANSLATE_BATCH_SIZE.",
                approx_prompt_tokens,
                len(batch),
                target_lang,
            )
        raw = None
        try:
            raw = llm_client.complete(
                system=system,
                user=user,
                response_format="json_object",
                temperature=0,
            )
            if not (raw and str(raw).strip()):
                raise ValueError("Translate API returned empty response")
            data = json.loads(raw)
            # Унифицированный парсинг форматов ответа:
            # - массив объектов;
            # - {"data": [...]} / {"subtitles": [...]} / {"items": [...]} / {"segments": [...]};
            # - одиночный объект {"id": ..., "text": ...}.
            data = _normalize_translate_response(data)
            # items may be dicts с "id"/"text"; собираем карту id -> перевод
            translated: dict[int, str] = {
                int(item["id"]): str(item.get("text", "")).strip()
                for item in data
                if isinstance(item, dict) and "id" in item
            }
            if profile.expected_script == "latin" and DEBUG_SUBTITLES_LOG:
                cyrillic_issues = [text for text in translated.values() if CYRILLIC_RE.search(text or "")]
                if cyrillic_issues:
                    logger.warning(
                        "Translated subtitles for target_lang=%s contain Cyrillic letters in %d segment(s). "
                        "Убедитесь, что используется письменность из translation language profile. "
                        "Примеры: %s",
                        target_lang,
                        len(cyrillic_issues),
                        "; ".join(cyrillic_issues[:3]),
                    )

            # После основного ответа проверяем, все ли id получили перевод.
            expected_ids = {item["id"] for item in batch}
            missing_ids = list(expected_ids - set(translated.keys()))

            # Делаем несколько попыток дозапроса только пропущенных id,
            # чтобы не терять уже успешно переведённые сегменты.
            fill_rounds = 0
            max_fill_rounds = 2
            while missing_ids and fill_rounds < max_fill_rounds:
                fill_rounds += 1
                if DEBUG_SUBTITLES_LOG:
                    logger.warning(
                        "Translate batch fill-missing round %d (target_lang=%s): %d id(s) to fill: %s",
                        fill_rounds,
                        target_lang,
                        len(missing_ids),
                        missing_ids[:10],
                    )
                for mid in list(missing_ids):
                    src = next((s for s in batch if s.get("id") == mid), None)
                    if not src:
                        continue
                    one_payload = [{"id": mid, "text_ru": src.get("text_ru", "")}]
                    user_one = (
                        f"Переведи одну строку на {target_lang_name}. "
                        f"Соблюдай письменность целевого языка: {profile.script_instruction}. "
                        "Верни ТОЛЬКО один JSON-объект: {\"id\": <number>, \"text\": \"...\"}.\n\n"
                        + json.dumps(one_payload, ensure_ascii=False)
                    )
                    try:
                        raw_one = llm_client.complete(
                            system=system,
                            user=user_one,
                            response_format="json_object",
                            temperature=0,
                        )
                        if not (raw_one and str(raw_one).strip()):
                            raise ValueError("Translate API returned empty response for fill-missing")
                        obj = json.loads(raw_one)
                        items = _normalize_translate_response(obj)
                        if items and isinstance(items[0], dict):
                            obj = items[0]
                        translated[mid] = str(obj.get("text", "")).strip()
                    except Exception as e_fill:
                        if DEBUG_SUBTITLES_LOG:
                            logger.warning(
                                "Fill-missing failed for id=%s (target_lang=%s): %s. Оставим RU-текст.",
                                mid,
                                target_lang,
                                e_fill,
                            )
                    finally:
                        # В любом случае убираем id из missing_ids, чтобы не зациклиться
                        if mid in missing_ids:
                            missing_ids.remove(mid)

            # Заполняем переводы по id; если после fill-missing чего-то всё ещё нет,
            # оставляем русский текст как окончательный фолбэк.
            for j, idx in enumerate(indices):
                seg = segments[idx]
                sid = seg.get("id", idx + 1)
                translated_text = translated.get(sid)
                if translated_text is None:
                    if DEBUG_SUBTITLES_LOG:
                        logger.warning(
                            "Missing translation for segment id=%s (target_lang=%s), оставляем RU-текст без перевода.",
                            sid,
                            target_lang,
                        )
                    translated_text = (seg.get("text") or "").strip()
                result[idx] = {**seg, "text": translated_text}
        except Exception as e:
            if DEBUG_SUBTITLES_LOG:
                logger.warning(
                    "Translate batch failed (size=%d, target_lang=%s). Will shrink batch or fallback to per-segment mode.",
                    len(batch),
                    target_lang,
                )
                if raw is not None:
                    snippet = (raw[:2000] + "...") if len(raw) > 2000 else raw
                    logger.warning("Translate API raw response (snippet): %s", snippet)
                else:
                    logger.warning("Translate API raw response (snippet): (None)")
                logger.warning("Translate batch exception: %s", e, exc_info=True)
            # Если уже пробуем маленькими батчами или всего один сегмент — переходим в режим
            # перевода по одному сегменту с жёстким fallback, чтобы не зациклиться.
            if batch_size <= 1 or len(batch) == 1:
                for idx in indices:
                    seg = segments[idx]
                    one = [
                        {
                            "id": seg.get("id", idx + 1),
                            "text_ru": (seg.get("text") or "").strip(),
                        }
                    ]
                    user_one = (
                        f"Переведи одну строку на {target_lang_name}. "
                        f"Соблюдай письменность целевого языка: {profile.script_instruction}. Верни JSON: "
                        f'{{"id": {seg.get("id", idx + 1)}, "text": "..."}}\n\n'
                        f"{json.dumps(one, ensure_ascii=False)}"
                    )
                    try:
                        raw_one = llm_client.complete(
                            system=system,
                            user=user_one,
                            response_format="json_object",
                            temperature=0,
                        )
                        if not (raw_one and str(raw_one).strip()):
                            raise ValueError("Translate API returned empty response")
                        obj = json.loads(raw_one)
                        # Поддерживаем те же варианты обёртки, что и для батчей.
                        items = _normalize_translate_response(obj)
                        if items and isinstance(items[0], dict):
                            obj = items[0]
                        result[idx] = {
                            **segments[idx],
                            "text": str(obj.get("text", "")).strip(),
                        }
                    except Exception:
                        # В самом жёстком fallback'е оставляем оригинальный русский текст,
                        # чтобы не зациклиться вовсе.
                        result[idx] = {
                            **segments[idx],
                            "text": (segments[idx].get("text") or "").strip(),
                        }
                # После обработки по одному просто переходим к следующему циклу с тем же batch_size
                # (новые batch'и будут строиться уже только по необработанным сегментам).
                continue
            # Для больших батчей постепенно уменьшаем размер без нижней границы TRANSLATE_BATCH_MIN_RETRY,
            # чтобы гарантированно дойти до 1 и не зациклиться на маленьких входах.
            batch_size = max(1, batch_size // 2)
            continue
    for i in range(len(segments)):
        if result[i] is None:
            result[i] = {**segments[i], "text": (segments[i].get("text") or "").strip()}
    return result


def _normalize_translate_response(data) -> list[dict]:
    """
    Приводит ответ LLM-переводчика к списку объектов с полями как минимум id/text.
    Поддерживаем форматы:
    - [ {...}, {...} ]
    - {"data": [ ... ]}, {"subtitles": [ ... ]}, {"items": [ ... ]}, {"segments": [ ... ]}
    - {"id": ..., "text": ...}
    - произвольные обёртки вида {"result": [ {...} ]}, если внутри есть список словарей с "id".
    """
    # Уже список — считаем это целевым контейнером.
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        # Явные ключи-контейнеры
        for key in ("data", "subtitles", "items", "segments"):
            value = data.get(key)
            if isinstance(value, list):
                return value

        # Прямой объект сегмента
        if "id" in data and "text" in data:
            return [data]

        # Неявный контейнер: ищем первый список словарей с "id"
        for value in data.values():
            if isinstance(value, list) and any(isinstance(x, dict) and "id" in x for x in value):
                return value

    # Фоллбек — оборачиваем как единственный элемент
    return [data]


def build_ass(segments: list[dict], style_preset: str = "boxed") -> str:
    """
    Генерирует ASS. style_preset: boxed (белый текст + подложка) или outline (белый + обводка).
    Выравнивание: по центру снизу (Alignment=2). Белый текст с чёрной обводкой.
    PlayResX/PlayResY задают разрешение для совместимости с плеерами.
    """
    script_info = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "\n"
    )
    # V4+ Styles: BorderStyle=1 (outline+shadow), Outline=2 (обводка), Shadow=1, Alignment=2 (низ по центру)
    # Цвета: &HAABBGGRR (alpha, blue, green, red). OutlineColour=чёрный.
    if style_preset == "outline":
        style_line = "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1"
    else:
        style_line = "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1"
    styles_section = (
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style_line}\n\n"
    )
    events_header = "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    body = []
    for seg in segments:
        start, end = seg.get("start", 0), seg.get("end", 0)
        raw = (seg.get("text") or "").strip()
        if not raw:
            continue
        text = raw.replace("\n", "\\N").replace("{", "{{").replace("}", "}}")
        body.append(f"Dialogue: 0,{_format_ts_ass(start)},{_format_ts_ass(end)},Default,,0,0,0,,{text}")
    return script_info + styles_section + events_header + "\n".join(body)


def _build_ffmpeg_vcodec() -> list[str]:
    """Параметры кодирования видео из env VIDEO_ENCODER."""
    if VIDEO_ENCODER == "libx264":
        return [
            "-c:v", "libx264",
            "-preset", os.getenv("X264_PRESET", "veryfast"),
            "-crf", os.getenv("X264_CRF", "20"),
        ]
    if VIDEO_ENCODER == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", os.getenv("NVENC_PRESET", "p4"),
            "-cq", os.getenv("NVENC_CQ", "23"),
        ]
    if VIDEO_ENCODER in ("h264_qsv", "h264_amf"):
        return ["-c:v", VIDEO_ENCODER]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def render_burned_video(
    input_video: str,
    srt_path: str,
    output_path: str,
    progress_callback: Callable[[str], None] | None = None,
) -> None:
    """
    Рендерит MP4 с вжёженными субтитрами из SRT.
    - Уникальное имя временного SRT (без гонок при параллельных задачах).
    - cwd по умолчанию = output_dir; fallback = input_dir (Windows).
    - Аудио: сначала -c:a copy, при ошибке — перекодирование в AAC.
    - VIDEO_ENCODER: libx264 (default), h264_nvenc, h264_qsv, h264_amf.
    """
    if progress_callback:
        progress_callback("render_video")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg не найден")
    srt_file = Path(srt_path).resolve()
    if not srt_file.is_file():
        raise FileNotFoundError(f"Файл субтитров не найден: {srt_file}")
    input_path = Path(input_video).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"Входное видео не найдено: {input_path}")
    input_dir = input_path.parent
    output_abs = Path(output_path).resolve()
    output_dir = output_abs.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_name = f"subtitles_{uuid.uuid4().hex}.srt"

    def run_ffmpeg(cwd: Path, srt_rel: str, audio_mode: str) -> None:
        vcodec = _build_ffmpeg_vcodec()
        if audio_mode == "copy":
            acodec = ["-c:a", "copy"]
        else:
            acodec = ["-c:a", "aac", "-b:a", os.getenv("AAC_BITRATE", "128k")]
        cmd = [
            ffmpeg, "-y", "-i", str(input_path),
            "-map", "0:v:0", "-map", "0:a?",
            "-vf", f"subtitles={srt_rel}",
            *vcodec,
            *acodec,
            "-movflags", "+faststart",
            str(output_abs),
        ]
        if DEBUG_SUBTITLES_LOG:
            logger.info("ffmpeg burn subtitles cwd=%s audio=%s cmd=%s", cwd, audio_mode, cmd)
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed (cwd={cwd}, audio={audio_mode}). stderr: {(r.stderr or '')[-1000:]}")
        if DEBUG_SUBTITLES_LOG and r.stderr:
            logger.info("ffmpeg stderr (last 800 chars): %s", (r.stderr or "")[-800:])

    ass_in_output_dir = output_dir / temp_name
    try:
        shutil.copy2(srt_file, ass_in_output_dir)
        try:
            run_ffmpeg(cwd=output_dir, srt_rel=temp_name, audio_mode="copy")
            return
        except RuntimeError:
            run_ffmpeg(cwd=output_dir, srt_rel=temp_name, audio_mode="aac")
            return
    except (OSError, RuntimeError):
        ass_in_input_dir = input_dir / temp_name
        try:
            shutil.copy2(srt_file, ass_in_input_dir)
            try:
                run_ffmpeg(cwd=input_dir, srt_rel=temp_name, audio_mode="copy")
                return
            except RuntimeError:
                run_ffmpeg(cwd=input_dir, srt_rel=temp_name, audio_mode="aac")
                return
        finally:
            if ass_in_input_dir.is_file():
                try:
                    ass_in_input_dir.unlink()
                except OSError:
                    pass
        raise
    finally:
        if ass_in_output_dir.is_file():
            try:
                ass_in_output_dir.unlink()
            except OSError:
                pass


def run_burned_subs_pipeline(
    video_path: str | Path,
    target_lang: str,
    output_mode: str,
    subtitle_style: str,
    output_dir: str,
    progress_callback: Callable[[str], None] | None = None,
    llm_client=None,
) -> dict:
    """
    Полный пайплайн. output_mode: burned_video | subtitles_only | both.
    subtitle_style: boxed | outline.
    output_dir: директория для сохранения файлов (уже создана под request_id).
    Возвращает словарь с ключами: video_path, vtt_path, srt_path, ass_path, transcript_path, segments.
    """
    from content_factory.platform.llm.factory import create_llm_client
    if llm_client is None:
        llm_client = create_llm_client(
            model=_resolve_subtitle_translate_model(),
            default_role="translator",
            enable_cache=True,
            enable_batching=True,
        )
    video_path = Path(video_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = None
    try:
        audio_path = extract_audio(video_path, progress_callback)
        segments_ru_raw = transcribe_ru_whisper(audio_path, progress_callback)
        if not segments_ru_raw:
            raise RuntimeError("Транскрипция не вернула сегментов")
        # Для продакшн-режима избегаем дополнительного прохода LLM
        # по всем сегментам (correct_asr_segments), чтобы не удваивать
        # нагрузку и время. Переводим сразу по результатам ASR.
        segments_ru = segments_ru_raw
        segments = translate_segments_llm(segments_ru, target_lang, llm_client, progress_callback)
        if progress_callback:
            progress_callback("build_subtitles")
        vtt_path = output_dir / "subtitles.vtt"
        srt_path = output_dir / "subtitles.srt"
        ass_path = output_dir / "subtitles.ass"
        transcript_path = output_dir / "transcript_ru.json"
        vtt_path.write_text(build_vtt(segments), encoding="utf-8")
        srt_path.write_text(build_srt(segments), encoding="utf-8")
        ass_path.write_text(build_ass(segments, subtitle_style), encoding="utf-8-sig")
        transcript_data = [
            {"id": s.get("id"), "start_ms": int(s["start"] * 1000), "end_ms": int(s["end"] * 1000), "text_ru": (s.get("text") or "").strip()}
            for s in segments_ru
        ]
        transcript_path.write_text(json.dumps({"segments": transcript_data}, ensure_ascii=False, indent=2), encoding="utf-8")
        result = {"vtt_path": str(vtt_path), "srt_path": str(srt_path), "ass_path": str(ass_path), "transcript_path": str(transcript_path), "segments": segments}
        if output_mode in ("burned_video", "both"):
            video_out = output_dir / "output_with_subs.mp4"
            try:
                render_burned_video(str(video_path), str(srt_path), str(video_out), progress_callback)
                result["video_path"] = str(video_out)
            except Exception as e:
                logger.warning("Рендер видео с субтитрами не удался, субтитры доступны для скачивания: %s", e, exc_info=True)
                result["render_error"] = str(e)
        return result
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.unlink(audio_path)
            except OSError:
                pass
