"""
content_gen/agents/practice.py

Агент генерации практических задач.

Генерирует Главу 3 (Практика) с задачами, содержащими:
- Цель задачи
- Подход (шаги выполнения)
- Входные данные
- Локация результата
"""

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..artifact_chain import ArtifactChainPlan, GenericArtifactChainPlanner, is_generic_repo_path_template
from ..config.loader import get_agent_config
from ..config.thresholds import CODE_EXAMPLE_CONFIG
from ..didactics.composer import compose_didactics_context
from ..models.schemas import PracticeTask, ProjectSeed
from .base.agent import BaseAgent
from .base.llm_client import LLMClientProtocol
from .practice_bonus_service import BonusPracticeService
from .practice_finalizer import PracticeTaskFinalizer
from .practice_generation_service import PracticeGenerationService
from .practice_contracts import (
    artifact_kind_from_task,
    artifact_review_subject,
    describe_expected_artifact,
    ensure_p2p_criteria,
    extract_sjm_task_anchors,
    fix_result_artifact,
    is_generic_expected_artifact,
    is_observable_p2p_criterion,
    normalize_approach_bullets,
    normalize_sentence,
)
from .practice_parsing import (
    parse_approach_bullets,
    parse_p2p_criteria,
)
from .practice_prompting import (
    build_practice_content_type_section,
    build_practice_curriculum_context_section,
    build_practice_formulas_requirements,
    build_practice_sjm_section,
    determine_practice_content_type,
)
from .practice_repair import (
    extract_theory_topics,
    fix_goal_active_form,
    fix_task_risk,
    fix_task_situation,
    force_active_goal,
    infer_covered_outcomes,
    infer_theory_support,
    is_programming_topic,
    summarize_approach_to_limit,
    token_set,
)
from ..repair.style_guard import StyleGuardRepair

if TYPE_CHECKING:
    from .code_example import CodeExampleAgent

SYSTEM = ""

USER_TMPL = ""


BONUS_USER_TMPL = ""


@dataclass
class PracticeResult:
    """Результат генерации практических задач."""

    tasks: list[PracticeTask]
    bonus_tasks: list[PracticeTask] = field(default_factory=list)


