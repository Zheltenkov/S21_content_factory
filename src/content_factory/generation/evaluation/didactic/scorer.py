"""DidacticQualityScorer — оркестрация дидактической оси (вторая ось качества)."""

from __future__ import annotations

import concurrent.futures
import statistics
from collections.abc import Callable

from ...config.didactic_config import (
    ABSTAIN_CONFIDENCE,
    DEBATE_ON_ESCALATE,
    DEFAULT_DEBATE_ROLES,
    DIDACTIC_FLOOR,
    resolve_jury_models,
)
from .dimensions import DIMENSIONS
from .jury import JuryBackend, LLMJuryBackend, judge_dimension
from .models import DidacticDimensionScore, DidacticQualityReport
from .signals import collect_signals

BackendFactory = Callable[[str], JuryBackend]


def _default_backend_factory(md: str) -> JuryBackend:
    return LLMJuryBackend(md)


def _sanitize_debate_roles(
    roles: dict[str, str], models: list[str], generator_model: str | None
) -> dict[str, str]:
    """Убрать генератор из ролей дискуссии (анти-self-bias), подставив модель жюри."""
    fallback = models[0] if models else ""
    sanitized: dict[str, str] = {}
    for role, model in roles.items():
        sanitized[role] = fallback if (generator_model and model == generator_model) else model
    return sanitized


class DidacticQualityScorer:
    """Оценивает README дидактическим жюри моделей → DidacticQualityReport."""

    def __init__(
        self,
        *,
        backend_factory: BackendFactory | None = None,
        jury_models: list[str] | None = None,
        debate_roles: dict[str, str] | None = None,
        floor: float = DIDACTIC_FLOOR,
        abstain_confidence: float = ABSTAIN_CONFIDENCE,
        debate_on_escalate: bool = DEBATE_ON_ESCALATE,
        max_workers: int = 6,
    ) -> None:
        self._backend_factory = backend_factory or _default_backend_factory
        self._configured_models = jury_models
        self._debate_roles = debate_roles or dict(DEFAULT_DEBATE_ROLES)
        self._floor = floor
        self._abstain_confidence = abstain_confidence
        self._debate_on_escalate = debate_on_escalate
        self._max_workers = max_workers

    def score(
        self,
        md: str,
        learning_outcomes: list[str] | None = None,
        generator_model: str | None = None,
    ) -> DidacticQualityReport:
        """Прогнать жюри по всем дименшенам. Генератор исключается из жюри (анти-self-bias)."""
        outcomes = list(learning_outcomes or [])
        base_models = self._configured_models or resolve_jury_models()
        models = [m for m in base_models if m != generator_model]
        debate_roles = _sanitize_debate_roles(self._debate_roles, models, generator_model)

        signals = collect_signals(md)
        backend = self._backend_factory(md)

        def _judge(dim_index: int) -> tuple[int, DidacticDimensionScore]:
            dim = DIMENSIONS[dim_index]
            return dim_index, judge_dimension(
                dim,
                models,
                signals,
                outcomes,
                backend,
                floor=self._floor,
                abstain_confidence=self._abstain_confidence,
                debate_on_escalate=self._debate_on_escalate,
                debate_roles=debate_roles,
            )

        results: list[DidacticDimensionScore | None] = [None] * len(DIMENSIONS)
        with concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            for index, score in executor.map(_judge, range(len(DIMENSIONS))):
                results[index] = score
        dimensions = [score for score in results if score is not None]

        return self._build_report(dimensions, models)

    def _build_report(
        self, dimensions: list[DidacticDimensionScore], models: list[str]
    ) -> DidacticQualityReport:
        scores = [d.score for d in dimensions]
        overall = round(statistics.median(scores), 2) if scores else 0.0
        abstain: list[str] = []
        for dim in dimensions:
            if dim.confidence < self._abstain_confidence:
                abstain.append(f"jury_split:{dim.dimension}")
            if dim.score < self._floor:
                abstain.append(f"below_floor:{dim.dimension}")
        return DidacticQualityReport(
            dimensions=dimensions,
            overall_raw=overall,
            needs_human_review=bool(abstain),
            abstain_reasons=sorted(set(abstain)),
            jury=models,
            n_jury=len(models),
        )
