"""RBAC guard for catalog write routes (ToolAuthCookieMiddleware.admin_write_prefixes).

Mutating requests under /app/spravochnik require the admin role; reads stay open to
any authenticated user; unauthenticated requests redirect to login; DISABLE_AUTH
resolves to a dev admin.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from content_factory.api.integrations import auth_cookie
from content_factory.api.integrations.auth_cookie import ToolAuthCookieMiddleware


def _make_client(monkeypatch: pytest.MonkeyPatch, role: str | None) -> TestClient:
    async def fake_validate(request, db=None):  # type: ignore[no-untyped-def]
        if role is None:
            raise HTTPException(status_code=401, detail="Требуется аутентификация")
        return {"id": "u", "username": "u", "role": role}

    monkeypatch.setattr(auth_cookie, "validate_request_user", fake_validate)

    app = FastAPI()
    app.add_middleware(
        ToolAuthCookieMiddleware,
        protected_prefixes=("/app/spravochnik",),
        admin_write_prefixes=("/app/spravochnik",),
    )

    @app.get("/app/spravochnik/intake")
    def _read() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.post("/app/spravochnik/intake")
    def _write() -> PlainTextResponse:
        return PlainTextResponse("written")

    return TestClient(app)


def test_admin_can_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, "admin")
    assert client.post("/app/spravochnik/intake").status_code == 200


def test_plain_user_cannot_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, "user")
    resp = client.post("/app/spravochnik/intake")
    assert resp.status_code == 403


def test_plain_user_can_read(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, "user")
    assert client.get("/app/spravochnik/intake").status_code == 200


def test_unauthenticated_mutation_redirects_to_login(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client(monkeypatch, None)
    resp = client.post("/app/spravochnik/intake", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/?next=")


def test_disable_auth_resolves_to_dev_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISABLE_AUTH", "true")
    # use the real validate_request_user (dev short-circuit), not the fake
    app = FastAPI()
    app.add_middleware(
        ToolAuthCookieMiddleware,
        protected_prefixes=("/app/spravochnik",),
        admin_write_prefixes=("/app/spravochnik",),
    )

    @app.post("/app/spravochnik/intake")
    def _write() -> PlainTextResponse:
        return PlainTextResponse("written")

    client = TestClient(app)
    assert client.post("/app/spravochnik/intake").status_code == 200
