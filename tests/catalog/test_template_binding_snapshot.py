"""Relational template-binding snapshots for reproducible UP versions."""

from __future__ import annotations

import json
from typing import Any

import pytest

from content_factory.catalog.pipeline import storage
from content_factory.catalog.pipeline.template_binding_snapshots import (
    TemplateBindingIntegrityError,
    TemplateBindingVersionMismatchError,
    decode_and_verify_template_snapshot,
)
from content_factory.catalog.viewer.curriculum_ops import get_curriculum_plan


def _plan_payload(template_binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "draft",
        "title": "Тестовый учебный план",
        "audience_level": "Начальный",
        "summary": {"blocks": 1, "projects": 1, "total_hours": 12, "total_days": 2, "total_xp": 100},
        "rows": [
            {
                "block_index": 1,
                "row_number": 1,
                "project_index_in_block": 1,
                "block_title": "Исследование",
                "block_goal": "Проверить гипотезу.",
                "project_name": "Исследование пользовательской проблемы",
                "project_summary": "Подготовить проверяемый аналитический результат.",
                "artifact": "Отчёт об исследовании",
                "artifact_family": "analysis",
                "artifact_template_code": template_binding["template_code"],
                "validation_criteria": "Есть исходные данные, вывод и подтверждающие свидетельства.",
                "effort_hours": 12,
                "template_binding": template_binding,
            }
        ],
        "report": {},
    }


def _upsert_global_template(conn: Any, *, description: str) -> dict[str, Any]:
    storage.upsert_curriculum_artifact_template(
        conn,
        code="research-report",
        title="Отчёт об исследовании",
        artifact_family="analysis",
        artifact_description=description,
        validation_criteria="Есть выборка, результаты и обоснованный вывод.",
        source="manual",
        scopes=[{"scope_type": "coverage_area", "scope_name": "Исследование пользователей"}],
    )
    return next(
        template
        for template in storage.load_curriculum_artifact_templates(conn)
        if template["code"] == "research-report"
    )


def _binding(template: dict[str, Any]) -> dict[str, Any]:
    return {
        "template_code": str(template["code"]),
        "template_version": str(template.get("updated_at") or ""),
        "source": "global",
        "repeatable": bool(template.get("repeatable", False)),
    }


def test_saved_plan_keeps_exact_template_snapshot_after_catalog_edit(catalog_conn: Any) -> None:
    brief_id = storage.save_brief(catalog_conn, "Нужен курс по исследованию пользователей", {})
    original = _upsert_global_template(catalog_conn, description="Оригинальный проверяемый отчёт.")

    saved = storage.save_curriculum_plan(catalog_conn, brief_id, _plan_payload(_binding(original)))
    binding_row = catalog_conn.execute(
        """
        SELECT b.*, r.plan_id
        FROM curriculum_plan_row_template_binding b
        JOIN curriculum_plan_row r ON r.id = b.plan_row_id
        WHERE r.plan_id = ?
        """,
        (saved["plan_id"],),
    ).fetchone()
    assert binding_row is not None
    snapshot = decode_and_verify_template_snapshot(
        str(binding_row["snapshot_json"]),
        str(binding_row["snapshot_sha256"]),
    )
    assert snapshot["artifact_description"] == "Оригинальный проверяемый отчёт."
    assert snapshot["template_id"] == original["id"]

    # A later catalog edit changes the active template, but not the released UP provenance.
    changed = _upsert_global_template(catalog_conn, description="Новая версия шаблона.")
    assert changed["updated_at"] != original["updated_at"]
    persisted_plan = get_curriculum_plan(catalog_conn, saved["plan_id"])
    assert persisted_plan is not None
    assert persisted_plan["rows"][0]["template_binding"] == _binding(original)

    stored_again = catalog_conn.execute(
        "SELECT snapshot_json, snapshot_sha256 FROM curriculum_plan_row_template_binding WHERE id = ?",
        (binding_row["id"],),
    ).fetchone()
    assert stored_again is not None
    immutable_snapshot = decode_and_verify_template_snapshot(
        str(stored_again["snapshot_json"]),
        str(stored_again["snapshot_sha256"]),
    )
    assert immutable_snapshot["artifact_description"] == "Оригинальный проверяемый отчёт."


def test_plan_save_rejects_template_changed_after_planning(catalog_conn: Any) -> None:
    brief_id = storage.save_brief(catalog_conn, "Нужен курс по исследованию пользователей", {})
    planned_template = _upsert_global_template(catalog_conn, description="Версия на этапе planning.")
    planned_binding = _binding(planned_template)
    _upsert_global_template(catalog_conn, description="Версия, изменённая перед save.")

    with pytest.raises(TemplateBindingVersionMismatchError):
        storage.save_curriculum_plan(catalog_conn, brief_id, _plan_payload(planned_binding))

    plan_count = catalog_conn.execute(
        "SELECT COUNT(*) AS count FROM curriculum_plan WHERE brief_id = ?",
        (brief_id,),
    ).fetchone()
    assert plan_count is not None
    assert int(plan_count["count"]) == 0


def test_normalized_binding_overrides_legacy_payload_copy(catalog_conn: Any) -> None:
    brief_id = storage.save_brief(catalog_conn, "Нужен курс по исследованию пользователей", {})
    template = _upsert_global_template(catalog_conn, description="Исходная версия.")
    saved = storage.save_curriculum_plan(catalog_conn, brief_id, _plan_payload(_binding(template)))
    plan_row = catalog_conn.execute(
        "SELECT payload_json FROM curriculum_plan WHERE id = ?",
        (saved["plan_id"],),
    ).fetchone()
    assert plan_row is not None
    payload = json.loads(str(plan_row["payload_json"]))
    payload["rows"][0]["template_binding"] = {
        "template_code": "tampered",
        "template_version": "wrong",
        "source": "brief",
        "repeatable": True,
    }
    catalog_conn.execute(
        "UPDATE curriculum_plan SET payload_json = ? WHERE id = ?",
        (json.dumps(payload, ensure_ascii=False), saved["plan_id"]),
    )
    catalog_conn.commit()

    persisted_plan = get_curriculum_plan(catalog_conn, saved["plan_id"])

    assert persisted_plan is not None
    assert persisted_plan["rows"][0]["template_binding"] == _binding(template)


def test_hydration_rejects_corrupted_snapshot(catalog_conn: Any) -> None:
    brief_id = storage.save_brief(catalog_conn, "Нужен курс по исследованию пользователей", {})
    template = _upsert_global_template(catalog_conn, description="Исходная версия.")
    saved = storage.save_curriculum_plan(catalog_conn, brief_id, _plan_payload(_binding(template)))
    catalog_conn.execute(
        """
        UPDATE curriculum_plan_row_template_binding
        SET snapshot_json = '{"corrupted":true}'
        WHERE plan_row_id IN (SELECT id FROM curriculum_plan_row WHERE plan_id = ?)
        """,
        (saved["plan_id"],),
    )
    catalog_conn.commit()

    with pytest.raises(TemplateBindingIntegrityError):
        get_curriculum_plan(catalog_conn, saved["plan_id"])
