from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import generation_workflow_db
from api.db.models import Base, GenerationWorkflowState


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_record_checkpoint_updates_existing_row_and_increments_retry_count(monkeypatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(generation_workflow_db, "SessionLocal", session_factory)

    first = generation_workflow_db.record_generation_workflow_checkpoint(
        request_id="req-1",
        user_id="u1",
        node_id="theory",
        node_name="Theory",
        status="success",
        input_hash="h1",
        output_artifact={"markdown": "# Old"},
        context_snapshot={"markdown": "# Old"},
        checkpoint_index=4,
    )
    second = generation_workflow_db.record_generation_workflow_checkpoint(
        request_id="req-1",
        user_id="u1",
        node_id="theory",
        node_name="Theory",
        status="success",
        input_hash="h2",
        output_artifact={"markdown": "# New"},
        context_snapshot={"markdown": "# New"},
        checkpoint_index=4,
    )

    assert first is not None
    assert first["retry_count"] == 0
    assert second is not None
    assert second["retry_count"] == 1
    assert second["input_hash"] == "h2"
    assert second["output_artifact"]["markdown"] == "# New"
    assert second["context_snapshot"]["markdown"] == "# New"


def test_mark_interrupted_generation_workflows_keeps_review_sessions_active(monkeypatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(generation_workflow_db, "SessionLocal", session_factory)
    db = session_factory()
    db.add_all(
        [
            GenerationWorkflowState(request_id="running", user_id="u1", status="running"),
            GenerationWorkflowState(request_id="review", user_id="u1", status="needs_review"),
        ]
    )
    db.commit()
    db.close()

    interrupted = generation_workflow_db.mark_interrupted_generation_workflows(error="server restarted")

    assert [item["request_id"] for item in interrupted] == ["running"]
    db = session_factory()
    try:
        running = db.query(GenerationWorkflowState).filter_by(request_id="running").one()
        review = db.query(GenerationWorkflowState).filter_by(request_id="review").one()
        assert running.status == "interrupted"
        assert running.error == "server restarted"
        assert running.meta_data["interrupted_from_status"] == "running"
        assert review.status == "needs_review"
    finally:
        db.close()
