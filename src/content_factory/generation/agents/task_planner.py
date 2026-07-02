"""
content_gen/agents/task_planner.py

Агент планирования практических задач.

Определяет количество задач и их условную сложность на основе
входного уровня аудитории и curriculum context.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agents.context_analysis import ContextAnalysisResult
from ..config.loader import get_agent_config
from ..config.thresholds import THRESHOLDS
from ..curriculum.graph import CurriculumInsights, analyze_curriculum_position
from ..models.schemas import ProjectContextMeta, ProjectSeed

logger = logging.getLogger(__name__)


class TaskPlan(BaseModel):
    """Результат планирования практики."""

    model_config = ConfigDict(extra="forbid", strict=True)

    tasks_count: int = Field(
        description="Количество практических задач (от 2 до 8)",
        ge=2,
        le=8
    )
    complexity: Literal["easy", "medium", "hard"] = Field(
        description="Уровень сложности задач: 'easy', 'medium' или 'hard'"
    )
    level_index: int = Field(
        description="Индекс уровня аудитории: 0 (базовый), 1 (средний), 2 (продвинутый)",
        ge=0,
        le=2
    )
    level_source: Literal["context+audience", "audience_only", "curriculum_adjusted"] = Field(
        description="Источник определения уровня: 'context+audience', 'audience_only' или 'curriculum_adjusted'"
    )
    rationale: str = Field(
        description="Краткое обоснование выбранного уровня и количества задач"
    )
    explanation: str = Field(
        description="Подробное объяснение планирования с учетом истории трека и curriculum"
    )
    curriculum_context: dict[str, Any] | None = Field(
        default=None,
        description="Контекст из curriculum графа: прогресс, навыки, корректировки уровня"
    )

    def as_dict(self) -> dict:
        """Возвращает словарь для обратной совместимости."""
        return self.model_dump(exclude_none=True)


class TaskPlanner:
    """Определяет параметр практики на основе уровня аудитории и истории трека."""

    CONFIG_NAME = "task_planner"

    LEVEL_KEYWORDS = {
        0: ("base", "beginner", "novice", "intro", "junior", "нович", "баз", "старт"),
        1: ("middle", "intermediate", "mid", "опыт", "серед"),
        2: ("advanced", "expert", "senior", "углуб", "продвин"),
    }

    LEVEL_CONFIG = {
        0: {"tasks": 6, "complexity": "easy"},
        1: {"tasks": 5, "complexity": "medium"},
        2: {"tasks": 3, "complexity": "hard"},
    }

    def __init__(self):
        lo, hi = THRESHOLDS["practice_tasks_range"]
        self.min_tasks = lo
        self.max_tasks = hi
        self.config = get_agent_config(self.CONFIG_NAME)
        options = self.config.options or {}

        level_keywords_cfg = options.get("level_keywords", {})
        self.level_keywords = {
            0: tuple(level_keywords_cfg.get("base", self.LEVEL_KEYWORDS[0])),
            1: tuple(level_keywords_cfg.get("middle", self.LEVEL_KEYWORDS[1])),
            2: tuple(level_keywords_cfg.get("advanced", self.LEVEL_KEYWORDS[2])),
        }

        level_config_cfg = options.get("level_config", {})
        self.level_config = {
            0: level_config_cfg.get("base", self.LEVEL_CONFIG[0]),
            1: level_config_cfg.get("middle", self.LEVEL_CONFIG[1]),
            2: level_config_cfg.get("advanced", self.LEVEL_CONFIG[2]),
        }
        self.rationale_template = options.get(
            "rationale_template",
            "История трека (order={order}) => уровень {context_level}; "
            "Уровень аудитории => {audience_level}; Финальный уровень => {final_level}",
        )

    def plan(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta | None,
        context_analysis: ContextAnalysisResult | None,
    ) -> TaskPlan:
        """Возвращает план практики."""

        audience_level_idx = self._audience_level_to_index(seed.audience_level)
        context_level_idx = self._context_level_from_history(context_meta, context_analysis)

        if context_level_idx is not None:
            level_idx = max(audience_level_idx, context_level_idx)
            level_source = "context+audience"
        else:
            level_idx = audience_level_idx
            level_source = "audience_only"

        level_idx = max(0, min(level_idx, 2))
        curriculum = analyze_curriculum_position(
            track=seed.thematic_block,
            last_order=context_meta.last_order if context_meta else None,
            current_skills=seed.skills,
        )

        level_idx = self._adjust_level_by_curriculum(level_idx, curriculum.level_adjust)

        config = self.level_config.get(level_idx, self.LEVEL_CONFIG[1])
        tasks = self._clamp_tasks(config["tasks"] + curriculum.task_adjust)
        complexity = config["complexity"]
        if curriculum.level_adjust > 0 and level_idx == 2:
            complexity = "hard"
        elif curriculum.level_adjust < 0 and level_idx == 0:
            complexity = "easy"

        rationale = self._build_rationale(level_idx, audience_level_idx, context_level_idx, context_meta, curriculum)
        explanation = self._build_explanation(
            level_idx,
            seed,
            context_meta,
            context_analysis,
            tasks,
            complexity,
            curriculum,
        )

        plan = TaskPlan(
            tasks_count=tasks,
            complexity=complexity,
            level_index=level_idx,
            level_source=level_source,
            rationale=rationale,
            explanation=explanation,
            curriculum_context=curriculum.to_dict(),
        )
        logger.info(
            "📊 TaskPlanner: tasks=%s complexity=%s level=%s source=%s",
            plan.tasks_count,
            plan.complexity,
            plan.level_index,
            plan.level_source,
        )
        return plan

    def _audience_level_to_index(self, value: str | None) -> int:
        if not value:
            return 1
        norm = value.strip().lower()
        fixed_levels = {
            "beginner": 0,
            "basic": 0,
            "base": 0,
            "базовый": 0,
            "начальный": 0,
            "beginner_plus": 1,
            "beginner+": 1,
            "middle": 1,
            "advanced": 2,
            "professional": 2,
        }
        if norm in fixed_levels:
            return fixed_levels[norm]
        for idx, keywords in self.level_keywords.items():
            if any(k in norm for k in keywords):
                return idx
        return 1

    def _context_level_from_history(
        self,
        context_meta: ProjectContextMeta | None,
        context_analysis: ContextAnalysisResult | None,
    ) -> int | None:
        if not context_meta or not context_analysis:
            return None
        if context_analysis.is_first_project:
            return None

        last_order = context_meta.last_order if context_meta.last_order is not None else 0
        similar_count = len(context_analysis.similar_projects or [])

        if last_order >= 7 or similar_count >= 6:
            return 2
        if last_order >= 3 or similar_count >= 3:
            return 1
        return 0

    def _clamp_tasks(self, value: int) -> int:
        return max(self.min_tasks, min(self.max_tasks, value))

    def _adjust_level_by_curriculum(self, level_idx: int, delta: int) -> int:
        if not delta:
            return level_idx
        return max(0, min(2, level_idx + delta))

    def _build_rationale(
        self,
        final_idx: int,
        audience_idx: int,
        context_idx: int | None,
        context_meta: ProjectContextMeta | None,
        curriculum: CurriculumInsights,
    ) -> str:
        order = context_meta.last_order if context_meta else 0
        context_level = context_idx if context_idx is not None else "n/a"
        progress = ""
        if curriculum.graph_available:
            progress = f"; прогресс по графу={curriculum.progress_ratio}"
        return self.rationale_template.format(
            order=order or 0,
            context_level=context_level,
            audience_level=audience_idx,
            final_level=final_idx,
        ) + progress

    def _build_explanation(
        self,
        level_idx: int,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta | None,
        context_analysis: ContextAnalysisResult | None,
        tasks: int,
        complexity: str,
        curriculum: CurriculumInsights,
    ) -> str:
        level_names = {0: "базовом", 1: "среднем", 2: "продвинутом"}
        audience_label = level_names.get(self._audience_level_to_index(seed.audience_level), "целевом")
        parts = [f"Запланировано {tasks} {self._plural(tasks, 'задача', 'задачи', 'задач')} уровня {complexity}."]

        if context_meta and context_analysis and not context_analysis.is_first_project:
            order = context_meta.last_order or 0
            skills_alignment = context_analysis.skills_alignment or {}
            lo_alignment = context_analysis.learning_outcomes_alignment or {}
            skills_prev = ", ".join(skills_alignment.get("intersection", [])[:3]) or "базовые навыки"
            skills_new = ", ".join(skills_alignment.get("new", [])[:2])
            parts.append(f"В ветке уже {order} проектов: участники уверенно работают с {skills_prev}.")
            if skills_new:
                parts.append(f"Добавляем развитие навыков: {skills_new}.")
            lo_new = ", ".join(lo_alignment.get("new", [])[:2])
            if lo_new:
                parts.append(f"Новые результаты обучения: {lo_new}.")
        else:
            parts.append("Это первый проект в треке, поэтому задания ориентированы на входной уровень.")

        parts.append(f"Уровень аудитории заявлен как {audience_label}.")

        if curriculum.graph_available:
            prev_titles = ", ".join(node["title"] for node in curriculum.previous_nodes) or "нет данных"
            next_titles = ", ".join(node["title"] for node in curriculum.next_nodes) or "нет рекомендаций"
            skills_to_prepare = ", ".join(curriculum.skills_to_prepare) or "актуальные навыки трека"
            parts.append(
                f"Curriculum: учитываем предыдущие проекты ({prev_titles}) и готовим к следующим ({next_titles})."
            )
            parts.append(f"Практика укрепляет навыки: {skills_to_prepare}.")

        return " ".join(parts)

    @staticmethod
    def _plural(value: int, form1: str, form2: str, form5: str) -> str:
        n = abs(value) % 100
        n1 = n % 10
        if 10 < n < 20:
            return form5
        if 1 < n1 < 5:
            return form2
        if n1 == 1:
            return form1
        return form5
