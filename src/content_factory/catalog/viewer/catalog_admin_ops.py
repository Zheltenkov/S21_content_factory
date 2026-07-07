"""Catalog-admin CRUD operations — groups / skills / indicators / aliases / skill-sets.

Extracted from ``viewer/app.py`` (slice 5b). The self-contained catalog-admin CRUD
island: list/get + create/update/remove/restore for groups, skills and indicators;
skill aliases and skill-sets; catalog-skill search + merge; skill-complexity rollups;
and artifact-template scope parsing. Consumed by ``catalog/web/routers/catalog_admin.py``.
Owns the complexity option/label/order constants (``COMPLEXITY_OPTIONS`` is re-exported
from ``app.py`` for ``web/rendering.py``). Depends only on shared helpers in ``_common``
(+ local imports that travel with the bodies) — no back-import of ``app.py`` (acyclic).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import (
    fetch_all,
    fetch_one,
    normalize_catalog_key,
    normalize_search_text,
    parse_optional_float,
    slugify,
    table_exists,
)

COMPLEXITY_OPTIONS = [
    ("", "Не указано"),
    ("trainee", "Стажер"),
    ("junior_minus", "Начальный (junior-)"),
    ("junior", "Начальный (junior)"),
    ("basic", "Базовый"),
    ("junior_plus", "Базовый (junior+)"),
    ("middle", "Продвинутый (middle)"),
    ("senior", "Продвинутый (senior)"),
    ("master", "Мастерский"),
]
COMPLEXITY_LABELS = {value: label for value, label in COMPLEXITY_OPTIONS if value}
COMPLEXITY_ORDER = {value: index for index, (value, _label) in enumerate(COMPLEXITY_OPTIONS) if value}


def complexity_label_for_band(band: str | None) -> str | None:
    if not band:
        return None
    return COMPLEXITY_LABELS.get(band, band.replace("_", " "))


def build_complexity_summary(
    min_band: str | None,
    max_band: str | None,
    min_label: str | None,
    max_label: str | None,
) -> str | None:
    if not min_band and not max_band:
        return None
    left = min_label or complexity_label_for_band(min_band)
    right = max_label or complexity_label_for_band(max_band)
    if not left:
        return right
    if not right or left == right:
        return left
    return f"{left} -> {right}"


def refresh_catalog_skill_complexity(conn: CatalogConnection, skill_id: int, commit: bool = True) -> None:
    rows = conn.execute(
        """
        SELECT
            complexity_band,
            complexity_label,
            complexity_sort_order,
            source_scale_title
        FROM indicator
        WHERE skill_id = ?
          AND complexity_sort_order IS NOT NULL
        ORDER BY complexity_sort_order, id
        """,
        (skill_id,),
    ).fetchall()

    if not rows:
        conn.execute(
            """
            UPDATE skill
            SET complexity_min_band = NULL,
                complexity_max_band = NULL,
                complexity_summary = NULL,
                source_scale_title = NULL
            WHERE id = ?
            """,
            (skill_id,),
        )
        if commit:
            conn.commit()
        return

    min_row = rows[0]
    max_row = rows[-1]
    scale_titles = {row["source_scale_title"] for row in rows if row["source_scale_title"]}
    scale_title = next(iter(scale_titles)) if len(scale_titles) == 1 else ("Смешанная шкала" if scale_titles else None)
    complexity_summary = build_complexity_summary(
        min_row["complexity_band"],
        max_row["complexity_band"],
        min_row["complexity_label"],
        max_row["complexity_label"],
    )
    conn.execute(
        """
        UPDATE skill
        SET complexity_min_band = ?,
            complexity_max_band = ?,
            complexity_summary = ?,
            source_scale_title = ?
        WHERE id = ?
        """,
        (
            min_row["complexity_band"],
            max_row["complexity_band"],
            complexity_summary,
            scale_title,
            skill_id,
        ),
    )
    if commit:
        conn.commit()


def parse_artifact_template_scopes(form_data: dict[str, str]) -> list[dict[str, Any]]:
    scope_type = form_data.get("scope_type", "coverage_area").strip() or "coverage_area"
    raw_names = form_data.get("scope_names", "").strip()
    weight = parse_optional_float(form_data.get("scope_weight")) or 1.0
    if scope_type == "any":
        return [{"scope_type": "any", "scope_name": "*", "weight": weight}]
    names = [
        item.strip()
        for item in re.split(r"[\n;]+", raw_names)
        if item.strip()
    ]
    return [{"scope_type": scope_type, "scope_name": name, "weight": weight} for name in names]


def list_catalog_groups(conn: CatalogConnection) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT
            sg.id,
            sg.name,
            sg.code,
            sg.sort_order,
            sg.status,
            sg.source,
            COUNT(DISTINCT s.id) AS skill_count,
            COUNT(DISTINCT i.id) AS indicator_count,
            (
                SELECT COUNT(*)
                FROM skill s_all
                WHERE s_all.group_id = sg.id
            ) AS total_skill_count,
            (
                SELECT COUNT(*)
                FROM indicator i_all
                JOIN skill s_all2 ON s_all2.id = i_all.skill_id
                WHERE s_all2.group_id = sg.id
            ) AS total_indicator_count
        FROM skill_group sg
        LEFT JOIN skill s ON s.group_id = sg.id AND s.is_active = 1
        LEFT JOIN indicator i ON i.skill_id = s.id AND i.is_active = 1
        WHERE sg.status != 'deprecated'
          AND (
              COALESCE(sg.source, '') = 'manual'
              OR EXISTS (
                  SELECT 1
                  FROM skill s_visible
                  WHERE s_visible.group_id = sg.id
                    AND COALESCE(s_visible.is_active, 1) = 1
              )
          )
        GROUP BY sg.id, sg.name, sg.code, sg.sort_order, sg.status, sg.source
        ORDER BY sg.sort_order, sg.name
        """,
    )


