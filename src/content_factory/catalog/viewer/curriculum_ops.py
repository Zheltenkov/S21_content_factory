"""Curriculum-plan (УП) operations — read + CRUD + CSV export + quality metrics.

Extracted from ``viewer/app.py`` (slice 4). The leaf curriculum-plan cluster: plan/row
read + create/update/delete, payload assembly from rows, quality metrics, CSV export, and
the jobs-payload sync helpers. Consumed by ``catalog/web/routers/up.py`` and by the intake
orchestration that still lives in ``app.py`` (``build_deferred_curriculum_plan_payload`` /
``update_jobs_curriculum_plan_payload`` are re-exported there). Depends only on shared
helpers in ``_common`` — no back-import of ``app.py`` (keeps the module acyclic).
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.pipeline import config
from content_factory.catalog.pipeline.curriculum.workload import build_workload_contract
from content_factory.catalog.viewer._common import (
    _as_dict,
    _as_list,
    parse_optional_float,
    parse_optional_int,
    table_exists,
    utc_now_iso,
)


def build_deferred_curriculum_plan_payload(message: str, audience_level: str = "Начальный") -> dict[str, Any]:
    return {
        "status": "deferred",
        "message": message,
        "title": "Черновик учебного плана",
        "audience_level": audience_level,
        "source_policy": "accepted_only",
        "summary": {
            "blocks": 0,
            "projects": 0,
            "total_hours": 0,
            "total_days": 0,
            "workload": build_workload_contract(0, None, default_hours_per_week=config.UP_HOURS_PER_WEEK).as_dict(),
            "total_xp": 0,
        },
        "rows": [],
        "blocks": [],
        "csv_primary_header": [],
        "csv_secondary_header": [],
        "report": {"coverage_ok": False, "order_violations": [], "project_violations": []},
    }


def update_jobs_curriculum_plan_payload(
    conn: CatalogConnection,
    brief_id: int,
    plan_payload: dict[str, Any],
    persisted_update: dict[str, Any] | None = None,
) -> None:
    rows = conn.execute(
        """
        SELECT id, result_payload
        FROM intake_job
        WHERE status = 'succeeded'
          AND json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        """,
        (brief_id,),
    ).fetchall()
    for row in rows:
        payload = json.loads(row["result_payload"])
        payload["curriculum_plan"] = plan_payload
        if persisted_update and isinstance(payload.get("persisted"), dict):
            payload["persisted"].update(persisted_update)
        conn.execute(
            "UPDATE intake_job SET result_payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), utc_now_iso(), row["id"]),
        )
    conn.commit()


def curriculum_plan_status_label(status: str | None) -> str:
    mapping = {
        "draft": "Черновик",
        "built": "Собран",
        "deferred": "Отложен",
        "invalid": "Невалиден",
    }
    return mapping.get((status or "").strip().casefold(), "Неизвестно")


def weighted_skills_from_row(row: dict[str, Any]) -> str:
    existing = str(row.get("weighted_skills") or "").strip()
    if existing:
        return existing
    skills = [
        item.strip()
        for item in str(row.get("skills_list") or "").split(",")
        if item.strip()
    ]
    if not skills:
        return ""
    base_weight = round(100 / len(skills))
    weights = [base_weight] * len(skills)
    weights[-1] += 100 - sum(weights)
    return ", ".join(f"{skill}: {weight}%" for skill, weight in zip(skills, weights, strict=False))


def curriculum_plan_to_csv_bytes(plan_payload: dict[str, Any]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    primary_header = plan_payload.get("csv_primary_header") or []
    secondary_header = plan_payload.get("csv_secondary_header") or []
    if isinstance(primary_header, list) and primary_header:
        writer.writerow(primary_header)
    if isinstance(secondary_header, list) and secondary_header:
        writer.writerow(secondary_header)
    for row in plan_payload.get("rows", []):
        if not isinstance(row, dict):
            continue
        writer.writerow(
            [
                row.get("block_title", ""),
                row.get("block_goal", ""),
                row.get("row_number", ""),
                row.get("project_name", ""),
                row.get("project_summary", ""),
                row.get("outcomes_know", ""),
                row.get("outcomes_can", ""),
                row.get("outcomes_skills", ""),
                row.get("required_tools", ""),
                row.get("materials", ""),
                row.get("storytelling", ""),
                row.get("delivery_format", ""),
                row.get("group_size", ""),
                row.get("effort_hours", ""),
                row.get("effort_days", ""),
                row.get("cumulative_days", ""),
                row.get("xp", ""),
                row.get("completion_percent", ""),
                row.get("p2p_checks", ""),
                weighted_skills_from_row(row),
                row.get("platform_project_name", ""),
                row.get("artifact_links", ""),
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


def load_curriculum_plan_rows(conn: CatalogConnection, plan_id: int) -> list[dict[str, Any]]:
    if not table_exists(conn, "curriculum_plan_row"):
        return []
    rows = conn.execute(
        """
        SELECT *
        FROM curriculum_plan_row
        WHERE plan_id = ?
        ORDER BY row_number ASC, id ASC
        """,
        (plan_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _count_up_outcomes(row: dict[str, Any]) -> int:
    total = 0
    for key in ("outcomes_know", "outcomes_can", "outcomes_skills"):
        total += len([line for line in str(row.get(key) or "").splitlines() if line.strip()])
    return total


def _count_up_skills(row: dict[str, Any]) -> int:
    raw_node_ids = row.get("node_ids")
    if isinstance(raw_node_ids, list):
        return len(raw_node_ids)
    raw_skills = str(row.get("skills_list") or "").strip()
    if not raw_skills:
        return 0
    return len([item for item in raw_skills.split(",") if item.strip()])


def build_curriculum_quality_metrics_for_ui(
    rows: list[dict[str, Any]],
    raw_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    metrics = dict(raw_metrics or {})
    project_count = len(rows)
    skill_counts = [_count_up_skills(row) for row in rows]
    primary_skill_counts = [int(row.get("primary_skill_count", _count_up_skills(row)) or 0) for row in rows]
    repeat_skill_counts = [int(row.get("repeat_skill_count", 0) or 0) for row in rows]
    outcome_counts = [_count_up_outcomes(row) for row in rows]
    target_skills = metrics.get("target_skills_per_project") if isinstance(metrics.get("target_skills_per_project"), list) else []
    target_outcomes = metrics.get("target_outcomes_per_project") if isinstance(metrics.get("target_outcomes_per_project"), list) else []
    max_skills = int(target_skills[-1]) if target_skills else 0
    max_outcomes = int(target_outcomes[-1]) if target_outcomes else 0
    enriched_project_count = sum(
        1
        for row in rows
        if all(
            str(row.get(field) or "").strip()
            for field in (
                "project_summary",
                "artifact",
                "materials",
                "storytelling",
                "validation_criteria",
                "delivery_format",
            )
        )
    )
    artifact_field_count = sum(1 for row in rows if str(row.get("artifact") or "").strip())
    validation_criteria_count = sum(1 for row in rows if str(row.get("validation_criteria") or "").strip())
    if project_count:
        metrics["avg_skills_per_project"] = round(sum(skill_counts) / project_count, 2)
        metrics["avg_primary_skills_per_project"] = round(sum(primary_skill_counts) / project_count, 2)
        metrics["avg_repeat_skills_per_project"] = round(sum(repeat_skill_counts) / project_count, 2)
        metrics["avg_outcomes_per_project"] = round(sum(outcome_counts) / project_count, 2)
        metrics["single_skill_project_count"] = sum(1 for count in skill_counts if count <= 1)
        metrics["overloaded_project_count"] = sum(
            1
            for skill_count, outcome_count in zip(skill_counts, outcome_counts, strict=False)
            if (max_skills and skill_count > max_skills) or (max_outcomes and outcome_count > max_outcomes)
        )
        metrics["enriched_project_count"] = enriched_project_count
        metrics["enrichment_completeness_pct"] = round(enriched_project_count / project_count * 100, 1)
        metrics["artifact_field_count"] = artifact_field_count
        metrics["validation_criteria_count"] = validation_criteria_count
    else:
        metrics.setdefault("avg_skills_per_project", 0.0)
        metrics.setdefault("avg_primary_skills_per_project", 0.0)
        metrics.setdefault("avg_repeat_skills_per_project", 0.0)
        metrics.setdefault("avg_outcomes_per_project", 0.0)
        metrics.setdefault("single_skill_project_count", 0)
        metrics.setdefault("overloaded_project_count", 0)
        metrics.setdefault("enriched_project_count", 0)
        metrics.setdefault("enrichment_completeness_pct", 0.0)
        metrics.setdefault("artifact_field_count", 0)
        metrics.setdefault("validation_criteria_count", 0)
    metrics.setdefault("core_thread_count", 0)
    metrics.setdefault("repeated_thread_count", 0)
    metrics.setdefault("spiral_enabled", False)
    metrics.setdefault("artifact_project_count", 0)
    metrics.setdefault("db_template_project_count", 0)
    metrics.setdefault("unassigned_node_count", 0)
    return metrics


def build_curriculum_plan_payload_from_rows(
    plan_meta: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    from content_factory.catalog.pipeline.stage_dag_to_up import CSV_PRIMARY_HEADER, CSV_SECONDARY_HEADER

    payload = {}
    if isinstance(plan_meta.get("payload_json"), str) and plan_meta.get("payload_json"):
        try:
            payload = json.loads(str(plan_meta["payload_json"]))
        except json.JSONDecodeError:
            payload = {}

    payload_rows_by_number: dict[int, dict[str, Any]] = {}
    if isinstance(payload.get("rows"), list):
        for payload_row in payload.get("rows") or []:
            if not isinstance(payload_row, dict):
                continue
            row_number = int(payload_row.get("row_number", 0) or 0)
            if row_number:
                payload_rows_by_number[row_number] = payload_row

    normalized_rows: list[dict[str, Any]] = []
    for source_row in rows:
        row = dict(source_row)
        payload_row = payload_rows_by_number.get(int(row.get("row_number", 0) or 0), {})
        for transient_key in (
            "node_ids",
            "node_names",
            "primary_skill_count",
            "repeat_skill_count",
            "occurrence_count",
            "outcome_count",
            "artifact",
            "artifact_key",
            "artifact_family",
            "artifact_template_code",
            "project_content_type",
            "content_profile_decision",
        ):
            if transient_key in payload_row and transient_key not in row:
                row[transient_key] = payload_row[transient_key]
        if not any(row.get(key) for key in ("outcomes_know", "outcomes_can", "outcomes_skills")) and row.get("learning_outcomes"):
            row["outcomes_can"] = row.get("learning_outcomes")
        row.setdefault("materials", "")
        row["weighted_skills"] = weighted_skills_from_row(row)
        normalized_rows.append(row)
    rows = normalized_rows

    total_hours = sum(float(row.get("effort_hours", 0) or 0) for row in rows)
    total_days = sum(float(row.get("effort_days", 0) or 0) for row in rows)
    total_xp = sum(int(row.get("xp", 0) or 0) for row in rows)

    rows_by_block: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_block.setdefault(int(row.get("block_index", 0) or 0), []).append(row)

    block_payloads: list[dict[str, Any]] = []
    for block_index in sorted(rows_by_block):
        block_rows = sorted(rows_by_block[block_index], key=lambda item: (int(item.get("row_number", 0) or 0), int(item.get("id", 0) or 0)))
        block_payloads.append(
            {
                "block_index": block_index,
                "title": str(block_rows[0].get("block_title") or f"Блок {block_index or 1}"),
                "goal": str(block_rows[0].get("block_goal") or ""),
                "project_count": len(block_rows),
                "total_hours": sum(float(item.get("effort_hours", 0) or 0) for item in block_rows),
                "total_days": round(sum(float(item.get("effort_days", 0) or 0) for item in block_rows), 2),
                "rows": block_rows,
            }
        )

    status = str(plan_meta.get("status") or "draft")
    if rows and status == "deferred":
        status = "draft"
    default_message = "Черновик УП доступен для ручной доработки." if rows else "Черновик УП пока не построен."
    message = str(payload.get("message") or default_message)
    if rows and "пока не стро" in message.casefold():
        message = default_message
    payload_report = _as_dict(payload.get("report"))
    raw_quality_metrics = _as_dict(payload_report.get("quality_metrics"))
    report: dict[str, Any] = {
        "coverage_ok": bool(payload_report.get("coverage_ok", False)),
        "order_violations": _as_list(payload_report.get("order_violations")),
        "recommended_order_notes": _as_list(payload_report.get("recommended_order_notes")),
        "project_violations": _as_list(payload_report.get("project_violations")),
        "quality_metrics": build_curriculum_quality_metrics_for_ui(rows, raw_quality_metrics),
    }
    design_spec = _as_dict(payload.get("design_spec"))

    built_payload = {
        "plan_id": int(plan_meta["id"]),
        "status": status,
        "status_label": curriculum_plan_status_label(status),
        "message": message,
        "title": str(plan_meta.get("title") or payload.get("title") or "Черновик учебного плана"),
        "audience_level": str(plan_meta.get("audience_level") or payload.get("audience_level") or "Начальный"),
        "source_policy": str(plan_meta.get("source_policy") or payload.get("source_policy") or "accepted_only"),
        "summary": {
            "blocks": len(block_payloads),
            "projects": len(rows),
            "total_hours": int(total_hours) if isfinite(total_hours) else 0,
            "total_days": round(total_days, 2) if isfinite(total_days) else 0.0,
            "workload": _as_dict(_as_dict(payload.get("summary")).get("workload"))
            or build_workload_contract(
                total_hours if isfinite(total_hours) else 0,
                None,
                default_hours_per_week=config.UP_HOURS_PER_WEEK,
            ).as_dict(),
            "total_xp": int(total_xp),
        },
        "rows": rows,
        "row_count": len(rows),
        "blocks": block_payloads,
        "template_proposal_count": int(payload.get("template_proposal_count") or 0),
        "template_proposal_status": str(payload.get("template_proposal_status") or "none"),
        "csv_primary_header": payload.get("csv_primary_header") or CSV_PRIMARY_HEADER,
        "csv_secondary_header": payload.get("csv_secondary_header") or CSV_SECONDARY_HEADER,
        "report": report,
        "design_spec": design_spec,
    }
    return built_payload


def get_curriculum_plan(conn: CatalogConnection, plan_id: int) -> dict[str, Any] | None:
    if not table_exists(conn, "curriculum_plan"):
        return None
    row = conn.execute(
        """
        SELECT
            cp.*,
            pb.role AS brief_role,
            pb.seniority AS brief_seniority,
            pb.domain AS brief_domain,
            (
                SELECT ij.id
                FROM intake_job ij
                WHERE ij.status = 'succeeded'
                  AND json_valid(ij.result_payload)
                  AND CAST(json_extract(ij.result_payload, '$.brief_id') AS INTEGER) = cp.brief_id
                ORDER BY ij.created_at DESC
                LIMIT 1
            ) AS latest_job_id
        FROM curriculum_plan cp
        LEFT JOIN profile_brief pb ON pb.id = cp.brief_id
        WHERE cp.id = ?
        """,
        (plan_id,),
    ).fetchone()
    if not row:
        return None
    plan_meta = dict(row)
    row_records = load_curriculum_plan_rows(conn, plan_id)
    plan_payload = build_curriculum_plan_payload_from_rows(plan_meta, row_records)
    if table_exists(conn, "curriculum_artifact_template_proposal"):
        proposal_stats = conn.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count
            FROM curriculum_artifact_template_proposal
            WHERE brief_id = ?
            """,
            (int(plan_meta.get("brief_id") or 0),),
        ).fetchone()
        if proposal_stats:
            total_count = int(proposal_stats["total_count"] or 0)
            open_count = int(proposal_stats["open_count"] or 0)
            plan_payload["template_proposal_count"] = total_count
            plan_payload["template_proposal_status"] = "open" if open_count else ("done" if total_count else "none")
    plan_payload.update(
        {
            "id": int(plan_meta["id"]),
            "brief_id": plan_meta.get("brief_id"),
            "updated_at": plan_meta.get("updated_at"),
            "created_at": plan_meta.get("created_at"),
            "latest_job_id": plan_meta.get("latest_job_id"),
            "brief_role": plan_meta.get("brief_role"),
            "brief_seniority": plan_meta.get("brief_seniority"),
            "brief_domain": plan_meta.get("brief_domain"),
        }
    )
    return plan_payload


