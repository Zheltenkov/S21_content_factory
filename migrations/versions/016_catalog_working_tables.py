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

import hashlib
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

# Frozen for this migration (see 014): sha256 pins the exact .sql content authored against.
_DDL_SHA256 = "bc58a4cbd5933b0e23ceac409687952f634dbea749acd58807c3aa8afe2507b7"
_FUNCTIONS_SHA256 = "fbb9aa88ca4b35a8619fc3a163c5718fe3e54d2b23c7a8aa47b434bb04cbc95a"


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
    raw = _read_frozen(_DDL_PATH, _DDL_SHA256)
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
    op.execute(_read_frozen(_FUNCTIONS_PATH, _FUNCTIONS_SHA256))


def downgrade() -> None:
    for func in _FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS catalog.{func}")
    for table in _WORKING_TABLES:
        op.execute(f"DROP TABLE IF EXISTS catalog.{table} CASCADE")
