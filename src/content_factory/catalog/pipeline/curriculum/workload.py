"""Build the canonical WorkloadContract for a curriculum plan.

Slice 2 of the project-contract epic (see ``docs/project-contract-epic.md``): the plan's
total hours are authoritative; calendar duration is *derived* (weeks = hours / weekly
intensity, months = weeks / 4.345). This replaces the misleading "total_days" figure
(hours divided by a fixed ``UP_HOURS_PER_DAY`` constant) that read like a program length.

Pure leaf: depends only on the ``WorkloadContract`` dataclass + stdlib.
"""

from __future__ import annotations

from typing import Any

from .domain import WorkloadContract

#: Average weeks per month for month conversion (matches the brief-workload extractor).
WEEKS_PER_MONTH = 4.345


def build_workload_contract(
    total_hours: float,
    spec: dict[str, Any] | None,
    *,
    default_hours_per_week: float,
) -> WorkloadContract:
    """Derive weeks/months from the built UP's total hours and the study intensity.

    ``hours_per_week`` comes from the brief when stated, otherwise the planning default.
    ``study_days_per_week`` is populated only when the brief provides it (never invented).
    """
    spec = spec or {}
    hours_per_week = int(round(float(spec.get("hours_per_week") or default_hours_per_week)))
    hours = int(round(float(total_hours or 0)))
    duration_weeks = round(hours / hours_per_week, 1) if hours_per_week else 0.0
    duration_months = round(duration_weeks / WEEKS_PER_MONTH, 1) if duration_weeks else 0.0
    raw_study_days = spec.get("study_days_per_week")
    study_days_per_week = int(raw_study_days) if raw_study_days else None
    return WorkloadContract(
        total_hours=hours,
        hours_per_week=hours_per_week,
        duration_weeks=duration_weeks,
        duration_months=duration_months,
        study_days_per_week=study_days_per_week,
    )
