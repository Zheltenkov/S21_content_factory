"""
content_gen/agents/theory.py

Агент генерации теоретического раздела.

Генерирует Главу 2 (Теория) с частями, примерами и вопросами к практике.
Интегрируется с TheoryEnhancementManager для добавления формул, таблиц и диаграмм.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..config.loader import get_agent_config
from ..didactics.composer import compose_didactics_context
from ..models.schemas import ProjectContextMeta, ProjectSeed, TheoryPart
from .base.agent import BaseAgent
from .base.llm_client import LLMClientProtocol
from ..repair.style_guard import StyleGuardRepair
from .theory_generation_service import TheoryGenerationService
from .theory_generation import (
    pick_theory_parts_count,
    semantic_cover,
    theory_anchor_terms,
    validate_bridge_questions,
)
from .theory_materializer import TheoryPartMaterializer
from .theory_sanitizer import (
    _normalize_definition_bold,
    _sanitize_theory_body_text,
    _sanitize_theory_example_text,
)
from .theory_prompting import (
    build_theory_content_type_section,
    build_theory_curriculum_context_section,
    build_theory_formulas_requirements,
    build_theory_questions_prompt,
    build_theory_sjm_section,
    determine_theory_content_type,
)

SYSTEM = ""

USER_TMPL = ""


@dataclass
class TheoryResult:
    """Результат генерации теории."""

    parts: list[TheoryPart]


class TheoryAgent(BaseAgent):
    """Генерирует теоретический раздел проекта."""

    CONFIG_NAME = "theory"

    def __init__(self, llm: LLMClientProtocol):
        super().__init__(llm)
        self.logger = logging.getLogger("content_gen.agents.theory")
        self.style = StyleGuardRepair()
        self.rx_part = re.compile(r"^###\s+2\.(\d+)\.\s*(.+?)\s*$", re.M)
        self.rx_example = re.compile(r"\*\*Пример:\*\*\s*(.+)")
        self.rx_qs = re.compile(r"\*\*Вопросы к практике:\*\*([\s\S]+?)(?=\n###|\Z)", re.M)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        try:
            self.didactics_context, self.didactics_trace = compose_didactics_context(self.CONFIG_NAME)
        except Exception:
            self.didactics_context, self.didactics_trace = "", {}
        self.part_materializer = TheoryPartMaterializer(style_rewrite=self.style.rewrite)
        self.generation_service = TheoryGenerationService(
            llm=self.llm,
            config=self.config,
            llm_kwargs=self.llm_kwargs,
            didactics_context=self.didactics_context,
            style_rewrite=self.style.rewrite,
            materializer=self.part_materializer,
            logger=self.logger,
        )

    def _pick_n_parts(self, desired: int) -> int:
        """Выбирает количество частей в допустимом диапазоне."""
        return pick_theory_parts_count(desired)

    def _semantic_cover(self, body: str, los: list[str]) -> list[str]:
        """Определяет покрытие LO по смыслу."""
        return semantic_cover(body, los)

    def _anchor_terms(self, seed: ProjectSeed) -> list[str]:
        """Собирает якорные термины из описания проекта и входных данных."""
        return theory_anchor_terms(seed)

    def polish_part(self, part: TheoryPart, seed: ProjectSeed) -> TheoryPart:
        """Локально выравнивает теорию под didactics после любой генерации/редактуры."""
        return self.part_materializer.polish_part(part, seed)

    def _validate_questions(self, questions: list[str], seed: ProjectSeed) -> bool:
        """Проверяет качество вопросов: конкретность, связь с проектом, сложность."""
        return validate_bridge_questions(questions, seed, self._anchor_terms(seed))

    def _build_questions_prompt(self, part_data: dict, seed: ProjectSeed) -> str:
        """Build the fallback prompt for missing practice bridge questions."""
        return build_theory_questions_prompt(part_data, seed)

    def _build_curriculum_context_section(
        self,
        seed: ProjectSeed,
        section_context: dict[str, Any] | None = None,
    ) -> str:
        """Build the curriculum context section for the theory prompt."""
        return build_theory_curriculum_context_section(seed, section_context=section_context)

    def _build_sjm_section(
        self,
        seed: ProjectSeed,
        section_context: dict[str, Any] | None = None,
    ) -> str:
        """Build the SJM/case section for the theory prompt."""
        return build_theory_sjm_section(seed, section_context=section_context)

    def _determine_content_type(self, seed: ProjectSeed) -> str:
        """Return the theory content profile: hard_code, low_code, or no_code."""
        return determine_theory_content_type(seed)

    def _build_content_type_section(self, content_type: str) -> str:
        """Build prompt instructions for the detected theory content profile."""
        return build_theory_content_type_section(content_type)

    def _build_formulas_requirements(self, seed: ProjectSeed, content_type: str) -> str:
        """Build formula/code requirements for the theory prompt."""
        return build_theory_formulas_requirements(seed, content_type)

    def generate(
        self,
        seed: ProjectSeed,
        context_meta: ProjectContextMeta,
        desired_parts: int = 3,
        practice_plan_contract: Any | None = None,
        section_context: dict[str, Any] | None = None,
    ) -> TheoryResult:
        """
        Генерирует теоретический раздел.

        Args:
            seed: Входные данные проекта
            context_meta: Метаданные curriculum context
            desired_parts: Желаемое количество частей

        Returns:
            TheoryResult с частями теории
        """
        outcome = self.generation_service.generate(
            seed,
            context_meta,
            desired_parts,
            practice_plan_contract=practice_plan_contract,
            section_context=section_context,
        )
        return TheoryResult(parts=outcome.parts)