def list_skill_sets(conn: CatalogConnection) -> list[dict[str, Any]]:
    if not table_exists(conn, "skill_set"):
        return []
    return fetch_all(
        conn,
        """
        SELECT
            ss.id,
            ss.code,
            ss.title,
            ss.description,
            ss.source_type,
            ss.source_id,
            ss.source_ref,
            ss.status,
            ss.metadata_json,
            ss.created_at,
            ss.updated_at,
            COUNT(DISTINCT ssi.skill_id) AS skill_count,
            COUNT(DISTINCT CASE WHEN ssi.role = 'target' THEN ssi.skill_id END) AS target_count,
            COUNT(DISTINCT CASE WHEN ssi.role = 'prerequisite' THEN ssi.skill_id END) AS prerequisite_count,
            COUNT(DISTINCT CASE WHEN ssi.role = 'reinforcement' THEN ssi.skill_id END) AS reinforcement_count,
            COUNT(DISTINCT CASE WHEN ssi.role = 'assessment' THEN ssi.skill_id END) AS assessment_count
        FROM skill_set ss
        LEFT JOIN skill_set_item ssi ON ssi.skill_set_id = ss.id
        WHERE ss.status != 'archived'
        GROUP BY ss.id, ss.code, ss.title, ss.description, ss.source_type, ss.source_id,
                 ss.source_ref, ss.status, ss.metadata_json, ss.created_at, ss.updated_at
        ORDER BY ss.updated_at DESC, ss.created_at DESC, ss.id DESC
        """,
    )


def get_skill_set(conn: CatalogConnection, skill_set_id: int) -> dict[str, Any] | None:
    if not table_exists(conn, "skill_set"):
        return None
    return fetch_one(
        conn,
        """
        SELECT
            ss.*,
            COUNT(DISTINCT ssi.skill_id) AS skill_count
        FROM skill_set ss
        LEFT JOIN skill_set_item ssi ON ssi.skill_set_id = ss.id
        WHERE ss.id = ?
        GROUP BY ss.id
        """,
        (skill_set_id,),
    )


def list_skill_set_items(conn: CatalogConnection, skill_set_id: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "skill_set_item"):
        return []
    return fetch_all(
        conn,
        """
        SELECT
            ssi.id,
            ssi.skill_id,
            ssi.suggestion_id,
            ssi.plan_row_id,
            ssi.role,
            ssi.weight,
            ssi.sort_order,
            ssi.rationale,
            s.canonical_name,
            COALESCE(sg.name, '') AS group_name
        FROM skill_set_item ssi
        JOIN skill s ON s.id = ssi.skill_id
        LEFT JOIN skill_group sg ON sg.id = s.group_id
        WHERE ssi.skill_set_id = ?
        ORDER BY ssi.sort_order, ssi.id
        """,
        (skill_set_id,),
    )


