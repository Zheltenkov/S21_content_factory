"""Canonical, reproducible snapshot of curriculum-plan structure.

The raw registry and CSV adapters may expose different field names, but the RPD pipeline must see
the same program topology: modules, projects and their hour distribution.  These models persist
that topology with every build so a total-hours match cannot hide a module-level source drift.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from .up_models import UpPlan


class CanonicalProjectSpec(BaseModel):
    """Source identity and workload of one curriculum project."""

    name: str = ""
    hours: str = ""


class CanonicalModuleSpec(BaseModel):
    """Ordered module snapshot used by fidelity checks and audit logs."""

    title: str = ""
    hours: str = ""
    project_count: int = 0
    projects: list[CanonicalProjectSpec] = Field(default_factory=list)


class CanonicalProgramSpec(BaseModel):
    """Normalized source contract persisted with an editable RPD build."""

    title: str = ""
    declared_total_hours: str = ""
    calculated_total_hours: str = ""
    module_count: int = 0
    project_count: int = 0
    competency_count: int = 0
    modules: list[CanonicalModuleSpec] = Field(default_factory=list)
    fingerprint: str = ""


def canonicalize_up_plan(up: UpPlan) -> CanonicalProgramSpec:
    """Create an ordered deep snapshot whose fingerprint is stable across source adapters."""
    modules = [
        CanonicalModuleSpec(
            title=_compact(module.title),
            hours=module.hours,
            project_count=len(module.rows),
            projects=[
                CanonicalProjectSpec(name=_compact(row.name), hours=row.hours)
                for row in module.rows
            ],
        )
        for module in up.modules
    ]
    calculated = sum(
        (
            value
            for module in up.modules
            for row in module.rows
            if (value := _decimal(row.hours)) is not None
        ),
        Decimal("0"),
    )
    payload = {
        "title": _compact(up.title),
        "declared_total_hours": up.total_hours,
        "calculated_total_hours": _decimal_str(calculated),
        "modules": [module.model_dump(mode="json") for module in modules],
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return CanonicalProgramSpec(
        **payload,
        module_count=len(modules),
        project_count=sum(module.project_count for module in modules),
        competency_count=len(up.competency_rows()),
        fingerprint=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    )


def _compact(value: str) -> str:
    return " ".join(str(value or "").split())


def _decimal(value: str) -> Decimal | None:
    text = str(value or "").strip().replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _decimal_str(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = [
    "CanonicalModuleSpec",
    "CanonicalProgramSpec",
    "CanonicalProjectSpec",
    "canonicalize_up_plan",
]
