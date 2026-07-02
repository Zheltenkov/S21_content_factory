"""Main practice generation service."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..artifact_chain import ArtifactChainPlan
from ..config.loader import prompt_trace_kwargs
from ..config.thresholds import THRESHOLDS
from ..models.schemas import PracticeTask, ProjectSeed
from ..project_planning import render_practice_plan_contract_section
from ..recovery import ModelOutputNormalizer
from .base.llm_client import LLMClientProtocol
from .practice_finalizer import ArtifactChainPlannerProtocol, PracticeTaskFinalizer
from .practice_materializer import CodeTaskAgentProtocol, PracticeTaskMaterializer
from .practice_prompting import (
    build_practice_content_type_section,
    build_practice_curriculum_context_section,
    build_practice_formulas_requirements,
    build_practice_repo_info,
    build_practice_sjm_section,
    build_reference_practice_section,
    determine_practice_content_type,
)


StyleRewrite = Callable[[str, str], str]
ArtifactLocationBuilder = Callable[..., str]


@dataclass
class PracticeGenerationOutcome:
    """Output of the main practice generation service."""

    tasks: list[PracticeTask]
    artifact_chain_plan: ArtifactChainPlan


class PracticeGenerationService:
    """Build prompt, invoke LLM and materialize main practice tasks."""

    RX_TASK = re.compile(r"^###\s+Задани(?:е|я)\s+(\d+)\.\s*(.+?)\s*$", re.M)

    def __init__(
        self,
        *,
        llm: LLMClientProtocol,
        config: Any,
        llm_kwargs: dict[str, Any],
        didactics_context: str,
        style_rewrite: StyleRewrite,
        artifact_location_for_task: ArtifactLocationBuilder,
        artifact_chain_planner: ArtifactChainPlannerProtocol,
        finalizer: PracticeTaskFinalizer,
        code_agent: CodeTaskAgentProtocol | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.llm = llm
        self.config = config
        self.llm_kwargs = llm_kwargs
        self.didactics_context = didactics_context
        self.style_rewrite = style_rewrite
        self.artifact_location_for_task = artifact_location_for_task
        self.artifact_chain_planner = artifact_chain_planner
        self.finalizer = finalizer
        self.code_agent = code_agent
        self.output_normalizer = ModelOutputNormalizer()
        self.logger = logger or logging.getLogger("content_gen.agents.practice_generation_service")

    def generate(
        self,
        seed: ProjectSeed,
        *,
        instruction_text: str = "",
        theory_summary: str = "",
        practice_plan_contract: Any | None = None,
        artifact_chain_plan: ArtifactChainPlan | dict[str, Any] | None = None,
        section_context: dict[str, Any] | None = None,
    ) -> PracticeGenerationOutcome:
        """Generate and finalize the main practice task list."""
        task_count = seed.tasks_count or THRESHOLDS["practice_tasks_recommend"][0]
        theory_summary = theory_summary or "Конспект теории не предоставлен. Ориентируйся на описание проекта и LO."
        artifact_chain_plan = self._coerce_or_build_artifact_plan(
            seed,
            task_count,
            theory_summary,
            artifact_chain_plan,
        )

        system_prompt = self._build_system_prompt(seed)
        user_prompt = self._build_user_prompt(
            seed,
            task_count=task_count,
            instruction_text=instruction_text,
            theory_summary=theory_summary,
            practice_plan_contract=practice_plan_contract,
            artifact_chain_plan=artifact_chain_plan,
            section_context=section_context,
        )

        generation_kwargs = self.llm_kwargs.copy()
        generation_kwargs.setdefault("temperature", 0.2)
        generation_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system",
                "user_template",
                output_schema="PracticeTask[]",
            )
        )
        markdown = self.llm.complete(system=system_prompt, user=user_prompt, **generation_kwargs)
        normalization = self.output_normalizer.normalize_practice_markdown(markdown)
        markdown = normalization.markdown
        if normalization.changed:
            self.logger.warning("Practice model output normalized: %s", "; ".join(normalization.changes))
        tasks = self._materialize_tasks(markdown, seed=seed, theory_summary=theory_summary)
        tasks, artifact_chain_plan = self.finalizer.finalize_generated_tasks(
            tasks,
            seed,
            seed.language,
            artifact_chain_plan,
            sjm_override=(section_context or {}).get("sjm_context"),
        )
        return PracticeGenerationOutcome(tasks=tasks, artifact_chain_plan=artifact_chain_plan)

    def _build_system_prompt(self, seed: ProjectSeed) -> str:
        """Build system prompt with didactics context."""
        system_prompt = self.config.get_prompt("system").format(language=seed.language)
        if self.didactics_context:
            system_prompt = f"{system_prompt}\n\n=== DIDACTICS CONTEXT ===\n{self.didactics_context}"
        return system_prompt

    def _build_user_prompt(
        self,
        seed: ProjectSeed,
        *,
        task_count: int,
        instruction_text: str,
        theory_summary: str,
        practice_plan_contract: Any | None,
        artifact_chain_plan: ArtifactChainPlan,
        section_context: dict[str, Any] | None,
    ) -> str:
        """Build the main practice user prompt."""
        group_size = seed.group_size if seed.project_type == "group" else None
        curriculum_context_section = build_practice_curriculum_context_section(seed, section_context=section_context)
        sjm_section = build_practice_sjm_section(seed, section_context=section_context)
        practice_plan_section = render_practice_plan_contract_section(
            practice_plan_contract or (section_context or {}).get("practice_plan_contract")
        )

        content_type = determine_practice_content_type(seed)
        direction = getattr(seed, "direction", "") or seed.thematic_block or "—"
        self.logger.info("Тип контента для практики: %s (direction=%s)", content_type, direction)
        self._log_curriculum_context(seed)

        filtered_learning_outcomes = (section_context or {}).get("learning_outcomes") or seed.learning_outcomes
        filtered_skills = (section_context or {}).get("skills") or seed.skills
        filtered_required_tools = (section_context or {}).get("required_tools") or seed.required_tools
        filtered_required_software = (section_context or {}).get("required_software") or getattr(seed, "required_software", [])
        filtered_project_description = (section_context or {}).get("project_description") or seed.project_description

        user_prompt = self.config.get_prompt("user_template").format(
            n=task_count,
            i="{i}",
            required_tools=", ".join(filtered_required_tools) if filtered_required_tools else "—",
            required_software=", ".join(filtered_required_software) if filtered_required_software else "—",
            project_description=filtered_project_description,
            learning_outcomes="; ".join(filtered_learning_outcomes),
            skills="; ".join(filtered_skills),
            group_size=group_size or "—",
            repo_info=build_practice_repo_info(seed),
            instruction_text=instruction_text or "—",
            theory_summary=theory_summary,
            curriculum_context_section=curriculum_context_section,
            sjm_section=sjm_section,
            reference_practice_section=build_reference_practice_section(seed),
            content_type_section=build_practice_content_type_section(content_type),
            formulas_code_requirements=build_practice_formulas_requirements(seed, content_type),
            direction=direction,
            platform_name=getattr(seed, "platform_name", None) or "project",
            gitlab_link=getattr(seed, "gitlab_link", None) or "—",
        )
        if practice_plan_section:
            user_prompt = (
                f"{user_prompt}\n\n=== PRACTICE PLAN CONTRACT ===\n"
                "Следуй этому плану задач. Можно менять формулировки, но нельзя ломать causal chain, "
                "LO coverage и правило raw evidence.\n\n"
                f"{practice_plan_section}"
            )

        user_prompt = f"{user_prompt}\n\n=== ARTIFACT CHAIN CONTRACT ===\n{artifact_chain_plan.to_prompt_context()}"
        if seed.reference_project_hint and seed.reference_project_hint.strip():
            user_prompt = (
                f"{user_prompt}\n\n=== ЭТАЛОН ИДЕАЛЬНОГО ПРОЕКТА ===\n"
                "Ниже дан идеальный образ проекта. Используй его как ориентир по плотности, стилю и качеству заданий,"
                " но факты, ограничения и тему бери из текущего проекта и УП.\n\n"
                f"{seed.reference_project_hint.strip()}"
            )
        return user_prompt

    def _materialize_tasks(self, markdown: str, *, seed: ProjectSeed, theory_summary: str) -> list[PracticeTask]:
        """Split generated markdown into task blocks and materialize typed tasks."""
        task_materializer = PracticeTaskMaterializer(
            style_rewrite=self.style_rewrite,
            artifact_location_for_task=self.artifact_location_for_task,
            code_agent=self.code_agent,
            logger=self.logger,
        )
        tasks: list[PracticeTask] = []
        indices = [match.start() for match in self.RX_TASK.finditer(markdown)] + [len(markdown)]
        header_matches = list(self.RX_TASK.finditer(markdown))
        for idx, header in enumerate(header_matches):
            start = header.end()
            end = indices[idx + 1]
            block = markdown[start:end].strip()
            title = header.group(2).strip()
            tasks.append(
                task_materializer.materialize(
                    block=block,
                    title=title,
                    task_index=idx,
                    seed=seed,
                    theory_summary=theory_summary,
                )
            )
        return tasks

    def _coerce_or_build_artifact_plan(
        self,
        seed: ProjectSeed,
        task_count: int,
        theory_summary: str,
        artifact_chain_plan: ArtifactChainPlan | dict[str, Any] | None,
    ) -> ArtifactChainPlan:
        """Return an ArtifactChainPlan from explicit input or planner fallback."""
        if isinstance(artifact_chain_plan, dict):
            return ArtifactChainPlan(**artifact_chain_plan)
        if artifact_chain_plan is not None:
            return artifact_chain_plan
        return self.artifact_chain_planner.plan(seed, task_count, theory_summary=theory_summary)

    def _log_curriculum_context(self, seed: ProjectSeed) -> None:
        """Log compact curriculum context diagnostics."""
        ctx = getattr(seed, "curriculum_context", None)
        if not ctx:
            return
        self.logger.info("=" * 50)
        self.logger.info("PRACTICE AGENT - Контекст УП для промпта:")
        self.logger.info("  Блок: %s", ctx.get("block_name", "N/A"))
        self.logger.info("  Номер проекта: %s", ctx.get("current_project_order", "N/A"))
        self.logger.info("  Проектов ДО: %s, ПОСЛЕ: %s", len(ctx.get("previous_projects", [])), len(ctx.get("next_projects", [])))
        self.logger.info("  SJM: %s", "Да" if ctx.get("sjm_context") else "Нет")
        self.logger.info("=" * 50)
