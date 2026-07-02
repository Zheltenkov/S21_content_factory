"""Контекст для логирования (request_id, user_id)."""

from contextvars import ContextVar

# Context variables для хранения контекста запроса
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("user_id", default=None)


def set_request_id(request_id: str) -> None:
    """
    Устанавливает request_id в контексте.
    
    Args:
        request_id: ID запроса
    """
    _request_id.set(request_id)


def get_request_id() -> str | None:
    """
    Получает request_id из контекста.
    
    Returns:
        ID запроса или None
    """
    return _request_id.get()


def set_user_id(user_id: str) -> None:
    """
    Устанавливает user_id в контексте.
    
    Args:
        user_id: ID пользователя
    """
    _user_id.set(user_id)


def get_user_id() -> str | None:
    """
    Получает user_id из контекста.
    
    Returns:
        ID пользователя или None
    """
    return _user_id.get()


def clear_context() -> None:
    """Очищает контекст (для тестирования)."""
    _request_id.set(None)
    _user_id.set(None)

