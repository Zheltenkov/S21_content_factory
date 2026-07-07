"""Candidate-competency operations — review/merge/rename/resolve + similarity.

Extracted from ``viewer/app.py`` (slice 5a). The self-contained candidate-competency
island: list active-competency options, list/merge/move/rename/resolve candidate
competencies, similarity scoring, and the profile-competency housekeeping helpers
(close-if-empty / ensure-service-profile / prune-empty). Consumed by
``catalog/web/routers/catalog_admin.py``. Depends only on shared helpers in
``_common`` (+ local ``competency_catalog`` imports that travel with the bodies) —
no back-import of ``app.py`` (module stays acyclic).
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import (
    fetch_all,
    normalize_competency_title,
    table_exists,
    utc_now_iso,
)


def list_candidate_competencies(conn: CatalogConnection) -> list[dict[str, Any]]:
    from content_factory.catalog.pipeline import competency_catalog

    if not all(table_exists(conn, name) for name in ("profile", "profile_competency", "competency")):
        return []
    rows = fetch_all(
        conn,
        """
        SELECT
            pc.id AS profile_competency_id,
            pc.review_state,
            c.id AS competency_id,
            c.title,
            c.status,
            rq.id AS review_id,
            rq.reason_code,
            rq.details,
            rq.created_at,
            COUNT(DISTINCT cs.skill_id) AS skill_count,
            GROUP_CONCAT(DISTINCT s.canonical_name) AS skill_names
        FROM profile p
        JOIN profile_competency pc ON pc.profile_id = p.id
        JOIN competency c ON c.id = pc.competency_id
        LEFT JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        LEFT JOIN skill s ON s.id = cs.skill_id
        LEFT JOIN review_queue rq
          ON rq.entity_type = 'competency'
         AND rq.entity_id = c.id
         AND rq.status = 'open'
        WHERE p.slug = ?
          AND (
              pc.review_state = 'needs_review'
              OR c.status = 'candidate'
              OR rq.id IS NOT NULL
          )
        GROUP BY pc.id, pc.review_state, c.id, c.title, c.status, rq.id, rq.reason_code, rq.details, rq.created_at
        ORDER BY rq.created_at DESC, pc.id DESC
        """,
        (competency_catalog.SERVICE_PROFILE_SLUG,),
    )
    for row in rows:
        row["skills"] = list_candidate_competency_skills(conn, int(row["profile_competency_id"]))
        similar = list_competency_similarity_candidates(
            conn,
            int(row["competency_id"]),
            int(row["profile_competency_id"]),
        )
        row["similar_competencies"] = similar
        row["nearest_competency"] = similar[0] if similar else None
    return rows


def list_candidate_competency_skills(conn: CatalogConnection, profile_competency_id: int) -> list[dict[str, Any]]:
    if not all(table_exists(conn, name) for name in ("competency_skill", "skill")):
        return []
    return fetch_all(
        conn,
        """
        SELECT
            cs.id AS competency_skill_id,
            cs.skill_id,
            cs.source_skill_name,
            cs.review_state,
            s.canonical_name,
            s.status AS skill_status
        FROM competency_skill cs
        LEFT JOIN skill s ON s.id = cs.skill_id
        WHERE cs.profile_competency_id = ?
        ORDER BY cs.skill_order, cs.id
        """,
        (profile_competency_id,),
    )


def competency_token_set(value: object | None) -> set[str]:
    stopwords = {
        "и",
        "в",
        "во",
        "на",
        "для",
        "по",
        "с",
        "со",
        "к",
        "от",
        "до",
        "при",
        "или",
        "а",
        "the",
        "and",
        "of",
        "for",
    }
    return {token for token in normalize_competency_title(value).split() if len(token) > 2 and token not in stopwords}


def _competency_similarity_label(score: float) -> tuple[str, str]:
    if score >= 82:
        return "Высокая похожесть", "merge"
    if score >= 62:
        return "Средняя похожесть", "review"
    return "Слабая похожесть", "create"


def list_competency_similarity_candidates(
    conn: CatalogConnection,
    competency_id: int,
    profile_competency_id: int,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find existing competencies that may duplicate a candidate grouping."""
    if not all(table_exists(conn, name) for name in ("competency", "profile_competency", "competency_skill")):
        return []
    candidate = conn.execute(
        """
        SELECT c.id, c.title
        FROM competency c
        JOIN profile_competency pc ON pc.competency_id = c.id
        WHERE c.id = ? AND pc.id = ?
        """,
        (competency_id, profile_competency_id),
    ).fetchone()
    if not candidate:
        return []
    candidate_skill_ids = {
        int(row["skill_id"])
        for row in conn.execute(
            "SELECT skill_id FROM competency_skill WHERE profile_competency_id = ? AND skill_id IS NOT NULL",
            (profile_competency_id,),
        )
    }
    candidate_tokens = competency_token_set(candidate["title"])
    rows = fetch_all(
        conn,
        """
        SELECT
            c.id,
            c.title,
            c.status,
            COUNT(DISTINCT pc.profile_id) AS profile_count,
            COUNT(DISTINCT cs.skill_id) AS skill_count,
            GROUP_CONCAT(DISTINCT cs.skill_id) AS skill_ids
        FROM competency c
        LEFT JOIN profile_competency pc ON pc.competency_id = c.id
        LEFT JOIN competency_skill cs ON cs.profile_competency_id = pc.id
        WHERE c.status = 'active'
          AND c.id != ?
        GROUP BY c.id, c.title, c.status
        HAVING COUNT(DISTINCT cs.skill_id) > 0
        """,
        (competency_id,),
    )
    scored: list[dict[str, Any]] = []
    for row in rows:
        target_tokens = competency_token_set(row["title"])
        token_union = candidate_tokens | target_tokens
        token_overlap = len(candidate_tokens & target_tokens) / len(token_union) if token_union else 0.0
        title_ratio = SequenceMatcher(
            None,
            normalize_competency_title(candidate["title"]),
            normalize_competency_title(row["title"]),
        ).ratio()
        target_skill_ids = {
            int(value)
            for value in str(row.get("skill_ids") or "").split(",")
            if value and str(value).isdigit()
        }
        skill_overlap_count = len(candidate_skill_ids & target_skill_ids)
        skill_overlap = skill_overlap_count / max(1, len(candidate_skill_ids)) if candidate_skill_ids else 0.0
        score = round((0.45 * title_ratio + 0.35 * token_overlap + 0.20 * skill_overlap) * 100, 2)
        if score < 28 and skill_overlap_count == 0:
            continue
        label, recommendation = _competency_similarity_label(score)
        row.update(
            {
                "score": score,
                "label": label,
                "recommendation": recommendation,
                "token_overlap_pct": round(token_overlap * 100, 2),
                "title_similarity_pct": round(title_ratio * 100, 2),
                "skill_overlap_count": skill_overlap_count,
                "candidate_skill_count": len(candidate_skill_ids),
            }
        )
        scored.append(row)
    scored.sort(key=lambda item: (float(item["score"]), int(item["skill_overlap_count"])), reverse=True)
    return scored[:limit]


