"""curriculum-plan relational mirror (Phase 4c B-lite)

Adds ``catalog.curriculum_plan`` + ``catalog.curriculum_plan_row`` — the relational
replacement for the lossy JSON-blob ``SpravochnikCatalogEntity`` curriculum mirror.
Authoring stays in the SQLite intake pipeline; a structured sync fills these tables
and the generator reads them directly.

Revision ID: 015
Revises: 014
"""

import hashlib
from pathlib import Path

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

# Frozen for this migration (see 014 for the rationale): the sha256 pins the exact .sql content.
_DDL_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "content_factory"
    / "catalog"
    / "sql"
    / "catalog_curriculum_plan_postgres.sql"
)
_DDL_SHA256 = "19ec8e58245b0dfb5388ce6502dcc79da4b7b5dd22b04cb4a74dd0bb31e4abf5"


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
