"""Endpoints для получения метрик и отчетов."""


from fastapi import APIRouter, Depends, HTTPException

from api.db.models import LogEntry
from api.db.session import SessionLocal
from api.dependencies import get_current_user
from api.utils.result_cache import get_generation_owner, get_generation_status, get_result

router = APIRouter()


def _get_logs_by_request_id(request_id: str) -> list:
    """Получает логи по request_id."""
    db = SessionLocal()
    try:
        return db.query(LogEntry).filter(LogEntry.request_id == request_id).all()
    finally:
        db.close()


def _ensure_request_owner(request_id: str, user: dict) -> None:
    """Запрещает доступ к метрикам чужого запуска."""
    owner_id = get_generation_owner(request_id)
    current_user_id = user.get("id")
    if owner_id and current_user_id and owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к запуску другого пользователя")
    if not owner_id:
        raise HTTPException(status_code=403, detail="Владелец запуска не определен")


@router.get("/metrics/{request_id}")
async def get_metrics(
    request_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Получает метрики и отчеты для запроса генерации.
    
    Args:
        request_id: ID запроса генерации
        user: Данные пользователя
        
    Returns:
        Словарь с метриками и отчетами
    """
    # Проверяем статус генерации
    status = get_generation_status(request_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Запрос генерации не найден")
    _ensure_request_owner(request_id, user)

    # Получаем логи для этого запроса (доступны даже для незавершенных генераций)
    logs = _get_logs_by_request_id(request_id)

    def log_to_dict(log: LogEntry) -> dict:
        """Преобразует LogEntry в словарь."""
        return {
            "id": log.id,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "level": log.level,
            "message": log.message,
            "phase": log.phase,
            "metadata": log.meta_data  # Используем meta_data из модели
        }

    # Если генерация завершена, возвращаем полные данные
    if status == "completed":
        cached = get_result(request_id)
        if cached:
            return {
                "request_id": request_id,
                "status": status,
                "rubric": cached.get("rubric", {}),
                "text_stats": cached.get("report_json", {}).get("text_stats"),
                "logs": [log_to_dict(log) for log in logs],
                "metrics": {
                    "total_logs": len(logs),
                    "error_count": sum(1 for log in logs if log.level == "ERROR"),
                    "warning_count": sum(1 for log in logs if log.level == "WARNING"),
                }
            }

    # Для незавершенных генераций возвращаем только логи
    return {
        "request_id": request_id,
        "status": status,
        "logs": [log_to_dict(log) for log in logs],
        "metrics": {
            "total_logs": len(logs),
            "error_count": sum(1 for log in logs if log.level == "ERROR"),
            "warning_count": sum(1 for log in logs if log.level == "WARNING"),
        }
    }


@router.get("/rubric/{request_id}")
async def get_rubric(
    request_id: str,
    user: dict = Depends(get_current_user)
):
    """
    Получает rubric (оценку) для запроса генерации.
    
    Args:
        request_id: ID запроса генерации
        user: Данные пользователя
        
    Returns:
        Rubric данные
    """
    # Получаем результат из кэша
    cached = get_result(request_id)
    if not cached:
        raise HTTPException(status_code=404, detail="Результат генерации не найден или истек срок хранения")
    _ensure_request_owner(request_id, user)

    return {
        "request_id": request_id,
        "rubric": cached.get("rubric", {}),
        "regenerated_rubric": cached.get("regenerated", {}).get("rubric") if cached.get("regenerated") else None
    }