def list_active_competency_options(conn: CatalogConnection, limit: int = 200) -> list[dict[str, Any]]:
    if not table_exists(conn, "competency"):
        return []
    return fetch_all(
        conn,
        """
        SELECT id, title, status
        FROM competency
        WHERE status = 'active'
        ORDER BY title
        LIMIT ?
        """,
        (limit,),
    )


def rename_candidate_competency(conn: CatalogConnection, competency_id: int, new_title: str) -> dict[str, Any]:
    title = " ".join(str(new_title or "").split())
    if not title:
        return {"status": "empty_title", "competency_id": competency_id}
    normalized_title = normalize_competency_title(title)
    existing = conn.execute(
        "SELECT id FROM competency WHERE normalized_title = ? AND id != ?",
        (normalized_title, competency_id),
    ).fetchone()
    if existing:
        return {"status": "conflict", "competency_id": competency_id, "target_competency_id": int(existing["id"])}
    conn.execute(
        "UPDATE competency SET title = ?, normalized_title = ? WHERE id = ?",
        (title, normalized_title, competency_id),
    )
    conn.execute(
        "UPDATE profile_competency SET title_in_source = COALESCE(NULLIF(title_in_source, ''), ?) WHERE competency_id = ?",
        (title, competency_id),
    )
    if table_exists(conn, "source_block"):
        conn.execute(
            """
            UPDATE source_block
            SET raw_title = ?
            WHERE id IN (
                SELECT source_block_id FROM profile_competency WHERE competency_id = ?
            )
            """,
            (title, competency_id),
        )
    conn.commit()
    return {"status": "renamed", "competency_id": competency_id, "title": title}


