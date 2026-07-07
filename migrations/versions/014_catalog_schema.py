"""catalog schema (Spravochnik canonical catalog folded into Postgres)

Phase-4b hybrid merge: the canonical competency catalog (28 tables + 2 views) moves
from the Spravochnik SQLite store into a dedicated PostgreSQL ``catalog`` schema.
The intake/DAG working tables (new_tables.sql) stay in SQLite for now.

Data is loaded separately by ``scripts/migrate_catalog_to_postgres.py`` after this
migration creates the empty schema.

Revision ID: 014
Revises: 013
"""

import hashlib
from pathlib import Path

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

# Postgres DDL lives with the catalog package but is FROZEN for this migration: the sha256
# below pins the exact content this revision was authored against. Editing the .sql after it is
# applied would silently change history — the guard fails loud instead. Schema changes go in a
# NEW migration, never by editing an applied one.
_DDL_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "content_factory"
    / "catalog"
    / "sql"
    / "catalog_schema_postgres.sql"
)
_DDL_SHA256 = "71fa2e5f929ae82591ac628373fcfcb936ac144420edb2efd18d8441d53d9d0a"


def _read_frozen(path: Path, expected_sha: str) -> str:
    raw = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if digest != expected_sha:
        raise RuntimeError(
            f"{path.name} changed since migration {revision} was authored "
            f"(sha {digest[:12]} != {expected_sha[:12]}). Never edit an applied migration's SQL — "
            "add a NEW migration instead."
        )
    return raw


def _statements() -> list[str]:
    raw = _read_frozen(_DDL_PATH, _DDL_SHA256)
    # split on ";" that terminates a statement (statements are ";"-terminated, blank-line separated)
    stmts: list[str] = []
    for chunk in raw.split(";\n"):
        s = chunk.strip()
        if not s or s.startswith("--"):
            # keep leading comment lines out; strip pure-comment chunks
            lines = [ln for ln in s.splitlines() if ln.strip() and not ln.strip().startswith("--")]
            if not lines:
                continue
            s = "\n".join(lines)
        if s:
            stmts.append(s)
    return stmts


def upgrade() -> None:
    for stmt in _statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS catalog CASCADE")
