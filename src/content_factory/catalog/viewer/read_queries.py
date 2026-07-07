"""Read-side catalog queries (competencies, profiles, directory hierarchy).

Extracted from ``viewer/app.py`` (slice 3). Read-only lookups consumed by the
``catalog/web/routers/pages.py`` router; depends only on shared helpers in _common.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import (
    display_catalog_title,
    fetch_all,
    fetch_one,
    load_summary,
    normalize_competency_title,
    table_exists,
)

_BASE_DIR = Path(__file__).resolve().parent
DEFAULT_COMPARE_REPORT = _BASE_DIR.parent / "artifacts" / "live_catalog_comparison.json"


def resolve_directory_profile(conn: CatalogConnection) -> dict[str, Any] | None:
    comparison_report = load_summary(DEFAULT_COMPARE_REPORT)
    preferred_name = comparison_report.get("profile_name") if isinstance(comparison_report, dict) else None
    if preferred_name:
        preferred = fetch_one(conn, "SELECT id, name, source_kind FROM profile WHERE name = ?", (preferred_name,))
        if preferred:
            return preferred

    return fetch_one(
        conn,
        """
        SELECT id, name, source_kind
        FROM profile
        ORDER BY CASE WHEN name LIKE '%Java%' THEN 0 ELSE 1 END, name
        LIMIT 1
        """,
    )


def has_directory_hierarchy(conn: CatalogConnection) -> bool:
    if not table_exists(conn, "typed_competency") or not table_exists(conn, "typed_competency_skill"):
        return False
    row = conn.execute("SELECT COUNT(*) AS cnt FROM typed_competency").fetchone()
    return bool(row and row["cnt"])


def list_directory_hierarchy(
    conn: CatalogConnection,
    query: str,
    scope: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    profile = resolve_directory_profile(conn)
    typed_competencies = fetch_all(
        conn,
        """
        SELECT id, name, sort_order
        FROM typed_competency
        WHERE status = 'active'
        ORDER BY sort_order, name
        """,
    )
    typed_skills = fetch_all(
        conn,
        """
        SELECT
            tcs.id,
            tcs.typed_competency_id,
            tcs.source_skill_name,
            tcs.sort_order,
            tcs.resolution_status,
            tcs.match_note,
            s.id AS skill_id,
            s.canonical_name
        FROM typed_competency_skill tcs
        LEFT JOIN skill s ON s.id = tcs.skill_id
        WHERE tcs.source = 'live_snapshot'
        ORDER BY tcs.typed_competency_id, tcs.sort_order
        """,
    )

    indicator_map: dict[int, list[dict[str, Any]]] = {}
    if profile:
        indicator_rows = fetch_all(
            conn,
            """
            SELECT
                s.id AS skill_id,
                COALESCE(d.title, 'Не указано') AS dimension_title,
                ilc.raw_value
            FROM profile_competency pc
            JOIN competency_skill cs ON cs.profile_competency_id = pc.id
            JOIN skill s ON s.id = cs.skill_id
            LEFT JOIN indicator_row ir ON ir.competency_skill_id = cs.id
            LEFT JOIN dimension d ON d.id = ir.dimension_id
            LEFT JOIN indicator_level_cell ilc ON ilc.indicator_row_id = ir.id
            WHERE pc.profile_id = ?
              AND COALESCE(TRIM(ilc.raw_value), '') <> ''
            ORDER BY
                s.canonical_name,
                CASE COALESCE(d.title, '')
                    WHEN 'Знает' THEN 1
                    WHEN 'Умеет' THEN 2
                    ELSE 3
                END,
                ilc.sort_order,
                ilc.raw_value
            """,
            (profile["id"],),
        )
        seen_by_skill: dict[int, set[str]] = {}
        for row in indicator_rows:
            skill_id = row["skill_id"]
            full_text = f"{row['dimension_title']}: {row['raw_value']}".strip()
            seen_by_skill.setdefault(skill_id, set())
            if full_text in seen_by_skill[skill_id]:
                continue
            seen_by_skill[skill_id].add(full_text)
            indicator_map.setdefault(skill_id, []).append(
                {
                    "dimension": row["dimension_title"],
                    "text": row["raw_value"],
                    "full_text": full_text,
                }
            )

    query_folded = query.casefold()
    groups: list[dict[str, Any]] = []
    skill_rows_by_group: dict[int, list[dict[str, Any]]] = {}
    for row in typed_skills:
        skill_rows_by_group.setdefault(row["typed_competency_id"], []).append(row)

    resolution_labels = {
        "matched": "совпало",
        "alias": "сопоставлено по alias",
        "manual": "сопоставлено вручную",
        "fuzzy": "сопоставлено нечетко",
        "missing": "нет локального skill",
    }

    for typed_competency in typed_competencies:
        group_name = typed_competency["name"]
        group_matches = bool(query_folded) and query_folded in group_name.casefold()
        all_skills: list[dict[str, Any]] = []

        for skill_row in skill_rows_by_group.get(typed_competency["id"], []):
            display_name = skill_row["canonical_name"] or skill_row["source_skill_name"]
            indicators = indicator_map.get(skill_row["skill_id"], []) if skill_row["skill_id"] else []
            skill_entry = {
                "id": skill_row["id"],
                "display_name": display_name,
                "source_name": skill_row["source_skill_name"],
                "resolved_name": skill_row["canonical_name"],
                "resolution_status": skill_row["resolution_status"],
                "resolution_label": resolution_labels.get(skill_row["resolution_status"], skill_row["resolution_status"]),
                "match_note": skill_row["match_note"],
                "indicator_count": len(indicators),
                "indicators": indicators,
            }
            all_skills.append(skill_entry)

        if not query_folded:
            matched_skills = all_skills
        elif scope == "competencies":
            matched_skills = all_skills if group_matches else []
        else:
            matched_skills = []
            for skill_entry in all_skills:
                skill_matches = query_folded in skill_entry["display_name"].casefold() or query_folded in skill_entry["source_name"].casefold()
                indicator_matches = any(query_folded in indicator["full_text"].casefold() for indicator in skill_entry["indicators"])
                if scope == "skills" and skill_matches:
                    matched_skills.append(skill_entry)
                elif scope == "indicators" and indicator_matches:
                    matched_skills.append(skill_entry)
                elif scope == "all" and (group_matches or skill_matches or indicator_matches):
                    matched_skills.append(skill_entry)
            if scope == "all" and group_matches:
                matched_skills = all_skills

        if not matched_skills:
            continue

        groups.append(
            {
                "id": typed_competency["id"],
                "name": group_name,
                "skill_count": len(matched_skills),
                "indicator_count": sum(skill["indicator_count"] for skill in matched_skills),
                "skills": matched_skills,
                "open_on_load": bool(query_folded),
            }
        )

    existing_group_names = {normalize_competency_title(group["name"]) for group in groups}
    groups.extend(list_canonical_directory_additions(conn, query, scope, existing_group_names))
    return groups, profile


def list_canonical_directory_additions(
    conn: CatalogConnection,
    query: str,
    scope: str,
    existing_group_names: set[str],
) -> list[dict[str, Any]]:
    """Return accepted canonical competencies that are not part of the imported live hierarchy yet."""
    from content_factory.catalog.pipeline import competency_catalog

    required_tables = ("profile", "profile_competency", "competency", "competency_skill", "skill")
    if not all(table_exists(conn, name) for name in required_tables):
        return []

    profile_competencies = fetch_all(
        conn,
        """
        SELECT
            pc.id AS profile_competency_id,
            c.id AS competency_id,
            c.title,
            c.status,
            COUNT(DISTINCT cs.skill_id) AS skill_count
        FROM profile p
        JOIN profile_competency pc ON pc.profile_id = p.id
        JOIN competency c ON c.id = pc.competency_id
        LEFT JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        WHERE p.slug = ?
          AND pc.review_state = 'accepted'
          AND c.status = 'active'
        GROUP BY pc.id, c.id, c.title, c.status
        HAVING COUNT(DISTINCT cs.skill_id) > 0
        ORDER BY c.title
        """,
        (competency_catalog.SERVICE_PROFILE_SLUG,),
    )
    profile_competencies = [
        row for row in profile_competencies if normalize_competency_title(row["title"]) not in existing_group_names
    ]
    if not profile_competencies:
        return []

    pc_ids = [int(row["profile_competency_id"]) for row in profile_competencies]
    placeholders = ", ".join("?" for _ in pc_ids)
    skill_rows = fetch_all(
        conn,
        f"""
        SELECT
            cs.id,
            cs.profile_competency_id,
            cs.source_skill_name,
            cs.skill_order,
            s.id AS skill_id,
            s.canonical_name,
            COALESCE(s.resolution_status, 'matched') AS resolution_status,
            s.match_note
        FROM competency_skill cs
        JOIN skill s ON s.id = cs.skill_id
        WHERE cs.profile_competency_id IN ({placeholders})
        ORDER BY cs.profile_competency_id, cs.skill_order, s.canonical_name
        """,
        tuple(pc_ids),
    )
    indicator_rows = fetch_all(
        conn,
        f"""
        SELECT
            cs.id AS competency_skill_id,
            s.id AS skill_id,
            COALESCE(d.title, 'Не указано') AS dimension_title,
            COALESCE(NULLIF(TRIM(ilc.raw_value), ''), NULLIF(TRIM(ir.base_text), '')) AS raw_value,
            COALESCE(ilc.sort_order, 0) AS sort_order
        FROM competency_skill cs
        JOIN skill s ON s.id = cs.skill_id
        LEFT JOIN indicator_row ir ON ir.competency_skill_id = cs.id
        LEFT JOIN dimension d ON d.id = ir.dimension_id
        LEFT JOIN indicator_level_cell ilc ON ilc.indicator_row_id = ir.id
        WHERE cs.profile_competency_id IN ({placeholders})
          AND COALESCE(NULLIF(TRIM(ilc.raw_value), ''), NULLIF(TRIM(ir.base_text), '')) IS NOT NULL
        ORDER BY
            s.canonical_name,
            CASE COALESCE(d.title, '')
                WHEN 'Знает' THEN 1
                WHEN 'Умеет' THEN 2
                ELSE 3
            END,
            COALESCE(ilc.sort_order, 0),
            raw_value
        """,
        tuple(pc_ids),
    )

    indicator_map: dict[int, list[dict[str, Any]]] = {}
    seen_by_skill: dict[int, set[str]] = {}
    for row in indicator_rows:
        skill_id = int(row["skill_id"])
        full_text = f"{row['dimension_title']}: {row['raw_value']}".strip()
        seen_by_skill.setdefault(skill_id, set())
        if full_text in seen_by_skill[skill_id]:
            continue
        seen_by_skill[skill_id].add(full_text)
        indicator_map.setdefault(skill_id, []).append(
            {
                "dimension": row["dimension_title"],
                "text": row["raw_value"],
                "full_text": full_text,
            }
        )

    resolution_labels = {
        "matched": "совпало",
        "alias": "сопоставлено по alias",
        "manual": "сопоставлено вручную",
        "fuzzy": "сопоставлено нечетко",
        "missing": "нет локального skill",
    }
    skills_by_pc: dict[int, list[dict[str, Any]]] = {}
    for row in skill_rows:
        display_name = row["canonical_name"] or row["source_skill_name"]
        indicators = indicator_map.get(int(row["skill_id"]), [])
        skills_by_pc.setdefault(int(row["profile_competency_id"]), []).append(
            {
                "id": row["id"],
                "display_name": display_name,
                "source_name": row["source_skill_name"] or display_name,
                "resolved_name": row["canonical_name"],
                "resolution_status": row["resolution_status"],
                "resolution_label": resolution_labels.get(row["resolution_status"], row["resolution_status"]),
                "match_note": row["match_note"],
                "indicator_count": len(indicators),
                "indicators": indicators,
            }
        )

    query_folded = query.casefold()
    groups: list[dict[str, Any]] = []
    for row in profile_competencies:
        group_name = display_catalog_title(row["title"])
        group_matches = bool(query_folded) and query_folded in group_name.casefold()
        all_skills = skills_by_pc.get(int(row["profile_competency_id"]), [])
        if not query_folded:
            matched_skills = all_skills
        elif scope == "competencies":
            matched_skills = all_skills if group_matches else []
        else:
            matched_skills = []
            for skill_entry in all_skills:
                skill_matches = query_folded in skill_entry["display_name"].casefold() or query_folded in skill_entry["source_name"].casefold()
                indicator_matches = any(query_folded in indicator["full_text"].casefold() for indicator in skill_entry["indicators"])
                if scope == "skills" and skill_matches:
                    matched_skills.append(skill_entry)
                elif scope == "indicators" and indicator_matches:
                    matched_skills.append(skill_entry)
                elif scope == "all" and (group_matches or skill_matches or indicator_matches):
                    matched_skills.append(skill_entry)
            if scope == "all" and group_matches:
                matched_skills = all_skills

        if not matched_skills:
            continue
        groups.append(
            {
                "id": f"canonical-{row['competency_id']}",
                "name": group_name,
                "skill_count": len(matched_skills),
                "indicator_count": sum(skill["indicator_count"] for skill in matched_skills),
                "skills": matched_skills,
                "open_on_load": bool(query_folded),
            }
        )
    return groups


def list_competencies(conn: CatalogConnection, query: str, scope: str) -> list[dict[str, Any]]:
    params: list[object] = []
    where_parts: list[str] = []
    if query:
        like = f"%{query}%"
        if scope == "competencies":
            where_parts.append("(c.title LIKE ? OR COALESCE(c.description, '') LIKE ?)")
            params.extend([like, like])
        elif scope == "skills":
            where_parts.append(
                """EXISTS (
                    SELECT 1
                    FROM profile_competency pc2
                    JOIN competency_skill cs2 ON cs2.profile_competency_id = pc2.id
                    JOIN skill s2 ON s2.id = cs2.skill_id
                    WHERE pc2.competency_id = c.id
                      AND s2.canonical_name LIKE ?
                )"""
            )
            params.append(like)
        elif scope == "indicators":
            where_parts.append(
                """EXISTS (
                    SELECT 1
                    FROM profile_competency pc2
                    JOIN competency_skill cs2 ON cs2.profile_competency_id = pc2.id
                    JOIN indicator_row ir2 ON ir2.competency_skill_id = cs2.id
                    WHERE pc2.competency_id = c.id
                      AND COALESCE(ir2.base_text, '') LIKE ?
                )"""
            )
            params.append(like)
        else:
            where_parts.append(
                """(
                    c.title LIKE ?
                    OR COALESCE(c.description, '') LIKE ?
                    OR EXISTS (
                        SELECT 1
                        FROM profile_competency pc2
                        JOIN competency_skill cs2 ON cs2.profile_competency_id = pc2.id
                        JOIN skill s2 ON s2.id = cs2.skill_id
                        WHERE pc2.competency_id = c.id
                          AND s2.canonical_name LIKE ?
                    )
                    OR EXISTS (
                        SELECT 1
                        FROM profile_competency pc3
                        JOIN competency_skill cs3 ON cs3.profile_competency_id = pc3.id
                        JOIN indicator_row ir3 ON ir3.competency_skill_id = cs3.id
                        WHERE pc3.competency_id = c.id
                          AND COALESCE(ir3.base_text, '') LIKE ?
                    )
                )"""
            )
            params.extend([like, like, like, like])

    sql = f"""
        SELECT
            c.id,
            c.title,
            c.description,
            c.status,
            COUNT(DISTINCT pc.profile_id) AS profile_count,
            COUNT(DISTINCT cs.skill_id) AS skill_count,
            COUNT(DISTINCT ir.id) AS indicator_count
        FROM competency c
        LEFT JOIN profile_competency pc ON pc.competency_id = c.id
        LEFT JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        LEFT JOIN indicator_row ir ON ir.competency_skill_id = cs.id
        {"WHERE " + " AND ".join(where_parts) if where_parts else ""}
        GROUP BY c.id, c.title, c.description, c.status
        HAVING COUNT(DISTINCT cs.skill_id) > 0
        ORDER BY c.title
    """
    return fetch_all(conn, sql, tuple(params))


def get_competency(conn: CatalogConnection, competency_id: int) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        SELECT
            c.id,
            c.title,
            c.description,
            c.status,
            COUNT(DISTINCT pc.profile_id) AS profile_count,
            COUNT(DISTINCT cs.skill_id) AS skill_count,
            COUNT(DISTINCT ir.id) AS indicator_count
        FROM competency c
        LEFT JOIN profile_competency pc ON pc.competency_id = c.id
        LEFT JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        LEFT JOIN indicator_row ir ON ir.competency_skill_id = cs.id
        WHERE c.id = ?
        GROUP BY c.id, c.title, c.description, c.status
        """,
        (competency_id,),
    )


