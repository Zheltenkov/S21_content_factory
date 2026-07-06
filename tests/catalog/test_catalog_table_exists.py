"""Тесты backend-aware table_exists (sqlite_master vs information_schema)."""

import sqlite3
from typing import Any

from content_factory.catalog.db.pg_compat import PgConnection, is_postgres_connection
from content_factory.catalog.viewer.app import table_exists


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._rows = list(rows)

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> dict | None:
        return self._rows.pop(0) if self._rows else None

    def close(self) -> None:  # pragma: no cover - unused
        pass


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


def test_is_postgres_connection_detects_wrapper() -> None:
    pg = PgConnection(_FakeConn(_FakeCursor([])))
    assert is_postgres_connection(pg) is True
    assert is_postgres_connection(sqlite3.connect(":memory:")) is False


def test_table_exists_postgres_uses_information_schema() -> None:
    cur = _FakeCursor(rows=[{"exists": 1}])
    conn = PgConnection(_FakeConn(cur))
    assert table_exists(conn, "skill") is True
    sql, params = cur.executed[-1]
    assert "information_schema.tables" in sql
    assert "%s" in sql  # ? транслирован
    assert params == ("catalog", "skill")


def test_table_exists_sqlite_uses_sqlite_master() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE skill (id INTEGER PRIMARY KEY)")
    assert table_exists(conn, "skill") is True
    assert table_exists(conn, "nope") is False
    conn.close()
