"""Функции для записи и чтения логов из базы данных."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .models import LogEntry, RequestLog, utc_now_naive

# Пул потоков для асинхронной записи логов
_executor = ThreadPoolExecutor(max_workers=5)


def _ensure_executor() -> ThreadPoolExecutor:
    """Возвращает рабочий executor и пересоздаёт его после shutdown."""
    global _executor
    if _executor is None or getattr(_executor, "_shutdown", False):
        _executor = ThreadPoolExecutor(max_workers=5)
    return _executor


def write_log(
    db: Session,
    request_id: str,
    level: str,
    message: str,
    user_id: str | None = None,
    agent_name: str | None = None,
    phase: str | None = None,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> LogEntry:
    """
    Записывает лог в базу данных.
    
    Args:
        db: Сессия БД
        request_id: ID запроса
        level: Уровень лога (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        message: Текст сообщения
        user_id: ID пользователя (опционально)
        agent_name: Имя агента (опционально)
        phase: Фаза выполнения (опционально)
        metadata: Дополнительные метаданные (опционально)
        timestamp: Временная метка (опционально, по умолчанию текущее время)
        
    Returns:
        Созданная запись лога
    """
    log_entry = LogEntry(
        request_id=request_id,
        user_id=user_id,
        timestamp=timestamp or utc_now_naive(),
        level=level.upper(),
        message=message,
        agent_name=agent_name,
        phase=phase,
        meta_data=metadata,  # Передаем в поле meta_data модели
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


async def write_log_async(
    request_id: str,
    level: str,
    message: str,
    user_id: str | None = None,
    agent_name: str | None = None,
    phase: str | None = None,
    metadata: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> None:
    """
    Асинхронно записывает лог в базу данных (не блокирует основной поток).
    
    Args:
        request_id: ID запроса
        level: Уровень лога
        message: Текст сообщения
        user_id: ID пользователя (опционально)
        agent_name: Имя агента (опционально)
        phase: Фаза выполнения (опционально)
        metadata: Дополнительные метаданные (опционально)
        timestamp: Временная метка (опционально)
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        _ensure_executor(),
        _write_log_sync,
        request_id,
        level,
        message,
        user_id,
        agent_name,
        phase,
        metadata,
        timestamp,
    )


def _write_log_sync(
    request_id: str,
    level: str,
    message: str,
    user_id: str | None,
    agent_name: str | None,
    phase: str | None,
    metadata: dict[str, Any] | None,
    timestamp: datetime | None,
) -> None:
    """Синхронная функция для записи лога (используется в executor)."""
    from .session import SessionLocal, is_database_available
    if is_database_available() is False:
        return
    db = SessionLocal()
    try:
        write_log(
            db=db,
            request_id=request_id,
            level=level,
            message=message,
            user_id=user_id,
            agent_name=agent_name,
            phase=phase,
            metadata=metadata,  # Используем параметр metadata функции
            timestamp=timestamp,
        )
    except Exception as e:
        # Логируем ошибку в stderr, чтобы не потерять информацию
        import sys
        # Используем print, так как logger может вызвать рекурсию при ошибке записи в БД
        print(f"⚠️ Ошибка записи лога в БД: {e}", file=sys.stderr, flush=True)
    finally:
        db.close()


def get_logs_by_request_id(
    db: Session,
    request_id: str,
    limit: int | None = None
) -> list[LogEntry]:
    """
    Получает логи по request_id.
    
    Args:
        db: Сессия БД
        request_id: ID запроса
        limit: Максимальное количество записей
        
    Returns:
        Список записей логов
    """
    query = db.query(LogEntry).filter(LogEntry.request_id == request_id).order_by(LogEntry.timestamp)
    if limit:
        query = query.limit(limit)
    return query.all()


