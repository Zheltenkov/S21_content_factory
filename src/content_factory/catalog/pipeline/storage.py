"""Персистентность: применяет миграцию недостающих таблиц и пишет результаты."""
from __future__ import annotations

from pathlib import Path

from content_factory.catalog.db import (
    CatalogConnection,
    is_postgres_connection,
)
from content_factory.catalog.pipeline._storage_common import (
    _existing_cols,
    _quoted_columns,
    _table_exists,
)
from content_factory.catalog.pipeline.artifact_templates import (
    accept_curriculum_artifact_template_proposal,  # noqa: F401  re-exported for catalog consumers
    generate_curriculum_artifact_template_proposals,  # noqa: F401  re-exported for catalog consumers
    load_curriculum_artifact_template_proposals,  # noqa: F401  re-exported for catalog consumers
    load_curriculum_artifact_templates,  # noqa: F401  re-exported for catalog consumers
    reject_curriculum_artifact_template_proposal,  # noqa: F401  re-exported for catalog consumers
    update_curriculum_artifact_template_proposal,  # noqa: F401  re-exported for catalog consumers
    upsert_curriculum_artifact_template,  # noqa: F401  re-exported for catalog consumers
)
from content_factory.catalog.pipeline.brief_persistence import (
    save_brief,  # noqa: F401  re-exported for catalog consumers
    save_curriculum_plan,  # noqa: F401  re-exported for catalog consumers
    save_evidence,  # noqa: F401  re-exported for catalog consumers
    save_prerequisite_reviews,  # noqa: F401  re-exported for catalog consumers
    save_prerequisites,  # noqa: F401  re-exported for catalog consumers
    save_suggestions,  # noqa: F401  re-exported for catalog consumers
)
from content_factory.catalog.pipeline.skill_promotion import (
    link_suggestion_to_nearest,  # noqa: F401  re-exported for catalog consumers
    promote_suggestion_to_catalog,  # noqa: F401  re-exported for catalog consumers
    revert_suggestion_promotion,  # noqa: F401  re-exported for catalog consumers
    sync_brief_skill_set,  # noqa: F401  re-exported for catalog consumers
    sync_curriculum_plan_skill_set,  # noqa: F401  re-exported for catalog consumers
    sync_promotions_for_brief,  # noqa: F401  re-exported for catalog consumers
)

_REQUIRED_COLS = {
    "skill_suggestion": [
        ("coverage_area", "TEXT"),
        ("source_name", "TEXT"),
        ("indicators_json", "TEXT"),
        ("entity_type", "TEXT NOT NULL DEFAULT 'skill'"),
        ("atomicity", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("parent_suggestion_id", "INTEGER"),
        ("atomize_rationale", "TEXT"),
        ("match_score", "REAL"),
        ("nearest_skill_id", "INTEGER"),
        ("nearest_name", "TEXT"),
        ("nearest_group", "TEXT"),
    ],
    "skill_prerequisite": [
        ("brief_id", "INTEGER"),
        ("src_suggestion_id", "INTEGER"),
        ("dst_suggestion_id", "INTEGER"),
    ],
    "curriculum_plan_row": [
        ("outcomes_know", "TEXT"),
        ("outcomes_can", "TEXT"),
        ("outcomes_skills", "TEXT"),
        ("materials", "TEXT"),
        ("validation_criteria", "TEXT"),
        ("completion_percent", "REAL"),
        ("p2p_checks", "INTEGER"),
        ("weighted_skills", "TEXT"),
    ],
}











def _copy_common_columns(con: CatalogConnection, source_table: str, target_table: str) -> None:
    source_cols = _existing_cols(con, source_table)
    target_cols = _existing_cols(con, target_table)
    common = [column for column in target_cols if column in source_cols]
    if not common:
        return
    column_sql = _quoted_columns(common)
    con.execute(f'INSERT INTO "{target_table}"({column_sql}) SELECT {column_sql} FROM "{source_table}"')


def _ensure_curriculum_plan_accepts_invalid(con: CatalogConnection, sql_path: str) -> None:
    """Rebuild old curriculum_plan tables whose CHECK does not allow invalid.

    SQLite cannot alter CHECK constraints in place. The rebuild preserves parent
    and row records, then recreates indexes through the idempotent schema.
    """

    if is_postgres_connection(con):
        # PG-схема (alembic) уже допускает status='invalid'; SQLite-only rebuild не нужен.
        return
    if not _table_exists(con, "curriculum_plan"):
        return
    row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='curriculum_plan'").fetchone()
    table_sql = str(row[0] or "") if row else ""
    if "invalid" in table_sql:
        return

    child_exists = _table_exists(con, "curriculum_plan_row")
    fk_state = int(con.execute("PRAGMA foreign_keys").fetchone()[0] or 0)
    con.commit()
    con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute("DROP TABLE IF EXISTS _curriculum_plan_row_backup")
        if child_exists:
            con.execute("CREATE TEMP TABLE _curriculum_plan_row_backup AS SELECT * FROM curriculum_plan_row")
            con.execute("DROP TABLE curriculum_plan_row")
        con.execute("DROP INDEX IF EXISTS idx_curriculum_plan_brief_policy")
        con.execute("ALTER TABLE curriculum_plan RENAME TO _curriculum_plan_old")
        con.executescript(Path(sql_path).read_text(encoding="utf-8"))
        _copy_common_columns(con, "_curriculum_plan_old", "curriculum_plan")
        con.execute("DROP TABLE _curriculum_plan_old")
        if child_exists:
            _copy_common_columns(con, "_curriculum_plan_row_backup", "curriculum_plan_row")
            con.execute("DROP TABLE _curriculum_plan_row_backup")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_curriculum_plan_brief_policy "
            "ON curriculum_plan(brief_id, source_policy)"
        )
        con.commit()
    finally:
        con.execute(f"PRAGMA foreign_keys = {fk_state}")




def apply_migration(con: CatalogConnection, sql_path: str) -> None:
    con.executescript(Path(sql_path).read_text(encoding="utf-8"))
    _ensure_curriculum_plan_accepts_invalid(con, sql_path)
    for table, cols in _REQUIRED_COLS.items():
        existing = _existing_cols(con, table)
        for name, decl in cols:
            if name not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_skill_suggestion_brief_decision ON skill_suggestion(brief_id, entity_type, atomicity, decision)"
    )
    if "brief_id" in _existing_cols(con, "skill_prerequisite"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_skill_prerequisite_brief ON skill_prerequisite(brief_id)")
    if _existing_cols(con, "review_queue"):
        con.execute("CREATE INDEX IF NOT EXISTS idx_review_queue_source_ref ON review_queue(source_ref, status)")
    if _table_exists(con, "skill_set_item"):
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_set_item_unique ON skill_set_item(skill_set_id, skill_id, role, COALESCE(plan_row_id, 0))"
        )
    con.commit()
