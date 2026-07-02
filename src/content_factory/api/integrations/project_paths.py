"""Resolve in-package data paths for the unified service.

Audit and catalog now live inside ``content_factory``; the former sibling-folder
resolution (sys.path injection, ``resolve_project_root``) is gone. What remains is
the catalog SQLite source used by the (transitional) SQLite->Postgres sync, until
the catalog is fully merged into Postgres.
"""

from __future__ import annotations

import os
from pathlib import Path

# __file__ = <repo>/src/content_factory/api/integrations/project_paths.py
# parents[2] = <repo>/src/content_factory  (package root)
# parents[4] = <repo>                      (workspace root; holds the root .env)
GENERATOR_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = Path(__file__).resolve().parents[4]

_CATALOG_ARTIFACTS = GENERATOR_ROOT / "catalog" / "artifacts"


def spravochnik_sqlite_path() -> Path:
    """Return the catalog SQLite path used as the migration source."""

    configured = os.getenv("SPRAVOCHNIK_SQLITE_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (_CATALOG_ARTIFACTS / "skills_catalog.sqlite").resolve()


def spravochnik_summary_path() -> Path:
    """Return the catalog summary JSON path."""

    configured = os.getenv("SPRAVOCHNIK_SUMMARY_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (_CATALOG_ARTIFACTS / "catalog_summary.json").resolve()
