"""Зависимости для FastAPI endpoints."""

import os
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from content_factory.api.db.models import UserSession
from content_factory.api.db.session import get_db_session

# JWT настройки
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
# HttpOnly cookie the browser sends automatically on same-origin API calls (set at
# login). Auth reads it as a fallback so the SPA never stores the token in JS.
AUTH_COOKIE_NAME = "content_gen_auth"

security = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """
    Проверяет JWT токен и возвращает данные пользователя.

    Токен берётся из заголовка ``Authorization: Bearer`` либо, если его нет, из
    HttpOnly-cookie ``content_gen_auth`` (её браузер шлёт сам для same-origin
    запросов). Так SPA не хранит токен в JS и не уязвим к его краже через XSS.

    Returns:
        Словарь с данными пользователя

    Raises:
        HTTPException: Если токен невалиден или отсутствует
    """
    # В режиме разработки можно отключить аутентификацию
    if os.getenv("DISABLE_AUTH", "false").lower() == "true":
        return {"id": "dev_user", "username": "dev"}

    # Prefer the Authorization header, but fall back to the HttpOnly cookie. A
    # blank/"null"/"undefined" bearer (e.g. a migrated client that dropped the
    # localStorage token) is treated as absent so the cookie still authenticates.
    token = credentials.credentials.strip() if credentials else ""
    if token.lower() in {"", "null", "undefined"}:
        token = request.cookies.get(AUTH_COOKIE_NAME, "").strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется аутентификация",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = jwt.decode(
            token,
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
        ) from None