def get_logs_by_user_id(
    db: Session,
    user_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int | None = None
) -> list[LogEntry]:
    """
    Получает логи по user_id с фильтрацией по времени.
    
    Args:
        db: Сессия БД
        user_id: ID пользователя
        start_time: Начало временного диапазона (опционально)
        end_time: Конец временного диапазона (опционально)
        limit: Максимальное количество записей
        
    Returns:
        Список записей логов
    """
    query = db.query(LogEntry).filter(LogEntry.user_id == user_id)

    if start_time:
        query = query.filter(LogEntry.timestamp >= start_time)
    if end_time:
        query = query.filter(LogEntry.timestamp <= end_time)

    query = query.order_by(LogEntry.timestamp.desc())

    if limit:
        query = query.limit(limit)

    return query.all()


def write_request_log(
    db: Session,
    request_id: str,
    user_id: str | None,
    method: str,
    path: str,
    status_code: int,
    request_body: dict[str, Any] | None = None,
    response_time_ms: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    timestamp: datetime | None = None,
) -> RequestLog:
    """
    Записывает лог HTTP запроса в базу данных.
    
    Args:
        db: Сессия БД
        request_id: ID запроса
        user_id: ID пользователя (опционально)
        method: HTTP метод (GET, POST, etc.)
        path: Путь запроса
        status_code: HTTP статус код ответа
        request_body: Тело запроса (опционально, с маскированием)
        response_time_ms: Время ответа в миллисекундах (опционально)
        ip_address: IP адрес клиента (опционально)
        user_agent: User-Agent заголовок (опционально)
        timestamp: Временная метка (опционально, по умолчанию текущее время)
        
    Returns:
        Созданная запись лога запроса
    """
    log_entry = RequestLog(
        request_id=request_id,
        user_id=user_id,
        method=method,
        path=path,
        status_code=status_code,
        request_body=request_body,
        response_time_ms=response_time_ms,
        ip_address=ip_address,
        user_agent=user_agent,
        timestamp=timestamp or utc_now_naive(),
    )
    db.add(log_entry)
    db.commit()
    db.refresh(log_entry)
    return log_entry


