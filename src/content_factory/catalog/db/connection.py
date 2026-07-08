"""Единая фабрика подключения к каталогу (только Postgres).

Каталог полностью на Postgres: `open_catalog_connection` открывает psycopg-подключение с
`search_path=catalog` (sqlite3-совместимая обёртка `PgConnection`). SQLite-путь удалён.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def resolve_backend() -> str:
    """Backend каталога — всегда `postgres` (SQLite удалён)."""
    return "postgres"


def catalog_database_url() -> str | None:
    """URL Postgres для каталога: `CATALOG_DATABASE_URL` или общий `DATABASE_URL`."""
    return os.getenv("CATALOG_DATABASE_URL") or os.getenv("DATABASE_URL") or None


def open_catalog_connection(sqlite_path: Path | str | None = None, *, check_same_thread: bool = True) -> Any:
    """Открыть Postgres-подключение к каталогу (`search_path=catalog`).

    `sqlite_path`/`check_same_thread` сохранены в сигнатуре для совместимости с call-sites и
    игнорируются (SQLite-путь удалён).
    """
    return _open_postgres()


def _open_postgres() -> Any:
    """Подключение к Postgres-каталогу (search_path=catalog). Ленивый импорт psycopg2."""
    url = catalog_database_url()
    if not url:
        raise RuntimeError(
            "CATALOG_DB=postgres, но не задан CATALOG_DATABASE_URL/DATABASE_URL"
        )
    import psycopg2  # noqa: PLC0415 - lazy: только для Postgres-пути
    from psycopg2.extras import RealDictCursor  # noqa: PLC0415

    from .pg_compat import PgConnection  # noqa: PLC0415

    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    with conn.cursor() as cur:
        cur.execute("SET search_path TO catalog, public")
    conn.commit()
    # sqlite3-совместимая обёртка: существующий код работает без переписывания.
    return PgConnection(conn)
