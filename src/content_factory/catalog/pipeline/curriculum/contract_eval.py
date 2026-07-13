"""Golden contract eval over a built UP (contract epic, slice 9).

Reads the contract quality metrics + the publication-gate verdict from a UP plan payload
and renders a human-readable eval, optionally against a saved baseline (e.g. the pre-contract
UP #30 run) so the improvement is visible. Pure/stdlib leaf — the CLI wrapper
(``scripts/curriculum_contract_eval.py``) supplies the payload from the DB or a JSON file.
"""

from __future__ import annotations

from typing import Any

#: (metric key, human label, "good" direction). direction: "zero" == want 0,
#: "full" == want 100, "low" == lower is better, "info" == informational only.
CONTRACT_METRICS: tuple[tuple[str, str, str], ...] = (
    ("title_violation_count", "Нарушений названий", "zero"),
    ("generic_artifact_count", "Generic-артефактов", "zero"),
    ("generic_criterion_count", "Generic-критериев", "zero"),
    ("policy_area_coverage_pct", "Покрытие policy-областей, %", "full"),
    ("testable_criteria_coverage_pct", "Проверяемых критериев, %", "full"),
    ("blocking_question_count", "Блокирующих вопросов", "zero"),
    ("single_skill_project_pct", "Однонавыковых проектов, %", "low"),
    ("template_bound_project_count", "Проектов с template-binding", "info"),
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Extract gate verdict, contract metrics, and workload from a UP plan payload."""
    report = _as_dict(plan.get("report"))
    metrics = _as_dict(report.get("quality_metrics"))
    gate = _as_dict(report.get("publication_gate"))
    summary = _as_dict(plan.get("summary"))
    return {
        "plan_id": plan.get("plan_id"),
        "status": plan.get("status"),
        "projects": summary.get("projects"),
        "gate": gate,
        "metrics": {key: metrics.get(key) for key, _label, _dir in CONTRACT_METRICS},
        "workload": _as_dict(summary.get("workload")),
    }


def render_summary(summary: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    """Render the eval as text; when a baseline summary is given, show before -> after."""
    lines: list[str] = []
    lines.append(f"UP #{summary.get('plan_id')} · статус {summary.get('status')} · проектов {summary.get('projects')}")

    workload = summary.get("workload") or {}
    if workload:
        lines.append(
            f"Трудоёмкость: {workload.get('total_hours')} ч · "
            f"{workload.get('duration_weeks')} нед · ≈ {workload.get('duration_months')} мес "
            f"({workload.get('hours_per_week')} ч/нед)"
        )

    base_metrics = (baseline or {}).get("metrics", {}) if baseline else {}
    lines.append("Метрики контракта:")
    for key, label, _direction in CONTRACT_METRICS:
        value = summary["metrics"].get(key)
        if baseline and key in base_metrics:
            lines.append(f"  {label}: {base_metrics.get(key)} -> {value}")
        else:
            lines.append(f"  {label}: {value}")

    gate = summary.get("gate") or {}
    if gate.get("passed"):
        lines.append("Публикация: ГОТОВ (0 блокирующих нарушений)")
    else:
        failures = gate.get("failures") or []
        lines.append(f"Публикация: ЗАБЛОКИРОВАН ({len(failures)} нарушений)")
        for failure in failures:
            lines.append(f"  - {failure.get('message')} ({failure.get('value')} / порог {failure.get('threshold')})")
    waived = gate.get("waived") or []
    if waived:
        lines.append(f"Waiver: {', '.join(str(item.get('code')) for item in waived)}")
    return "\n".join(lines)


def gate_passed(summary: dict[str, Any]) -> bool:
    return bool((summary.get("gate") or {}).get("passed"))
