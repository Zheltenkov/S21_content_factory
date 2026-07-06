"""sqlite3-совместимая обёртка над psycopg-подключением.

Позволяет существующему каталожному коду (356 `conn.execute("… ?")` вызовов, `.lastrowid`,
`sqlite3.Row`-доступ по имени/индексу) работать на Postgres без переписывания каждого места.
Транслирует `?`→`%s`, эмулирует `lastrowid` через `RETURNING id`, гасит `PRAGMA`, переводит
`INSERT OR IGNORE`. Истинно диалектные запросы (`OR REPLACE`, `GROUP_CONCAT`, `strftime`,
`rowid`) правятся точечно в конкретных местах — обёртка их НЕ переводит.

Подключение инъектируется (любой DBAPI-conn с `RealDictCursor`-подобными строками), поэтому
модуль юнит-тестируется без живого Postgres.
"""

from __future__ import annotations

from typing import Any

from .dialect import (
    adapt_write_sql,
    is_pragma,
    translate_current_timestamp,
    translate_group_concat,
    translate_like,
    translate_placeholders,
)


def is_postgres_connection(conn: Any) -> bool:
    """Является ли подключение Postgres-обёрткой (для backend-специфичного SQL)."""
    return isinstance(conn, PgConnection)


class Row(dict):
    """Строка результата с доступом и по имени, и по индексу (как sqlite3.Row)."""

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _wrap_row(row: Any) -> Row | None:
    if row is None:
        return None
    return Row(row)


class PgCursor:
    """Курсор с sqlite3-подобным API поверх DBAPI-курсора."""

    def __init__(self, raw_cursor: Any) -> None:
        self._cur = raw_cursor
        self.lastrowid: int | None = None

    def execute(self, sql: str, params: Any = ()) -> PgCursor:
        if is_pragma(sql):
            # PG применяет FK всегда; PRAGMA не нужен — гасим в no-op.
            self.lastrowid = None
            return self
        # psycopg2 treats '%' as a param marker only when vars are passed. Double literal '%'
        # (e.g. LIKE 'brief:%') to '%%' before '?'→'%s', but only when params are bound.
        raw = sql.replace("%", "%%") if params else sql
        adapted, wants_returning = adapt_write_sql(
            translate_current_timestamp(
                translate_like(translate_group_concat(translate_placeholders(raw)))
            )
        )
        self._cur.execute(adapted, tuple(params) if params else None)
        self.lastrowid = None
        if wants_returning:
            returned = self._cur.fetchone()
            if returned is not None:
                self.lastrowid = returned["id"] if isinstance(returned, dict) else returned[0]
        return self

    def executemany(self, sql: str, seq_of_params: Any) -> PgCursor:
        self._cur.executemany(translate_placeholders(sql), [tuple(p) for p in seq_of_params])
        return self

    def fetchone(self) -> Row | None:
        return _wrap_row(self._cur.fetchone())

    def fetchall(self) -> list[Row]:
        return [Row(r) for r in self._cur.fetchall()]

    def fetchmany(self, size: int | None = None) -> list[Row]:
        rows = self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()
        return [Row(r) for r in rows]

    def __iter__(self) -> Any:
        for row in self._cur:
            yield Row(row)

    @property
    def rowcount(self) -> int:
        return int(self._cur.rowcount)

    @property
    def description(self) -> Any:
        return self._cur.description

    def close(self) -> None:
        self._cur.close()


class PgConnection:
    """Подключение с sqlite3-подобным API поверх DBAPI-подключения (psycopg)."""

    def __init__(self, raw_conn: Any) -> None:
        self._conn = raw_conn
        self.row_factory: Any = None  # приём для совместимости; строки всегда Row

    def execute(self, sql: str, params: Any = ()) -> PgCursor:
        return PgCursor(self._conn.cursor()).execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Any) -> PgCursor:
        return PgCursor(self._conn.cursor()).executemany(sql, seq_of_params)

    def executescript(self, script: str) -> PgCursor:
        cursor = PgCursor(self._conn.cursor())
        cursor._cur.execute(translate_placeholders(script))
        return cursor

    def cursor(self) -> PgCursor:
        return PgCursor(self._conn.cursor())

    def create_function(self, *_args: Any, **_kwargs: Any) -> None:
        # search_norm и т.п. в PG — SQL-функции (см. working_tables_postgres.sql); no-op.
        return None

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> PgConnection:
        return self

    def __exit__(self, *_exc: Any) -> None:
        # sqlite3-семантика: commit при успехе, rollback при исключении.
        if _exc[0] is None:
            self._conn.commit()
        else:
            self._conn.rollback()
