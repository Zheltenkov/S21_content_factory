"""Curriculum planning layer.

The package keeps pedagogical planning separate from DAG construction and CSV/UI
rendering. The public entry point is ``build_curriculum_blocks``.
"""

from .domain import CurriculumBlock, PlanNode, PlanQualityMetrics, ProjectBlueprint, SkillOccurrence
from .journey import (
    CurriculumDesignSpec,
    CurriculumStageSpec,
    approve_curriculum_design_spec,
    build_curriculum_design_spec,
)
from .planner import build_curriculum_blocks

__all__ = [
    "CurriculumBlock",
    "CurriculumDesignSpec",
    "CurriculumStageSpec",
    "PlanNode",
    "PlanQualityMetrics",
    "ProjectBlueprint",
    "SkillOccurrence",
    "approve_curriculum_design_spec",
    "build_curriculum_blocks",
    "build_curriculum_design_spec",
]
