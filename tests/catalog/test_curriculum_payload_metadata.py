import json

from content_factory.catalog.viewer.curriculum_ops import build_curriculum_plan_payload_from_rows


def test_payload_metadata_restores_artifact_before_enrichment_metrics() -> None:
    stored_row = {
        "row_number": 1,
        "artifact": "Проверяемый прототип",
        "project_content_type": "hard_code",
        "content_profile_decision": {"profile": "hard_code", "source": "project_signals"},
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
    assert payload["report"]["quality_metrics"]["enrichment_completeness_pct"] == 100.0
