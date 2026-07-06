"""catalog working/intake tables (full-PG cutover, Phase-4c)

Adds the intake/DAG working tables (new_tables.sql) to the PostgreSQL ``catalog`` schema
plus the ``catalog.search_norm`` function, so the whole catalog can live in Postgres.
``curriculum_plan`` / ``curriculum_plan_row`` are already created by migration 015 and are
not redefined here.

Data is loaded separately by ``scripts/migrate_catalog_to_postgres.py`` (working tables) after
this migration creates the empty tables.

Revision ID: 016
Revises: 015
"""

from pathlib import Path

from alembic import op

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None

_SQL_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "content_factory"
    / "catalog"
    / "sql"
)
_DDL_PATH = _SQL_DIR / "working_tables_postgres.sql"
# Compatibility functions (plpgsql bodies contain ';') — applied WHOLE, not statement-split.
_FUNCTIONS_PATH = _SQL_DIR / "catalog_functions_postgres.sql"

_FUNCTIONS = ["search_norm(text)", "json_valid(text)", "json_extract(text, text)"]

# Working tables added by this migration (drop order = reverse FK dependency).
_WORKING_TABLES = [
    "skill_promotion_log",
    "curriculum_artifact_template_proposal",
    "skill_set_item",
    "skill_set",
    "curriculum_artifact_template_scope",
    "curriculum_artifact_template",
    "intake_job",
    "prerequisite_edge_decision",
    "skill_prerequisite",
    "skill_suggestion",
    "evidence_query_cache",
    "evidence_source",
    "profile_brief",
]


def _statements() -> list[str]:
    raw = _DDL_PATH.read_text(encoding="utf-8")
    stmts: list[str] = []
    for chunk in raw.split(";\n"):
        s = chunk.strip()
        if not s or s.startswith("--"):
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
    # Functions applied whole (dollar-quoted plpgsql bodies contain ';').
    op.execute(_FUNCTIONS_PATH.read_text(encoding="utf-8"))


def downgrade() -> None:
    for func in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS catalog.{func}")
    for table in _WORKING_TABLES:
        op.execute(f"DROP TABLE IF EXISTS catalog.{table} CASCADE")
