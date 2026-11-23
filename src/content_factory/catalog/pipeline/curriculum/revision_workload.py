"""Compare estimated core practice with the requested calendar without rescaling rows."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .. import config
from .workload import build_workload_contract


def refresh_revision_workload(plan: dict[str, Any], calendar: dict[str, Any]) -> None:
    """Refresh from the current rows, also after edits in the curriculum editor."""
    rows = plan["rows"]
    core = sum(float(row.get("effort_hours") or 0) for row in rows if row.get("route") not in {"specialization", "needs_design"})
    electives = sum(float(row.get("effort_hours") or 0) for row in rows if row.get("route") == "specialization")
    provisional = [row for row in rows if row.get("route") == "needs_design"]
    alternatives: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("route") == "specialization" and row.get("choose_one_group"):
            alternatives[row["choose_one_group"]].append(float(row.get("effort_hours") or 0))
    shortest = core + sum(min(options) for options in alternatives.values())
    longest = core + sum(max(options) for options in alternatives.values())
    target = calendar.get("target_total_hours")
    try:
        target = float(target) if target else None
    except (ValueError, TypeError):
        target = None
    plan["summary"]["workload"] = build_workload_contract(
        shortest, calendar, default_hours_per_week=config.UP_HOURS_PER_WEEK,
    ).as_dict()
    plan["summary"]["revision_workload"] = {
        "core_hours": round(core, 2), "elective_hours": round(electives, 2),
        "target_hours": target, "difference_hours": round(shortest - target, 2) if target is not None else None,
        "min_route_hours": round(shortest, 2), "max_route_hours": round(longest, 2),
        "max_route_difference": round(longest - target, 2) if target is not None else None,
        "choice_groups": list(alternatives),
        "pending_projects": len(provisional), "pending_hours": sum(float(r.get("effort_hours") or 0) for r in provisional),
        "historical_estimates": sum(row.get("hours_basis") == "historical_reference" for row in rows),
    }
