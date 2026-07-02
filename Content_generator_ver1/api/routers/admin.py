"""Endpoints для администраторов: статистика, мониторинг, управление."""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.db.models import LogEntry, RequestLog, User, UserSession
from api.db.session import get_db_session
from api.dependencies import get_current_user
from api.utils.logger import get_logger

router = APIRouter()
logger = get_logger("admin")


def _db_user_id_from_subject(user_id: Any) -> int | None:
    """JWT subject is stored as user_<db_id>; admin checks need the numeric DB id."""
    if isinstance(user_id, int):
        return user_id
    value = str(user_id or "").strip()
    if value.startswith("user_"):
        value = value.removeprefix("user_")
    return int(value) if value.isdigit() else None


def is_admin(
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """
    Проверяет, является ли пользователь администратором.
    
    В текущей реализации все авторизованные пользователи считаются админами.
    В будущем можно добавить проверку роли в БД.
    
    Args:
        user: Данные пользователя из аутентификации
        
    Returns:
        Данные пользователя
        
    Raises:
        HTTPException: Если пользователь не является администратором
    """
    user_id = user.get("id") if isinstance(user, dict) else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не аутентифицирован",
        )

    db_user_id = _db_user_id_from_subject(user_id)
    if db_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Администратор не найден",
        )

    db_user = db.query(User).filter(User.id == db_user_id).first()
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Администратор не найден",
        )

    if not getattr(db_user, "is_active", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт администратора деактивирован",
        )

    if getattr(db_user, "role", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуются права администратора",
        )

    return user


@router.get("/stats/users")
async def get_user_stats(
    days: int = 7,
    db: Session = Depends(get_db_session),
    admin: dict = Depends(is_admin)
) -> dict[str, Any]:
    """
    Получает статистику по пользователям.
    
    Args:
        days: Количество дней для анализа (по умолчанию 7)
        db: Сессия БД
        admin: Данные администратора
        
    Returns:
        Статистика по пользователям
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    # Общее количество уникальных пользователей
    total_users = db.query(func.count(func.distinct(UserSession.user_id))).scalar()

    # Активные пользователи (с активными сессиями)
    active_users = db.query(func.count(func.distinct(UserSession.user_id))).filter(
        UserSession.is_active == "true"
    ).scalar()

    # Новые пользователи за период
    new_users = db.query(func.count(func.distinct(UserSession.user_id))).filter(
        UserSession.started_at >= start_date
    ).scalar()

    # Пользователи с активностью за период
    active_users_period = db.query(func.count(func.distinct(RequestLog.user_id))).filter(
        RequestLog.timestamp >= start_date
    ).scalar()

    return {
        "total_users": total_users,
        "active_users": active_users,
        "new_users_last_days": new_users,
        "active_users_last_days": active_users_period,
        "period_days": days,
    }


@router.get("/stats/requests")
async def get_request_stats(
    days: int = 7,
    db: Session = Depends(get_db_session),
    admin: dict = Depends(is_admin)
) -> dict[str, Any]:
    """
    Получает статистику по запросам.
    
    Args:
        days: Количество дней для анализа (по умолчанию 7)
        db: Сессия БД
        admin: Данные администратора
        
    Returns:
        Статистика по запросам
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    # Общее количество запросов
    total_requests = db.query(func.count(RequestLog.id)).filter(
        RequestLog.timestamp >= start_date
    ).scalar()

    # Запросы по методам
    requests_by_method = db.query(
        RequestLog.method,
        func.count(RequestLog.id).label("count")
    ).filter(
        RequestLog.timestamp >= start_date
    ).group_by(RequestLog.method).all()

    # Запросы по статус кодам
    requests_by_status = db.query(
        RequestLog.status_code,
        func.count(RequestLog.id).label("count")
    ).filter(
        RequestLog.timestamp >= start_date
    ).group_by(RequestLog.status_code).all()

    # Среднее время ответа
    avg_response_time = db.query(
        func.avg(RequestLog.response_time_ms)
    ).filter(
        RequestLog.timestamp >= start_date,
        RequestLog.response_time_ms.isnot(None)
    ).scalar()

    # Топ путей по количеству запросов
    top_paths = db.query(
        RequestLog.path,
        func.count(RequestLog.id).label("count")
    ).filter(
        RequestLog.timestamp >= start_date
    ).group_by(RequestLog.path).order_by(
        func.count(RequestLog.id).desc()
    ).limit(10).all()

    return {
        "total_requests": total_requests,
        "requests_by_method": {method: count for method, count in requests_by_method},
        "requests_by_status": {status: count for status, count in requests_by_status},
        "avg_response_time_ms": float(avg_response_time) if avg_response_time else None,
        "top_paths": [{"path": path, "count": count} for path, count in top_paths],
        "period_days": days,
    }


