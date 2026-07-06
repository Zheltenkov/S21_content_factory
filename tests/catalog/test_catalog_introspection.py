"""Тесты backend-aware интроспекции (existing_columns/table_exists)."""

import sqlite3
from typing import Any

from content_factory.catalog.db.introspection import existing_columns, table_exists
from content_factory.catalog.db.pg_compat import PgConnection


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._rows = list(rows)

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return list(self._rows)


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


def test_existing_columns_sqlite_uses_pragma() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, note TEXT)")
    assert existing_columns(conn, "t") == {"id", "name", "note"}
    conn.close()


def test_existing_columns_postgres_uses_information_schema() -> None:
    cur = _FakeCursor(rows=[{"column_name": "id"}, {"column_name": "name"}])
    conn = PgConnection(_FakeConn(cur))
    assert existing_columns(conn, "skill") == {"id", "name"}
    sql, params = cur.executed[-1]
    assert "information_schema.columns" in sql
    assert "PRAGMA" not in sql
    assert params == ("catalog", "skill")


def test_table_exists_postgres_information_schema() -> None:
    cur = _FakeCursor(rows=[{"1": 1}])
    conn = PgConnection(_FakeConn(cur))
    assert table_exists(conn, "profile") is True
    sql, params = cur.executed[-1]
    assert "information_schema.tables" in sql
    assert params == ("catalog", "profile")