async def write_request_log_async(
    request_id: str,
    user_id: str | None,
    method: str,
    path: str,
    status_code: int,
    request_body: dict[str, Any] | None = None,
    response_time_ms: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    timestamp: datetime | None = None,
) -> None:
    """
    Асинхронно записывает лог HTTP запроса в базу данных (не блокирует основной поток).
    
    Args:
        request_id: ID запроса
        user_id: ID пользователя (опционально)
        method: HTTP метод
        path: Путь запроса
        status_code: HTTP статус код
        request_body: Тело запроса (опционально)
        response_time_ms: Время ответа в миллисекундах (опционально)
        ip_address: IP адрес клиента (опционально)
        user_agent: User-Agent заголовок (опционально)
        timestamp: Временная метка (опционально)
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        _ensure_executor(),
        _write_request_log_sync,
        request_id,
        user_id,
        method,
        path,
        status_code,
        request_body,
        response_time_ms,
        ip_address,
        user_agent,
        timestamp,
    )


def _write_request_log_sync(
    request_id: str,
    user_id: str | None,
    method: str,
    path: str,
    status_code: int,
    request_body: dict[str, Any] | None,
    response_time_ms: int | None,
    ip_address: str | None,
    user_agent: str | None,
    timestamp: datetime | None,
) -> None:
    """Синхронная функция для записи лога запроса (используется в executor)."""
    from .session import SessionLocal, is_database_available
    if is_database_available() is False:
        return
    db = SessionLocal()
    try:
        write_request_log(
            db=db,
            request_id=request_id,
            user_id=user_id,
            method=method,
            path=path,
            status_code=status_code,
            request_body=request_body,
            response_time_ms=response_time_ms,
            ip_address=ip_address,
            user_agent=user_agent,
            timestamp=timestamp,
        )
    except Exception as e:
        # Логируем ошибку в stderr, чтобы не потерять информацию
        import sys
        print(f"⚠️ Ошибка записи лога запроса в БД: {e}", file=sys.stderr, flush=True)
    finally:
        db.close()


def get_request_logs_by_user_id(
    db: Session,
    user_id: str,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int | None = None
) -> list[RequestLog]:
    """
    Получает логи запросов по user_id с фильтрацией по времени.
    
    Args:
        db: Сессия БД
        user_id: ID пользователя
        start_time: Начало временного диапазона (опционально)
        end_time: Конец временного диапазона (опционально)
        limit: Максимальное количество записей
        
    Returns:
        Список записей логов запросов
    """
    query = db.query(RequestLog).filter(RequestLog.user_id == user_id)

    if start_time:
        query = query.filter(RequestLog.timestamp >= start_time)
    if end_time:
        query = query.filter(RequestLog.timestamp <= end_time)

    query = query.order_by(RequestLog.timestamp.desc())

    if limit:
        query = query.limit(limit)

    return query.all()


def get_request_logs_by_request_id(
    db: Session,
    request_id: str,
    limit: int | None = None
) -> list[RequestLog]:
    """
    Получает логи запросов по request_id.
    
    Args:
        db: Сессия БД
        request_id: ID запроса
        limit: Максимальное количество записей
        
    Returns:
        Список записей логов запросов
    """
    query = db.query(RequestLog).filter(RequestLog.request_id == request_id).order_by(RequestLog.timestamp)
    if limit:
        query = query.limit(limit)
    return query.all()


def cleanup_old_logs(
    db: Session,
    days_to_keep: int = 7,
    batch_size: int = 1000
) -> int:
    """
    Удаляет старые логи из базы данных.
    
    Args:
        db: Сессия БД
        days_to_keep: Количество дней для хранения логов (по умолчанию 7)
        batch_size: Размер батча для удаления (по умолчанию 1000)
        
    Returns:
        Количество удаленных записей
    """
    cutoff_date = utc_now_naive() - timedelta(days=days_to_keep)

    deleted_count = _delete_old_rows_by_id(
        db=db,
        model=LogEntry,
        timestamp_column=LogEntry.timestamp,
        cutoff_date=cutoff_date,
        batch_size=batch_size,
    )
    deleted_count += _delete_old_rows_by_id(
        db=db,
        model=RequestLog,
        timestamp_column=RequestLog.timestamp,
        cutoff_date=cutoff_date,
        batch_size=batch_size,
    )
    return deleted_count


def _delete_old_rows_by_id(
    db: Session,
    model: Any,
    timestamp_column: Any,
    cutoff_date: datetime,
    batch_size: int,
) -> int:
    """Delete old rows in batches without materializing full ORM payloads."""
    total_deleted = 0
    while True:
        row_ids = [
            row_id
            for (row_id,) in (
                db.query(model.id)
                .filter(timestamp_column < cutoff_date)
                .order_by(model.id)
                .limit(batch_size)
                .all()
            )
        ]
        if not row_ids:
            break

        deleted = (
            db.query(model)
            .filter(model.id.in_(row_ids))
            .delete(synchronize_session=False)
        )
        db.commit()
        total_deleted += int(deleted or 0)

        if len(row_ids) < batch_size:
            break

    return total_deleted


async def cleanup_old_logs_async(
    days_to_keep: int = 7,
    batch_size: int = 1000
) -> int:
    """
    Асинхронно удаляет старые логи из базы данных.
    
    Args:
        days_to_keep: Количество дней для хранения логов (по умолчанию 7)
        batch_size: Размер батча для удаления (по умолчанию 1000)
        
    Returns:
        Количество удаленных записей
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _ensure_executor(),
        _cleanup_old_logs_sync,
        days_to_keep,
        batch_size,
    )


def _cleanup_old_logs_sync(
    days_to_keep: int,
    batch_size: int,
) -> int:
    """Синхронная функция для очистки логов (используется в executor)."""
    from .session import SessionLocal, is_database_available
    if is_database_available() is False:
        return 0
    db = SessionLocal()
    try:
        return cleanup_old_logs(db, days_to_keep, batch_size)
    except Exception as e:
        import sys
        print(f"⚠️ Ошибка очистки старых логов: {e}", file=sys.stderr, flush=True)
        return 0
    finally:
        db.close()
