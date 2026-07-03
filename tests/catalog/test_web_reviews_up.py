"""Phase 5.4 — native FastAPI reviews + curriculum-plan (УП) UI."""

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

    from content_factory.catalog.viewer.app import ensure_intake_runtime_schema, open_db

    conn = open_db(db)
    ensure_intake_runtime_schema(conn, db)
    conn.close()

    from content_factory.api.main import app

    return TestClient(app)


# --------------------------------------------------------------------------- #
# reviews
# --------------------------------------------------------------------------- #
def test_reviews_get_renders(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/reviews")
    assert r.status_code == 200
    assert f'action="{_PREFIX}/reviews"' in r.text


def test_reviews_get_with_filters(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/reviews?status=resolved&severity=high&reason=all&entity_type=all")
    assert r.status_code == 200


def test_reviews_post_invalid_status_404(client: TestClient) -> None:
    r = client.post(
        f"{_PREFIX}/reviews",
        data={"review_id": "1", "new_status": "bogus"},
        follow_redirects=False,
    )
    assert r.status_code == 404


def test_reviews_post_valid_redirects_with_filters(client: TestClient) -> None:
    # no review row with id 1 — update_review_status is a no-op, but PRG still applies
    r = client.post(
        f"{_PREFIX}/reviews",
        data={"review_id": "1", "new_status": "resolved", "severity": "high"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"{_PREFIX}/reviews?status=open&severity=high"


# --------------------------------------------------------------------------- #
# up (curriculum plans)
# --------------------------------------------------------------------------- #
def test_up_index_renders(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/up")
    assert r.status_code == 200
    assert f'action="{_PREFIX}/up/cleanup-empty"' in r.text


def test_up_cleanup_empty_redirects(client: TestClient) -> None:
    r = client.post(f"{_PREFIX}/up/cleanup-empty", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"{_PREFIX}/up"


def test_up_missing_plan_detail_404(client: TestClient) -> None:
    assert client.get(f"{_PREFIX}/up/plans/999999").status_code == 404


def test_up_missing_plan_csv_404(client: TestClient) -> None:
    assert client.get(f"{_PREFIX}/up/plans/999999/csv").status_code == 404


def test_up_delete_missing_plan_redirects(client: TestClient) -> None:
    # delete is idempotent: missing plan still lands back on the index
    r = client.post(f"{_PREFIX}/up/plans/999999/delete", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"{_PREFIX}/up"


def test_up_row_new_on_missing_plan_404(client: TestClient) -> None:
    assert client.post(f"{_PREFIX}/up/plans/999999/rows/new").status_code == 404


def _seed_plan(client: TestClient) -> int:
    """Insert a minimal curriculum_plan row and return its id."""
    from content_factory.api.integrations.project_paths import spravochnik_sqlite_path
    from content_factory.catalog.viewer.app import open_db, utc_now_iso

    conn = open_db(spravochnik_sqlite_path())
    try:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(curriculum_plan)")}
        now = utc_now_iso()
        payload: dict[str, object] = {}
        if "status" in cols:
            payload["status"] = "built"
        if "created_at" in cols:
            payload["created_at"] = now
        if "updated_at" in cols:
            payload["updated_at"] = now
        keys = ", ".join(payload)
        placeholders = ", ".join("?" for _ in payload)
        if keys:
            cur = conn.execute(f"INSERT INTO curriculum_plan({keys}) VALUES ({placeholders})", tuple(payload.values()))
        else:
            cur = conn.execute("INSERT INTO curriculum_plan DEFAULT VALUES")
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def test_up_plan_detail_renders(client: TestClient) -> None:
    plan_id = _seed_plan(client)
    r = client.get(f"{_PREFIX}/up/plans/{plan_id}")
    assert r.status_code == 200
    assert f"УП #{plan_id}" in r.text


def test_up_plan_csv_invalid_status_409(client: TestClient) -> None:
    from content_factory.api.integrations.project_paths import spravochnik_sqlite_path
    from content_factory.catalog.viewer.app import open_db

    plan_id = _seed_plan(client)
    conn = open_db(spravochnik_sqlite_path())
    try:
        conn.execute("UPDATE curriculum_plan SET status = 'invalid' WHERE id = ?", (plan_id,))
        conn.commit()
    finally:
        conn.close()
    r = client.get(f"{_PREFIX}/up/plans/{plan_id}/csv")
    assert r.status_code == 409
    assert "DAG order violations" in r.text
