from __future__ import annotations

from content_factory.catalog.viewer import intake_runtime


def test_intake_runtime_preflight_keys_ready_state_by_postgres_url(monkeypatch, tmp_path) -> None:
    calls: list[object] = []
    stale_calls: list[object] = []
    conn = object()
    legacy_path = tmp_path / "ignored.sqlite"

    monkeypatch.setattr(intake_runtime, "resolve_backend", lambda: "postgres")
    monkeypatch.setattr(intake_runtime, "repair_intake_review_links", lambda current_conn: calls.append(current_conn) or 0)
    monkeypatch.setattr(intake_runtime, "repair_stale_intake_jobs", lambda current_conn: stale_calls.append(current_conn) or 0)

    try:
        intake_runtime.INTAKE_SCHEMA_READY.clear()
        monkeypatch.setattr(intake_runtime, "catalog_database_url", lambda: "postgresql://localhost/db_one")

        intake_runtime.ensure_intake_runtime_schema(conn, legacy_path)
        intake_runtime.ensure_intake_runtime_schema(conn, legacy_path)

        monkeypatch.setattr(intake_runtime, "catalog_database_url", lambda: "postgresql://localhost/db_two")
        intake_runtime.ensure_intake_runtime_schema(conn, legacy_path)
    finally:
        intake_runtime.INTAKE_SCHEMA_READY.clear()

    assert calls == [conn, conn]
    assert stale_calls == [conn, conn, conn]


def test_intake_runtime_preflight_keeps_sqlite_path_key_for_non_postgres(monkeypatch, tmp_path) -> None:
    calls: list[object] = []
    conn = object()
    first_path = tmp_path / "first.sqlite"
    second_path = tmp_path / "second.sqlite"

    monkeypatch.setattr(intake_runtime, "resolve_backend", lambda: "sqlite")
    monkeypatch.setattr(intake_runtime, "repair_intake_review_links", lambda current_conn: calls.append(current_conn) or 0)
    monkeypatch.setattr(intake_runtime, "repair_stale_intake_jobs", lambda _current_conn: 0)

    try:
        intake_runtime.INTAKE_SCHEMA_READY.clear()

        intake_runtime.ensure_intake_runtime_schema(conn, first_path)
        intake_runtime.ensure_intake_runtime_schema(conn, first_path)
        intake_runtime.ensure_intake_runtime_schema(conn, second_path)
    finally:
        intake_runtime.INTAKE_SCHEMA_READY.clear()

    assert calls == [conn, conn]
