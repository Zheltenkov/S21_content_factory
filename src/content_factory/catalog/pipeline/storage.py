"""Персистентность: применяет миграцию недостающих таблиц и пишет результаты."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from content_factory.catalog.db import (
    CatalogConnection,
    CatalogRow,
    is_postgres_connection,
)
from content_factory.catalog.pipeline._storage_common import (
    _as_dict,
    _existing_cols,
    _normalize_catalog_key,
    _quoted_columns,
    _review_queue_entity_type,
    _slug_catalog_key,
    _supports_superseded,
    _table_exists,
    _utc_now_iso,
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

from . import competency_catalog
from .models import Evidence, SkillCandidate

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


def _ensure_skill_group(con: CatalogConnection, group_name: str | None) -> int | None:
    if not _table_exists(con, "skill_group"):
        return None
    name = (group_name or "Прочие навыки").strip() or "Прочие навыки"
    code = f"group-{_slug_catalog_key(name)}"
    row = con.execute(
        "SELECT id FROM skill_group WHERE code = ? OR name = ? ORDER BY id LIMIT 1",
        (code, name),
    ).fetchone()
    if row:
        return int(row["id"])
    max_order = con.execute("SELECT COALESCE(MAX(sort_order), 0) FROM skill_group").fetchone()[0] or 0
    cursor = con.execute(
        """
        INSERT INTO skill_group(code, name, sort_order, status, source, updated_at)
        VALUES (?, ?, ?, 'active', 'derived', ?)
        """,
        (code, name, int(max_order) + 10, _utc_now_iso()),
    )
    return int(cursor.lastrowid or 0)


def _load_skill_suggestion_row(con: CatalogConnection, suggestion_id: int) -> CatalogRow | None:
    row = con.execute(
        """
        SELECT id, brief_id, suggested_name, source_name, group_name, coverage_area, resolution,
               canonical_skill_id, nearest_skill_id, nearest_name, nearest_group,
               decision, entity_type, atomicity, indicators_json
        FROM skill_suggestion
        WHERE id = ?
        """,
        (suggestion_id,),
    ).fetchone()
    return cast("CatalogRow | None", row)


def _find_skill_by_id(con: CatalogConnection, skill_id: int) -> CatalogRow | None:
    row = con.execute(
        "SELECT id, normalized_name, canonical_name, skill_type, status FROM skill WHERE id = ?",
        (skill_id,),
    ).fetchone()
    return cast("CatalogRow | None", row)


def _find_skill_by_normalized_name(con: CatalogConnection, normalized_name: str) -> CatalogRow | None:
    row = con.execute(
        "SELECT id, normalized_name, canonical_name, skill_type, status FROM skill WHERE normalized_name = ?",
        (normalized_name,),
    ).fetchone()
    return cast("CatalogRow | None", row)


def _ensure_skill_alias(con: CatalogConnection, skill_id: int, alias: str, source: str) -> bool:
    if not alias or not alias.strip():
        return False
    normalized_alias = _normalize_catalog_key(alias)
    if not normalized_alias:
        return False
    exists = con.execute(
        "SELECT 1 FROM skill_alias WHERE skill_id = ? AND normalized_alias = ?",
        (skill_id, normalized_alias),
    ).fetchone()
    if exists:
        return False
    con.execute(
        """
        INSERT INTO skill_alias(skill_id, alias, normalized_alias, source)
        VALUES (?, ?, ?, ?)
        """,
        (skill_id, alias.strip(), normalized_alias, source),
    )
    return True


def _existing_promotion(con: Any, suggestion_id: int) -> Any:
    if not _table_exists(con, "skill_promotion_log"):
        return None
    row = con.execute(
        """
        SELECT id, suggestion_id, skill_id, alias, normalized_alias, created_skill, created_alias, status
        FROM skill_promotion_log
        WHERE suggestion_id = ?
        """,
        (suggestion_id,),
    ).fetchone()
    return cast("CatalogRow | None", row)


def _skillset_code(*parts: object) -> str:
    return "skillset-" + "-".join(_slug_catalog_key(str(part)) for part in parts if str(part or "").strip())


def _accepted_atomic_skill_rows(con: CatalogConnection, brief_id: int) -> list[CatalogRow]:
    if not _table_exists(con, "skill_suggestion"):
        return []
    return con.execute(  # type: ignore[no-any-return]
        """
        SELECT
            ss.id AS suggestion_id,
            ss.canonical_skill_id AS skill_id,
            ss.suggested_name,
            ss.group_name,
            ss.coverage_area,
            ss.confidence,
            s.canonical_name
        FROM skill_suggestion ss
        JOIN skill s ON s.id = ss.canonical_skill_id
        WHERE ss.brief_id = ?
          AND ss.entity_type = 'skill'
          AND ss.atomicity = 'atomic'
          AND ss.decision = 'accepted'
          AND ss.canonical_skill_id IS NOT NULL
        ORDER BY COALESCE(ss.coverage_area, ss.group_name, ''), ss.id
        """,
        (brief_id,),
    ).fetchall()


def upsert_skill_set(
    con: CatalogConnection,
    *,
    code: str,
    title: str,
    source_type: str,
    source_id: int | None = None,
    source_ref: str = "",
    description: str = "",
    status: str = "active",
    metadata: dict[str, Any] | None = None,
) -> int | None:
    """Create or update a reusable skill set without touching catalog taxonomy."""
    if not _table_exists(con, "skill_set"):
        return None
    normalized_code = _skillset_code(code)
    metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
    con.execute(
        """
        INSERT INTO skill_set(
            code, title, description, source_type, source_id, source_ref,
            status, metadata_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            title = excluded.title,
            description = excluded.description,
            source_type = excluded.source_type,
            source_id = excluded.source_id,
            source_ref = excluded.source_ref,
            status = excluded.status,
            metadata_json = excluded.metadata_json,
            updated_at = excluded.updated_at
        """,
        (
            normalized_code,
            title.strip(),
            description.strip(),
            source_type,
            source_id,
            source_ref.strip(),
            status,
            metadata_json,
            _utc_now_iso(),
        ),
    )
    row = con.execute("SELECT id FROM skill_set WHERE code = ?", (normalized_code,)).fetchone()
    return int(row["id"]) if row else None


def replace_skill_set_items(
    con: CatalogConnection,
    skill_set_id: int,
    items: list[dict[str, Any]],
) -> int:
    """Rewrite skill-set membership idempotently."""
    if not _table_exists(con, "skill_set_item"):
        return 0
    con.execute("DELETE FROM skill_set_item WHERE skill_set_id = ?", (skill_set_id,))
    inserted = 0
    for index, item in enumerate(items, start=1):
        skill_id = int(item.get("skill_id") or 0)
        if not skill_id:
            continue
        con.execute(
            """
            INSERT OR IGNORE INTO skill_set_item(
                skill_set_id, skill_id, suggestion_id, plan_row_id, role,
                weight, sort_order, rationale
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                skill_set_id,
                skill_id,
                item.get("suggestion_id"),
                item.get("plan_row_id"),
                str(item.get("role") or "target"),
                float(item.get("weight") or 1.0),
                int(item.get("sort_order") or index),
                str(item.get("rationale") or ""),
            ),
        )
        inserted += 1
    return inserted