def get_competency_skills(conn: CatalogConnection, competency_id: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT
            s.id AS skill_id,
            s.canonical_name,
            s.skill_type,
            COUNT(DISTINCT pc.profile_id) AS profile_count,
            COUNT(DISTINCT ir.id) AS indicator_count,
            GROUP_CONCAT(DISTINCT p.name) AS profile_names
        FROM profile_competency pc
        JOIN profile p ON p.id = pc.profile_id
        JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        JOIN skill s ON s.id = cs.skill_id
        LEFT JOIN indicator_row ir ON ir.competency_skill_id = cs.id
        WHERE pc.competency_id = ?
        GROUP BY s.id, s.canonical_name, s.skill_type
        ORDER BY s.canonical_name
        """,
        (competency_id,),
    )

    indicator_rows = fetch_all(
        conn,
        """
        SELECT
            s.id AS skill_id,
            s.canonical_name,
            d.title AS dimension_title,
            COALESCE(ir.base_text, '') AS indicator_text,
            ilc.raw_level_label,
            ilc.raw_value,
            ilc.value_kind
        FROM profile_competency pc
        JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        JOIN skill s ON s.id = cs.skill_id
        JOIN indicator_row ir ON ir.competency_skill_id = cs.id
        LEFT JOIN dimension d ON d.id = ir.dimension_id
        LEFT JOIN indicator_level_cell ilc ON ilc.indicator_row_id = ir.id
        WHERE pc.competency_id = ?
        ORDER BY s.canonical_name, ir.id, ilc.sort_order
        """,
        (competency_id,),
    )

    skill_map: dict[int, dict[str, Any]] = {row["skill_id"]: {**row, "indicators": []} for row in rows}
    indicator_map: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in indicator_rows:
        skill = skill_map.get(row["skill_id"])
        if not skill:
            continue
        key = (row["skill_id"], row["dimension_title"] or "Не указано", row["indicator_text"])
        if key not in indicator_map:
            indicator_map[key] = {
                "dimension_title": row["dimension_title"] or "Не указано",
                "indicator_text": row["indicator_text"] or "[нет текста]",
                "levels": [],
            }
            skill["indicators"].append(indicator_map[key])
        if row["raw_level_label"]:
            indicator_map[key]["levels"].append(
                {
                    "label": row["raw_level_label"],
                    "value": row["raw_value"],
                    "kind": row["value_kind"],
                }
            )
    return list(skill_map.values())


def list_profiles(conn: CatalogConnection, include_service: bool = False) -> list[dict[str, Any]]:
    where_clause = "" if include_service else "WHERE p.slug != 'intake-accepted-skills'"
    return fetch_all(
        conn,
        f"""
        SELECT
            p.id,
            p.name,
            p.slug,
            p.source_kind,
            COUNT(DISTINCT pc.id) AS competency_count,
            COUNT(DISTINCT cs.id) AS skill_count,
            COUNT(DISTINCT ir.id) AS indicator_count,
            COUNT(DISTINCT CASE WHEN pc.review_state = 'needs_review' THEN pc.id END) AS review_competencies
        FROM profile p
        LEFT JOIN profile_competency pc ON pc.profile_id = p.id
        LEFT JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        LEFT JOIN indicator_row ir ON ir.competency_skill_id = cs.id
        {where_clause}
        GROUP BY p.id, p.name, p.slug, p.source_kind
        ORDER BY p.name
        """,
    )


def get_profile(conn: CatalogConnection, profile_id: int) -> dict[str, Any] | None:
    return fetch_one(
        conn,
        """
        SELECT
            p.id,
            p.name,
            p.source_kind,
            COUNT(DISTINCT pc.id) AS competency_count,
            COUNT(DISTINCT cs.id) AS skill_count,
            COUNT(DISTINCT ir.id) AS indicator_count
        FROM profile p
        LEFT JOIN profile_competency pc ON pc.profile_id = p.id
        LEFT JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        LEFT JOIN indicator_row ir ON ir.competency_skill_id = cs.id
        WHERE p.id = ?
        GROUP BY p.id, p.name, p.source_kind
        """,
        (profile_id,),
    )


def get_profile_tree(conn: CatalogConnection, profile_id: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT
            pc.id AS profile_competency_id,
            pc.sort_order AS competency_order,
            pc.description_in_source AS competency_description,
            pc.prerequisites_text,
            pc.review_state,
            c.title AS competency_title,
            ps.title AS scale_title,
            cs.id AS competency_skill_id,
            cs.skill_order,
            s.canonical_name,
            ir.id AS indicator_row_id,
            ir.base_text,
            ir.source_row_number,
            d.title AS dimension_title,
            ilc.raw_level_label,
            ilc.raw_value,
            ilc.value_kind
        FROM profile_competency pc
        JOIN competency c ON c.id = pc.competency_id
        LEFT JOIN proficiency_scale ps ON ps.id = pc.scale_id
        LEFT JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        LEFT JOIN skill s ON s.id = cs.skill_id
        LEFT JOIN indicator_row ir ON ir.competency_skill_id = cs.id
        LEFT JOIN dimension d ON d.id = ir.dimension_id
        LEFT JOIN indicator_level_cell ilc ON ilc.indicator_row_id = ir.id
        WHERE pc.profile_id = ?
        ORDER BY pc.sort_order, cs.skill_order, ir.source_row_number, ilc.sort_order
        """,
        (profile_id,),
    )

    competencies: list[dict[str, Any]] = []
    competency_map: dict[int, dict[str, Any]] = {}
    skill_map: dict[int, dict[str, Any]] = {}
    indicator_map: dict[int, dict[str, Any]] = {}

    for row in rows:
        pc_id = row["profile_competency_id"]
        competency = competency_map.get(pc_id)
        if competency is None:
            competency = {
                "id": pc_id,
                "title": row["competency_title"],
                "description": row["competency_description"],
                "prerequisites": row["prerequisites_text"],
                "scale_title": row["scale_title"],
                "review_state": row["review_state"],
                "skills": [],
            }
            competency_map[pc_id] = competency
            competencies.append(competency)

        skill_id = row["competency_skill_id"]
        if skill_id is not None:
            skill = skill_map.get(skill_id)
            if skill is None:
                skill = {
                    "id": skill_id,
                    "name": row["canonical_name"],
                    "indicators": [],
                }
                skill_map[skill_id] = skill
                competency["skills"].append(skill)

            indicator_id = row["indicator_row_id"]
            if indicator_id is not None:
                indicator = indicator_map.get(indicator_id)
                if indicator is None:
                    indicator = {
                        "id": indicator_id,
                        "dimension_title": row["dimension_title"] or "Не указано",
                        "text": row["base_text"] or "[нет текста]",
                        "levels": [],
                    }
                    indicator_map[indicator_id] = indicator
                    skill["indicators"].append(indicator)
                if row["raw_level_label"]:
                    indicator["levels"].append(
                        {
                            "label": row["raw_level_label"],
                            "value": row["raw_value"],
                            "kind": row["value_kind"],
                        }
                    )
    return competencies