def get_catalog_group(conn: CatalogConnection, group_id: int) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        SELECT
            sg.id,
            sg.name,
            sg.code,
            sg.sort_order,
            sg.status,
            COUNT(DISTINCT s.id) AS skill_count,
            COUNT(DISTINCT i.id) AS indicator_count,
            (
                SELECT COUNT(*)
                FROM skill s_all
                WHERE s_all.group_id = sg.id
            ) AS total_skill_count,
            (
                SELECT COUNT(*)
                FROM indicator i_all
                JOIN skill s_all2 ON s_all2.id = i_all.skill_id
                WHERE s_all2.group_id = sg.id
            ) AS total_indicator_count
        FROM skill_group sg
        LEFT JOIN skill s ON s.group_id = sg.id AND s.is_active = 1
        LEFT JOIN indicator i ON i.skill_id = s.id AND i.is_active = 1
        WHERE sg.id = ?
        GROUP BY sg.id, sg.name, sg.code, sg.sort_order, sg.status
        """,
        (group_id,),
    )


def list_catalog_group_skills(conn: CatalogConnection, group_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT
            s.id,
            s.name,
            s.code,
            s.sort_order,
            s.complexity_summary,
            s.source_scale_title,
            s.source_skill_name,
            s.resolution_status,
            s.match_note,
            s.is_active,
            COUNT(i.id) AS indicator_count,
            (
                SELECT COUNT(*)
                FROM indicator i_all
                WHERE i_all.skill_id = s.id
            ) AS total_indicator_count
        FROM skill s
        LEFT JOIN indicator i ON i.skill_id = s.id AND i.is_active = 1
        WHERE s.group_id = ?
          AND s.is_active = 1
        GROUP BY s.id, s.name, s.code, s.sort_order, s.complexity_summary, s.source_scale_title, s.source_skill_name, s.resolution_status, s.match_note, s.is_active
        ORDER BY s.is_active DESC, s.sort_order, s.name, s.id
        """,
        (group_id,),
    )


def get_catalog_skill(conn: CatalogConnection, skill_id: int) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        SELECT
            s.id,
            s.group_id,
            s.name,
            s.code,
            s.normalized_name,
            s.sort_order,
            s.complexity_min_band,
            s.complexity_max_band,
            s.complexity_summary,
            s.source_scale_title,
            s.description,
            s.source_skill_name,
            s.resolution_status,
            s.match_note,
            s.is_active,
            s.status,
            (
                SELECT COUNT(*)
                FROM indicator i_all
                WHERE i_all.skill_id = s.id
            ) AS total_indicator_count,
            sg.name AS group_name
        FROM skill s
        JOIN skill_group sg ON sg.id = s.group_id
        WHERE s.id = ?
        """,
        (skill_id,),
    )


def get_catalog_indicator(conn: CatalogConnection, indicator_id: int) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        SELECT
            id,
            skill_id,
            indicator_type,
            text,
            sort_order,
            complexity_band,
            complexity_label,
            complexity_sort_order,
            is_active,
            source_profile_name,
            source_scale_title
        FROM indicator
        WHERE id = ?
        """,
        (indicator_id,),
    )


