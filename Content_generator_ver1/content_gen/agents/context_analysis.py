"""Typed contracts for curriculum-aware context analysis."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..curriculum.models import CurriculumEntry


class ContextAnalysisResult(BaseModel):
    """Structured curriculum context consumed by downstream generation steps."""

    is_first_project: bool = Field(description="Является ли это первым проектом в тематическом блоке")
    context_summary: str = Field(description="Краткое резюме контекста для агентов генерации")
    narrative_anchor: str = Field(default="", description="Мостик к предыдущим проектам")
    similar_projects: list[CurriculumEntry] = Field(
        default_factory=list,
        description="Соседние или похожие элементы учебного плана",
    )
    relevant_chunks: list[str] = Field(
        default_factory=list,
        description="Явные фрагменты curriculum/reference context, если они были переданы",
    )
    skills_alignment: dict[str, Any] = Field(default_factory=dict)
    learning_outcomes_alignment: dict[str, Any] = Field(default_factory=dict)
    tools_alignment: dict[str, Any] = Field(default_factory=dict)
    audience_level_match: bool = True
    metrics: dict[str, Any] = Field(default_factory=dict)