def sync_brief_skill_set(con: CatalogConnection, brief_id: int) -> dict[str, Any]:
    """Persist accepted atomic skills as a reusable skill set for the brief."""
    rows = _accepted_atomic_skill_rows(con, brief_id)
    if not _table_exists(con, "skill_set"):
        return {"status": "skipped", "brief_id": brief_id, "item_count": 0}
    if not rows:
        code = _skillset_code(f"brief-{brief_id}-accepted")
        existing = con.execute("SELECT id FROM skill_set WHERE code = ?", (code,)).fetchone()
        if existing:
            skill_set_id = int(existing["id"])
            if _table_exists(con, "skill_set_item"):
                con.execute("DELETE FROM skill_set_item WHERE skill_set_id = ?", (skill_set_id,))
            con.execute(
                "UPDATE skill_set SET status = 'archived', updated_at = ? WHERE id = ?",
                (_utc_now_iso(), skill_set_id),
            )
        return {"status": "archived_empty", "brief_id": brief_id, "item_count": 0}
    skill_set_id = upsert_skill_set(
        con,
        code=f"brief-{brief_id}-accepted",
        title=f"Набор skills по брифу #{brief_id}",
        source_type="brief",
        source_id=brief_id,
        source_ref=f"brief:{brief_id}",
        description="Принятые методологом атомарные skills, используемые для DAG и УП.",
        metadata={
            "brief_id": brief_id,
            "item_count": len(rows),
            "coverage_areas": sorted({str(row["coverage_area"] or row["group_name"] or "").strip() for row in rows if str(row["coverage_area"] or row["group_name"] or "").strip()}),
        },
    ) or 0
    if skill_set_id is None:
        return {"status": "skipped", "brief_id": brief_id, "item_count": 0}
    items = [
        {
            "skill_id": int(row["skill_id"]),
            "suggestion_id": int(row["suggestion_id"]),
            "role": "target",
            "weight": 1.0,
            "sort_order": index,
            "rationale": f"accepted_atomic:{row['suggestion_id']}",
        }
        for index, row in enumerate(rows, start=1)
    ]
    item_count = replace_skill_set_items(con, skill_set_id, items)
    return {"status": "synced", "brief_id": brief_id, "skill_set_id": skill_set_id, "item_count": item_count}