@router.get("/stats/errors")
async def get_error_stats(
    days: int = 7,
    db: Session = Depends(get_db_session),
    admin: dict = Depends(is_admin)
) -> dict[str, Any]:
    """
    Получает статистику по ошибкам.
    
    Args:
        days: Количество дней для анализа (по умолчанию 7)
        db: Сессия БД
        admin: Данные администратора
        
    Returns:
        Статистика по ошибкам
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    # Ошибки в логах (уровень ERROR и выше)
    error_logs = db.query(func.count(LogEntry.id)).filter(
        LogEntry.timestamp >= start_date,
        LogEntry.level.in_(["ERROR", "CRITICAL"])
    ).scalar()

    # Ошибки в запросах (статус 4xx и 5xx)
    error_requests = db.query(func.count(RequestLog.id)).filter(
        RequestLog.timestamp >= start_date,
        RequestLog.status_code >= 400
    ).scalar()

    # Ошибки по типам (из метаданных логов)
    # Это упрощенная версия - в реальности нужно парсить metadata
    critical_errors = db.query(func.count(LogEntry.id)).filter(
        LogEntry.timestamp >= start_date,
        LogEntry.level == "CRITICAL"
    ).scalar()

    # Топ ошибок по сообщениям
    top_errors = db.query(
        LogEntry.message,
        func.count(LogEntry.id).label("count")
    ).filter(
        LogEntry.timestamp >= start_date,
        LogEntry.level.in_(["ERROR", "CRITICAL"])
    ).group_by(LogEntry.message).order_by(
        func.count(LogEntry.id).desc()
    ).limit(10).all()

    return {
        "error_logs": error_logs,
        "error_requests": error_requests,
        "critical_errors": critical_errors,
        "top_errors": [{"message": msg, "count": count} for msg, count in top_errors],
        "period_days": days,
    }


@router.get("/stats/sessions")
async def get_session_stats(
    db: Session = Depends(get_db_session),
    admin: dict = Depends(is_admin)
) -> dict[str, Any]:
    """
    Получает статистику по сессиям.
    
    Args:
        db: Сессия БД
        admin: Данные администратора
        
    Returns:
        Статистика по сессиям
    """
    # Активные сессии
    active_sessions = db.query(func.count(UserSession.id)).filter(
        UserSession.is_active == "true"
    ).scalar()

    # Общее количество сессий
    total_sessions = db.query(func.count(UserSession.id)).scalar()

    # Сессии за последние 24 часа
    last_24h = datetime.utcnow() - timedelta(hours=24)
    sessions_24h = db.query(func.count(UserSession.id)).filter(
        UserSession.started_at >= last_24h
    ).scalar()

    # Средняя длительность активных сессий
    active_sessions_with_duration = db.query(
        func.avg(
            func.extract('epoch', datetime.utcnow() - UserSession.started_at)
        )
    ).filter(
        UserSession.is_active == "true"
    ).scalar()

    return {
        "active_sessions": active_sessions,
        "total_sessions": total_sessions,
        "sessions_last_24h": sessions_24h,
        "avg_active_session_duration_seconds": float(active_sessions_with_duration) if active_sessions_with_duration else None,
    }


@router.get("/logs/recent")
async def get_recent_logs(
    limit: int = 100,
    level: str | None = None,
    db: Session = Depends(get_db_session),
    admin: dict = Depends(is_admin)
) -> list[dict[str, Any]]:
    """
    Получает последние логи.
    
    Args:
        limit: Максимальное количество записей (по умолчанию 100)
        level: Фильтр по уровню (опционально)
        db: Сессия БД
        admin: Данные администратора
        
    Returns:
        Список логов
    """
    query = db.query(LogEntry)

    if level:
        query = query.filter(LogEntry.level == level.upper())

    logs = query.order_by(LogEntry.timestamp.desc()).limit(limit).all()

    return [log.to_dict() for log in logs]


@router.get("/requests/recent")
async def get_recent_requests(
    limit: int = 100,
    status_code: int | None = None,
    db: Session = Depends(get_db_session),
    admin: dict = Depends(is_admin)
) -> list[dict[str, Any]]:
    """
    Получает последние запросы.
    
    Args:
        limit: Максимальное количество записей (по умолчанию 100)
        status_code: Фильтр по статус коду (опционально)
        db: Сессия БД
        admin: Данные администратора
        
    Returns:
        Список запросов
    """
    query = db.query(RequestLog)

    if status_code:
        query = query.filter(RequestLog.status_code == status_code)

    requests = query.order_by(RequestLog.timestamp.desc()).limit(limit).all()

    return [req.to_dict() for req in requests]