def list_curriculum_plans(conn: CatalogConnection, limit: int = 50) -> list[dict[str, Any]]:
    if not table_exists(conn, "curriculum_plan"):
        return []
    rows = conn.execute(
        """
        SELECT
            cp.id,
            cp.brief_id,
            cp.status,
            cp.title,
            cp.audience_level,
            cp.total_blocks,
            cp.total_projects,
            cp.total_hours,
            cp.total_days,
            cp.total_xp,
            cp.updated_at,
            pb.role AS brief_role,
            pb.domain AS brief_domain,
            (
                SELECT ij.id
                FROM intake_job ij
                WHERE ij.status = 'succeeded'
                  AND json_valid(ij.result_payload)
                  AND CAST(json_extract(ij.result_payload, '$.brief_id') AS INTEGER) = cp.brief_id
                ORDER BY ij.created_at DESC
                LIMIT 1
            ) AS latest_job_id
        FROM curriculum_plan cp
        LEFT JOIN profile_brief pb ON pb.id = cp.brief_id
        ORDER BY cp.updated_at DESC, cp.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["status_label"] = curriculum_plan_status_label(str(item.get("status")))
        items.append(item)
    return items


def sync_curriculum_plan_payload(conn: CatalogConnection, plan_id: int) -> dict[str, Any] | None:
    plan_payload = get_curriculum_plan(conn, plan_id)
    if not plan_payload:
        return None
    summary = _as_dict(plan_payload.get("summary"))
    conn.execute(
        """
        UPDATE curriculum_plan
        SET status = ?,
            title = ?,
            audience_level = ?,
            total_blocks = ?,
            total_projects = ?,
            total_hours = ?,
            total_days = ?,
            total_xp = ?,
            payload_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            str(plan_payload.get("status") or "draft"),
            plan_payload.get("title"),
            plan_payload.get("audience_level"),
            int(summary.get("blocks", 0) or 0),
            int(summary.get("projects", 0) or 0),
            float(summary.get("total_hours", 0) or 0),
            float(summary.get("total_days", 0) or 0),
            int(summary.get("total_xp", 0) or 0),
            json.dumps(plan_payload, ensure_ascii=False),
            plan_id,
        ),
    )
    conn.commit()
    brief_id = plan_payload.get("brief_id")
    if isinstance(brief_id, int):
        update_jobs_curriculum_plan_payload(
            conn,
            brief_id,
            plan_payload,
            persisted_update={"curriculum_plan_rows": int(plan_payload.get("row_count", 0) or 0)},
        )
    return get_curriculum_plan(conn, plan_id)


