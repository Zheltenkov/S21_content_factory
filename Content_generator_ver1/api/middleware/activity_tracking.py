"""Middleware для отслеживания активности пользователей и обновления last_activity в сессиях."""

import os
import time
from collections.abc import Callable
from datetime import datetime

from fastapi import Request, Response
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware

from api.db.models import UserSession
from api.db.session import SessionLocal, is_database_available

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"

# Интервал обновления активности (в секундах) - обновляем не чаще чем раз в минуту
ACTIVITY_UPDATE_INTERVAL = int(os.getenv("ACTIVITY_UPDATE_INTERVAL_SECONDS", "60"))


async def extract_user_id_from_token(request: Request) -> str | None:
    """
    Извлекает user_id и session_token из JWT токена в заголовке Authorization.
    
    Args:
        request: FastAPI Request объект
        
    Returns:
        (user_id, session_token) или (None, None), если токен невалиден или отсутствует
    """
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.startswith("Bearer "):
        return None, None

    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        session_token = payload.get("session_token")
        return user_id, session_token
    except JWTError:
        return None, None


class ActivityTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware для автоматического обновления last_activity в сессиях пользователей."""

    def __init__(self, app, update_interval_seconds: int = ACTIVITY_UPDATE_INTERVAL):
        """
        Инициализация middleware.
        
        Args:
            app: FastAPI приложение
            update_interval_seconds: Интервал обновления активности в секундах
        """
        super().__init__(app)
        self.update_interval = update_interval_seconds
        self._last_update_time = {}  # Кэш последнего времени обновления по session_token

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Обрабатывает запрос и обновляет активность пользователя.
        
        Args:
            request: FastAPI Request объект
            call_next: Следующий middleware или endpoint
            
        Returns:
            Response объект
        """
        # Извлекаем user_id и session_token из токена
        user_id, session_token = await extract_user_id_from_token(request)

        # Обновляем активность, если есть валидный токен
        if user_id and session_token:
            current_time = time.time()
            last_update = self._last_update_time.get(session_token, 0)

            # Обновляем только если прошло достаточно времени
            if current_time - last_update >= self.update_interval:
                self._update_activity(session_token)
                self._last_update_time[session_token] = current_time

        # Выполняем запрос
        response = await call_next(request)
        return response

    def _update_activity(self, session_token: str) -> None:
        """
        Обновляет last_activity для сессии в БД.
        
        Args:
            session_token: Токен сессии
        """
        if is_database_available() is False:
            return

        db = SessionLocal()
        try:
            session = db.query(UserSession).filter(
                UserSession.session_token == session_token,
                UserSession.is_active == "true"
            ).first()

            if session:
                session.last_activity = datetime.utcnow()
                db.commit()
        except Exception as e:
            # Логируем ошибку, но не прерываем выполнение запроса
            import sys
            print(f"⚠️ Ошибка обновления активности: {e}", file=sys.stderr, flush=True)
            db.rollback()
        finally:
            db.close()