def ensure_service_profile_competency(conn: CatalogConnection, target_competency_id: int) -> int | None:
    from content_factory.catalog.pipeline import competency_catalog

    context = competency_catalog.ensure_catalog_context(conn)
    if context is None:
        return None
    existing = conn.execute(
        """
        SELECT id
        FROM profile_competency
        WHERE profile_id = ? AND competency_id = ?
        ORDER BY id LIMIT 1
        """,
        (context.profile_id, target_competency_id),
    ).fetchone()
    if existing:
        return int(existing["id"])
    target = conn.execute("SELECT title FROM competency WHERE id = ?", (target_competency_id,)).fetchone()
    if not target:
        return None
    block_no = int(
        conn.execute(
            "SELECT COALESCE(MAX(block_no), 0) + 10 FROM source_block WHERE source_sheet_id = ?",
            (context.source_sheet_id,),
        ).fetchone()[0]
        or 10
    )
    cursor = conn.execute(
        """
        INSERT INTO source_block(
            source_sheet_id, block_no, header_row_number, level_row_number,
            end_row_number, raw_title, raw_description, raw_prerequisites, raw_scale_signature
        )
        VALUES (?, ?, ?, NULL, NULL, ?, ?, NULL, NULL)
        """,
        (context.source_sheet_id, block_no, block_no, target["title"], "Служебный блок intake для переноса skill."),
    )
    source_block_id = int(cursor.lastrowid or 0)
    sort_order = int(
        conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 10 FROM profile_competency WHERE profile_id = ?",
            (context.profile_id,),
        ).fetchone()[0]
        or 10
    )
    cursor = conn.execute(
        """
        INSERT INTO profile_competency(
            profile_id, competency_id, source_block_id, scale_id, title_in_source,
            description_in_source, prerequisites_text, sort_order, review_state
        )
        VALUES (?, ?, ?, NULL, ?, NULL, NULL, ?, 'accepted')
        """,
        (context.profile_id, target_competency_id, source_block_id, target["title"], sort_order),
    )
    return int(cursor.lastrowid or 0)


def move_candidate_competency_skill(
    conn: CatalogConnection,
    competency_skill_id: int,
    target_competency_id: int,
) -> dict[str, Any]:
    target_profile_competency_id = ensure_service_profile_competency(conn, target_competency_id)
    if target_profile_competency_id is None:
        return {"status": "target_missing", "competency_skill_id": competency_skill_id}
    row = conn.execute(
        """
        SELECT
            cs.id,
            cs.profile_competency_id,
            cs.skill_id,
            cs.source_skill_name,
            pc.competency_id AS source_competency_id
        FROM competency_skill cs
        JOIN profile_competency pc ON pc.id = cs.profile_competency_id
        WHERE cs.id = ?
        """,
        (competency_skill_id,),
    ).fetchone()
    if not row:
        return {"status": "missing", "competency_skill_id": competency_skill_id}
    source_competency_id = int(row["source_competency_id"])
    existing = conn.execute(
        """
        SELECT id
        FROM competency_skill
        WHERE profile_competency_id = ? AND skill_id = ?
        """,
        (target_profile_competency_id, row["skill_id"]),
    ).fetchone()
    if existing:
        target_competency_skill_id = int(existing["id"])
        conn.execute(
            "UPDATE indicator_row SET competency_skill_id = ? WHERE competency_skill_id = ?",
            (target_competency_skill_id, competency_skill_id),
        )
        conn.execute("DELETE FROM competency_skill WHERE id = ?", (competency_skill_id,))
    else:
        next_order = int(
            conn.execute(
                "SELECT COALESCE(MAX(skill_order), 0) + 10 FROM competency_skill WHERE profile_competency_id = ?",
                (target_profile_competency_id,),
            ).fetchone()[0]
            or 10
        )
        conn.execute(
            """
            UPDATE competency_skill
            SET profile_competency_id = ?,
                skill_order = ?,
                review_state = 'accepted'
            WHERE id = ?
            """,
            (target_profile_competency_id, next_order, competency_skill_id),
        )
    prune_empty_profile_competencies(conn)
    close_candidate_competency_if_empty(
        conn,
        source_competency_id,
        f"Все skills перенесены в существующую competency #{target_competency_id}.",
    )
    conn.commit()
    return {
        "status": "moved",
        "competency_skill_id": competency_skill_id,
        "target_competency_id": target_competency_id,
    }


