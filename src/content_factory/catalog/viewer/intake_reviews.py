"""Review queue persistence and UI hydration for intake workflows.

The intake orchestration module should not also own review-row repair, status
transitions, or display-specific hydration. This module keeps the review queue
interface small while hiding the SQL/details parsing needed by the UI.
"""

from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.viewer._common import (
    extract_quoted_name,
    fetch_all,
    format_percent,
    parse_brief_id,
    parse_optional_float,
    table_exists,
    utc_now_iso,
)
from content_factory.catalog.viewer.labels import (
    review_entity_label,
    review_reason_label,
    review_severity_label,
    review_status_label,
    review_text_label,
)

ReviewListResult = tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, str]],
    list[dict[str, str]],
]


def repair_intake_review_links(conn: CatalogConnection) -> int:
    if not table_exists(conn, "review_queue") or not table_exists(conn, "skill_suggestion"):
        return 0

    updated = 0
    rows = conn.execute(
        """
        SELECT id, source_ref, details
        FROM review_queue
        WHERE entity_id IS NULL
          AND source_ref LIKE 'brief:%'
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        brief_id = parse_brief_id(row["source_ref"])
        suggestion_name = extract_quoted_name(row["details"])
        if brief_id is None or not suggestion_name:
            continue
        match_rows = conn.execute(
            """
            SELECT id
            FROM skill_suggestion
            WHERE brief_id = ? AND suggested_name = ?
            ORDER BY id
            """,
            (brief_id, suggestion_name),
        ).fetchall()
        if len(match_rows) != 1:
            continue
        conn.execute("UPDATE review_queue SET entity_id = ? WHERE id = ?", (match_rows[0]["id"], row["id"]))
        updated += 1
    if updated:
        conn.commit()
    return updated


def parse_review_details_json(details: str | None) -> dict[str, Any]:
    if not details:
        return {}
    try:
        data = json.loads(details)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def split_review_reason_codes(reason_code: str | None, details: dict[str, Any] | None = None) -> list[str]:
    codes: list[str] = []
    raw_reasons = (details or {}).get("reasons")
    if isinstance(raw_reasons, list):
        codes.extend(str(item).strip() for item in raw_reasons if str(item).strip())
    if reason_code:
        codes.extend(part.strip() for part in str(reason_code).split(",") if part.strip())
    seen: set[str] = set()
    unique_codes: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            unique_codes.append(code)
    return unique_codes


def split_edge_label(edge_label: object | None) -> tuple[str, str]:
    text = str(edge_label or "").strip()
    if " -> " in text:
        src, dst = text.split(" -> ", 1)
        return src.strip(), dst.strip()
    if "→" in text:
        src, dst = text.split("→", 1)
        return src.strip(), dst.strip()
    return text or "первый навык", "следующий навык"


def format_prerequisite_edge_review(item: dict[str, Any]) -> None:
    details = parse_review_details_json(str(item.get("details") or ""))
    if details.get("review_kind") != "prerequisite_edge":
        item["display_reason"] = review_text_label(item.get("reason_label"))
        item["display_check"] = review_text_label(item.get("details"))
        return

    src_name, dst_name = split_edge_label(details.get("edge_label"))
    reason_codes = split_review_reason_codes(str(item.get("reason_code") or ""), details)
    reason_labels = [review_reason_label(code) for code in reason_codes]
    confidence = format_percent(details.get("confidence"))
    relation_type = str(details.get("relation_type") or "").strip()

    item["display_reason"] = "; ".join(reason_labels) if reason_labels else "Связь требует методологической проверки"

    notes: list[str] = [
        f"Проверяемая связь: «{src_name}» должен быть изучен до «{dst_name}».",
    ]
    if confidence:
        notes.append(f"Уверенность системы: {confidence}.")
    if relation_type == "soft":
        notes.append("Тип связи: мягкая методическая связь. Она не считается обязательной, пока методолог её не подтвердит.")
    if "bloom_direction" in reason_codes:
        notes.append("Причина проверки: возможен спорный порядок по уровню сложности. Проверьте, не должен ли второй навык идти раньше первого.")
    if "ai_proposed" in reason_codes:
        notes.append("Причина проверки: связь предложена автоматически, поэтому её нельзя использовать в рабочем графе без подтверждения.")
    notes.append("Что решить: подтвердить связь, если первый навык действительно нужен как основа для второго; отклонить, если порядок неверный или связь только тематическая.")
    item["display_check"] = "\n".join(notes)


def save_prerequisite_edge_decision(
    conn: CatalogConnection,
    *,
    brief_id: int,
    details: dict[str, Any],
    decision: str,
    resolution_note: str,
) -> None:
    if not table_exists(conn, "prerequisite_edge_decision"):
        return
    edge_key = str(details.get("edge_key") or "").strip()
    if "->" not in edge_key:
        return
    src_raw, dst_raw = edge_key.split("->", 1)

    def suggestion_id(raw: object) -> int | None:
        value = str(raw or "").strip()
        if value.startswith("S") and value[1:].isdigit():
            return int(value[1:])
        if value.isdigit():
            return int(value)
        return None

    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO prerequisite_edge_decision(
            brief_id, edge_key, src_suggestion_id, dst_suggestion_id,
            relation_type, confidence, source, decision, resolution_note, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(brief_id, edge_key) DO UPDATE SET
            src_suggestion_id = excluded.src_suggestion_id,
            dst_suggestion_id = excluded.dst_suggestion_id,
            relation_type = excluded.relation_type,
            confidence = excluded.confidence,
            source = excluded.source,
            decision = excluded.decision,
            resolution_note = excluded.resolution_note,
            updated_at = excluded.updated_at
        """,
        (
            brief_id,
            edge_key,
            suggestion_id(details.get("src_id") or src_raw),
            suggestion_id(details.get("dst_id") or dst_raw),
            str(details.get("relation_type") or "soft"),
            parse_optional_float(str(details.get("confidence"))) if details.get("confidence") is not None else None,
            str(details.get("source") or "review"),
            decision,
            resolution_note.strip() or None,
            now,
        ),
    )


