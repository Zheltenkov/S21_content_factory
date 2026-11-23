"""Production session verification through the existing application's auth API."""

from contextvars import ContextVar

import httpx
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp

current_owner: ContextVar[str] = ContextVar("project_lab_owner", default="local")


class SessionIdentity(BaseModel):
    id: str = Field(min_length=1, max_length=200)


class SessionGuard(BaseHTTPMiddleware):
    """Validate active sessions on every request; fail closed if auth is unavailable."""

    def __init__(self, app: ASGIApp, auth_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__(app)
        self.auth_url = auth_url
        self.transport = transport

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        token = request.cookies.get("content_gen_auth", "")
        authorization = request.headers.get("authorization", "")
        if not token and not authorization:
            return self.unauthorized(request)
        # Forward only credentials to the configured internal auth endpoint.
        headers = {"Authorization": authorization} if authorization else {}
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False, transport=self.transport) as client:
                result = await client.get(self.auth_url, headers=headers, cookies={"content_gen_auth": token})
            if result.status_code in {401, 403}:
                return self.unauthorized(request)
            result.raise_for_status()
            identity = SessionIdentity.model_validate(result.json())
        except (httpx.HTTPError, ValidationError, ValueError):
            return JSONResponse({"detail": "Проверка входа временно недоступна. Повторите попытку."}, status_code=503)
        owner_context = current_owner.set(identity.id)
        try:
            response = await call_next(request)
            response.headers["Cache-Control"] = "private, no-store"
            return response
        finally:
            current_owner.reset(owner_context)

    @staticmethod
    def unauthorized(request: Request) -> Response:
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/", status_code=303)
        return JSONResponse({"detail": "Войдите в систему, чтобы открыть подготовку проекта."}, status_code=401)
