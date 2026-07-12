"""Curriculum semantics for prerequisite and thematic edges."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

CurriculumEdgeRole = Literal["required", "recommended", "related"]


def curriculum_edge_role(edge: Mapping[str, object]) -> CurriculumEdgeRole:
    """Normalize new role metadata and legacy hard/soft relation types."""

    explicit = str(edge.get("curriculum_role") or "").strip().casefold()
    if explicit in {"required", "recommended", "related"}:
        return cast(CurriculumEdgeRole, explicit)
    relation_type = str(edge.get("relation_type") or "").strip().casefold()
    if relation_type == "hard":
        return "required"
    if relation_type == "related":
        return "related"
    return "recommended"


def curriculum_edge_label(role: CurriculumEdgeRole) -> str:
    """Return a methodologist-facing label for an edge role."""

    return {
        "required": "Обязательный пререквизит",
        "recommended": "Рекомендуемый порядок",
        "related": "Тематически связаны",
    }[role]
