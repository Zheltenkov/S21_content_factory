"""Cookie bridge for browser navigation into mounted tools."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from content_factory.api.db.session import SessionLocal
from content_factory.api.dependencies import get_current_user

AUTH_COOKIE_NAME = "content_gen_auth"


def _cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}


def set_auth_cookie(response: Response, token: str) -> None:
    """Store the JWT in an HttpOnly cookie for normal page navigation."""

    max_age = int(os.getenv("JWT_EXPIRATION_HOURS", "24")) * 60 * 60
    response.set_cookie(
        AUTH_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """Clear the navigation auth cookie on logout."""

    response.delete_cookie(AUTH_COOKIE_NAME, path="/", samesite="lax", secure=_cookie_secure())


def request_token(request: Request) -> str:
    """Resolve a bearer token from Authorization or the navigation cookie."""

    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get(AUTH_COOKIE_NAME, "").strip()


async def validate_request_user(request: Request, db: Session | None = None) -> dict[str, Any]:
    """Validate a request using the existing generator auth dependency contract."""

    if os.getenv("DISABLE_AUTH", "false").lower() == "true":
        return {"id": "dev_user", "username": "dev"}
    token = request_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Требуется аутентификация")
    owns_session = db is None
    session = db or SessionLocal()
    try:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        return await get_current_user(credentials=credentials, db=session)
    finally:
        if owns_session:
            session.close()


class ToolAuthCookieMiddleware(BaseHTTPMiddleware):
    """Require generator login before mounted browser-only tools are served."""

    def __init__(self, app, protected_prefixes: tuple[str, ...]) -> None:
        super().__init__(app)
        self.protected_prefixes = protected_prefixes

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if any(path == prefix or path.startswith(f"{prefix}/") for prefix in self.protected_prefixes):
            try:
                await validate_request_user(request)
            except HTTPException:
                return RedirectResponse("/", status_code=303)
        return await call_next(request)

