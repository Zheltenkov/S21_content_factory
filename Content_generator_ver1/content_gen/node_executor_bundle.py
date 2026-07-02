"""Concrete node executor bundle for AgentFlow orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .context_phase_executor import ContextPhaseExecutor
from .generation_runtime import GenerationRuntimeContainer
from .phase_executors import EvaluationPhaseExecutor, QualityPhaseExecutor, TranslationPhaseExecutor
from .practice_phase_executor import PracticePhaseExecutor
from .structure_phase_executor import StructurePhaseExecutor
from .theory_phase_executor import TheoryPhaseExecutor


@dataclass
class GenerationNodeExecutorBundle:
    """Concrete node executors used by AgentFlow services."""

    context: ContextPhaseExecutor
    structure: StructurePhaseExecutor
    theory: TheoryPhaseExecutor
    practice: PracticePhaseExecutor
    quality: QualityPhaseExecutor
    evaluation: EvaluationPhaseExecutor
    translation: TranslationPhaseExecutor
    runtime: GenerationRuntimeContainer

    @classmethod
    def from_runtime(cls, runtime: GenerationRuntimeContainer) -> "GenerationNodeExecutorBundle":
        """Build all node executors from the generation runtime container."""
        return cls(
            context=ContextPhaseExecutor(runtime),
            structure=StructurePhaseExecutor(runtime),
            theory=TheoryPhaseExecutor(runtime),
            practice=PracticePhaseExecutor(runtime),
            quality=QualityPhaseExecutor(runtime),
            evaluation=EvaluationPhaseExecutor(runtime),
            translation=TranslationPhaseExecutor(runtime),
            runtime=runtime,
        )
