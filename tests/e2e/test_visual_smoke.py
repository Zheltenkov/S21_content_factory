"""Playwright visual E2E smoke over the server-rendered pages.

For every curated route: navigate with a real browser, assert the main document
did not 5xx and no sub-request 5xx'd during load, and write a full-page
screenshot to ``tests/e2e/screenshots/``. This catches request-time regressions
that import-time green tests miss (e.g. a dropped re-export that 500s a
server-rendered catalog fragment only when actually rendered).

Run:  RUN_E2E=1 python -m pytest tests/e2e -m e2e
(needs the local Docker Postgres up; the app boots with DISABLE_AUTH=true.)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page

pytestmark = pytest.mark.e2e

SCREENSHOT_DIR = Path(__file__).resolve().parent / "screenshots"

# Server-rendered catalog pages + auditor — these hit Postgres at request time.
CATALOG_PAGES = [
    "/app/spravochnik/competencies",
    "/app/spravochnik/competencies/1",
    "/app/spravochnik/profiles",
    "/app/spravochnik/reviews",
    "/app/spravochnik/up",
    "/app/spravochnik/intake",
    "/app/spravochnik/catalog-admin",
    "/app/spravochnik/catalog-admin/candidate-competencies",
    "/app/spravochnik/catalog-admin/archive",
    "/app/spravochnik/catalog-admin/artifact-templates",
    "/app/spravochnik/catalog-admin/skillsets",
    "/app/spravochnik/catalog-admin/groups",
    "/app/auditor",
]

# Static SPA shells (FileResponse + client-side JS render).
SPA_PAGES = [
    "/",
    "/app",
    "/app/generate",
    "/app/curriculum",
    "/app/learning-projects",
    "/app/translate",
]

ALL_PAGES = CATALOG_PAGES + SPA_PAGES


def _slug(path: str) -> str:
    cleaned = path.strip("/").replace("/", "_") or "root"
    return cleaned


@pytest.mark.parametrize("path", ALL_PAGES, ids=[_slug(p) for p in ALL_PAGES])
def test_page_renders_without_server_error(page: Page, live_server: str, path: str) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    server_errors: list[str] = []
    page.on(
        "response",
        lambda response: server_errors.append(f"{response.status} {response.url}")
        if response.status >= 500
        else None,
    )

    response = page.goto(f"{live_server}{path}", wait_until="networkidle", timeout=30_000)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{_slug(path)}.png"), full_page=True)

    assert response is not None, f"no response object for {path}"
    assert response.status < 500, f"{path} returned {response.status}"
    assert not server_errors, f"{path} triggered 5xx sub-requests: {server_errors}"
