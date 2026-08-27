"""Cross-branch overview download route + the download button on the main auditor page."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from content_factory.api.routers import auditor
from content_factory.audit.branch import BranchProjectResult, BranchReport


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DISABLE_AUTH", "true")
    monkeypatch.setattr(auditor, "_BRANCHES_LOADED", True)  # skip disk scan; we seed in-memory
    app = FastAPI()
    app.include_router(auditor.page_router)
    return TestClient(app)


def _seed(monkeypatch: pytest.MonkeyPatch) -> None:
    report = BranchReport(
        projects=[
            BranchProjectResult(
                name="BSA00_Decomposition",
                status="completed",
                summary={"findings_total": 10, "by_severity": {"critical": 1, "major": 3}},
            )
        ],
        skipped=[],
    )
    monkeypatch.setitem(auditor._BRANCH_RUNS, "OVBR", report)
    monkeypatch.setitem(auditor._BRANCH_CREATED, "OVBR", {"archive_name": "BSA.zip", "created_at": "2026-08-20T10:00:00"})


def test_overview_download_returns_self_contained_html_attachment(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    _seed(monkeypatch)

    resp = client.get("/app/auditor/overview")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "attachment" in resp.headers["content-disposition"]
    assert "audit_overview_" in resp.headers["content-disposition"]
    body = resp.text
    assert body.strip().startswith("<!DOCTYPE html")
    assert "веток аудировано" in body  # banner
    assert "BSA.zip" in body  # the branch is listed
    assert "на живых" in body  # fix-first metric


def test_recent_branches_panel_has_overview_download_button(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(monkeypatch)
    html = auditor._render_recent_branches()
    assert '/app/auditor/overview' in html
    assert "Скачать обзор всех аудитов" in html