def count_open_skill_reviews_for_brief(conn: CatalogConnection, brief_id: int) -> int:
    if not table_exists(conn, "review_queue"):
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM review_queue
            WHERE source_ref = ?
              AND entity_type = 'skill'
              AND status = 'open'
              AND NOT (
                  json_valid(details)
                  AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
              )
            """,
            (f"brief:{brief_id}",),
        ).fetchone()[0]
    )


def count_open_prerequisite_edge_reviews_for_brief(conn: CatalogConnection, brief_id: int) -> int:
    if not table_exists(conn, "review_queue"):
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM review_queue
            WHERE source_ref = ?
              AND entity_type = 'prerequisite_edge'
              AND status = 'open'
              AND json_valid(details)
              AND json_extract(details, '$.review_kind') = 'prerequisite_edge'
            """,
            (f"brief:{brief_id}",),
        ).fetchone()[0]
    )


def count_open_candidate_competencies(conn: CatalogConnection) -> int:
    if not table_exists(conn, "review_queue"):
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM review_queue
            WHERE entity_type = 'competency'
              AND status = 'open'
              AND source_ref LIKE 'intake_accept:%'
            """
        ).fetchone()[0]
    )


def update_review_status(conn: CatalogConnection, review_id: int, new_status: str, resolution_note: str) -> None:
    repair_intake_review_links(conn)
    review_row = conn.execute(
        """
        SELECT id, entity_type, entity_id, source_ref, reason_code, details
        FROM review_queue
        WHERE id = ?
        """,
        (review_id,),
    ).fetchone()
    if not review_row:
        return

    reviewed_at = utc_now_iso() if new_status != "open" else None
    conn.execute(
        """
        UPDATE review_queue
        SET status = ?,
            resolution_note = ?,
            reviewed_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (new_status, resolution_note.strip() or None, reviewed_at, utc_now_iso(), review_id),
    )
    brief_id = parse_brief_id(review_row["source_ref"])
    suggestion_id = review_row["entity_id"]
    details = parse_review_details_json(review_row["details"])
    rebuild_brief_id: int | None = None
    if review_row["entity_type"] == "competency" and suggestion_id:
        from content_factory.catalog.pipeline import competency_catalog

        competency_id = int(suggestion_id)
        if new_status == "resolved":
            competency_catalog.resolve_competency_candidate(conn, competency_id=competency_id, accepted=True)
        elif new_status == "ignored":
            competency_catalog.resolve_competency_candidate(conn, competency_id=competency_id, accepted=False)
        else:
            competency_catalog.reopen_competency_candidate(conn, competency_id=competency_id)
    elif brief_id is not None and details.get("review_kind") == "prerequisite_edge":
        if new_status in {"resolved", "ignored"}:
            save_prerequisite_edge_decision(
                conn,
                brief_id=brief_id,
                details=details,
                decision="accepted" if new_status == "resolved" else "rejected",
                resolution_note=resolution_note,
            )
        elif table_exists(conn, "prerequisite_edge_decision"):
            conn.execute(
                "DELETE FROM prerequisite_edge_decision WHERE brief_id = ? AND edge_key = ?",
                (brief_id, str(details.get("edge_key") or "")),
            )
        from content_factory.catalog.viewer.intake_ops import clear_brief_dag_artifacts

        clear_brief_dag_artifacts(conn, brief_id)
    elif suggestion_id and brief_id is not None:
        from content_factory.catalog.pipeline import storage
        from content_factory.catalog.viewer.intake_ops import clear_brief_dag_artifacts

        mapped_decision = "needs_review"
        if new_status == "resolved":
            mapped_decision = "accepted"
        elif new_status == "ignored":
            mapped_decision = "rejected"
        conn.execute(
            "UPDATE skill_suggestion SET decision = ? WHERE id = ?",
            (mapped_decision, suggestion_id),
        )
        if mapped_decision == "accepted":
            storage.promote_suggestion_to_catalog(conn, suggestion_id)
        else:
            storage.revert_suggestion_promotion(conn, suggestion_id)
        clear_brief_dag_artifacts(conn, brief_id)
        rebuild_brief_id = brief_id
    conn.commit()
    if rebuild_brief_id is not None:
        from content_factory.catalog.viewer.intake_ops import build_dag_for_brief

        build_dag_for_brief(conn, rebuild_brief_id)


