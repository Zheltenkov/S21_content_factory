"""Resolve sibling project paths used by the unified service."""

from __future__ import annotations

import os
import sys
from pathlib import Path

GENERATOR_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = GENERATOR_ROOT.parent


def resolve_project_root(env_name: str, default_name: str) -> Path:
    """Return an absolute sibling project root with an env override."""

    configured = os.getenv(env_name)
    root = Path(configured).expanduser() if configured else WORKSPACE_ROOT / default_name
    return root.resolve()


def ensure_import_path(path: Path) -> None:
    """Put a package root on sys.path once, preserving existing import order."""

    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)


def proverka_src_root() -> Path:
    """Return the source root for the auditor package."""

    return resolve_project_root("PROVERKA_PROJECT_ROOT", "Proverka") / "src"


def spravochnik_root() -> Path:
    """Return the source root for the catalog viewer package."""

    return resolve_project_root("SPRAVOCHNIK_PROJECT_ROOT", "Spravochnik")


def spravochnik_sqlite_path() -> Path:
    """Return the current catalog SQLite path used as the migration source."""

    configured = os.getenv("SPRAVOCHNIK_SQLITE_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (spravochnik_root() / "artifacts" / "skills_catalog.sqlite").resolve()


def spravochnik_summary_path() -> Path:
    """Return the current catalog summary JSON path."""

    configured = os.getenv("SPRAVOCHNIK_SUMMARY_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return (spravochnik_root() / "artifacts" / "catalog_summary.json").resolve()

