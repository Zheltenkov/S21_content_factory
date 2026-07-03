"""Phase 5.2 — native FastAPI catalog-admin (GET pages + POST/PRG forms)."""

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
    con.commit()
    con.close()
    monkeypatch.setenv("SPRAVOCHNIK_SQLITE_PATH", str(db))
    monkeypatch.setenv("SPRAVOCHNIK_SUMMARY_PATH", str(tmp_path / "missing_summary.json"))
    monkeypatch.setenv("DISABLE_AUTH", "true")
    # intake/DAG working tables live in new_tables.sql; catalog-admin needs them
    from content_factory.catalog.viewer.app import ensure_intake_runtime_schema, open_db

    conn = open_db(db)
    ensure_intake_runtime_schema(conn, db)
    conn.close()
    from content_factory.api.main import app

    return TestClient(app)


@pytest.mark.parametrize(
    "sub",
    [
        "/catalog-admin/groups",
        "/catalog-admin/skillsets",
        "/catalog-admin/candidate-competencies",
        "/catalog-admin/archive",
        "/catalog-admin/artifact-templates",
    ],
)
def test_admin_get_pages_render(client: TestClient, sub: str) -> None:
    r = client.get(f"{_PREFIX}{sub}")
    assert r.status_code == 200
    # links carry the mount prefix
    assert f'{_PREFIX}/catalog-admin' in r.text


def test_admin_root_redirects_to_groups(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/catalog-admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"{_PREFIX}/catalog-admin/groups"


def test_group_create_then_appears_and_detail(client: TestClient) -> None:
    r = client.post(
        f"{_PREFIX}/catalog-admin/groups",
        data={"action": "create_group", "name": "Группа-Тест", "sort_order": "1", "status": "active"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"{_PREFIX}/catalog-admin/groups"

    listing = client.get(f"{_PREFIX}/catalog-admin/groups")
    assert "Группа-Тест" in listing.text


def test_create_skill_under_group(client: TestClient) -> None:
    client.post(
        f"{_PREFIX}/catalog-admin/groups",
        data={"action": "create_group", "name": "ग", "sort_order": "1"},
        follow_redirects=False,
    )
    # first (and only) group has id 1
    r = client.post(
        f"{_PREFIX}/catalog-admin/groups/1",
        data={"action": "create_skill", "name": "Навык-Тест", "sort_order": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"{_PREFIX}/catalog-admin/groups/1"
    detail = client.get(f"{_PREFIX}/catalog-admin/groups/1")
    assert "Навык-Тест" in detail.text


def test_missing_group_detail_404(client: TestClient) -> None:
    assert client.get(f"{_PREFIX}/catalog-admin/groups/9999").status_code == 404
