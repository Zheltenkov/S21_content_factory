from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from content_factory.api.db import curriculum_project_runs_db
from content_factory.api.db.models import Base, CurriculumProjectGenerationRun


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _origin() -> dict[str, object]:
    return {
        "source_plan_id": 10,
        "plan_version": "v1:abcdef123456",
        "plan_hash": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        "pipeline_run_id": "pipe_test_1",
        "plan_row_id": 100,
        "row_hash": "rowhash",
        "block_index": 1,
        "row_number": 1,
        "project_index": 1,
        "project_order": 1,
        "project_title": "Анализ датасета",
    }


def test_curriculum_project_snapshot_and_run_lifecycle(monkeypatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(curriculum_project_runs_db, "SessionLocal", session_factory)
    origin = _origin()
    context = {
        "block_name": "Блок 1. Данные",
        "curriculum_origin": origin,
    }
    seed = {
        "title_seed": "Анализ датасета",
        "pipeline_run_id": origin["pipeline_run_id"],
        "curriculum_context": context,
        "curriculum_origin": origin,
    }

    snapshot = curriculum_project_runs_db.record_curriculum_project_snapshot(
        user_id="methodologist",
        context_payload=context,
        readiness={"ready": True, "blockers": []},
    )
    run = curriculum_project_runs_db.record_curriculum_project_generation_run(
        request_id="req-1",
        user_id="methodologist",
        seed_payload=seed,
    )
    completed = curriculum_project_runs_db.mark_curriculum_project_generation_run(
        request_id="req-1",
        status="completed",
        stage="completed",
        result_url="/api/v1/download/req-1",
        score={"total": 9, "max": 10, "label": "9/10"},
    )
    rows = curriculum_project_runs_db.list_curriculum_project_runs_for_plan(source_plan_id=10, user_id="methodologist")

    assert snapshot is not None
    assert snapshot["pipeline_run_id"] == "pipe_test_1"
    assert snapshot["plan_row_id"] == 100
    assert run is not None
    assert run["request_id"] == "req-1"
    assert run["snapshot_id"] == snapshot["snapshot_id"]
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None
    assert rows[0]["request_id"] == "req-1"
    assert rows[0]["score"]["label"] == "9/10"

    db = session_factory()
    try:
        assert db.query(CurriculumProjectGenerationRun).count() == 1
    finally:
        db.close()


def test_curriculum_project_run_helpers_ignore_manual_seed(monkeypatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(curriculum_project_runs_db, "SessionLocal", session_factory)

    row = curriculum_project_runs_db.record_curriculum_project_generation_run(
        request_id="manual",
        user_id="u1",
        seed_payload={"title_seed": "Manual project"},
    )

    assert row is None