class PracticeAgent(BaseAgent):
    """Генерирует практические задачи проекта."""

    CONFIG_NAME = "practice"

    def __init__(self, llm: LLMClientProtocol):
        super().__init__(llm)
        self.logger = logging.getLogger("content_gen.agents.practice")
        self.style = StyleGuardRepair()
        self.code_agent: CodeExampleAgent | None = None
        if CODE_EXAMPLE_CONFIG["enable_code_tasks_in_practice"]:
            try:
                from .code_example import CodeExampleAgent
                self.code_agent = CodeExampleAgent(llm)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("⚠️ CodeExampleAgent недоступен, продолжаю без code-подзадач: %s", exc)
        self.rx_task = re.compile(r"^###\s+Задани(?:е|я)\s+(\d+)\.\s*(.+?)\s*$", re.M)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        try:
            self.didactics_context, self.didactics_trace = compose_didactics_context(self.CONFIG_NAME)
        except Exception:
            self.didactics_context, self.didactics_trace = "", {}
        self.artifact_chain_planner = GenericArtifactChainPlanner()
        self.task_finalizer = PracticeTaskFinalizer(
            style_rewrite=self.style.rewrite,
            artifact_location_for_task=self._artifact_location_for_task,
            artifact_chain_planner=self.artifact_chain_planner,
        )
        self.bonus_service = BonusPracticeService(
            llm=self.llm,
            config=self.config,
            llm_kwargs=self.llm_kwargs,
            didactics_context=self.didactics_context,
            style_rewrite=self.style.rewrite,
            artifact_location_for_task=self._artifact_location_for_task,
            finalizer=self.task_finalizer,
        )
        self.generation_service = PracticeGenerationService(
            llm=self.llm,
            config=self.config,
            llm_kwargs=self.llm_kwargs,
            didactics_context=self.didactics_context,
            style_rewrite=self.style.rewrite,
            artifact_location_for_task=self._artifact_location_for_task,
            artifact_chain_planner=self.artifact_chain_planner,
            finalizer=self.task_finalizer,
            code_agent=self.code_agent,
            logger=self.logger,
        )
        self.last_artifact_chain_plan: ArtifactChainPlan | None = None

    @staticmethod
    def _safe_artifact_root(seed: ProjectSeed) -> str:
        """Build a stable project folder name for generated artifact locations."""
        raw = (seed.platform_name or seed.title_seed or "project").strip()
        root = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
        return root[:80] or "project"

    def _artifact_location_for_task(
        self,
        seed: ProjectSeed,
        task_idx: int,
        *,
        bonus: bool = False,
    ) -> str:
        """Return a canonical artifact location without assuming a Git repository."""
        task_num = task_idx + 1
        if not bonus and seed.repo_path_template and not is_generic_repo_path_template(seed.repo_path_template):
            try:
                return seed.repo_path_template.format(num=task_num).replace("\\", "/")
            except (KeyError, ValueError):
                pass
        root = self._safe_artifact_root(seed)
        folder = f"bonus-{task_num:02d}" if bonus else f"task-{task_num:02d}"
        return f"{root}/part-03/{folder}/README.md"

    def _is_programming_topic(self, seed: ProjectSeed) -> bool:
        """Проверяет, касается ли тема проекта программирования или разработки."""
        return is_programming_topic(seed)

    def _parse_p2p_criteria(self, block: str) -> list[str]:
        """Parse P2P criteria from a generated task block."""
        return parse_p2p_criteria(block)

    def _parse_approach_bullets(self, approach: str) -> list[str]:
        """Parse markdown approach bullets while preserving multiline blocks."""
        return parse_approach_bullets(approach)

    def _summarize_to_150(self, bullets: list[str], language: str) -> list[str]:
        """Суммаризует подход до 150 слов."""
        return summarize_approach_to_limit(bullets, language)

    def _fix_goal_active_form(self, goal: str, language: str) -> str:
        """Исправляет цель, если она не в активной форме."""
        return fix_goal_active_form(goal, language)

    @staticmethod
    def _force_active_goal(goal: str) -> str:
        """Convert vague or imperfective Russian goal wording into an explicit action."""
        return force_active_goal(goal)

    def _fix_task_situation(self, situation: str, input_data: str, goal: str, language: str) -> str:
        """Нормализует блок «Ситуация»."""
        return fix_task_situation(
            situation,
            input_data,
            goal,
            language,
            style_rewrite=self.style.rewrite,
        )

    def _fix_task_risk(self, risk_text: str, situation: str, goal: str, language: str) -> str:
        """Нормализует блок «Ограничение / риск»."""
        return fix_task_risk(
            risk_text,
            situation,
            goal,
            language,
            style_rewrite=self.style.rewrite,
        )

    @staticmethod
    def _extract_theory_topics(theory_summary: str) -> list[str]:
        """Извлекает названия тем из краткого конспекта теории."""
        return extract_theory_topics(theory_summary)

    @staticmethod
    def _token_set(text: str) -> set[str]:
        return token_set(text)

    def _infer_covered_outcomes(self, seed: ProjectSeed, *task_texts: str) -> list[str]:
        """Привязывает задачу к 1-2 LO по пересечению смысловых токенов."""
        return infer_covered_outcomes(seed, *task_texts)

    def _infer_theory_support(self, theory_summary: str, *task_texts: str) -> list[str]:
        """Определяет, какие темы из теории поддерживают задачу."""
        return infer_theory_support(theory_summary, *task_texts)

    def _fix_result_artifact(self, result: str, seed: ProjectSeed, task_idx: int) -> tuple[str, str]:
        """Ensure expected result contains a concrete artifact and location."""
        return fix_result_artifact(result, seed, task_idx, self._artifact_location_for_task)

    @staticmethod
    def _is_generic_expected_artifact(result: str) -> bool:
        """Detect deliverable text that only points to a file without saying what is checked."""
        return is_generic_expected_artifact(result)

    @staticmethod
    def _artifact_kind_from_task(task: PracticeTask) -> str:
        """Infer a concrete review subject from the task instead of using a generic placeholder."""
        return artifact_kind_from_task(task)

    def _describe_expected_artifact(
        self,
        task: PracticeTask,
        artifact_location: str,
        language: str,
    ) -> str:
        """Build a concrete expected-result contract for P2P review."""
        return describe_expected_artifact(task, artifact_location, language)

    def _ensure_task_artifact_contract(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        language: str,
        artifact_chain_plan: ArtifactChainPlan | None = None,
    ) -> list[PracticeTask]:
        """Guarantee every task has a concrete deliverable, location and review checklist."""
        return self.task_finalizer.ensure_task_artifact_contract(
            tasks,
            seed,
            language,
            artifact_chain_plan,
        )

    @staticmethod
    def _normalize_sentence(text: str) -> str:
        return normalize_sentence(text)

    @staticmethod
    def _is_observable_p2p_criterion(text: str) -> bool:
        return is_observable_p2p_criterion(text)

    @staticmethod
    def _artifact_review_subject(expected_artifact: str, artifact_location: str) -> tuple[str, str]:
        return artifact_review_subject(expected_artifact, artifact_location)

    def _normalize_approach_bullets(
        self,
        bullets: list[str],
        theory_support: list[str],
        language: str,
    ) -> list[str]:
        """Normalize approach bullets and add a theory anchor when needed."""
        return normalize_approach_bullets(
            bullets,
            theory_support,
            language,
            style_rewrite=self.style.rewrite,
            token_set=self._token_set,
        )

    def _ensure_p2p_criteria(
        self,
        criteria: list[str],
        artifact_location: str,
        expected_artifact: str,
        theory_support: list[str],
        language: str,
    ) -> list[str]:
        """Make P2P criteria binary, observable and bound to the artifact."""
        return ensure_p2p_criteria(
            criteria,
            artifact_location,
            expected_artifact,
            theory_support,
            language,
            style_rewrite=self.style.rewrite,
        )

    @staticmethod
    def _extract_sjm_task_anchors(sjm: str) -> list[str]:
        """Extract compact SJM anchors that must remain visible in practice tasks."""
        return extract_sjm_task_anchors(sjm)

    def _ensure_sjm_task_anchors(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        language: str,
        sjm_override: str | None = None,
    ) -> list[PracticeTask]:
        """Deterministically keep SJM role/constraint anchors visible in task wording."""
        return self.task_finalizer.ensure_sjm_task_anchors(
            tasks,
            seed,
            language,
            sjm_override=sjm_override,
        )

    def _enforce_learning_activity_contract(self, tasks: list[PracticeTask], language: str) -> list[PracticeTask]:
        """Keeps materials as raw inputs and chains each task to the previous output."""
        return self.task_finalizer.enforce_learning_activity_contract(tasks, language)

    def _build_curriculum_context_section(
        self,
        seed: ProjectSeed,
        section_context: dict[str, Any] | None = None,
    ) -> str:
        """Build the curriculum context section for the practice prompt."""
        return build_practice_curriculum_context_section(seed, section_context=section_context)

    def _build_sjm_section(
        self,
        seed: ProjectSeed,
        section_context: dict[str, Any] | None = None,
    ) -> str:
        """Build the SJM/case section for the practice prompt."""
        return build_practice_sjm_section(seed, section_context=section_context)

    def _determine_content_type(self, seed: ProjectSeed) -> str:
        """Return the practice content profile: hard_code, low_code, or no_code."""
        return determine_practice_content_type(seed)

    def _build_content_type_section(self, content_type: str) -> str:
        """Build prompt instructions for the detected practice content profile."""
        return build_practice_content_type_section(content_type)

    def _build_formulas_requirements(self, seed: ProjectSeed, content_type: str) -> str:
        """Build formula/code requirements for the practice prompt."""
        return build_practice_formulas_requirements(seed, content_type)

    def generate(
        self,
        seed: ProjectSeed,
        instruction_text: str = "",
        theory_summary: str = "",
        practice_plan_contract: Any | None = None,
        artifact_chain_plan: ArtifactChainPlan | dict[str, Any] | None = None,
        section_context: dict[str, Any] | None = None,
    ) -> PracticeResult:
        """
        Генерирует практические задачи.

        Args:
            seed: Входные данные проекта
            instruction_text: Текст инструкции из Главы 1 (для избежания дублирования в задачах)
            theory_summary: Краткий конспект Главы 2 (ключевые понятия и темы)

        Returns:
            PracticeResult с задачами
        """
        outcome = self.generation_service.generate(
            seed,
            instruction_text=instruction_text,
            theory_summary=theory_summary,
            practice_plan_contract=practice_plan_contract,
            artifact_chain_plan=artifact_chain_plan,
            section_context=section_context,
        )
        self.last_artifact_chain_plan = outcome.artifact_chain_plan
        return PracticeResult(tasks=outcome.tasks, bonus_tasks=[])

    def generate_bonus(self, seed: ProjectSeed, n_bonus: int = 1) -> list[PracticeTask]:
        """
        Генерирует бонусные задания.

        Args:
            seed: Входные данные проекта
            n_bonus: Количество бонусных заданий (1-2)

        Returns:
            Список бонусных заданий
        """
        return self.bonus_service.generate(seed, n_bonus)

    def finalize_bonus_tasks(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        language: str,
        *,
        sjm_override: str | None = None,
    ) -> list[PracticeTask]:
        """Apply the same task-level contracts to optional bonus tasks."""
        return self.task_finalizer.finalize_bonus_tasks(
            tasks,
            seed,
            language,
            sjm_override=sjm_override,
        )