def list_reviews(
    conn: CatalogConnection,
    status_filter: str,
    severity_filter: str,
    reason_filter: str,
    entity_type_filter: str = "all",
) -> ReviewListResult:
    repair_intake_review_links(conn)
    params: list[object] = []
    where_parts: list[str] = []
    if status_filter != "all":
        where_parts.append("status = ?")
        params.append(status_filter)
    if severity_filter != "all":
        where_parts.append("severity = ?")
        params.append(severity_filter)
    if reason_filter != "all":
        where_parts.append("reason_code = ?")
        params.append(reason_filter)
    if entity_type_filter != "all":
        where_parts.append("entity_type = ?")
        params.append(entity_type_filter)
    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    status_totals = fetch_all(
        conn,
        """
        SELECT status, COUNT(*) AS cnt
        FROM review_queue
        GROUP BY status
        ORDER BY CASE status
            WHEN 'open' THEN 1
            WHEN 'resolved' THEN 2
            WHEN 'ignored' THEN 3
            ELSE 4
        END
        """,
    )
    for item in status_totals:
        item["status_label"] = review_status_label(str(item["status"]))

    breakdown = fetch_all(
        conn,
        f"""
        SELECT reason_code, severity, COUNT(*) AS cnt
        FROM review_queue
        {where_clause}
        GROUP BY reason_code, severity
        ORDER BY cnt DESC, reason_code
        """,
        tuple(params),
    )
    for item in breakdown:
        item["reason_label"] = review_reason_label(str(item["reason_code"]))
        item["severity_label"] = review_severity_label(str(item["severity"]))

    items = fetch_all(
        conn,
        f"""
        SELECT id, entity_type, entity_id, source_ref, reason_code, severity, details, status, resolution_note, created_at, reviewed_at
        FROM review_queue
        {where_clause}
        ORDER BY
            CASE severity
                WHEN 'error' THEN 1
                WHEN 'warning' THEN 2
                ELSE 3
            END,
            created_at DESC
        LIMIT 500
        """,
        tuple(params),
    )
    for item in items:
        item["reason_label"] = review_reason_label(str(item["reason_code"]))
        item["severity_label"] = review_severity_label(str(item["severity"]))
        item["status_label"] = review_status_label(str(item["status"]))
        format_prerequisite_edge_review(item)

    reason_options = [
        {"code": row["reason_code"], "label": review_reason_label(str(row["reason_code"]))}
        for row in conn.execute("SELECT DISTINCT reason_code FROM review_queue ORDER BY reason_code")
    ]
    entity_type_options = [
        {"code": row["entity_type"], "label": review_entity_label(str(row["entity_type"]))}
        for row in conn.execute("SELECT DISTINCT entity_type FROM review_queue ORDER BY entity_type")
    ]
    return status_totals, breakdown, items, reason_options, entity_type_options
