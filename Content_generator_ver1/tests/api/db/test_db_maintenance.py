from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db.logging_db import cleanup_old_logs
from api.db.maintenance_db import cleanup_old_runtime_state
from api.db.models import (
    Base,
    GenerationWorkflowCheckpoint,
    GenerationWorkflowState,
    LogEntry,
    PausedGenerationSession,
    RequestLog,
)


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_cleanup_old_logs_deletes_in_batches_without_touching_fresh_rows() -> None:
    db = _session()
    old = _utc_now() - timedelta(days=10)
    fresh = _utc_now()
    db.add_all(
        [
            LogEntry(request_id="old-log", level="INFO", message="old", timestamp=old),
            LogEntry(request_id="fresh-log", level="INFO", message="fresh", timestamp=fresh),
            RequestLog(request_id="old-req", method="GET", path="/old", status_code=200, timestamp=old),
            RequestLog(request_id="fresh-req", method="GET", path="/fresh", status_code=200, timestamp=fresh),
        ]
    )
    db.commit()

    deleted = cleanup_old_logs(db, days_to_keep=7, batch_size=1)

    assert deleted == 2
    assert db.query(LogEntry).count() == 1
    assert db.query(RequestLog).count() == 1


def test_cleanup_old_runtime_state_keeps_active_reviews_and_deletes_terminal_payloads() -> None:
    db = _session()
    old = _utc_now() - timedelta(days=30)
    fresh = _utc_now()
    db.add_all(
        [
            GenerationWorkflowState(
                request_id="old-completed",
                user_id="u1",
                status="completed",
                updated_at=old,
                created_at=old,
            ),
            GenerationWorkflowCheckpoint(
                request_id="old-completed",
                checkpoint_index=1,
                node_id="theory",
                node_name="Theory",
                status="success",
                input_hash="h",
                created_at=old,
            ),
            GenerationWorkflowState(
                request_id="old-review",
                user_id="u1",
                status="needs_review",
                updated_at=old,
                created_at=old,
            ),
            PausedGenerationSession(
                request_id="old-paused-completed",
                user_id="u1",
                status="completed",
                context_payload={"large": "payload"},
                updated_at=old,
                created_at=old,
            ),
            PausedGenerationSession(
                request_id="old-paused-review",
                user_id="u1",
                status="needs_review",
                context_payload={"must": "stay"},
                updated_at=old,
                created_at=old,
            ),
            PausedGenerationSession(
                request_id="fresh-paused-completed",
                user_id="u1",
                status="completed",
                context_payload={"fresh": True},
                updated_at=fresh,
                created_at=fresh,
            ),
        ]
    )
    db.commit()

    deleted = cleanup_old_runtime_state(
        db,
        workflow_days_to_keep=14,
        paused_days_to_keep=14,
        batch_size=1,
    )

    assert deleted == {
        "generation_workflow_states": 1,
        "generation_workflow_checkpoints": 1,
        "paused_generation_sessions": 1,
    }
    assert db.query(GenerationWorkflowState).filter_by(request_id="old-review").one()
    assert db.query(PausedGenerationSession).filter_by(request_id="old-paused-review").one()
    assert db.query(PausedGenerationSession).filter_by(request_id="fresh-paused-completed").one()