def list_catalog_indicators(conn: CatalogConnection, skill_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT
            id,
            indicator_type,
            text,
            sort_order,
            complexity_band,
            complexity_label,
            complexity_sort_order,
            is_active,
            source_profile_name,
            source_scale_title
        FROM indicator
        WHERE skill_id = ?
          AND is_active = 1
        ORDER BY is_active DESC, sort_order, id
        """,
        (skill_id,),
    )


def list_skill_aliases(conn: CatalogConnection, skill_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        conn,
        """
        SELECT id, alias, normalized_alias, source
        FROM skill_alias
        WHERE skill_id = ?
        ORDER BY source, alias, id
        """,
        (skill_id,),
    )


def find_alias_owner(conn: CatalogConnection, normalized_alias: str, exclude_skill_id: int | None = None) -> dict[str, Any] | None:
    params: list[object] = [normalized_alias]
    exclude_clause = ""
    if exclude_skill_id is not None:
        exclude_clause = "AND s.id <> ?"
        params.append(exclude_skill_id)
    return fetch_one(
        conn,
        f"""
        SELECT s.id, s.name, s.canonical_name, s.is_active, s.status
        FROM skill_alias sa
        JOIN skill s ON s.id = sa.skill_id
        WHERE sa.normalized_alias = ?
          {exclude_clause}
          AND COALESCE(s.is_active, 1) = 1
          AND COALESCE(s.status, 'active') = 'active'
        ORDER BY s.id
        LIMIT 1
        """,
        tuple(params),
    )


def add_skill_alias(conn: CatalogConnection, skill_id: int, alias: str, source: str = "manual") -> str:
    cleaned = alias.strip()
    normalized_alias = normalize_catalog_key(cleaned)
    if not cleaned or not normalized_alias:
        return "empty"
    skill = get_catalog_skill(conn, skill_id)
    if not skill:
        return "missing_skill"
    conflict = find_alias_owner(conn, normalized_alias, exclude_skill_id=skill_id)
    if conflict:
        return "conflict"
    conn.execute(
        """
        INSERT OR IGNORE INTO skill_alias(skill_id, alias, normalized_alias, source)
        VALUES (?, ?, ?, ?)
        """,
        (skill_id, cleaned, normalized_alias, source),
    )
    conn.commit()
    return "added"


def remove_skill_alias(conn: CatalogConnection, skill_id: int, alias_id: int) -> str:
    row = fetch_one(
        conn,
        "SELECT id FROM skill_alias WHERE id = ? AND skill_id = ?",
        (alias_id, skill_id),
    )
    if not row:
        return "missing"
    conn.execute("DELETE FROM skill_alias WHERE id = ? AND skill_id = ?", (alias_id, skill_id))
    conn.commit()
    return "removed"


def search_catalog_skills(
    conn: CatalogConnection,
    query: str,
    exclude_skill_id: int | None = None,
    limit: int = 15,
) -> list[dict[str, Any]]:
    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return []
    params: list[object] = [normalized_query, normalized_query, normalized_query, normalized_query]
    exclude_clause = ""
    if exclude_skill_id is not None:
        exclude_clause = "AND s.id <> ?"
        params.append(exclude_skill_id)
    params.append(limit)
    return fetch_all(
        conn,
        f"""
        SELECT
            s.id,
            s.name,
            s.canonical_name,
            s.normalized_name,
            s.group_id,
            sg.name AS group_name,
            s.is_active,
            s.status,
            COUNT(DISTINCT i.id) AS indicator_count,
            COUNT(DISTINCT sa.id) AS alias_count
        FROM skill s
        LEFT JOIN skill_group sg ON sg.id = s.group_id
        LEFT JOIN indicator i ON i.skill_id = s.id AND i.is_active = 1
        LEFT JOIN skill_alias sa ON sa.skill_id = s.id
        WHERE (
            instr(search_norm(COALESCE(s.name, '')), ?) > 0
            OR instr(search_norm(COALESCE(s.canonical_name, '')), ?) > 0
            OR instr(search_norm(COALESCE(s.normalized_name, '')), ?) > 0
            OR EXISTS (
                SELECT 1
                FROM skill_alias sa2
                WHERE sa2.skill_id = s.id
                  AND instr(search_norm(sa2.alias), ?) > 0
            )
        )
        {exclude_clause}
        GROUP BY s.id, s.name, s.canonical_name, s.normalized_name, s.group_id, sg.name, s.is_active, s.status
        ORDER BY COALESCE(s.is_active, 1) DESC, s.name
        LIMIT ?
        """,
        tuple(params),
    )


def merge_catalog_skills(conn: CatalogConnection, source_skill_id: int, target_skill_id: int) -> dict[str, int | str]:
    if source_skill_id == target_skill_id:
        return {"status": "same_skill"}
    source = get_catalog_skill(conn, source_skill_id)
    target = get_catalog_skill(conn, target_skill_id)
    if not source or not target:
        return {"status": "missing_skill"}

    moved_aliases = 0
    moved_indicators = 0
    archived_duplicate_indicators = 0
    now = datetime.now(UTC).isoformat()

    # Preserve the source canonical label as an alias of the merge target.
    for alias in [source.get("name"), source.get("canonical_name"), source.get("source_skill_name")]:
        if alias and add_skill_alias(conn, target_skill_id, str(alias), source="merge") == "added":
            moved_aliases += 1

    for alias_row in list_skill_aliases(conn, source_skill_id):
        alias = str(alias_row["alias"] or "").strip()
        normalized_alias = str(alias_row["normalized_alias"] or "").strip() or normalize_catalog_key(alias)
        if not alias or not normalized_alias:
            continue
        conflict = find_alias_owner(conn, normalized_alias, exclude_skill_id=source_skill_id)
        if conflict and int(conflict["id"]) != target_skill_id:
            continue
        inserted = conn.execute(
            """
            INSERT OR IGNORE INTO skill_alias(skill_id, alias, normalized_alias, source)
            VALUES (?, ?, ?, ?)
            """,
            (target_skill_id, alias, normalized_alias, str(alias_row["source"] or "merge")),
        ).rowcount
        moved_aliases += max(int(inserted or 0), 0)

    for indicator in fetch_all(conn, "SELECT * FROM indicator WHERE skill_id = ? ORDER BY sort_order, id", (source_skill_id,)):
        duplicate = fetch_one(
            conn,
            """
            SELECT id
            FROM indicator
            WHERE skill_id = ?
              AND indicator_type = ?
              AND normalized_text = ?
            """,
            (target_skill_id, indicator["indicator_type"], indicator["normalized_text"]),
        )
        if duplicate:
            conn.execute(
                """
                UPDATE indicator
                SET is_active = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, indicator["id"]),
            )
            archived_duplicate_indicators += 1
            continue
        conn.execute(
            """
            UPDATE indicator
            SET skill_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (target_skill_id, now, indicator["id"]),
        )
        moved_indicators += 1

    if table_exists(conn, "skill_suggestion"):
        conn.execute(
            """
            UPDATE skill_suggestion
            SET canonical_skill_id = ?,
                resolution = 'alias'
            WHERE canonical_skill_id = ?
            """,
            (target_skill_id, source_skill_id),
        )
    if table_exists(conn, "skill_promotion_log"):
        conn.execute("UPDATE skill_promotion_log SET skill_id = ? WHERE skill_id = ?", (target_skill_id, source_skill_id))
    if table_exists(conn, "skill_prerequisite"):
        conn.execute("UPDATE skill_prerequisite SET src_skill_id = ? WHERE src_skill_id = ?", (target_skill_id, source_skill_id))
        conn.execute("UPDATE skill_prerequisite SET dst_skill_id = ? WHERE dst_skill_id = ?", (target_skill_id, source_skill_id))
    if table_exists(conn, "competency_skill"):
        conn.execute("UPDATE competency_skill SET skill_id = ? WHERE skill_id = ?", (target_skill_id, source_skill_id))

    conn.execute(
        """
        UPDATE skill
        SET is_active = 0,
            status = 'deprecated',
            match_note = COALESCE(match_note || chr(10), '') || ?,
            updated_at = ?
        WHERE id = ?
        """,
        (f"Merged into skill #{target_skill_id}: {target.get('name') or target.get('canonical_name')}", now, source_skill_id),
    )
    refresh_catalog_skill_complexity(conn, target_skill_id, commit=False)
    refresh_catalog_skill_complexity(conn, source_skill_id, commit=False)
    conn.commit()
    return {
        "status": "merged",
        "moved_aliases": moved_aliases,
        "moved_indicators": moved_indicators,
        "archived_duplicate_indicators": archived_duplicate_indicators,
    }


def list_archived_groups(conn: CatalogConnection, query: str = "") -> list[dict[str, Any]]:
    params: list[object] = []
    where_parts = ["sg.status = 'deprecated'"]
    if query:
        needle = normalize_search_text(query)
        where_parts.append("(instr(search_norm(sg.name), ?) > 0 OR instr(search_norm(sg.code), ?) > 0)")
        params.extend([needle, needle])
    sql = f"""
        SELECT
            sg.id,
            sg.name,
            sg.code,
            sg.sort_order,
            sg.status,
            COUNT(DISTINCT s.id) AS total_skill_count,
            COUNT(DISTINCT i.id) AS total_indicator_count
        FROM skill_group sg
        LEFT JOIN skill s ON s.group_id = sg.id
        LEFT JOIN indicator i ON i.skill_id = s.id
        WHERE {' AND '.join(where_parts)}
        GROUP BY sg.id, sg.name, sg.code, sg.sort_order, sg.status
        ORDER BY sg.sort_order, sg.name
    """
    return fetch_all(conn, sql, tuple(params))


def list_archived_skills(conn: CatalogConnection, query: str = "") -> list[dict[str, Any]]:
    params: list[object] = []
    where_parts = ["s.is_active = 0"]
    if query:
        needle = normalize_search_text(query)
        where_parts.append(
            """
            (
                instr(search_norm(s.name), ?) > 0
                OR instr(search_norm(s.normalized_name), ?) > 0
                OR instr(search_norm(COALESCE(s.source_skill_name, '')), ?) > 0
                OR instr(search_norm(sg.name), ?) > 0
            )
            """
        )
        params.extend([needle, needle, needle, needle])
    sql = f"""
        SELECT
            s.id,
            s.group_id,
            s.name,
            s.sort_order,
            s.complexity_summary,
            s.source_scale_title,
            s.source_skill_name,
            s.resolution_status,
            sg.name AS group_name,
            COUNT(i.id) AS total_indicator_count
        FROM skill s
        JOIN skill_group sg ON sg.id = s.group_id
        LEFT JOIN indicator i ON i.skill_id = s.id
        WHERE {' AND '.join(where_parts)}
        GROUP BY s.id, s.group_id, s.name, s.sort_order, s.complexity_summary, s.source_scale_title, s.source_skill_name, s.resolution_status, sg.name, sg.sort_order
        ORDER BY sg.sort_order, s.sort_order, s.name, s.id
    """
    return fetch_all(conn, sql, tuple(params))


def list_archived_indicators(conn: CatalogConnection, query: str = "") -> list[dict[str, Any]]:
    params: list[object] = []
    where_parts = ["i.is_active = 0"]
    if query:
        needle = normalize_search_text(query)
        where_parts.append(
            """
            (
                instr(search_norm(i.text), ?) > 0
                OR instr(search_norm(i.normalized_text), ?) > 0
                OR instr(search_norm(i.indicator_type), ?) > 0
                OR instr(search_norm(s.name), ?) > 0
                OR instr(search_norm(s.normalized_name), ?) > 0
                OR instr(search_norm(sg.name), ?) > 0
                OR instr(search_norm(COALESCE(i.source_profile_name, '')), ?) > 0
            )
            """
        )
        params.extend([needle, needle, needle, needle, needle, needle, needle])
    sql = f"""
        SELECT
            i.id,
            i.skill_id,
            i.indicator_type,
            i.text,
            i.sort_order,
            i.complexity_band,
            i.complexity_label,
            i.source_profile_name,
            i.source_scale_title,
            s.name AS skill_name,
            sg.id AS group_id,
            sg.name AS group_name
        FROM indicator i
        JOIN skill s ON s.id = i.skill_id
        JOIN skill_group sg ON sg.id = s.group_id
        WHERE {' AND '.join(where_parts)}
        ORDER BY sg.sort_order, s.sort_order, i.sort_order, i.id
    """
    return fetch_all(conn, sql, tuple(params))


def create_catalog_group(conn: CatalogConnection, name: str, sort_order: int, status: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO skill_group (code, name, sort_order, status, source, updated_at)
        VALUES (?, ?, ?, ?, 'manual', ?)
        """,
        (f"group-{slugify(name)}", name.strip(), sort_order, status, datetime.now(UTC).isoformat()),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def update_catalog_group(conn: CatalogConnection, group_id: int, name: str, sort_order: int, status: str) -> None:
    conn.execute(
        """
        UPDATE skill_group
        SET code = ?, name = ?, sort_order = ?, status = ?, updated_at = ?
        WHERE id = ?
        """,
        (f"group-{slugify(name)}", name.strip(), sort_order, status, datetime.now(UTC).isoformat(), group_id),
    )
    conn.commit()


def remove_catalog_group(conn: CatalogConnection, group_id: int) -> str:
    row = fetch_one(
        conn,
        """
        SELECT
            sg.id,
            COALESCE((
                SELECT COUNT(*)
                FROM skill s_all
                WHERE s_all.group_id = sg.id
            ), 0) AS total_skill_count
        FROM skill_group sg
        WHERE sg.id = ?
        """,
        (group_id,),
    )
    if not row:
        return "missing"
    if row["total_skill_count"]:
        conn.execute(
            """
            UPDATE skill_group
            SET status = 'deprecated',
                updated_at = ?
            WHERE id = ?
            """,
            (datetime.now(UTC).isoformat(), group_id),
        )
        conn.commit()
        return "archived"

    conn.execute("DELETE FROM skill_group WHERE id = ?", (group_id,))
    conn.commit()
    return "deleted"


def restore_catalog_group(conn: CatalogConnection, group_id: int) -> str:
    group = get_catalog_group(conn, group_id)
    if not group and not fetch_one(conn, "SELECT id FROM skill_group WHERE id = ?", (group_id,)):
        return "missing"
    conn.execute(
        """
        UPDATE skill_group
        SET status = 'active',
            updated_at = ?
        WHERE id = ?
        """,
        (datetime.now(UTC).isoformat(), group_id),
    )
    conn.commit()
    return "restored"


def create_catalog_skill(
    conn: CatalogConnection,
    group_id: int,
    name: str,
    sort_order: int,
    description: str,
    source_skill_name: str,
    resolution_status: str,
    match_note: str,
    is_active: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO skill (
            group_id,
            code,
            canonical_name,
            name,
            normalized_name,
            skill_type,
            status,
            sort_order,
            description,
            source_skill_name,
            resolution_status,
            match_note,
            is_active,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'unknown', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            group_id,
            f"skill-{slugify(name)}-{group_id}",
            name.strip(),
            name.strip(),
            normalize_catalog_key(name),
            "active" if is_active else "candidate",
            sort_order,
            description.strip() or None,
            source_skill_name.strip() or None,
            resolution_status,
            match_note.strip() or None,
            is_active,
            datetime.now(UTC).isoformat(),
            datetime.now(UTC).isoformat(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def update_catalog_skill(
    conn: CatalogConnection,
    skill_id: int,
    name: str,
    sort_order: int,
    description: str,
    source_skill_name: str,
    resolution_status: str,
    match_note: str,
    is_active: int,
) -> None:
    skill = get_catalog_skill(conn, skill_id)
    if not skill:
        return
    conn.execute(
        """
        UPDATE skill
        SET code = ?,
            canonical_name = ?,
            name = ?,
            normalized_name = ?,
            status = ?,
            sort_order = ?,
            description = ?,
            source_skill_name = ?,
            resolution_status = ?,
            match_note = ?,
            is_active = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            f"skill-{slugify(name)}-{skill['group_id']}",
            name.strip(),
            name.strip(),
            normalize_catalog_key(name),
            "active" if is_active else "candidate",
            sort_order,
            description.strip() or None,
            source_skill_name.strip() or None,
            resolution_status,
            match_note.strip() or None,
            is_active,
            datetime.now(UTC).isoformat(),
            skill_id,
        ),
    )
    conn.commit()


def remove_catalog_skill(conn: CatalogConnection, skill_id: int) -> str:
    skill = get_catalog_skill(conn, skill_id)
    if not skill:
        return "missing"

    indicator_count = conn.execute("SELECT COUNT(*) FROM indicator WHERE skill_id = ?", (skill_id,)).fetchone()[0]
    if indicator_count:
        conn.execute(
            """
            UPDATE skill
            SET is_active = 0,
                status = 'candidate',
                updated_at = ?
            WHERE id = ?
            """,
            (datetime.now(UTC).isoformat(), skill_id),
        )
        conn.commit()
        return "archived"

    conn.execute("DELETE FROM skill WHERE id = ?", (skill_id,))
    conn.commit()
    return "deleted"


def restore_catalog_skill(conn: CatalogConnection, skill_id: int) -> str:
    skill = get_catalog_skill(conn, skill_id)
    if not skill:
        return "missing"
    conn.execute(
        """
        UPDATE skill
        SET is_active = 1,
            status = 'active',
            updated_at = ?
        WHERE id = ?
        """,
        (datetime.now(UTC).isoformat(), skill_id),
    )
    conn.execute(
        """
        UPDATE skill_group
        SET status = 'active',
            updated_at = ?
        WHERE id = ?
        """,
        (datetime.now(UTC).isoformat(), skill["group_id"]),
    )
    refresh_catalog_skill_complexity(conn, skill_id, commit=False)
    conn.commit()
    return "restored"


def create_catalog_indicator(
    conn: CatalogConnection,
    skill_id: int,
    indicator_type: str,
    text: str,
    sort_order: int,
    complexity_band: str,
    is_active: int,
) -> int:
    normalized_band = complexity_band.strip()
    complexity_label = complexity_label_for_band(normalized_band) if normalized_band else None
    complexity_sort_order = COMPLEXITY_ORDER.get(normalized_band) if normalized_band else None
    cursor = conn.execute(
        """
        INSERT INTO indicator (
            skill_id,
            indicator_type,
            text,
            normalized_text,
            sort_order,
            complexity_band,
            complexity_label,
            complexity_sort_order,
            source_profile_name,
            source_scale_title,
            is_active,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            skill_id,
            indicator_type.strip(),
            text.strip(),
            text.casefold().replace("ё", "е").strip(),
            sort_order,
            normalized_band or None,
            complexity_label,
            complexity_sort_order,
            "manual",
            None,
            is_active,
            datetime.now(UTC).isoformat(),
        ),
    )
    refresh_catalog_skill_complexity(conn, skill_id, commit=False)
    conn.commit()
    return int(cursor.lastrowid or 0)


def update_catalog_indicator(
    conn: CatalogConnection,
    indicator_id: int,
    indicator_type: str,
    text: str,
    sort_order: int,
    complexity_band: str,
    is_active: int,
) -> None:
    row = conn.execute("SELECT skill_id FROM indicator WHERE id = ?", (indicator_id,)).fetchone()
    if not row:
        return
    normalized_band = complexity_band.strip()
    complexity_label = complexity_label_for_band(normalized_band) if normalized_band else None
    complexity_sort_order = COMPLEXITY_ORDER.get(normalized_band) if normalized_band else None
    conn.execute(
        """
        UPDATE indicator
        SET indicator_type = ?,
            text = ?,
            normalized_text = ?,
            sort_order = ?,
            complexity_band = ?,
            complexity_label = ?,
            complexity_sort_order = ?,
            is_active = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            indicator_type.strip(),
            text.strip(),
            text.casefold().replace("ё", "е").strip(),
            sort_order,
            normalized_band or None,
            complexity_label,
            complexity_sort_order,
            is_active,
            datetime.now(UTC).isoformat(),
            indicator_id,
        ),
    )
    refresh_catalog_skill_complexity(conn, row["skill_id"], commit=False)
    conn.commit()


def remove_catalog_indicator(conn: CatalogConnection, indicator_id: int) -> str:
    indicator = get_catalog_indicator(conn, indicator_id)
    if not indicator:
        return "missing"

    skill_id = int(indicator["skill_id"])
    if indicator.get("source_profile_name") == "manual":
        conn.execute("DELETE FROM indicator WHERE id = ?", (indicator_id,))
        refresh_catalog_skill_complexity(conn, skill_id, commit=False)
        conn.commit()
        return "deleted"

    conn.execute(
        """
        UPDATE indicator
        SET is_active = 0,
            updated_at = ?
        WHERE id = ?
        """,
        (datetime.now(UTC).isoformat(), indicator_id),
    )
    refresh_catalog_skill_complexity(conn, skill_id, commit=False)
    conn.commit()
    return "archived"


def restore_catalog_indicator(conn: CatalogConnection, indicator_id: int) -> str:
    indicator = get_catalog_indicator(conn, indicator_id)
    if not indicator:
        return "missing"

    skill = get_catalog_skill(conn, int(indicator["skill_id"]))
    conn.execute(
        """
        UPDATE indicator
        SET is_active = 1,
            updated_at = ?
        WHERE id = ?
        """,
        (datetime.now(UTC).isoformat(), indicator_id),
    )
    if skill:
        conn.execute(
            """
            UPDATE skill
            SET is_active = 1,
                updated_at = ?
            WHERE id = ?
            """,
            (datetime.now(UTC).isoformat(), skill["id"]),
        )
        conn.execute(
            """
            UPDATE skill_group
            SET status = 'active',
                updated_at = ?
            WHERE id = ?
            """,
            (datetime.now(UTC).isoformat(), skill["group_id"]),
        )
        refresh_catalog_skill_complexity(conn, skill["id"], commit=False)
    conn.commit()
    return "restored"
