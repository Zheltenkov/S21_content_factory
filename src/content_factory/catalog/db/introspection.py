"""Backend-aware интроспекция схемы каталога (SQLite ↔ Postgres).

SQLite использует `sqlite_master` / `PRAGMA table_info`; Postgres — `information_schema`.
Единые хелперы, чтобы код пайплайна/вьюера не завязывался на конкретный backend.
"""

from __future__ import annotations

from typing import Any

from .pg_compat import is_postgres_connection

_PG_SCHEMA = "catalog"


def table_exists(conn: Any, table: str) -> bool:
    """Есть ли таблица (sqlite_master ↔ information_schema.tables)."""
    if is_postgres_connection(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?",
            (_PG_SCHEMA, table),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
    return row is not None


def existing_columns(conn: Any, table: str) -> set[str]:
    """Множество имён колонок (PRAGMA table_info ↔ information_schema.columns)."""
    if is_postgres_connection(conn):
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ?",
            (_PG_SCHEMA, table),
        ).fetchall()
        return {str(row["column_name"]) for row in rows}
    # PRAGMA table_info: колонка 1 — имя.
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def column_exists(conn: Any, table: str, column: str) -> bool:
    """Есть ли колонка в таблице."""
    return column in existing_columns(conn, table)
