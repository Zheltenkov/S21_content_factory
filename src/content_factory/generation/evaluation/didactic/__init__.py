"""Дидактическая ось качества (жюри моделей).

Вторая ось оценки сгенерированного README: не «есть ли блок» (это структурная ось,
`RubricScorer`), а «учит ли текст». Несколько LLM независимо оценивают дидактические
дименшены 1–5 → медиана + уверенность из разброса; спорные эскалируются в дискуссию.
Отчёт `DidacticQualityReport` идёт рядом с рубрикой и НЕ складывается с 39 критериями.
"""

from .models import (
    DidacticDimensionScore,
    DidacticQualityReport,
    JurorVerdict,
)
from .scorer import DidacticQualityScorer

__all__ = [
    "DidacticDimensionScore",
    "DidacticQualityReport",
    "DidacticQualityScorer",
    "JurorVerdict",
]
