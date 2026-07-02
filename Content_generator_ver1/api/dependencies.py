"""Зависимости для FastAPI endpoints."""

import os
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from api.db.models import UserSession
from api.db.session import get_db_session

# JWT настройки
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Проверяет JWT токен и возвращает данные пользователя.
    
    Args:
        credentials: JWT токен из заголовка Authorization
        
    Returns:
        Словарь с данными пользователя
        
    Raises:
        HTTPException: Если токен невалиден или отсутствует
    """
    # В режиме разработки можно отключить аутентификацию
    if os.getenv("DISABLE_AUTH", "false").lower() == "true":
        return {"id": "dev_user", "username": "dev"}

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется аутентификация",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM]
        )
        user_id: str = payload.get("sub")
        session_token: str | None = payload.get("session_token")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Невалидный токен"
            )
        if not session_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Сессия не найдена или завершена",
            )

        active_session = (
            db.query(UserSession)
            .filter(
                UserSession.user_id == user_id,
                UserSession.session_token == session_token,
                UserSession.is_active == "true",
            )
            .first()
        )
        if active_session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Сессия не найдена или завершена",
            )
        if active_session.user and not active_session.user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Аккаунт деактивирован",
            )
        return {
            "id": user_id,
            "username": payload.get("username", user_id),
            "email": payload.get("email"),
            "role": payload.get("role"),
            "session_token": session_token,
        }
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен"
        )



