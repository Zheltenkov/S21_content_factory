from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.viewer._common import utc_now_iso
from content_factory.catalog.viewer.intake_reviews import (
    list_reviews,
    parse_review_details_json,
    split_review_reason_codes,
    update_review_status,
)


def _insert_review(
    conn: Any,
    *,
    review_id: int,
    entity_type: str,
    source_ref: str,
    reason_code: str,
    severity: str,
    details: str | None = None,
    status: str = "open",
) -> int:
    conn.execute(
        """
        INSERT INTO review_queue(
            id, entity_type, source_ref, reason_code, severity, details, status, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (review_id, entity_type, source_ref, reason_code, severity, details, status, utc_now_iso()),
    )
    conn.commit()
    return review_id


def test_review_details_parsing_and_reason_deduplication() -> None:
    details = {"reasons": ["ai_proposed", "bloom_direction", "ai_proposed"]}

    assert parse_review_details_json(json.dumps(details)) == details
    assert parse_review_details_json("{bad-json") == {}
    assert split_review_reason_codes("bloom_direction, manual_check", details) == [
        "ai_proposed",
        "bloom_direction",
        "manual_check",
    ]


def test_list_reviews_hydrates_labels_and_prerequisite_edge_display(catalog_conn: Any) -> None:
    details = {
        "review_kind": "prerequisite_edge",
        "edge_label": "SQL -> Индексы",
        "edge_key": "S10->S11",
        "confidence": 0.82,
        "relation_type": "soft",
        "reasons": ["bloom_direction"],
    }
    _insert_review(
        catalog_conn,
        review_id=1,
        entity_type="prerequisite_edge",
        source_ref="brief:7",
        reason_code="ai_proposed",
        severity="warning",
        details=json.dumps(details, ensure_ascii=False),
    )

    status_totals, breakdown, items, reason_options, entity_type_options = list_reviews(
        catalog_conn,
        status_filter="open",
        severity_filter="all",
        reason_filter="all",
        entity_type_filter="all",
    )

    assert status_totals[0]["status_label"] == "Открыто"
    assert breakdown[0]["reason_label"] == "Связь предложена системой и требует проверки"
    assert breakdown[0]["severity_label"] == "Внимание"
    assert items[0]["display_reason"] == (
        "Возможный спорный порядок по уровню сложности; Связь предложена системой и требует проверки"
    )
    assert "SQL" in items[0]["display_check"]
    assert "Индексы" in items[0]["display_check"]
    assert "Уверенность системы" in items[0]["display_check"]
    assert reason_options == [{"code": "ai_proposed", "label": "Связь предложена системой и требует проверки"}]
    assert entity_type_options == [{"code": "prerequisite_edge", "label": "Связь зависимостей"}]


def test_update_review_status_updates_basic_review_row(catalog_conn: Any) -> None:
    review_id = _insert_review(
        catalog_conn,
        review_id=1,
        entity_type="skill",
        source_ref="manual",
        reason_code="ambiguous_skill_name",
        severity="warning",
        details="Needs clarification",
    )

    update_review_status(catalog_conn, review_id, "resolved", "accepted by reviewer")

    row = catalog_conn.execute(
        "SELECT status, resolution_note, reviewed_at, updated_at FROM review_queue WHERE id = ?",
        (review_id,),
    ).fetchone()
    assert row["status"] == "resolved"
    assert row["resolution_note"] == "accepted by reviewer"
    assert row["reviewed_at"]
    assert row["updated_at"]
