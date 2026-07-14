"""Read-path profile round-trip + re-resolution (redirect step 2).

GitNexus flagged evaluate_publication_gate / report_only_quality_metrics as CRITICAL blast
radius (row read/edit, CSV, builder UI), so the read path — not only stage_dag_to_up.run —
must preserve the profile identity and re-resolve it. These exercise
build_curriculum_plan_payload_from_rows directly (no DB needed).
"""

from __future__ import annotations

import json
from typing import Any

from content_factory.catalog.viewer.curriculum_ops import build_curriculum_plan_payload_from_rows


def _row(row_number: int, project_name: str) -> dict[str, Any]:
    return {
        "id": row_number,
        "row_number": row_number,
        "block_index": 1,
        "project_name": project_name,
        "artifact": "Запускаемый прототип",
        "validation_criteria": "- сервис: отвечает → 200 OK (лог, ручная)",
        "node_ids": ["a", "b"],
        "policy_area": "ai_automation",
        "policy_area_confidence": "high",
        "project_type": "project",
        "effort_hours": 20,
    }


def _plan_meta(profile_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"rows": [], "report": {"quality_metrics": {}}}
    if profile_snapshot is not None:
        payload["methodology_profile"] = profile_snapshot
    return {"id": 7, "status": "built", "title": "УП", "payload_json": json.dumps(payload, ensure_ascii=False)}


_ROWS = [_row(1, "Прототип продукта с AI"), _row(2, "Автоматизация поддержки клиентов")]


def test_readpath_preserves_and_resolves_known_profile() -> None:
    meta = _plan_meta({"profile_id": "digital_product_project_based", "version": "1"})
    built = build_curriculum_plan_payload_from_rows(meta, _ROWS)
    assert built["methodology_profile"] == {"profile_id": "digital_product_project_based", "version": "1"}
    assert built["report"]["methodology_profile_status"] == "resolved"
    assert "publication_gate" in built["report"]


def test_readpath_legacy_payload_without_profile_uses_v1() -> None:
    built = build_curriculum_plan_payload_from_rows(_plan_meta(None), _ROWS)
    assert built["report"]["methodology_profile_status"] == "legacy_default"
    assert built["methodology_profile"] == {"profile_id": "digital_product_project_based", "version": "1"}


def test_readpath_unknown_version_blocks_publish_not_draft() -> None:
    meta = _plan_meta({"profile_id": "digital_product_project_based", "version": "999"})
    built = build_curriculum_plan_payload_from_rows(meta, _ROWS)
    # draft still opens (payload built, rows present)
    assert built["rows"]
    assert built["report"]["methodology_profile_status"] == "unavailable"
    gate = built["report"]["publication_gate"]
    assert gate["passed"] is False
    assert any(f["code"] == "methodology_profile_unavailable" for f in gate["failures"])


def test_readpath_v2_keeps_unresolved_plan_editable_but_not_publishable() -> None:
    meta = _plan_meta({"profile_id": "digital_product_project_based", "version": "2"})

    built = build_curriculum_plan_payload_from_rows(meta, _ROWS)

    assert built["rows"]
    assert built["report"]["methodology_profile_status"] == "resolved"
    gate = built["report"]["publication_gate"]
    assert gate["passed"] is False
    assert {failure["code"] for failure in gate["failures"]} >= {
        "activity_archetype_incomplete",
        "artifact_contract_incomplete",
    }