def create_curriculum_plan_row(conn: CatalogConnection, plan_id: int) -> int:
    plan = get_curriculum_plan(conn, plan_id)
    if not plan:
        raise ValueError("Curriculum plan not found")
    existing_rows = _as_list(plan.get("rows"))
    next_row_number = max((int(row.get("row_number", 0) or 0) for row in existing_rows), default=0) + 1
    next_block_index = max((int(row.get("block_index", 0) or 0) for row in existing_rows), default=0) or 1
    next_project_index = max((int(row.get("project_index_in_block", 0) or 0) for row in existing_rows if int(row.get("block_index", 0) or 0) == next_block_index), default=0) + 1
    cur = conn.execute(
        """
        INSERT INTO curriculum_plan_row(
            plan_id, block_index, row_number, project_index_in_block, block_title, block_goal,
            project_name, project_summary, outcomes_know, outcomes_can, outcomes_skills,
            learning_outcomes, skills_list, audience_level, required_tools, materials,
            validation_criteria, storytelling, delivery_format, group_size, effort_hours, effort_days,
            cumulative_days, xp, platform_project_name, artifact_links
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan_id,
            next_block_index,
            next_row_number,
            next_project_index,
            f"Блок {next_block_index}",
            "",
            f"Новый проект {next_row_number}",
            "",
            "",
            "",
            "",
            "",
            "",
            plan.get("audience_level", "Начальный"),
            "",
            "",
            "",
            "",
            "индивидуальный",
            1,
            0.0,
            None,
            None,
            None,
            "",
            "",
        ),
    )
    conn.commit()
    sync_curriculum_plan_payload(conn, plan_id)
    return int(cur.lastrowid or 0)


def get_curriculum_plan_row(conn: CatalogConnection, plan_id: int, row_id: int) -> dict[str, Any] | None:
    if not table_exists(conn, "curriculum_plan_row"):
        return None
    row = conn.execute(
        "SELECT * FROM curriculum_plan_row WHERE id = ? AND plan_id = ?",
        (row_id, plan_id),
    ).fetchone()
    return dict(row) if row else None


def parse_scope_names(raw_names: str | None, scope_type: str = "coverage_area") -> list[str]:
    if scope_type == "any":
        return ["*"]
    return [item.strip() for item in re.split(r"[\n;]+", raw_names or "") if item.strip()]


def update_curriculum_plan_row(conn: CatalogConnection, plan_id: int, row_id: int, form_data: dict[str, str]) -> dict[str, Any]:
    row = get_curriculum_plan_row(conn, plan_id, row_id)
    if not row:
        raise ValueError("Curriculum plan row not found")
    outcomes_know = form_data.get("outcomes_know", "").strip()
    outcomes_can = form_data.get("outcomes_can", "").strip()
    outcomes_skills = form_data.get("outcomes_skills", "").strip()
    learning_outcomes = "\n".join(item for item in [outcomes_know, outcomes_can, outcomes_skills] if item)
    conn.execute(
        """
        UPDATE curriculum_plan_row
        SET block_index = ?,
            row_number = ?,
            project_index_in_block = ?,
            block_title = ?,
            block_goal = ?,
            project_name = ?,
            project_summary = ?,
            outcomes_know = ?,
            outcomes_can = ?,
            outcomes_skills = ?,
            learning_outcomes = ?,
            skills_list = ?,
            audience_level = ?,
            required_tools = ?,
            materials = ?,
            validation_criteria = ?,
            storytelling = ?,
            delivery_format = ?,
            group_size = ?,
            effort_hours = ?,
            effort_days = ?,
            cumulative_days = ?,
            xp = ?,
            platform_project_name = ?,
            artifact_links = ?
        WHERE id = ? AND plan_id = ?
        """,
        (
            parse_optional_int(form_data.get("block_index")) or 1,
            parse_optional_int(form_data.get("row_number")) or 1,
            parse_optional_int(form_data.get("project_index_in_block")) or 1,
            form_data.get("block_title", "").strip(),
            form_data.get("block_goal", "").strip(),
            form_data.get("project_name", "").strip(),
            form_data.get("project_summary", "").strip(),
            outcomes_know,
            outcomes_can,
            outcomes_skills,
            learning_outcomes,
            form_data.get("skills_list", "").strip(),
            form_data.get("audience_level", "").strip(),
            form_data.get("required_tools", "").strip(),
            form_data.get("materials", "").strip(),
            form_data.get("validation_criteria", "").strip(),
            form_data.get("storytelling", "").strip(),
            form_data.get("delivery_format", "").strip(),
            form_data.get("group_size", "").strip(),
            parse_optional_float(form_data.get("effort_hours")) or 0.0,
            None,
            None,
            None,
            "",
            "",
            row_id,
            plan_id,
        ),
    )
    conn.commit()
    sync_curriculum_plan_payload(conn, plan_id)
    updated_row = get_curriculum_plan_row(conn, plan_id, row_id)
    if not updated_row:
        raise ValueError("Curriculum plan row not found after update")
    return updated_row


def delete_curriculum_plan_row(conn: CatalogConnection, plan_id: int, row_id: int) -> None:
    conn.execute("DELETE FROM curriculum_plan_row WHERE id = ? AND plan_id = ?", (row_id, plan_id))
    conn.commit()
    sync_curriculum_plan_payload(conn, plan_id)


def reset_curriculum_plan_payload_in_jobs(conn: CatalogConnection, brief_id: int, message: str) -> None:
    if not table_exists(conn, "intake_job"):
        return
    rows = conn.execute(
        """
        SELECT id, result_payload
        FROM intake_job
        WHERE result_payload IS NOT NULL
          AND json_valid(result_payload)
          AND CAST(json_extract(result_payload, '$.brief_id') AS INTEGER) = ?
        """,
        (brief_id,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(str(row["result_payload"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        payload["curriculum_plan"] = build_deferred_curriculum_plan_payload(message)
        persisted = payload.get("persisted")
        if isinstance(persisted, dict):
            persisted["curriculum_plan_rows"] = 0
        conn.execute(
            "UPDATE intake_job SET result_payload = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), datetime.now(UTC).isoformat(), row["id"]),
        )


def delete_curriculum_plan(conn: CatalogConnection, plan_id: int) -> bool:
    if not table_exists(conn, "curriculum_plan"):
        return False
    row = conn.execute("SELECT id, brief_id FROM curriculum_plan WHERE id = ?", (plan_id,)).fetchone()
    if not row:
        return False
    brief_id = row["brief_id"]
    if table_exists(conn, "curriculum_plan_row"):
        conn.execute("DELETE FROM curriculum_plan_row WHERE plan_id = ?", (plan_id,))
    conn.execute("DELETE FROM curriculum_plan WHERE id = ?", (plan_id,))
    if isinstance(brief_id, int):
        reset_curriculum_plan_payload_in_jobs(conn, brief_id, "УП был удалён вручную.")
    conn.commit()
    return True


def cleanup_empty_curriculum_plans(conn: CatalogConnection) -> int:
    if not table_exists(conn, "curriculum_plan"):
        return 0
    rows = conn.execute(
        """
        SELECT cp.id
        FROM curriculum_plan cp
        WHERE cp.status = 'deferred'
           OR cp.total_projects = 0
           OR NOT EXISTS (
                SELECT 1
                FROM curriculum_plan_row cpr
                WHERE cpr.plan_id = cp.id
           )
        """
    ).fetchall()
    deleted = 0
    for row in rows:
        if delete_curriculum_plan(conn, int(row["id"])):
            deleted += 1
    return deleted
