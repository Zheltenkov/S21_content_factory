"""FastAPI dependencies for the catalog UI (per-request SQLite connection).

The catalog runs on its SQLite store (hybrid Phase-4b); the connection path honours
the same ``SPRAVOCHNIK_SQLITE_PATH`` override used elsewhere.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from pathlib import Path

from content_factory.api.integrations.project_paths import spravochnik_sqlite_path
from content_factory.catalog.viewer.app import open_db


def catalog_db_path() -> Path:
    """Path to the catalog SQLite store (honours SPRAVOCHNIK_SQLITE_PATH)."""

    return spravochnik_sqlite_path()


def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a per-request SQLite connection to the catalog store."""

    # check_same_thread=False: FastAPI may create this connection in a threadpool
    # thread and use it in the event-loop thread; access is sequential per request.
    conn = open_db(catalog_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()
