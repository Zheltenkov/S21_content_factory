import json

from content_factory.catalog.viewer.curriculum_ops import build_curriculum_plan_payload_from_rows


def test_payload_metadata_restores_artifact_before_enrichment_metrics() -> None:
    stored_row = {
        "row_number": 1,
        "primary_node_ids": ["S1"],
        "artifact": "Проверяемый прототип",
        "project_content_type": "hard_code",
        "content_profile_decision": {"profile": "hard_code", "source": "project_signals"},
        "activity_archetype": "",
        "activity_archetype_suggestion": "investigate",
        "activity_archetype_confidence": "medium",
        "activity_archetype_reasons": ["результаты: анализ", "score=5; margin=2"],
        "activity_archetype_modifiers": ["experiment"],
        "activity_archetype_source": "auto",
        "activity_archetype_version": "activity-archetype/v1",
        "activity_archetype_decision_key": "opaque-project-key",
        "artifact_contract": {
            "artifact_type": "evidence_report",
            "policy_area": "",
            "activity_archetype": "investigate",
        },
        "artifact_contract_sources": ["archetype_skeleton"],
        "artifact_slot_sources": {"artifact": ["archetype_skeleton"]},
        "artifact_merge_diagnostics": [
            {
                "code": "activity_skeleton_unavailable",
                "severity": "warning",
                "slot": "contract",
                "sources": ["archetype_skeleton"],
                "resolution": "Требуется методолог.",
            }
        ],
    }
    db_row = {
        "id": 10,
        "row_number": 1,
        "block_index": 1,
        "block_title": "Разработка продукта",
        "project_name": "Прототип продукта",
        "project_summary": "Собрать и проверить прототип.",
        "materials": "Стартовые материалы.",
        "storytelling": "Рабочий кейс.",
        "validation_criteria": "Прототип воспроизводится.",
        "delivery_format": "individual",
    }
    plan_meta = {
        "id": 3,
        "status": "draft",
        "payload_json": json.dumps({"rows": [stored_row], "report": {}}, ensure_ascii=False),
    }

    payload = build_curriculum_plan_payload_from_rows(plan_meta, [db_row])

    assert payload["rows"][0]["artifact"] == "Проверяемый прототип"
    assert payload["rows"][0]["project_content_type"] == "hard_code"
    assert payload["rows"][0]["activity_archetype_suggestion"] == "investigate"
    assert payload["rows"][0]["activity_archetype_modifiers"] == ["experiment"]
    assert payload["rows"][0]["activity_archetype_version"] == "activity-archetype/v1"
    assert payload["rows"][0]["primary_node_ids"] == ["S1"]
    assert payload["rows"][0]["activity_archetype_decision_key"] == "opaque-project-key"
    assert payload["rows"][0]["artifact_contract"]["artifact_type"] == "evidence_report"
    assert payload["rows"][0]["artifact_contract_sources"] == ["archetype_skeleton"]
    assert payload["rows"][0]["artifact_merge_diagnostics"][0]["severity"] == "warning"
    assert payload["report"]["quality_metrics"]["artifact_contract_coverage_pct"] == 100.0
    assert payload["report"]["quality_metrics"]["artifact_merge_warning_count"] == 1
    assert payload["report"]["quality_metrics"]["enrichment_completeness_pct"] == 100.0
