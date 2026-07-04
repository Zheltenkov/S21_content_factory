"""Phase 5.1 — native FastAPI catalog UI (read-only pages).

Exercises the ported routes through the real app with a temporary catalog SQLite,
asserting they render and that links carry the /app/spravochnik prefix (the base
global replacing the old PrefixRewrite hack).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[2]
_CATALOG_SCHEMA = _REPO / "src" / "content_factory" / "catalog" / "sql" / "catalog_schema.sql"
_PREFIX = "/app/spravochnik"


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    db = tmp_path / "catalog.sqlite"
    con = sqlite3.connect(db)
    con.executescript(_CATALOG_SCHEMA.read_text(encoding="utf-8"))
    # one profile + competency so detail pages have something to resolve
    con.execute("INSERT INTO profile (id, slug, name, source_kind) VALUES (1, 'be', 'Backend', 'role_profile')")
    con.execute("INSERT INTO competency (id, normalized_title, title, status) VALUES (1, 'algorithms', 'Алгоритмы', 'active')")
    con.commit()
    con.close()
    monkeypatch.setenv("SPRAVOCHNIK_SQLITE_PATH", str(db))
    monkeypatch.setenv("SPRAVOCHNIK_SUMMARY_PATH", str(tmp_path / "missing_summary.json"))
    monkeypatch.setenv("DISABLE_AUTH", "true")  # browser-tool paths are behind the shared auth cookie
    from content_factory.api.main import app

    return TestClient(app)


def test_competencies_page_renders_with_prefixed_links(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/competencies")
    assert r.status_code == 200
    assert "Справочник" in r.text
    # nav + static + search form must carry the mount prefix (base global)
    assert f'href="{_PREFIX}/static/styles.css?v=' in r.text
    assert f'action="{_PREFIX}/competencies"' in r.text


def test_profiles_page_renders(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/profiles")
    assert r.status_code == 200
    assert "Профили" in r.text
    assert f'href="{_PREFIX}/profiles/1"' in r.text


def test_profile_detail_page(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/profiles/1")
    assert r.status_code == 200
    assert "Backend" in r.text


def test_competency_detail_page(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/competencies/1")
    assert r.status_code == 200
    assert "Алгоритмы" in r.text


def test_missing_profile_returns_404(client: TestClient) -> None:
    assert client.get(f"{_PREFIX}/profiles/999").status_code == 404


def test_non_int_competency_id_is_rejected(client: TestClient) -> None:
    # int path converter rejects a non-numeric id (422), never renders a page
    assert client.get(f"{_PREFIX}/competencies/not-a-number").status_code == 422