def sync_curriculum_plan_skill_set(
    con: CatalogConnection,
    *,
    brief_id: int,
    plan_id: int,
    plan_payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist the skill set used by a curriculum plan without changing plan rows."""
    rows = _accepted_atomic_skill_rows(con, brief_id)
    if not rows or not _table_exists(con, "skill_set"):
        return {"status": "skipped", "plan_id": plan_id, "item_count": 0}
    report = _as_dict(plan_payload.get("report"))
    quality_metrics = _as_dict(report.get("quality_metrics"))
    skill_set_id = upsert_skill_set(
        con,
        code=f"curriculum-plan-{plan_id}-skills",
        title=f"Набор skills для УП #{plan_id}",
        source_type="curriculum_plan",
        source_id=plan_id,
        source_ref=f"curriculum_plan:{plan_id};brief:{brief_id}",
        description="Skills, на которых построен сохранённый черновик учебного плана.",
        metadata={
            "brief_id": brief_id,
            "plan_id": plan_id,
            "item_count": len(rows),
            "quality_metrics": quality_metrics,
        },
    )
    if skill_set_id is None:
        return {"status": "skipped", "plan_id": plan_id, "item_count": 0}
    items = [
        {
            "skill_id": int(row["skill_id"]),
            "suggestion_id": int(row["suggestion_id"]),
            "role": "target",
            "weight": 1.0,
            "sort_order": index,
            "rationale": f"curriculum_plan:{plan_id}",
        }
        for index, row in enumerate(rows, start=1)
    ]
    item_count = replace_skill_set_items(con, skill_set_id, items)
    return {"status": "synced", "plan_id": plan_id, "skill_set_id": skill_set_id, "item_count": item_count}


def promote_suggestion_to_catalog(con: CatalogConnection, suggestion_id: int) -> dict[str, Any]:
    row = _load_skill_suggestion_row(con, suggestion_id)
    if not row:
        return {"status": "missing_suggestion", "suggestion_id": suggestion_id}
    if row["entity_type"] != "skill" or row["atomicity"] != "atomic":
        return {"status": "skipped_non_atomic", "suggestion_id": suggestion_id}
    if row["decision"] != "accepted":
        return {"status": "skipped_not_accepted", "suggestion_id": suggestion_id}

    existing_promotion = _existing_promotion(con, suggestion_id)
    normalized_name = _normalize_catalog_key(str(row["suggested_name"] or ""))
    if not normalized_name:
        return {"status": "skipped_empty_name", "suggestion_id": suggestion_id}

    skill_cols = _existing_cols(con, "skill")
    group_id = _ensure_skill_group(con, row["group_name"] or row["coverage_area"])
    skill_row = None
    created_skill = False
    if row["canonical_skill_id"] is not None:
        skill_row = _find_skill_by_id(con, int(row["canonical_skill_id"]))
    if skill_row is None:
        skill_row = _find_skill_by_normalized_name(con, normalized_name)
    if skill_row is None:
        columns = ["normalized_name", "canonical_name", "skill_type", "status"]
        values: list[object] = [normalized_name, str(row["suggested_name"]).strip(), "unknown", "active"]
        if "group_id" in skill_cols and group_id is not None:
            columns.append("group_id")
            values.append(group_id)
        if "code" in skill_cols:
            columns.append("code")
            values.append(f"skill-{_slug_catalog_key(str(row['suggested_name']))}")
        if "name" in skill_cols:
            columns.append("name")
            values.append(str(row["suggested_name"]).strip())
        if "resolution_status" in skill_cols:
            columns.append("resolution_status")
            values.append("manual")
        if "is_active" in skill_cols:
            columns.append("is_active")
            values.append(1)
        if "created_at" in skill_cols:
            columns.append("created_at")
            values.append(_utc_now_iso())
        if "updated_at" in skill_cols:
            columns.append("updated_at")
            values.append(_utc_now_iso())
        placeholders = ", ".join("?" for _ in columns)
        cur = con.execute(
            f"INSERT INTO skill({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values),
        )
        skill_id = int(cur.lastrowid or 0)
        skill_row = _find_skill_by_id(con, skill_id)
        created_skill = True
    else:
        skill_id = int(skill_row["id"])
        if str(skill_row["status"] or "active") != "active":
            con.execute("UPDATE skill SET status = 'active' WHERE id = ?", (skill_id,))
        updates = []
        params: list[object] = []
        if "is_active" in skill_cols:
            updates.append("is_active = 1")
        if "group_id" in skill_cols and group_id is not None:
            updates.append("group_id = COALESCE(group_id, ?)")
            params.append(group_id)
        if "name" in skill_cols:
            updates.append("name = COALESCE(NULLIF(name, ''), ?)")
            params.append(str(row["suggested_name"]).strip())
        if "updated_at" in skill_cols:
            updates.append("updated_at = ?")
            params.append(_utc_now_iso())
        if updates:
            params.append(skill_id)
            con.execute(f"UPDATE skill SET {', '.join(updates)} WHERE id = ?", tuple(params))

    created_alias = _ensure_skill_alias(con, skill_id, str(row["suggested_name"]), "intake_accept")
    source_name = str(row["source_name"] or "").strip()
    if source_name and source_name.casefold() != str(row["suggested_name"] or "").strip().casefold():
        created_alias = _ensure_skill_alias(con, skill_id, source_name, "intake_original") or created_alias
    canonical_name = str(skill_row["canonical_name"] if skill_row else row["suggested_name"])
    resolution_after = "matched" if _normalize_catalog_key(canonical_name) == normalized_name else "alias"
    con.execute(
        """
        UPDATE skill_suggestion
        SET canonical_skill_id = ?, resolution = ?, decision = 'accepted'
        WHERE id = ?
        """,
        (skill_id, resolution_after, suggestion_id),
    )
    if existing_promotion:
        con.execute(
            """
            UPDATE skill_promotion_log
            SET skill_id = ?,
                alias = ?,
                normalized_alias = ?,
                resolution_after_promotion = ?,
                created_skill = CASE WHEN created_skill = 1 OR ? = 1 THEN 1 ELSE 0 END,
                created_alias = CASE WHEN created_alias = 1 OR ? = 1 THEN 1 ELSE 0 END,
                status = 'active',
                reverted_at = NULL
            WHERE suggestion_id = ?
            """,
            (
                skill_id,
                str(row["suggested_name"]).strip(),
                normalized_name,
                resolution_after,
                1 if created_skill else 0,
                1 if created_alias else 0,
                suggestion_id,
            ),
        )
    else:
        con.execute(
            """
            INSERT INTO skill_promotion_log(
                suggestion_id, skill_id, alias, normalized_alias, resolution_after_promotion,
                created_skill, created_alias, status, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'intake_accept')
            """,
            (
                suggestion_id,
                skill_id,
                str(row["suggested_name"]).strip(),
                normalized_name,
                resolution_after,
                1 if created_skill else 0,
                1 if created_alias else 0,
            ),
        )
    competency_link = competency_catalog.ensure_skill_competency_link(
        con,
        skill_id=skill_id,
        skill_name=str(row["suggested_name"]).strip(),
        competency_title=row["coverage_area"] or row["group_name"],
        indicators=row["indicators_json"],
        source_note=f"intake_accept:suggestion:{suggestion_id}",
    )
    skill_set = sync_brief_skill_set(con, int(row["brief_id"]))
    con.commit()
    return {
        "status": "promoted",
        "suggestion_id": suggestion_id,
        "skill_id": skill_id,
        "created_skill": created_skill,
        "created_alias": created_alias,
        "resolution_after": resolution_after,
        "competency_link": competency_link,
        "skill_set": skill_set,
    }


def revert_suggestion_promotion(con: CatalogConnection, suggestion_id: int) -> dict[str, Any]:
    row = _load_skill_suggestion_row(con, suggestion_id)
    promotion = _existing_promotion(con, suggestion_id)
    if not row or not promotion or str(promotion["status"]) != "active":
        return {"status": "noop", "suggestion_id": suggestion_id}

    skill_id = int(promotion["skill_id"])
    normalized_alias = str(promotion["normalized_alias"] or "")

    if int(promotion["created_alias"] or 0) == 1 and normalized_alias:
        con.execute(
            "DELETE FROM skill_alias WHERE skill_id = ? AND normalized_alias = ?",
            (skill_id, normalized_alias),
        )

    should_disable_skill = False
    if int(promotion["created_skill"] or 0) == 1:
        active_promotions = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM skill_promotion_log
                WHERE skill_id = ?
                  AND status = 'active'
                  AND suggestion_id <> ?
                """,
                (skill_id, suggestion_id),
            ).fetchone()[0]
        )
        other_accepted_refs = int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM skill_suggestion
                WHERE canonical_skill_id = ?
                  AND entity_type = 'skill'
                  AND atomicity = 'atomic'
                  AND decision = 'accepted'
                  AND id <> ?
                """,
                (skill_id, suggestion_id),
            ).fetchone()[0]
        )
        if active_promotions == 0 and other_accepted_refs == 0:
            should_disable_skill = True

    if should_disable_skill:
        skill_cols = _existing_cols(con, "skill")
        if "is_active" in skill_cols:
            con.execute("UPDATE skill SET status = 'candidate', is_active = 0 WHERE id = ?", (skill_id,))
        else:
            con.execute("UPDATE skill SET status = 'candidate' WHERE id = ?", (skill_id,))
        competency_catalog.remove_intake_competency_links_for_skill(con, skill_id)

    resolution_after = "new"
    canonical_skill_id: int | None = None
    fallback_skill = _find_skill_by_normalized_name(con, _normalize_catalog_key(str(row["suggested_name"] or "")))
    if fallback_skill and int(fallback_skill["id"]) != skill_id and str(fallback_skill["status"] or "active") == "active":
        canonical_skill_id = int(fallback_skill["id"])
        fallback_canonical = _normalize_catalog_key(str(fallback_skill["canonical_name"] or ""))
        resolution_after = "matched" if fallback_canonical == _normalize_catalog_key(str(row["suggested_name"] or "")) else "alias"

    con.execute(
        """
        UPDATE skill_suggestion
        SET canonical_skill_id = ?, resolution = ?
        WHERE id = ?
        """,
        (canonical_skill_id, resolution_after, suggestion_id),
    )
    con.execute(
        """
        UPDATE skill_promotion_log
        SET status = 'reverted',
            reverted_at = ?
        WHERE suggestion_id = ?
        """,
        (_utc_now_iso(), suggestion_id),
    )
    sync_brief_skill_set(con, int(row["brief_id"]))
    con.commit()
    return {
        "status": "reverted",
        "suggestion_id": suggestion_id,
        "skill_id": skill_id,
        "disabled_skill": should_disable_skill,
        "resolution_after": resolution_after,
    }


def link_suggestion_to_nearest(con: CatalogConnection, suggestion_id: int) -> dict[str, Any]:
    """Accept coverage by the nearest existing catalog skill without creating a new skill."""
    row = con.execute(
        """
        SELECT ss.id, ss.nearest_skill_id, s.canonical_name
        FROM skill_suggestion ss
        LEFT JOIN skill s ON s.id = ss.nearest_skill_id
        WHERE ss.id = ?
        """,
        (suggestion_id,),
    ).fetchone()
    if not row or row["nearest_skill_id"] is None:
        return {"status": "missing_nearest", "suggestion_id": suggestion_id}
    con.execute(
        """
        UPDATE skill_suggestion
        SET canonical_skill_id = nearest_skill_id,
            resolution = 'alias'
        WHERE id = ?
        """,
        (suggestion_id,),
    )
    con.commit()
    return {
        "status": "linked",
        "suggestion_id": suggestion_id,
        "skill_id": int(row["nearest_skill_id"]),
        "canonical_name": row["canonical_name"],
    }


def sync_promotions_for_brief(con: CatalogConnection, brief_id: int) -> dict[str, int]:
    promoted = 0
    reverted = 0
    accepted_rows = con.execute(
        """
        SELECT id
        FROM skill_suggestion
        WHERE brief_id = ?
          AND entity_type = 'skill'
          AND atomicity = 'atomic'
          AND decision = 'accepted'
        ORDER BY id
        """,
        (brief_id,),
    ).fetchall()
    for row in accepted_rows:
        result = promote_suggestion_to_catalog(con, int(row["id"]))
        if result.get("status") == "promoted":
            promoted += 1

    active_rows = con.execute(
        """
        SELECT spl.suggestion_id
        FROM skill_promotion_log spl
        JOIN skill_suggestion ss ON ss.id = spl.suggestion_id
        WHERE ss.brief_id = ?
          AND spl.status = 'active'
          AND (ss.decision IS NULL OR ss.decision <> 'accepted')
        """,
        (brief_id,),
    ).fetchall()
    for row in active_rows:
        result = revert_suggestion_promotion(con, int(row["suggestion_id"]))
        if result.get("status") == "reverted":
            reverted += 1

    return {"promoted": promoted, "reverted": reverted}


def save_brief(con: CatalogConnection, raw: str, spec: dict) -> int:
    cur = con.execute(
        "INSERT INTO profile_brief(raw_text, role, seniority, domain) VALUES (?,?,?,?)",
        (raw, spec.get("role"), spec.get("seniority"), spec.get("domain")))
    con.commit()
    return int(cur.lastrowid or 0)


def save_evidence(con: CatalogConnection, brief_id: int, evidence: list[Evidence]) -> dict[str, int]:
    idmap: dict[str, int] = {}
    for e in evidence:
        cur = con.execute(
            "INSERT INTO evidence_source(brief_id, claim, source_type, url, snippet, retrieved_at) VALUES (?,?,?,?,?,?)",
            (brief_id, e.claim, e.source_type, e.url, e.snippet, e.retrieved_at))
        idmap[e.id] = int(cur.lastrowid or 0)
    con.commit()
    return idmap


def save_suggestions(con: CatalogConnection, brief_id: int, cands: list[SkillCandidate], ev_idmap: dict[str, int]) -> dict[str, int]:
    tmp_to_db: dict[str, int] = {}
    allow_superseded = _supports_superseded(con)
    ordered = sorted(cands, key=lambda candidate: 0 if candidate.parent_tmp_id is None else 1)
    for c in ordered:
        parent_db_id = tmp_to_db.get(c.parent_tmp_id) if c.parent_tmp_id else None
        stored_decision = c.decision
        if (
            stored_decision == "needs_review"
            and c.atomicity == "composite"
            and "composite_decomposed" in (c.reasons or [])
        ):
            stored_decision = "superseded"
        if stored_decision == "superseded" and not allow_superseded:
            stored_decision = "rejected"
        suggestion_cursor = con.execute(
            """INSERT INTO skill_suggestion(brief_id, suggested_name, source_name, group_name, coverage_area, bloom,
               indicators_json, tools, resolution, canonical_skill_id, match_score,
               nearest_skill_id, nearest_name, nearest_group, confidence, council_agreement,
               evidence_ids, decision, entity_type, atomicity, parent_suggestion_id, atomize_rationale)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                brief_id,
                c.name,
                c.source_name,
                c.group,
                c.coverage_area,
                max((i.bloom for i in c.indicators), default=None),
                json.dumps([indicator.model_dump(mode="json") for indicator in c.indicators], ensure_ascii=False),
                json.dumps(c.tools, ensure_ascii=False),
                c.resolution,
                c.canonical_skill_id,
                c.match_score,
                c.nearest_skill_id,
                c.nearest_name,
                c.nearest_group,
                c.confidence,
                c.council_agreement,
                json.dumps([ev_idmap.get(x) for x in c.evidence_ids]),
                stored_decision or "pending",
                c.entity_type,
                c.atomicity,
                parent_db_id,
                c.atomize_rationale,
            ),
        )
        tmp_to_db[c.tmp_id] = int(suggestion_cursor.lastrowid or 0)
        # спорное -> в существующую review_queue (переиспользуем механизм каталога)
        if stored_decision == "needs_review":
            rq_entity_type = _review_queue_entity_type(c)
            primary_reason = c.reasons[0] if c.reasons else "needs_review"
            severity = "warning" if primary_reason in {"novel_skill", "council_split", "fuzzy_match_ambiguous", "low_confidence"} else "info"
            reasons_text = ", ".join(c.reasons) if c.reasons else "manual_review"
            details = (
                f"Интейк по брифу #{brief_id}: {c.entity_type} «{c.name}». "
                f"Атомарность: {c.atomicity}. "
                f"Резолв против каталога: {c.resolution or 'unknown'}, "
                f"уверенность {c.confidence:.2f}. "
                f"Причины проверки: {reasons_text}."
            )
            con.execute(
                """INSERT INTO review_queue(entity_type, entity_id, source_ref, reason_code, severity, details, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'open')""",
                (rq_entity_type, tmp_to_db[c.tmp_id], f"brief:{brief_id}", primary_reason, severity, details),
            )
    con.commit()
    return tmp_to_db


