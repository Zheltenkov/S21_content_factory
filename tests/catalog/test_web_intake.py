"""Compatibility endpoints for intake jobs after the UI moved to the UP constructor.

The LLM pipeline is never run: ``queue_intake_job`` is patched to a no-op so POST
/intake only creates + queues a job row. Job/status/CSV are exercised against rows
seeded directly, mirroring the legacy WSGI contract the inline polling JS depends on.
"""

from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

_PREFIX = "/app/spravochnik"


@pytest.fixture()
def client(catalog_conn, tmp_path, monkeypatch) -> TestClient:
    # working tables (intake/DAG) already exist in the PG catalog schema (alembic 016)
    monkeypatch.setenv("SPRAVOCHNIK_SUMMARY_PATH", str(tmp_path / "missing_summary.json"))
    monkeypatch.setenv("DISABLE_AUTH", "true")

    # never run the real pipeline (would call the LLM in a background thread)
    import content_factory.catalog.web.routers.intake as intake_router

    queued: list[int] = []
    monkeypatch.setattr(intake_router, "queue_intake_job", lambda _db, job_id: queued.append(job_id))

    from content_factory.api.main import app

    tc = TestClient(app)
    tc.queued = queued  # type: ignore[attr-defined]
    return tc


def test_intake_get_redirects_to_curriculum_builder(client: TestClient) -> None:
    r = client.get(f"{_PREFIX}/intake", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/app/curriculum"


def test_intake_post_text_brief_creates_and_queues_job(client: TestClient) -> None:
    r = client.post(
        f"{_PREFIX}/intake",
        data={"brief": "Нужен инженер данных, знающий SQL и Python."},
        follow_redirects=False,
    )
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/app/curriculum?job_id=")
    job_id = int(location.rsplit("=", 1)[-1])
    assert client.queued == [job_id]  # type: ignore[attr-defined]


def test_intake_post_empty_brief_is_400(client: TestClient) -> None:
    r = client.post(f"{_PREFIX}/intake", data={"brief": "   "}, follow_redirects=False)
    assert r.status_code == 400
    assert "Нужно вставить текст брифа" in r.text


def test_intake_post_file_upload_creates_job(client: TestClient) -> None:
    payload = "Разработчик бэкенда: Python, FastAPI, PostgreSQL.".encode()
    r = client.post(
        f"{_PREFIX}/intake",
        files={"brief_file": ("brief.txt", io.BytesIO(payload), "text/plain")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/app/curriculum?job_id=")


def _seed_job(client: TestClient, *, status: str = "queued", result_payload=None) -> int:
    from content_factory.catalog.db import open_catalog_connection
    from content_factory.catalog.viewer.app import create_intake_job

    conn = open_catalog_connection("unused-on-postgres")
    try:
        job_id = create_intake_job(
            conn,
            source_kind="text",
            source_name=None,
            file_path=None,
            brief_text="Тестовый бриф",
            use_council=False,
        )
        if status != "queued" or result_payload is not None:
            conn.execute(
                "UPDATE intake_job SET status = ?, result_payload = ? WHERE id = ?",
                (status, json.dumps(result_payload) if result_payload is not None else None, job_id),
            )
            conn.commit()
    finally:
        conn.close()
    return job_id


def test_status_json_contract(client: TestClient) -> None:
    job_id = _seed_job(client, status="queued")
    r = client.get(f"{_PREFIX}/intake/jobs/{job_id}/status")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == job_id
    assert set(body) >= {
        "id",
        "status",
        "status_label",
        "current_stage",
        "current_stage_label",
        "progress_note",
        "error_text",
        "finished_at",
    }


def test_status_missing_job_404(client: TestClient) -> None:
    assert client.get(f"{_PREFIX}/intake/jobs/999999/status").status_code == 404


def test_job_detail_redirects_to_curriculum_builder(client: TestClient) -> None:
    job_id = _seed_job(client, status="queued")
    r = client.get(f"{_PREFIX}/intake/jobs/{job_id}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/app/curriculum?job_id={job_id}"


def test_jobs_clear_redirects(client: TestClient) -> None:
    _seed_job(client, status="queued")
    r = client.post(f"{_PREFIX}/intake/jobs/clear", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/app/curriculum"


def test_plan_csv_without_plan_is_404(client: TestClient) -> None:
    job_id = _seed_job(client, status="succeeded", result_payload={"brief_id": 1})
    assert client.get(f"{_PREFIX}/intake/jobs/{job_id}/plan.csv").status_code == 404


def test_plan_csv_exports_rows(client: TestClient) -> None:
    plan = {
        "rows": [
            {"order": 1, "skill_name": "SQL", "complexity": "base"},
        ]
    }
    job_id = _seed_job(
        client,
        status="succeeded",
        result_payload={"brief_id": 7, "curriculum_plan": plan},
    )
    r = client.get(f"{_PREFIX}/intake/jobs/{job_id}/plan.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in r.headers["content-disposition"]
