"""FastAPI dependencies for the catalog UI (per-request SQLite connection).

The catalog runs on its SQLite store (hybrid Phase-4b); the connection path honours
the same ``SPRAVOCHNIK_SQLITE_PATH`` override used elsewhere.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from content_factory.api.integrations.project_paths import spravochnik_sqlite_path
from content_factory.catalog.viewer.app import open_db


def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a per-request SQLite connection to the catalog store."""

    conn = open_db(spravochnik_sqlite_path())
    try:
        yield conn
    finally:
        conn.close()
