"""Тесты sqlite3-совместимой обёртки над psycopg (через фейковый DBAPI, без живого PG)."""

from typing import Any

from content_factory.catalog.db.pg_compat import PgConnection, Row


class FakeCursor:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._queue: list[dict] = list(rows or [])
        self.rowcount = 0
        self.description = None
        self.closed = False

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def executemany(self, sql: str, seq: Any) -> None:
        self.executed.append((sql, list(seq)))

    def fetchone(self) -> dict | None:
        return self._queue.pop(0) if self._queue else None

    def fetchall(self) -> list[dict]:
        rows, self._queue = self._queue[:], []
        return rows

    def fetchmany(self, size: int | None = None) -> list[dict]:
        return self.fetchall()

    def __iter__(self) -> Any:
        while self._queue:
            yield self._queue.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeConn:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.committed = 0
        self.rolledback = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolledback += 1

    def close(self) -> None:
        self.closed = True


def test_execute_translates_placeholders() -> None:
    cur = FakeCursor(rows=[{"a": 1}])
    conn = PgConnection(FakeConn(cur))
    conn.execute("SELECT a FROM t WHERE id = ?", (5,))
    sql, params = cur.executed[-1]
    assert sql == "SELECT a FROM t WHERE id = %s"
    assert params == (5,)


def test_pragma_is_noop() -> None:
    cur = FakeCursor()
    conn = PgConnection(FakeConn(cur))
    conn.execute("PRAGMA foreign_keys=ON")
    assert cur.executed == []


def test_insert_emulates_lastrowid_via_returning() -> None:
    cur = FakeCursor(rows=[{"id": 42}])
    conn = PgConnection(FakeConn(cur))
    result = conn.execute("INSERT INTO t (a) VALUES (?)", (7,))
    sql, _ = cur.executed[-1]
    assert sql.endswith("RETURNING id")
    assert result.lastrowid == 42


def test_insert_or_ignore_translates() -> None:
    cur = FakeCursor(rows=[{"id": 9}])
    conn = PgConnection(FakeConn(cur))
    conn.execute("INSERT OR IGNORE INTO t (a) VALUES (?)", (1,))
    sql, _ = cur.executed[-1]
    assert "ON CONFLICT DO NOTHING" in sql
    assert "RETURNING" not in sql  # id-less-safe: ON CONFLICT не получает RETURNING


def test_on_conflict_upsert_not_broken_by_returning() -> None:
    cur = FakeCursor(rows=[])
    conn = PgConnection(FakeConn(cur))
    result = conn.execute(
        "INSERT INTO evidence_query_cache (cache_key) VALUES (?) "
        "ON CONFLICT(cache_key) DO UPDATE SET model = excluded.model",
        ("k",),
    )
    sql, _ = cur.executed[-1]
    assert "RETURNING" not in sql
    assert result.lastrowid is None


def test_rows_support_name_and_index_access() -> None:
    cur = FakeCursor(rows=[{"a": 1, "b": 2}])
    conn = PgConnection(FakeConn(cur))
    row = conn.execute("SELECT a, b FROM t").fetchone()
    assert isinstance(row, Row)
    assert row["a"] == 1
    assert row[0] == 1
    assert row[1] == 2
    assert row["b"] == 2


def test_fetchall_wraps_rows() -> None:
    cur = FakeCursor(rows=[{"a": 1}, {"a": 2}])
    conn = PgConnection(FakeConn(cur))
    rows = conn.execute("SELECT a FROM t").fetchall()
    assert [r["a"] for r in rows] == [1, 2]
    assert all(isinstance(r, Row) for r in rows)


def test_context_manager_commits_on_success() -> None:
    raw = FakeConn(FakeCursor())
    with PgConnection(raw):
        pass
    assert raw.committed == 1 and raw.rolledback == 0


def test_context_manager_rolls_back_on_error() -> None:
    raw = FakeConn(FakeCursor())
    try:
        with PgConnection(raw):
            raise ValueError("boom")
    except ValueError:
        pass
    assert raw.rolledback == 1 and raw.committed == 0


def test_executemany_translates() -> None:
    cur = FakeCursor()
    conn = PgConnection(FakeConn(cur))
    conn.executemany("INSERT INTO t (a) VALUES (?)", [(1,), (2,)])
    sql, seq = cur.executed[-1]
    assert sql == "INSERT INTO t (a) VALUES (%s)"
    assert seq == [(1,), (2,)]


def test_executemany_applies_full_dialect_chain() -> None:
    # Bulk writes get the same adapters as execute() (INSERT OR IGNORE, CURRENT_TIMESTAMP,
    # `%`-escaping) but never RETURNING — a batch has no single lastrowid.
    cur = FakeCursor()
    conn = PgConnection(FakeConn(cur))
    conn.executemany(
        "INSERT OR IGNORE INTO t (a, created_at) VALUES (?, CURRENT_TIMESTAMP)",
        [(1,), (2,)],
    )
    sql, seq = cur.executed[-1]
    assert "ON CONFLICT DO NOTHING" in sql
    assert "CURRENT_TIMESTAMP" not in sql and "to_char" in sql
    assert "RETURNING" not in sql
    assert seq == [(1,), (2,)]
