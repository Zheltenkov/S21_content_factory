"""curriculum-plan relational mirror (Phase 4c B-lite)

Adds ``catalog.curriculum_plan`` + ``catalog.curriculum_plan_row`` — the relational
replacement for the lossy JSON-blob ``SpravochnikCatalogEntity`` curriculum mirror.
Authoring stays in the SQLite intake pipeline; a structured sync fills these tables
and the generator reads them directly.

Revision ID: 015
Revises: 014
"""

from pathlib import Path

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

_DDL_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "content_factory"
    / "catalog"
    / "sql"
    / "catalog_curriculum_plan_postgres.sql"
)


def _statements() -> list[str]:
    raw = _DDL_PATH.read_text(encoding="utf-8")
    stmts: list[str] = []
    for chunk in raw.split(";\n"):
        s = chunk.strip()
        if not s:
            continue
        lines = [ln for ln in s.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        if not lines:
            continue
        stmts.append("\n".join(lines))
    return stmts


def upgrade() -> None:
    for stmt in _statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS catalog.curriculum_plan_row")
    op.execute("DROP TABLE IF EXISTS catalog.curriculum_plan")
