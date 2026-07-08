"""Cookie bridge for browser navigation into mounted tools."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request, Response
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from content_factory.api.db.session import SessionLocal
from content_factory.api.dependencies import AUTH_COOKIE_NAME, get_current_user

# HTTP methods that mutate server state; admin-gated on catalog write prefixes.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


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
        return {"id": "dev_user", "username": "dev", "role": "admin"}
    token = request_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Требуется аутентификация")
    owns_session = db is None
    session = db or SessionLocal()
    try:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        return await get_current_user(request=request, credentials=credentials, db=session)
    finally:
        if owns_session:
            session.close()


def _path_matches(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)


class ToolAuthCookieMiddleware(BaseHTTPMiddleware):
    """Require generator login before mounted browser-only tools are served.

    ``admin_write_prefixes`` additionally gate *mutating* requests (POST/PUT/PATCH/
    DELETE) under those prefixes behind the ``admin`` role, so a merely-logged-in
    user cannot change catalog data / templates / review decisions. Read (GET/HEAD)
    stays open to any authenticated user. ``DISABLE_AUTH`` resolves to a dev admin.
    """

    def __init__(
        self,
        app: ASGIApp,
        protected_prefixes: tuple[str, ...],
        admin_write_prefixes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(app)
        self.protected_prefixes = protected_prefixes
        self.admin_write_prefixes = admin_write_prefixes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if _path_matches(path, self.protected_prefixes):
            try:
                user = await validate_request_user(request)
            except HTTPException:
                # Preserve the requested page so login can send the user back to it.
                return RedirectResponse(f"/?next={quote(path, safe='/')}", status_code=303)
            if (
                request.method in _MUTATING_METHODS
                and _path_matches(path, self.admin_write_prefixes)
                and user.get("role") != "admin"
            ):
                return PlainTextResponse(
                    "Недостаточно прав: изменение каталога доступно только администратору.",
                    status_code=403,
                )
        return await call_next(request)

