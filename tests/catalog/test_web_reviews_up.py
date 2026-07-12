"""Phase 5.4 — native FastAPI reviews + curriculum-plan (УП) UI."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

_PREFIX = "/app/spravochnik"


@pytest.fixture()
def client(catalog_conn, tmp_path, monkeypatch) -> TestClient:
    # working tables (intake/DAG, curriculum_plan) already exist in the PG catalog schema
    monkeypatch.setenv("SPRAVOCHNIK_SUMMARY_PATH", str(tmp_path / "missing_summary.json"))
    monkeypatch.setenv("DISABLE_AUTH", "true")
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


def test_curriculum_builder_page_renders(client: TestClient) -> None:
    r = client.get("/app/curriculum")
    assert r.status_code == 200
    assert "Конструктор УП" in r.text
    assert "Загрузить бриф" in r.text
    assert 'id="curriculum-brief-form"' in r.text
    assert 'class="action-btn action-btn-primary builder-brief-submit"' in r.text
    assert 'action="/app/curriculum/briefs"' in r.text
    assert 'href="/app/spravochnik/intake"' not in r.text


def test_curriculum_builder_keeps_template_review_visible_for_existing_plan() -> None:
    template = (
        Path("src/content_factory/catalog/viewer/templates/up_builder.html")
        .read_text(encoding="utf-8")
    )

    assert "builder.snapshot.template_open > 0 or builder.snapshot.plan_row_count == 0" in template


def test_curriculum_builder_brief_post_stays_in_constructor(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from content_factory.api.routers import curriculum_builder

    queued_jobs: list[int] = []
    monkeypatch.setattr(curriculum_builder, "queue_intake_job", lambda _db_path, job_id: queued_jobs.append(job_id))

    r = client.post(
        "/app/curriculum/briefs",
        data={"brief": "Подготовить junior Python-разработчика для backend-проектов."},
        follow_redirects=False,
    )

    assert r.status_code == 303
    assert r.headers["location"].startswith("/app/curriculum?job_id=")
    assert queued_jobs


def test_curriculum_builder_reviews_candidate_without_leaving_constructor(
    client: TestClient,
    catalog_conn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from content_factory.api.routers import curriculum_builder
    from content_factory.catalog.viewer.intake_jobs import create_intake_job, update_intake_job

    brief_id = int(
        catalog_conn.execute(
            "INSERT INTO profile_brief(raw_text, role, seniority, domain) VALUES (?, ?, ?, ?)",
            ("brief", "Product manager", "junior", "product"),
        ).lastrowid
        or 0
    )
    suggestion_id = int(
        catalog_conn.execute(
            """
            INSERT INTO skill_suggestion(
                brief_id, suggested_name, source_name, group_name, bloom, resolution,
                decision, entity_type, atomicity, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brief_id,
                "Проведение интервью",
                "Провести интервью",
                "Research",
                "apply",
                "new",
                "needs_review",
                "skill",
                "atomic",
                0.82,
            ),
        ).lastrowid
        or 0
    )
    catalog_conn.commit()
    job_id = create_intake_job(
        catalog_conn,
        source_kind="text",
        source_name=None,
        file_path=None,
        brief_text="brief",
        use_council=False,
    )
    update_intake_job(
        catalog_conn,
        job_id,
        status="succeeded",
        result_payload={
            "brief_id": brief_id,
            "candidates": [
                {
                    "suggestion_id": suggestion_id,
                    "name": "Проведение интервью",
                    "group": "Research",
                    "bloom": "apply",
                    "entity_type": "skill",
                    "atomicity": "atomic",
                    "decision": "needs_review",
                }
            ],
        },
    )

    page = client.get(f"/app/curriculum?brief_id={brief_id}")
    assert page.status_code == 200
    assert 'id="skills-review"' in page.text
    assert f'action="/app/curriculum/jobs/{job_id}/candidate-decision"' in page.text

    response = client.post(
        f"/app/curriculum/jobs/{job_id}/candidate-decision",
        data={"suggestion_id": str(suggestion_id), "candidate_action": "accept"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/app/curriculum?brief_id={brief_id}#skills-review"
    row = catalog_conn.execute("SELECT decision FROM skill_suggestion WHERE id = ?", (suggestion_id,)).fetchone()
    assert row["decision"] == "accepted"

    updated_page = client.get(f"/app/curriculum?brief_id={brief_id}")
    assert updated_page.status_code == 200
    assert 'id="structure-transition"' in updated_page.text
    assert updated_page.text.count(f'action="/app/curriculum/jobs/{job_id}/apply-catalog"') == 1

    applied_briefs: list[int] = []
    monkeypatch.setattr(curriculum_builder, "apply_brief_catalog_decisions", lambda _conn, value: applied_briefs.append(value))
    apply_response = client.post(
        f"/app/curriculum/jobs/{job_id}/apply-catalog",
        follow_redirects=False,
    )
    assert apply_response.status_code == 303
    assert apply_response.headers["location"] == f"/app/curriculum?brief_id={brief_id}#structure-transition"
    assert applied_briefs == [brief_id]

    built_briefs: list[int] = []
    monkeypatch.setattr(curriculum_builder, "build_dag_for_brief", lambda _conn, value: built_briefs.append(value))
    dag_response = client.post(
        f"/app/curriculum/jobs/{job_id}/build-dag",
        follow_redirects=False,
    )
    assert dag_response.status_code == 303
    assert dag_response.headers["location"] == f"/app/curriculum?brief_id={brief_id}#structure-transition"
    assert built_briefs == [brief_id]


def test_curriculum_builder_accepts_templates_then_builds_plan_explicitly(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from content_factory.api.routers import curriculum_builder

    monkeypatch.setattr(curriculum_builder, "_require_builder_plan", lambda _conn, _plan_id: {"brief_id": 7})
    monkeypatch.setattr(curriculum_builder, "_require_builder_proposal", lambda _conn, _proposal_id, _brief_id: None)
    monkeypatch.setattr(curriculum_builder, "_require_template_readiness", lambda _conn, _brief_id: None)
    monkeypatch.setattr(curriculum_builder, "_require_design_readiness", lambda _conn, _brief_id: None)

    updates: list[int] = []
    accepts: list[int] = []
    builds: list[int] = []
    monkeypatch.setattr(
        curriculum_builder.intake_storage,
        "update_curriculum_artifact_template_proposal",
        lambda _conn, proposal_id, **_kwargs: updates.append(proposal_id),
    )
    monkeypatch.setattr(
        curriculum_builder.intake_storage,
        "accept_curriculum_artifact_template_proposal",
        lambda _conn, proposal_id: accepts.append(proposal_id),
    )

    proposal_response = client.post(
        "/app/curriculum/plans/30/template-proposals/12",
        data={
            "action": "accept_proposal",
            "title": "Отчёт",
            "artifact_family": "analysis",
            "scope_type": "coverage_area",
            "scope_names": "Customer discovery",
            "confidence": "0.9",
        },
        follow_redirects=False,
    )

    assert proposal_response.status_code == 303
    assert proposal_response.headers["location"] == "/app/curriculum?brief_id=7#template-review"
    assert updates == [12]
    assert accepts == [12]
    assert builds == []

    monkeypatch.setattr(
        curriculum_builder,
        "load_curriculum_builder_state",
        lambda _conn, **_kwargs: SimpleNamespace(
            snapshot=SimpleNamespace(dag_valid=True, template_open=0, template_accepted=1)
        ),
    )
    monkeypatch.setattr(
        curriculum_builder,
        "build_curriculum_plan_for_brief",
        lambda _conn, brief_id: builds.append(brief_id),
    )
    build_response = client.post(
        "/app/curriculum/briefs/7/build-plan",
        follow_redirects=False,
    )

    assert build_response.status_code == 303
    assert build_response.headers["location"] == "/app/curriculum?brief_id=7#plan-ready"
    assert builds == [7]


def test_curriculum_builder_approves_program_design_inside_constructor(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from content_factory.api.routers import curriculum_builder

    approved: list[int] = []
    monkeypatch.setattr(
        curriculum_builder,
        "approve_brief_curriculum_design",
        lambda _conn, brief_id: approved.append(brief_id) or {"approved": True},
    )

    response = client.post("/app/curriculum/briefs/7/design/approve", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/app/curriculum?brief_id=7#program-design"
    assert approved == [7]


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
    from content_factory.catalog.db import existing_columns, open_catalog_connection
    from content_factory.catalog.viewer.app import utc_now_iso

    conn = open_catalog_connection("unused-on-postgres")
    try:
        cols = set(existing_columns(conn, "curriculum_plan"))
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
    from content_factory.catalog.db import open_catalog_connection

    plan_id = _seed_plan(client)
    conn = open_catalog_connection("unused-on-postgres")
    try:
        conn.execute("UPDATE curriculum_plan SET status = 'invalid' WHERE id = ?", (plan_id,))
        conn.commit()
    finally:
        conn.close()
    r = client.get(f"{_PREFIX}/up/plans/{plan_id}/csv")
    assert r.status_code == 409
    assert "DAG order violations" in r.text