def merge_candidate_competency(
    conn: CatalogConnection,
    competency_id: int,
    target_competency_id: int,
) -> dict[str, Any]:
    if competency_id == target_competency_id:
        return {"status": "same_competency", "competency_id": competency_id}
    profile_rows = conn.execute(
        "SELECT id FROM profile_competency WHERE competency_id = ?",
        (competency_id,),
    ).fetchall()
    moved = 0
    for profile_row in profile_rows:
        for skill_row in conn.execute(
            "SELECT id FROM competency_skill WHERE profile_competency_id = ?",
            (int(profile_row["id"]),),
        ).fetchall():
            result = move_candidate_competency_skill(conn, int(skill_row["id"]), target_competency_id)
            if result.get("status") == "moved":
                moved += 1
    conn.execute("UPDATE competency SET status = 'deprecated' WHERE id = ?", (competency_id,))
    resolve_candidate_competency(conn, competency_id, "reject", f"Слито с competency #{target_competency_id}.")
    return {"status": "merged", "competency_id": competency_id, "target_competency_id": target_competency_id, "moved": moved}


def prune_empty_profile_competencies(conn: CatalogConnection) -> int:
    if not table_exists(conn, "profile_competency"):
        return 0
    deleted = conn.execute(
        """
        DELETE FROM profile_competency
        WHERE NOT EXISTS (
            SELECT 1 FROM competency_skill cs WHERE cs.profile_competency_id = profile_competency.id
        )
        AND review_state != 'accepted'
        """
    ).rowcount
    return int(deleted or 0)


def close_candidate_competency_if_empty(conn: CatalogConnection, competency_id: int, resolution_note: str) -> bool:
    if not all(table_exists(conn, name) for name in ("competency", "profile_competency", "competency_skill")):
        return False
    remaining = conn.execute(
        """
        SELECT 1
        FROM competency_skill cs
        JOIN profile_competency pc ON pc.id = cs.profile_competency_id
        WHERE pc.competency_id = ?
        LIMIT 1
        """,
        (competency_id,),
    ).fetchone()
    if remaining:
        return False
    conn.execute(
        """
        UPDATE competency
        SET status = 'deprecated'
        WHERE id = ?
          AND status = 'candidate'
        """,
        (competency_id,),
    )
    if table_exists(conn, "review_queue"):
        now = utc_now_iso()
        conn.execute(
            """
            UPDATE review_queue
            SET status = 'ignored',
                resolution_note = COALESCE(NULLIF(?, ''), resolution_note),
                reviewed_at = ?,
                updated_at = ?
            WHERE entity_type = 'competency'
              AND entity_id = ?
              AND status = 'open'
              AND source_ref LIKE 'intake_accept:%'
            """,
            (resolution_note, now, now, competency_id),
        )
    return True


def resolve_candidate_competency(
    conn: CatalogConnection,
    competency_id: int,
    action: str,
    resolution_note: str = "",
) -> dict[str, Any]:
    from content_factory.catalog.pipeline import competency_catalog

    if action == "accept":
        result = competency_catalog.resolve_competency_candidate(conn, competency_id=competency_id, accepted=True)
        review_status = "resolved"
    elif action == "reject":
        result = competency_catalog.resolve_competency_candidate(conn, competency_id=competency_id, accepted=False)
        review_status = "ignored"
    elif action == "review":
        result = competency_catalog.reopen_competency_candidate(conn, competency_id=competency_id)
        review_status = "open"
    else:
        return {"status": "invalid_action", "competency_id": competency_id}

    now = utc_now_iso()
    reviewed_at = None if review_status == "open" else now
    if table_exists(conn, "review_queue"):
        conn.execute(
            """
            UPDATE review_queue
            SET status = ?,
                resolution_note = COALESCE(?, resolution_note),
                reviewed_at = ?,
                updated_at = ?
            WHERE entity_type = 'competency'
              AND entity_id = ?
              AND source_ref LIKE 'intake_accept:%'
            """,
            (review_status, resolution_note.strip() or None, reviewed_at, now, competency_id),
        )
    conn.commit()
    return result
