"""Кэш для хранения результатов генерации."""

import os
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

from api.utils.logger import get_logger
from content_gen.orchestrator import OrchestratorResult
from content_gen.utils.markdown_display_normalizer import normalize_markdown_display_blocks
from content_gen.utils.rubric_export import convert_numpy_types

logger = get_logger("cache")

# In-memory кэш (в production можно заменить на Redis)
_result_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_cache_ttl = timedelta(hours=24)  # Время жизни кэша
_max_cache_size = int(os.getenv("MAX_RESULT_CACHE_SIZE", "100"))

# Статусы генерации: pending, in_progress, needs_review, completed, failed, cancelled
_generation_status: dict[str, str] = {}
_generation_errors: dict[str, str] = {}
_generation_methodology: dict[str, dict[str, Any]] = {}
_generation_owners: dict[str, str] = {}

# Активные задачи генерации для возможности отмены
_active_generation_tasks: dict[str, Any] = {}


def _utc_now() -> datetime:
    """Return UTC time as a naive datetime for legacy cache comparisons."""
    return datetime.now(UTC).replace(tzinfo=None)


def _normalize_created_at(value: Any) -> datetime | None:
    """Преобразует created_at из старого/нового формата в datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _evict_if_needed() -> None:
    """Удаляет самые старые записи при переполнении кэша."""
    while len(_result_cache) > _max_cache_size:
        oldest_request_id, _ = _result_cache.popitem(last=False)
        _generation_status.pop(oldest_request_id, None)
        _generation_errors.pop(oldest_request_id, None)
        _generation_methodology.pop(oldest_request_id, None)
        _generation_owners.pop(oldest_request_id, None)


def store_result(
    request_id: str,
    result: OrchestratorResult,
    regenerated: dict[str, Any] | None = None,
    user_id: str | None = None,
    project_seed_payload: dict[str, Any] | None = None,
) -> None:
    """
    Сохраняет результат генерации в кэш.
    
    Args:
        request_id: ID запроса
        result: Результат генерации
        regenerated: Перегенерированные данные (опционально)
        project_seed_payload: Исходный ProjectSeed payload для downstream regeneration/review
    """
    markdown = normalize_markdown_display_blocks(result.report_json.get("markdown", ""))
    if result.report_json.get("markdown") != markdown:
        result.report_json["markdown"] = markdown
    translated_markdown = result.report_json.get("translated_markdown")
    if translated_markdown:
        result.report_json["translated_markdown"] = normalize_markdown_display_blocks(translated_markdown)
    logger.debug(f"Сохранение результата: request_id={request_id}, markdown_len={len(markdown)}, rubric={bool(result.report_json.get('rubric'))}")

    # Конвертируем numpy типы в стандартные Python типы перед сохранением в кэш
    report_json_clean = convert_numpy_types(result.report_json)
    rubric_clean = convert_numpy_types(result.report_json.get("rubric", {}))

    if request_id in _result_cache:
        _result_cache.pop(request_id, None)

    _result_cache[request_id] = {
        "result": result,
        "report_json": report_json_clean,
        "warnings": result.warnings,
        "markdown": markdown,
        "rubric": rubric_clean,
        "regenerated": convert_numpy_types(regenerated) if regenerated else None,
        "assets": result.assets,
        "flow_trace": result.flow_trace,
        "methodology": report_json_clean.get("methodology_gate"),
        "project_seed_payload": convert_numpy_types(project_seed_payload) if project_seed_payload else None,
        "user_id": user_id,
        "created_at": _utc_now(),
    }
    if user_id:
        _generation_owners[request_id] = user_id
    if isinstance(report_json_clean.get("methodology_gate"), dict):
        set_generation_methodology(request_id, report_json_clean["methodology_gate"])
    _evict_if_needed()

    # Устанавливаем статус completed при сохранении результата
    set_generation_status(request_id, "completed")

    logger.info(f"✅ Результат сохранен в кэш. Всего записей: {len(_result_cache)}")


def get_result(request_id: str) -> dict[str, Any] | None:
    """
    Получает результат генерации из кэша.
    
    Args:
        request_id: ID запроса
        
    Returns:
        Данные результата или None, если не найдено или истек срок
    """
    logger.debug(f"Запрос результата: request_id={request_id}, всего в кэше: {len(_result_cache)} записей")

    if request_id not in _result_cache:
        logger.warning(f"⚠️ Результат не найден в кэше: request_id={request_id}")
        return None

    cached = _result_cache[request_id]

    # Проверяем срок действия
    created_at = _normalize_created_at(cached.get("created_at"))
    if created_at is None:
        del _result_cache[request_id]
        _generation_owners.pop(request_id, None)
        return None

    age = _utc_now() - created_at
    if age > _cache_ttl:
        logger.warning(f"⏰ Результат истек: request_id={request_id}, возраст={age}, TTL={_cache_ttl}")
        del _result_cache[request_id]
        _generation_status.pop(request_id, None)
        _generation_errors.pop(request_id, None)
        _generation_methodology.pop(request_id, None)
        _generation_owners.pop(request_id, None)
        return None

    _result_cache.move_to_end(request_id)
    logger.debug(f"✅ Результат найден в кэше: request_id={request_id}, возраст={age}, markdown_len={len(cached.get('markdown', ''))}")
    return cached


def clear_result(request_id: str) -> None:
    """
    Удаляет результат из кэша.
    
    Args:
        request_id: ID запроса
    """
    _result_cache.pop(request_id, None)
    _generation_status.pop(request_id, None)
    _generation_errors.pop(request_id, None)
    _generation_methodology.pop(request_id, None)
    _generation_owners.pop(request_id, None)


def clear_expired() -> None:
    """Очищает истекшие записи из кэша."""
    now = _utc_now()
    expired = [
        request_id for request_id, data in _result_cache.items()
        if (
            (created_at := _normalize_created_at(data.get("created_at"))) is None
            or now - created_at > _cache_ttl
        )
    ]
    for request_id in expired:
        del _result_cache[request_id]
        _generation_status.pop(request_id, None)
        _generation_errors.pop(request_id, None)
        _generation_methodology.pop(request_id, None)
        _generation_owners.pop(request_id, None)


def set_generation_status(request_id: str, status: str) -> None:
    """
    Устанавливает статус генерации.
    
    Args:
        request_id: ID запроса
        status: Статус (pending, in_progress, completed, failed)
    """
    _generation_status[request_id] = status
    logger.debug(f"Статус генерации установлен: request_id={request_id}, status={status}")


def set_generation_owner(request_id: str, user_id: str) -> None:
    """Сохраняет владельца runtime-задачи для пользовательских dashboard-сводок."""
    _generation_owners[request_id] = user_id


def get_generation_owner(request_id: str) -> str | None:
    """Возвращает владельца runtime-задачи, если он известен."""
    cached = _result_cache.get(request_id)
    if isinstance(cached, dict) and cached.get("user_id"):
        return str(cached["user_id"])
    return _generation_owners.get(request_id)


def get_active_generation_count(user_id: str | None = None) -> int:
    """Возвращает количество активных генераций, при необходимости только пользователя."""
    active_statuses = {"pending", "in_progress", "needs_review"}
    count = 0
    for request_id, status in _generation_status.items():
        if status not in active_statuses:
            continue
        if user_id is not None and _generation_owners.get(request_id) != user_id:
            continue
        count += 1
    return count


def get_generation_status(request_id: str) -> str | None:
    """
    Получает статус генерации.
    
    Args:
        request_id: ID запроса
        
    Returns:
        Статус генерации или None, если не найден
    """
    return _generation_status.get(request_id)


def store_generation_error(request_id: str, error: str) -> None:
    """
    Сохраняет ошибку генерации.
    
    Args:
        request_id: ID запроса
        error: Текст ошибки
    """
    _generation_errors[request_id] = error
    logger.debug(f"Ошибка генерации сохранена: request_id={request_id}, error={error[:100]}")


def get_generation_error(request_id: str) -> str | None:
    """
    Получает ошибку генерации.
    
    Args:
        request_id: ID запроса
        
    Returns:
        Текст ошибки или None, если не найдена
    """
    return _generation_errors.get(request_id)


def set_generation_methodology(request_id: str, methodology: dict[str, Any]) -> None:
    """
    Сохраняет live-снимок методологического gate для UI.

    Args:
        request_id: ID запроса
        methodology: Payload вида {"summary": ..., "decisions": ...}
    """
    _generation_methodology[request_id] = convert_numpy_types(methodology)
    logger.debug(f"Methodology snapshot updated: request_id={request_id}")


def get_generation_methodology(request_id: str) -> dict[str, Any] | None:
    """
    Получает live-снимок методологического gate.

    Args:
        request_id: ID запроса

    Returns:
        Methodology gate payload или None, если его ещё нет
    """
    return _generation_methodology.get(request_id)


def register_generation_task(request_id: str, task: Any) -> None:
    """
    Регистрирует активную задачу генерации для возможности отмены.
    
    Args:
        request_id: ID запроса
        task: Asyncio задача
    """
    _active_generation_tasks[request_id] = task
    logger.debug(f"Задача генерации зарегистрирована: request_id={request_id}")


def cancel_generation_task(request_id: str) -> bool:
    """
    Отменяет активную задачу генерации.
    
    Args:
        request_id: ID запроса
        
    Returns:
        True если задача была отменена, False если не найдена
    """
    if request_id not in _active_generation_tasks:
        logger.warning(f"⚠️ Задача генерации не найдена для отмены: request_id={request_id}")
        return False

    task = _active_generation_tasks.pop(request_id)
    if task and not task.done():
        task.cancel()
        logger.info(f"🛑 Задача генерации отменена: request_id={request_id}")
        set_generation_status(request_id, "cancelled")
        store_generation_error(request_id, "Генерация была остановлена пользователем")
        return True
    else:
        logger.debug(f"Задача уже завершена: request_id={request_id}")
        return False


def unregister_generation_task(request_id: str) -> None:
    """
    Удаляет задачу из реестра активных задач.
    
    Args:
        request_id: ID запроса
    """
    _active_generation_tasks.pop(request_id, None)


# --- Кэш задач перевода (асинхронный режим: POST возвращает request_id, статус опрашивается через GET) ---
_translation_jobs: dict[str, dict[str, Any]] = {}
_translation_ttl = timedelta(hours=2)


def set_translation_job(
    request_id: str,
    status: str,
    user_id: str | None = None,
    phase: str | None = None,
    original_markdown: str | None = None,
    translated_markdown: str | None = None,
    target_language: str | None = None,
    error: str | None = None,
    job_type: str | None = None,
    translated_subtitles: str | None = None,
    original_transcript: str | None = None,
    subtitle_format: str | None = None,
    progress: float | None = None,
    error_code: str | None = None,
    result_links: dict[str, str] | None = None,
    source_filename: str | None = None,
    source_format: str | None = None,
) -> None:
    """Создаёт или обновляет задачу перевода (status: pending, in_progress, completed, failed)."""
    now = _utc_now()
    if request_id not in _translation_jobs:
        _translation_jobs[request_id] = {"created_at": now}
    job = _translation_jobs[request_id]
    job["status"] = status
    if user_id is not None:
        job["user_id"] = user_id
    if phase is not None:
        job["phase"] = phase
    if original_markdown is not None:
        job["original_markdown"] = original_markdown
    if translated_markdown is not None:
        job["translated_markdown"] = translated_markdown
    if target_language is not None:
        job["target_language"] = target_language
    if error is not None:
        job["error"] = error
    if job_type is not None:
        job["job_type"] = job_type
    if translated_subtitles is not None:
        job["translated_subtitles"] = translated_subtitles
    if original_transcript is not None:
        job["original_transcript"] = original_transcript
    if subtitle_format is not None:
        job["subtitle_format"] = subtitle_format
    if progress is not None:
        job["progress"] = progress
    if error_code is not None:
        job["error_code"] = error_code
    if result_links is not None:
        job["result_links"] = result_links
    if source_filename is not None:
        job["source_filename"] = source_filename
    if source_format is not None:
        job["source_format"] = source_format
    job["updated_at"] = now
    logger.debug(f"Translation job updated: request_id={request_id}, status={status}, phase={phase}")


def get_translation_job_owner(request_id: str) -> str | None:
    """Возвращает владельца задачи перевода без раскрытия его в public status payload."""
    job = _translation_jobs.get(request_id)
    if not isinstance(job, dict):
        return None
    owner = job.get("user_id")
    return str(owner) if owner else None


def set_translation_phase(request_id: str, phase: str, progress: float | None = None) -> None:
    """Обновляет фазу и опционально прогресс текущей задачи перевода."""
    if request_id in _translation_jobs:
        _translation_jobs[request_id]["phase"] = phase
        if progress is not None:
            _translation_jobs[request_id]["progress"] = progress
        _translation_jobs[request_id]["updated_at"] = _utc_now()


def get_translation_job(request_id: str) -> dict[str, Any] | None:
    """Возвращает задачу перевода или None (если не найдена или истек TTL)."""
    if request_id not in _translation_jobs:
        return None
    job = _translation_jobs[request_id]
    if _utc_now() - job["created_at"] > _translation_ttl:
        del _translation_jobs[request_id]
        return None
    allowed = (
        "status", "phase", "original_markdown", "translated_markdown", "target_language",
        "error", "created_at", "job_type", "translated_subtitles", "original_transcript",
        "subtitle_format", "progress", "error_code", "result_links", "source_filename", "source_format",
    )
    return {k: v for k, v in job.items() if k in allowed}
