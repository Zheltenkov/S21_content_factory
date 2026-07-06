"""Типизированные модели дидактической оси (вторая ось, не мержится с 39 критериями)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JurorVerdict(BaseModel):
    """Вердикт одного члена жюри по одному дименшену (structured LLM output)."""

    model_config = ConfigDict(extra="ignore")

    score: float = Field(description="Балл 1–5 (5 = отлично)")
    rationale: str = Field(default="", description="Краткое обоснование")
    evidence: list[str] = Field(default_factory=list, description="Цитаты/факты из README")


class DidacticDimensionScore(BaseModel):
    """Свод по одному дидактическому дименшену после жюри (и возможной дискуссии)."""

    dimension: str
    title: str
    score: float = Field(description="Медиана по жюри (после дискуссии, если была)")
    confidence: float = Field(description="Уверенность из разброса жюри: 1 − pstdev/2")
    per_model: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)
    escalated: bool = False
    escalate_reason: str = ""
    debate_transcript: list[dict[str, Any]] = Field(default_factory=list)


class DidacticQualityReport(BaseModel):
    """Отчёт дидактической оси. Идёт рядом с рубрикой, не суммируется с ней."""

    dimensions: list[DidacticDimensionScore] = Field(default_factory=list)
    overall_raw: float = Field(default=0.0, description="Медиана баллов по дименшенам")
    needs_human_review: bool = False
    abstain_reasons: list[str] = Field(default_factory=list)
    jury: list[str] = Field(default_factory=list, description="Модели жюри (после анти-self-bias)")
    n_jury: int = 0
