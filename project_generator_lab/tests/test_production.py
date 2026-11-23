"""Production boundaries: active sessions, ownership, and a mounted URL prefix."""

from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.concept import runs as api_module
from project_generator_lab.concept.models import Brief, Run, StartRequest
from project_generator_lab.concept.planning import fingerprint
from project_generator_lab.core.access import SessionGuard

PREFIX = "/app/project-preparation"


def wrapped_app(transport: httpx.MockTransport) -> FastAPI:
    wrapper = FastAPI()
    wrapper.add_middleware(SessionGuard, auth_url="http://main/api/v1/auth/me", transport=transport)
    wrapper.mount(PREFIX, app_module.app)
    return wrapper


def auth_response(request: httpx.Request) -> httpx.Response:
    assert request.url == "http://main/api/v1/auth/me"
    cookie = request.headers.get("cookie", "")
    if "owner-a" in cookie:
        return httpx.Response(200, json={"id": "owner-a"})
    if "owner-b" in cookie:
        return httpx.Response(200, json={"id": "owner-b"})
    return httpx.Response(401)


def test_auth_is_required_for_pages_files_uploads_and_runs():
    with TestClient(wrapped_app(httpx.MockTransport(auth_response))) as client:
        for path in ("/api/config", "/static/workbench.js", "/api/runs/" + str(uuid4())):
            assert client.get(PREFIX + path).status_code == 401
        assert client.post(PREFIX + "/api/curriculum").status_code == 401
        response = client.get(PREFIX + "/", headers={"Accept": "text/html"}, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"
        client.cookies.set("content_gen_auth", "expired")
        assert client.get(PREFIX + "/api/config").status_code == 401


def test_auth_outage_and_malformed_identity_fail_closed():
    for response in (httpx.Response(503), httpx.Response(200, json={"username": "missing-id"})):
        def handler(request: httpx.Request, response: httpx.Response = response) -> httpx.Response:
            return response
        with TestClient(wrapped_app(httpx.MockTransport(handler))) as client:
            client.cookies.set("content_gen_auth", "owner-a")
            assert client.get(PREFIX + "/api/config").status_code == 503


def test_prefix_uploads_and_account_scoped_browser_drafts():
    with TestClient(wrapped_app(httpx.MockTransport(auth_response))) as client:
        client.cookies.set("content_gen_auth", "owner-a")
        config_a = client.get(PREFIX + "/api/config")
        assert config_a.headers["cache-control"] == "private, no-store"
        assert config_a.json()["home_url"] == "/app"
        page = client.get(PREFIX + "/")
        assert 'src="static/chat.js' in page.text
        # The previous interface stays reachable under the same prefix and authorization.
        assert 'src="static/workbench.js' in client.get(PREFIX + "/classic").text
        assert client.get(PREFIX + "/static/workbench.js").status_code == 200
        response = client.post(PREFIX + "/api/materials", headers={"Origin": "http://testserver"},
                               files={"file": ("notes.md", b"reference text")})
        assert response.status_code == 200
        assert client.post(PREFIX + "/api/materials", headers={"Origin": "https://evil.example"}).status_code == 403
        client.cookies.set("content_gen_auth", "owner-b")
        assert client.get(PREFIX + "/api/config").json()["storage_namespace"] != config_a.json()["storage_namespace"]


def test_run_read_export_approve_and_reuse_enforce_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RUNS_DIR", tmp_path)
    request = StartRequest(request_id=uuid4(), brief=Brief(
        title="Тест", description="Разобрать исходные данные", audience="Начинающие", outcomes="Анализировать данные",
    ))
    run = Run(id=str(request.request_id), owner_id="owner-a", fingerprint=fingerprint(request), request=request, status="review")
    api_module.save_run(run)
    with TestClient(wrapped_app(httpx.MockTransport(auth_response))) as client:
        client.cookies.set("content_gen_auth", "owner-a")
        assert client.get(f"{PREFIX}/api/runs/{run.id}").status_code == 200
        assert client.post(PREFIX + "/api/runs", json=request.model_dump(mode="json")).status_code == 202
        client.cookies.set("content_gen_auth", "owner-b")
        assert client.get(f"{PREFIX}/api/runs/{run.id}").status_code == 404
        assert client.get(f"{PREFIX}/api/runs/{run.id}/export/json").status_code == 404
        assert client.post(f"{PREFIX}/api/runs/{run.id}/approve").status_code == 404
        assert client.post(PREFIX + "/api/runs", json=request.model_dump(mode="json")).status_code == 404
