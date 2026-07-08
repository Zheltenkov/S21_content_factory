"""FastAPI dependencies for the catalog UI (per-request Postgres connection).

The catalog runs on Postgres (full cutover). ``catalog_db_path()`` is a legacy artifact
path kept only as an (ignored) positional argument for call sites that still pass it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from content_factory.api.integrations.project_paths import spravochnik_sqlite_path
from content_factory.catalog.db import open_catalog_connection


def catalog_db_path() -> Path:
    """Legacy catalog artifact path (ignored by the Postgres connection factory)."""

    return spravochnik_sqlite_path()


def get_conn() -> Iterator[Any]:
    """Yield a per-request Postgres catalog connection (backend fixed to Postgres)."""

    # check_same_thread=False: FastAPI may create this connection in a threadpool
    # thread and use it in the event-loop thread; access is sequential per request.
    conn = open_catalog_connection(catalog_db_path(), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()