def save_prerequisites(
    con: CatalogConnection,
    brief_id: int,
    DAG: Any,
    cands: list[SkillCandidate],
    tmp_to_db: dict[str, int] | None = None,
) -> int:
    by_tid = {c.tmp_id: c for c in cands}
    n = 0
    for u, v in DAG.edges():
        cu, cv = by_tid[u], by_tid[v]
        edge = DAG[u][v].get("edge")
        con.execute(
            """INSERT INTO skill_prerequisite(brief_id, src_skill_id, dst_skill_id, src_suggestion_id, dst_suggestion_id,
               src_name, dst_name, relation_type, confidence, source, review_state)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                brief_id,
                cu.canonical_skill_id,
                cv.canonical_skill_id,
                tmp_to_db.get(u) if tmp_to_db else None,
                tmp_to_db.get(v) if tmp_to_db else None,
                cu.name,
                cv.name,
                edge.relation_type if edge else "hard",
                DAG[u][v].get("conf"),
                edge.source if edge else "pipeline",
                "accepted" if (edge is None or edge.decision == "accept") else "needs_review",
            ),
        )
        n += 1
    con.commit()
    return n


def save_prerequisite_reviews(con: CatalogConnection, brief_id: int, edge_reviews: list[dict[str, Any]]) -> int:
    count = 0
    decided_edge_keys: set[str] = set()
    if _table_exists(con, "prerequisite_edge_decision"):
        decided_edge_keys = {
            str(row["edge_key"])
            for row in con.execute(
                "SELECT edge_key FROM prerequisite_edge_decision WHERE brief_id = ?",
                (brief_id,),
            )
        }
    for item in edge_reviews:
        edge_key = str(item.get("edge_key") or "")
        if edge_key and edge_key in decided_edge_keys:
            continue
        con.execute(
            """
            INSERT INTO review_queue(entity_type, entity_id, source_ref, reason_code, severity, details, status)
            VALUES ('prerequisite_edge', NULL, ?, ?, ?, ?, 'open')
            """,
            (
                f"brief:{brief_id}",
                str(item.get("reason_code", "needs_review")),
                str(item.get("severity", "info")),
                json.dumps(
                    {
                        "review_kind": "prerequisite_edge",
                        "edge_key": edge_key,
                        "src_id": item.get("src_id"),
                        "dst_id": item.get("dst_id"),
                        "edge_label": item.get("edge_label"),
                        "confidence": item.get("confidence"),
                        "source": item.get("source"),
                        "relation_type": item.get("relation_type"),
                        "reasons": item.get("reasons") or [],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        count += 1
    con.commit()
    return count


def clear_curriculum_plan(con: CatalogConnection, brief_id: int, source_policy: str = "accepted_only") -> None:
    plan_rows = con.execute(
        "SELECT id FROM curriculum_plan WHERE brief_id = ? AND source_policy = ?",
        (brief_id, source_policy),
    ).fetchall()
    for row in plan_rows:
        if _table_exists(con, "skill_set") and _table_exists(con, "skill_set_item"):
            skill_set_rows = con.execute(
                "SELECT id FROM skill_set WHERE source_type = 'curriculum_plan' AND source_id = ?",
                (row["id"],),
            ).fetchall()
            for skill_set_row in skill_set_rows:
                con.execute("DELETE FROM skill_set_item WHERE skill_set_id = ?", (skill_set_row["id"],))
            con.execute("DELETE FROM skill_set WHERE source_type = 'curriculum_plan' AND source_id = ?", (row["id"],))
        con.execute("DELETE FROM curriculum_plan_row WHERE plan_id = ?", (row["id"],))
    con.execute(
        "DELETE FROM curriculum_plan WHERE brief_id = ? AND source_policy = ?",
        (brief_id, source_policy),
    )
    con.commit()


def save_curriculum_plan(
    con: CatalogConnection,
    brief_id: int,
    plan_payload: dict[str, Any],
    source_policy: str = "accepted_only",
) -> dict[str, int]:
    clear_curriculum_plan(con, brief_id, source_policy)
    summary = _as_dict(plan_payload.get("summary"))
    cur = con.execute(
        """
        INSERT INTO curriculum_plan(
            brief_id, source_policy, status, title, audience_level,
            total_blocks, total_projects, total_hours, total_days, total_xp, payload_json, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            brief_id,
            source_policy,
            str(plan_payload.get("status", "draft")),
            plan_payload.get("title"),
            plan_payload.get("audience_level"),
            int(summary.get("blocks", 0) or 0),
            int(summary.get("projects", 0) or 0),
            float(summary.get("total_hours", 0) or 0),
            float(summary.get("total_days", 0) or 0),
            int(summary.get("total_xp", 0) or 0),
            json.dumps(plan_payload, ensure_ascii=False),
        ),
    )
    plan_id = int(cur.lastrowid or 0)
    row_count = 0
    for row in plan_payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        effort_days = None if row.get("effort_days") in (None, "") else float(row.get("effort_days", 0) or 0)
        cumulative_days = None if row.get("cumulative_days") in (None, "") else float(row.get("cumulative_days", 0) or 0)
        xp = None if row.get("xp") in (None, "") else int(row.get("xp", 0) or 0)
        con.execute(
            """
            INSERT INTO curriculum_plan_row(
                plan_id, block_index, row_number, project_index_in_block, block_title, block_goal,
                project_name, project_summary, outcomes_know, outcomes_can, outcomes_skills,
                learning_outcomes, skills_list, audience_level, required_tools, materials,
                validation_criteria, storytelling, delivery_format, group_size, effort_hours, effort_days,
                cumulative_days, xp, completion_percent, p2p_checks, weighted_skills,
                platform_project_name, artifact_links
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                int(row.get("block_index", 0) or 0),
                int(row.get("row_number", 0) or 0),
                int(row.get("project_index_in_block", 0) or 0),
                row.get("block_title"),
                row.get("block_goal"),
                row.get("project_name"),
                row.get("project_summary"),
                row.get("outcomes_know"),
                row.get("outcomes_can"),
                row.get("outcomes_skills"),
                row.get("learning_outcomes"),
                row.get("skills_list"),
                row.get("audience_level"),
                row.get("required_tools"),
                row.get("materials"),
                row.get("validation_criteria"),
                row.get("storytelling"),
                row.get("delivery_format"),
                row.get("group_size"),
                float(row.get("effort_hours", 0) or 0),
                effort_days,
                cumulative_days,
                xp,
                None if row.get("completion_percent") in (None, "") else float(row.get("completion_percent", 0) or 0),
                None if row.get("p2p_checks") in (None, "") else int(row.get("p2p_checks", 0) or 0),
                row.get("weighted_skills"),
                row.get("platform_project_name"),
                row.get("artifact_links"),
            ),
        )
        row_count += 1
    skill_set = sync_curriculum_plan_skill_set(
        con,
        brief_id=brief_id,
        plan_id=plan_id,
        plan_payload=plan_payload,
    )
    con.commit()
    return {"plan_id": plan_id, "row_count": row_count, "skill_set_id": int(skill_set.get("skill_set_id") or 0)}
