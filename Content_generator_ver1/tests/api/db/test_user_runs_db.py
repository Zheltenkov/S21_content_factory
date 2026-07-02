from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.db import user_runs_db
from api.db.models import Base, UserRun


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_reconcile_stale_active_user_runs_marks_old_runs_terminal(monkeypatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(user_runs_db, "SessionLocal", session_factory)
    now = datetime(2026, 5, 15, 12, 0, 0)
    old = now - timedelta(hours=25)
    fresh = now - timedelta(minutes=30)

    db = session_factory()
    db.add_all(
        [
            UserRun(
                request_id="old-generation",
                user_id="u1",
                kind="generation",
                status="resuming",
                title="Old generation",
                created_at=old,
                updated_at=old,
            ),
            UserRun(
                request_id="old-translation",
                user_id="u1",
                kind="translation",
                status="in_progress",
                title="Old translation",
                created_at=old,
                updated_at=old,
            ),
            UserRun(
                request_id="fresh-generation",
                user_id="u1",
                kind="generation",
                status="in_progress",
                title="Fresh generation",
                created_at=fresh,
                updated_at=fresh,
            ),
            UserRun(
                request_id="review-generation",
                user_id="u1",
                kind="generation",
                status="needs_review",
                title="Human review",
                created_at=old,
                updated_at=old,
            ),
            UserRun(
                request_id="other-user",
                user_id="u2",
                kind="generation",
                status="resuming",
                title="Other user",
                created_at=old,
                updated_at=old,
            ),
        ]
    )
    db.commit()
    db.close()

    reconciled = user_runs_db.reconcile_stale_active_user_runs(
        user_id="u1",
        stale_after_seconds=24 * 60 * 60,
        now=now,
    )

    assert {item["request_id"] for item in reconciled} == {"old-generation", "old-translation"}
    db = session_factory()
    try:
        statuses = {
            row.request_id: row.status
            for row in db.query(UserRun).order_by(UserRun.request_id).all()
        }
        assert statuses["old-generation"] == "interrupted"
        assert statuses["old-translation"] == "failed"
        assert statuses["fresh-generation"] == "in_progress"
        assert statuses["review-generation"] == "needs_review"
        assert statuses["other-user"] == "resuming"

        old_generation = db.query(UserRun).filter_by(request_id="old-generation").one()
        assert old_generation.meta_data["stale_from_status"] == "resuming"
        assert old_generation.meta_data["stale_after_seconds"] == 24 * 60 * 60
    finally:
        db.close()

    assert user_runs_db.count_active_user_runs("u1") == 2
    assert user_runs_db.count_active_user_runs("u2") == 1


def test_stale_active_run_seconds_uses_env_hours(monkeypatch) -> None:
    monkeypatch.delenv("USER_RUN_STALE_AFTER_SECONDS", raising=False)
    monkeypatch.setenv("USER_RUN_STALE_AFTER_HOURS", "2.5")

    assert user_runs_db.stale_active_run_seconds() == 9000


def test_mark_user_run_cancelled_only_updates_owned_row(monkeypatch) -> None:
    session_factory = _session_factory()
    monkeypatch.setattr(user_runs_db, "SessionLocal", session_factory)
    now = datetime(2026, 5, 15, 12, 0, 0)

    db = session_factory()
    db.add_all(
        [
            UserRun(
                request_id="run-1",
                user_id="u1",
                kind="generation",
                status="pending",
                title="Pending",
                created_at=now,
                updated_at=now,
            ),
            UserRun(
                request_id="run-2",
                user_id="u2",
                kind="generation",
                status="pending",
                title="Other user",
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    db.commit()
    db.close()

    cancelled = user_runs_db.mark_user_run_cancelled(
        request_id="run-1",
        user_id="u1",
        reason="test_cancel",
    )
    not_owned = user_runs_db.mark_user_run_cancelled(request_id="run-2", user_id="u1")

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert not_owned is None

    db = session_factory()
    try:
        run_1 = db.query(UserRun).filter_by(request_id="run-1").one()
        run_2 = db.query(UserRun).filter_by(request_id="run-2").one()
        assert run_1.status == "cancelled"
        assert run_1.meta_data["cancelled_from_status"] == "pending"
        assert run_1.meta_data["cancelled_reason"] == "test_cancel"
        assert run_2.status == "pending"
    finally:
        db.close()
