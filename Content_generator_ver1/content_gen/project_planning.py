"""Schema-first planning contracts built before theory/practice generation."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from .artifact_chain import ArtifactChainPlan, GenericArtifactChainPlanner
from .domain_contracts import NarrativeContract, build_narrative_contract
from .models.schemas import ProjectContextMeta, ProjectSeed


class StoryMapContract(BaseModel):
    """Narrative spine that must stay stable across annotation, theory and practice."""

    student_role: str = ""
    working_case: str = ""
    product_or_project: str = ""
    central_tension: str = ""
    constraints: list[str] = Field(default_factory=list)
    source_materials: list[str] = Field(default_factory=list)
    opening: str = ""
    development: list[str] = Field(default_factory=list)
    completion: str = ""

    def to_prompt_context(self) -> str:
        """Render a compact, prompt-safe story map."""
        lines = [
            "STORY MAP CONTRACT",
            f"- Роль студента: {self.student_role or 'участник проекта'}",
            f"- Рабочий кейс: {self.working_case or 'рабочая ситуация проекта'}",
            f"- Продукт/проект: {self.product_or_project or 'итоговый артефакт проекта'}",
            f"- Центральное напряжение: {self.central_tension or 'нужно принять проверяемое рабочее решение'}",
        ]
        if self.constraints:
            lines.append(f"- Ограничения: {'; '.join(self.constraints[:5])}")
        if self.source_materials:
            lines.append(f"- Источники данных: {'; '.join(self.source_materials[:5])}")
        if self.opening:
            lines.append(f"- Завязка: {self.opening}")
        if self.development:
            lines.append("- Развитие по задачам:")
            lines.extend(f"  {idx}. {item}" for idx, item in enumerate(self.development[:8], 1))
        if self.completion:
            lines.append(f"- Финал: {self.completion}")
        return "\n".join(lines)


class PracticePlanStep(BaseModel):
    """One planned learning activity before task text is generated."""

    task_index: int
    title_hint: str
    learning_outcomes: list[str] = Field(default_factory=list)
    activity_type: str
    input_refs: list[str] = Field(default_factory=list)
    depends_on: str | None = None
    expected_artifact: str
    artifact_location: str
    p2p_focus: list[str] = Field(default_factory=list)
    theory_needs: list[str] = Field(default_factory=list)
    student_must_derive: list[str] = Field(default_factory=list)

    def to_prompt_line(self) -> str:
        """Render the task plan as a deterministic transition."""
        inputs = ", ".join(f"`{ref}`" for ref in self.input_refs) if self.input_refs else "контекст проекта"
        dependency = f"; depends_on=`{self.depends_on}`" if self.depends_on else ""
        outcomes = "; ".join(self.learning_outcomes[:2]) or "LO проекта"
        theory = "; ".join(self.theory_needs[:3]) or "понятия, нужные для действия"
        derive = "; ".join(self.student_must_derive[:3]) or "самостоятельный вывод"
        return (
            f"{self.task_index}. {self.title_hint}: input={inputs}{dependency} -> "
            f"`{self.artifact_location}`; LO={outcomes}; theory_support={theory}; "
            f"student_derives={derive}"
        )


class PracticePlanContract(BaseModel):
    """Project-wide plan: learning outcomes -> activity chain -> artifacts."""

    project_goal: str
    task_count: int
    story_map: StoryMapContract
    artifact_chain_plan: ArtifactChainPlan
    steps: list[PracticePlanStep] = Field(default_factory=list)
    coverage_map: dict[str, list[int]] = Field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """Render the practice contract for downstream agents."""
        lines = [
            "PRACTICE PLAN CONTRACT",
            f"- Цель практической части: {self.project_goal}",
            f"- Количество задач: {self.task_count}",
            "- Теория должна обслуживать эту цепочку действий, а не быть отдельной лекцией.",
            "- Практика должна сохранить causal chain: raw evidence -> task 1 artifact -> next artifact.",
            "",
            self.story_map.to_prompt_context(),
            "",
            "План задач:",
        ]
        lines.extend(step.to_prompt_line() for step in self.steps)
        if self.coverage_map:
            lines.append("")
            lines.append("Покрытие LO:")
            for outcome, task_indexes in self.coverage_map.items():
                indexes = ", ".join(str(index) for index in task_indexes)
                lines.append(f"- {outcome}: задачи {indexes}")
        lines.extend(["", self.artifact_chain_plan.to_prompt_context()])
        return "\n".join(lines)

    def theory_support_topics(self) -> list[str]:
        """Return unique theory needs expected before practice generation."""
        topics: list[str] = []
        seen: set[str] = set()
        for step in self.steps:
            for item in step.theory_needs:
                key = item.lower()
                if key not in seen:
                    topics.append(item)
                    seen.add(key)
        return topics


class ProjectBlueprintPlanner:
    """Deterministic planning layer that runs before Chapter 2 generation."""

    def __init__(self, artifact_chain_planner: GenericArtifactChainPlanner | None = None) -> None:
        self.artifact_chain_planner = artifact_chain_planner or GenericArtifactChainPlanner()

    def build(
        self,
        seed: ProjectSeed,
        task_plan: Any | None,
        context_meta: ProjectContextMeta | None = None,
        context_bundle: Any | None = None,
    ) -> tuple[StoryMapContract, PracticePlanContract, ArtifactChainPlan]:
        """Build story, practice and artifact contracts from structured input only."""
        task_count = self._task_count(seed, task_plan)
        narrative = self._coerce_narrative(seed, context_bundle)
        artifact_chain_plan = self.artifact_chain_planner.plan(seed, task_count)
        story_map = self._build_story_map(seed, narrative, task_count)
        steps = self._build_steps(seed, story_map, artifact_chain_plan)
        coverage_map = self._coverage_map(steps)
        practice_plan = PracticePlanContract(
            project_goal=self._project_goal(seed, context_meta),
            task_count=task_count,
            story_map=story_map,
            artifact_chain_plan=artifact_chain_plan,
            steps=steps,
            coverage_map=coverage_map,
        )
        return story_map, practice_plan, artifact_chain_plan

    @staticmethod
    def _task_count(seed: ProjectSeed, task_plan: Any | None) -> int:
        planned = int(getattr(task_plan, "tasks_count", 0) or 0)
        return planned or int(seed.tasks_count or 3)

    @staticmethod
    def _coerce_narrative(seed: ProjectSeed, context_bundle: Any | None) -> NarrativeContract:
        payload = getattr(context_bundle, "narrative_contract", None) if context_bundle is not None else None
        if isinstance(payload, NarrativeContract):
            return payload
        if isinstance(payload, dict) and payload:
            try:
                return NarrativeContract(**payload)
            except Exception:
                pass
        curriculum_context = seed.curriculum_context if isinstance(seed.curriculum_context, dict) else {}
        if isinstance(curriculum_context.get("narrative_contract"), dict):
            try:
                return NarrativeContract(**curriculum_context["narrative_contract"])
            except Exception:
                pass
        previous_projects = curriculum_context.get("previous_projects", [])
        return build_narrative_contract(seed, curriculum_context, previous_projects)

    def _build_story_map(
        self,
        seed: ProjectSeed,
        narrative: NarrativeContract,
        task_count: int,
    ) -> StoryMapContract:
        tension = self._central_tension(seed, narrative)
        opening = (
            f"{narrative.student_role or 'Студент'} получает рабочий кейс и сырые данные, "
            "которые нужно превратить в проверяемое решение."
        )
        development = [
            self._story_event(index, task_count, seed)
            for index in range(1, task_count + 1)
        ]
        completion = (
            f"Итоговый артефакт по проекту «{narrative.product_or_project or seed.title_seed or 'project'}» "
            "можно проверить по P2P-критериям без устных пояснений автора."
        )
        return StoryMapContract(
            student_role=narrative.student_role,
            working_case=narrative.working_case,
            product_or_project=narrative.product_or_project,
            central_tension=tension,
            constraints=narrative.constraints,
            source_materials=narrative.data_sources,
            opening=opening,
            development=development,
            completion=completion,
        )

    def _build_steps(
        self,
        seed: ProjectSeed,
        story_map: StoryMapContract,
        artifact_chain_plan: ArtifactChainPlan,
    ) -> list[PracticePlanStep]:
        outcomes = self._learning_outcomes(seed)
        steps: list[PracticePlanStep] = []
        for chain_step in artifact_chain_plan.steps:
            index = chain_step.task_index
            outcome = outcomes[(index - 1) % len(outcomes)]
            activity_type = self._activity_type(index, len(artifact_chain_plan.steps))
            steps.append(
                PracticePlanStep(
                    task_index=index,
                    title_hint=self._title_hint(index, outcome, activity_type),
                    learning_outcomes=[outcome],
                    activity_type=activity_type,
                    input_refs=chain_step.input_refs,
                    depends_on=chain_step.depends_on,
                    expected_artifact=chain_step.artifact_kind,
                    artifact_location=chain_step.artifact_location,
                    p2p_focus=self._p2p_focus(chain_step.artifact_location),
                    theory_needs=self._theory_needs(outcome, seed, story_map),
                    student_must_derive=self._student_must_derive(activity_type, outcome),
                )
            )
        return steps

    @staticmethod
    def _learning_outcomes(seed: ProjectSeed) -> list[str]:
        outcomes = [item.strip() for item in seed.learning_outcomes if item and item.strip()]
        if outcomes:
            return outcomes
        return [seed.project_description.strip() or "Подготовить проверяемый результат проекта"]

    @staticmethod
    def _project_goal(seed: ProjectSeed, context_meta: ProjectContextMeta | None) -> str:
        outcome = "; ".join(seed.learning_outcomes[:2]) if seed.learning_outcomes else ""
        block = context_meta.thematic_block if context_meta else seed.thematic_block
        if outcome:
            return f"Отработать результаты обучения по проекту: {outcome}."
        return f"Собрать проверяемый практический результат по теме «{block or seed.title_seed or 'project'}»."

    @staticmethod
    def _central_tension(seed: ProjectSeed, narrative: NarrativeContract) -> str:
        constraints = "; ".join(narrative.constraints[:2])
        if constraints:
            return f"Нужно получить рабочий результат с учетом ограничений: {constraints}."
        if seed.workload_days:
            return f"Нужно уложить решение в трудоемкость {seed.workload_days} дня и сохранить проверяемость результата."
        return "Нужно перейти от неоднородных исходных данных к проверяемому рабочему артефакту."

    @staticmethod
    def _story_event(index: int, total: int, seed: ProjectSeed) -> str:
        if index == 1:
            return "Студент разбирает raw evidence и фиксирует первичную структуру проблемы."
        if index == total:
            return "Студент собирает итоговый артефакт и проверяет, что он выдерживает критерии ревью."
        skills = ", ".join(seed.skills[:2]) if seed.skills else "ключевые навыки проекта"
        return f"Студент развивает промежуточное решение, применяя {skills}."

    @staticmethod
    def _activity_type(index: int, total: int) -> str:
        if index == 1:
            return "raw_evidence_analysis"
        if index == total:
            return "final_solution_assembly"
        return "intermediate_artifact_refinement"

    @staticmethod
    def _title_hint(index: int, outcome: str, activity_type: str) -> str:
        compact_outcome = re.sub(r"\s+", " ", outcome).strip(" .")
        compact_outcome = " ".join(compact_outcome.split()[:8])
        if activity_type == "raw_evidence_analysis":
            return f"Выделить исходные факты для результата {index}"
        if activity_type == "final_solution_assembly":
            return f"Собрать итоговое решение: {compact_outcome}"
        return f"Развить промежуточный артефакт: {compact_outcome}"

    @staticmethod
    def _p2p_focus(artifact_location: str) -> list[str]:
        return [
            f"артефакт размещен по пути `{artifact_location}`",
            "результат содержит наблюдаемые разделы/поля",
            "выводы можно проверить без устных пояснений автора",
        ]

    @staticmethod
    def _theory_needs(outcome: str, seed: ProjectSeed, story_map: StoryMapContract) -> list[str]:
        needs = [f"понятия и критерии для результата обучения: {outcome}"]
        if seed.required_tools:
            needs.append(f"уместное применение инструментов: {', '.join(seed.required_tools[:3])}")
        if story_map.constraints:
            needs.append(f"учет ограничений кейса: {'; '.join(story_map.constraints[:2])}")
        elif seed.skills:
            needs.append(f"практическая опора на навыки: {', '.join(seed.skills[:3])}")
        return needs[:3]

    @staticmethod
    def _student_must_derive(activity_type: str, outcome: str) -> list[str]:
        if activity_type == "raw_evidence_analysis":
            return [
                "ключевые факты и ограничения из сырых материалов",
                "первичную структуру будущего решения",
            ]
        if activity_type == "final_solution_assembly":
            return [
                "итоговый вывод и обоснование решения",
                f"связь итогового артефакта с LO: {outcome}",
            ]
        return [
            "промежуточные выводы на основе предыдущего артефакта",
            "критерии выбора или улучшения следующего шага",
        ]

    @staticmethod
    def _coverage_map(steps: list[PracticePlanStep]) -> dict[str, list[int]]:
        coverage: dict[str, list[int]] = {}
        for step in steps:
            for outcome in step.learning_outcomes:
                coverage.setdefault(outcome, []).append(step.task_index)
        return coverage


def render_story_map_contract_section(contract: StoryMapContract | dict[str, Any] | None) -> str:
    """Render StoryMapContract from typed or serialized form."""
    if not contract:
        return ""
    if isinstance(contract, StoryMapContract):
        return contract.to_prompt_context()
    if isinstance(contract, dict):
        return StoryMapContract(**contract).to_prompt_context()
    return ""


def render_practice_plan_contract_section(contract: PracticePlanContract | dict[str, Any] | None) -> str:
    """Render PracticePlanContract from typed or serialized form."""
    if not contract:
        return ""
    if isinstance(contract, PracticePlanContract):
        return contract.to_prompt_context()
    if isinstance(contract, dict):
        return PracticePlanContract(**contract).to_prompt_context()
    return ""
