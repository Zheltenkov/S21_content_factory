"""Finalize and enforce list-level contracts for practice tasks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ..artifact_chain import ArtifactChainPlan
from ..models.schemas import PracticeTask, ProjectSeed
from ..practice_contract import normalize_task_input_for_learning_activity
from .practice_contracts import (
    RX_ARTIFACT_PATH,
    describe_expected_artifact,
    ensure_p2p_criteria,
    extract_sjm_task_anchors,
    fix_result_artifact,
    is_generic_expected_artifact,
    normalize_sentence,
)


StyleRewrite = Callable[[str, str], str]
ArtifactLocationBuilder = Callable[..., str]


class ArtifactChainPlannerProtocol(Protocol):
    """Minimal planner contract used by the practice finalization layer."""

    def plan(self, seed: ProjectSeed, task_count: int, **kwargs: object) -> ArtifactChainPlan:
        """Build an artifact chain plan for tasks."""

    def apply(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        plan: ArtifactChainPlan,
    ) -> tuple[list[PracticeTask], ArtifactChainPlan]:
        """Apply artifact chain locations/dependencies to tasks."""


class PracticeTaskFinalizer:
    """Apply deterministic list-level contracts to generated practice tasks."""

    def __init__(
        self,
        *,
        style_rewrite: StyleRewrite,
        artifact_location_for_task: ArtifactLocationBuilder,
        artifact_chain_planner: ArtifactChainPlannerProtocol,
    ) -> None:
        self.style_rewrite = style_rewrite
        self.artifact_location_for_task = artifact_location_for_task
        self.artifact_chain_planner = artifact_chain_planner

    def finalize_generated_tasks(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        language: str,
        artifact_chain_plan: ArtifactChainPlan,
        *,
        sjm_override: str | None = None,
    ) -> tuple[list[PracticeTask], ArtifactChainPlan]:
        """Apply production contracts to the main generated practice task list."""
        tasks = self.ensure_task_artifact_contract(tasks, seed, language, artifact_chain_plan)
        tasks, artifact_chain_plan = self.artifact_chain_planner.apply(tasks, seed, artifact_chain_plan)
        tasks = self.enforce_learning_activity_contract(tasks, language)
        tasks = self.ensure_task_artifact_contract(tasks, seed, language, artifact_chain_plan)
        tasks, artifact_chain_plan = self.artifact_chain_planner.apply(tasks, seed, artifact_chain_plan)
        tasks = self.ensure_sjm_task_anchors(tasks, seed, language, sjm_override=sjm_override)
        return tasks, artifact_chain_plan

    def finalize_bonus_tasks(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        language: str,
        *,
        sjm_override: str | None = None,
    ) -> list[PracticeTask]:
        """Apply the same task-level contracts to optional bonus tasks."""
        if not tasks:
            return []

        bonus_plan = self.artifact_chain_planner.plan(seed, len(tasks))
        for idx, step in enumerate(bonus_plan.steps, 1):
            step.artifact_location = self.artifact_location_for_task(seed, idx - 1, bonus=True)

        for idx, task in enumerate(tasks, 1):
            canonical_location = self.artifact_location_for_task(seed, idx - 1, bonus=True)
            task.artifact_location = canonical_location
            if task.expected_artifact:
                task.expected_artifact = RX_ARTIFACT_PATH.sub(canonical_location, task.expected_artifact)

        tasks, bonus_plan = self.artifact_chain_planner.apply(tasks, seed, bonus_plan)
        tasks = self.enforce_learning_activity_contract(tasks, language)
        tasks = self.ensure_task_artifact_contract(tasks, seed, language, bonus_plan)
        tasks, bonus_plan = self.artifact_chain_planner.apply(tasks, seed, bonus_plan)
        tasks = self.ensure_sjm_task_anchors(tasks, seed, language, sjm_override=sjm_override)
        tasks = self.ensure_task_artifact_contract(tasks, seed, language, bonus_plan)
        return tasks

    def ensure_task_artifact_contract(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        language: str,
        artifact_chain_plan: ArtifactChainPlan | None = None,
    ) -> list[PracticeTask]:
        """Guarantee every task has a concrete deliverable, location and review checklist."""
        planned_locations: dict[int, str] = {}
        if artifact_chain_plan is not None:
            for step in artifact_chain_plan.steps:
                planned_locations[step.task_index] = step.artifact_location

        for idx, task in enumerate(tasks, 1):
            raw_result = (task.expected_artifact or "").strip()
            planned_location = planned_locations.get(idx, "")
            explicit_paths = [match.group(1) for match in RX_ARTIFACT_PATH.finditer(raw_result)]

            if not (task.artifact_location or "").strip() and planned_location:
                task.artifact_location = planned_location

            fixed_result, fixed_location = fix_result_artifact(
                raw_result,
                seed,
                idx - 1,
                self.artifact_location_for_task,
            )
            if planned_location and not explicit_paths:
                fixed_location = planned_location

            if fixed_location:
                task.artifact_location = fixed_location

            artifact_location = (task.artifact_location or "").strip()
            if artifact_location and artifact_location.lower() not in fixed_result.lower():
                fixed_result = f"{fixed_result.rstrip('.')} Артефакт размещён по пути `{artifact_location}`."

            if not fixed_result.strip():
                fallback_location = artifact_location or planned_location or self.artifact_location_for_task(seed, idx - 1)
                task.artifact_location = fallback_location
                fixed_result = describe_expected_artifact(task, fallback_location, language)

            if is_generic_expected_artifact(fixed_result):
                fallback_location = artifact_location or planned_location or self.artifact_location_for_task(seed, idx - 1)
                task.artifact_location = fallback_location
                fixed_result = describe_expected_artifact(task, fallback_location, language)

            task.expected_artifact = self.style_rewrite(fixed_result.strip(), language)
            if not task.expected_artifact.strip():
                fallback_location = artifact_location or planned_location or self.artifact_location_for_task(seed, idx - 1)
                task.artifact_location = fallback_location
                task.expected_artifact = describe_expected_artifact(task, fallback_location, language)

            task.p2p_criteria = ensure_p2p_criteria(
                list(task.p2p_criteria or []),
                task.artifact_location,
                task.expected_artifact,
                task.theory_support,
                language,
                style_rewrite=self.style_rewrite,
            )

        return tasks

    def enforce_learning_activity_contract(self, tasks: list[PracticeTask], language: str) -> list[PracticeTask]:
        """Keep task inputs as raw materials and chain each task to the previous output."""
        for idx, task in enumerate(tasks, 1):
            previous = tasks[idx - 2] if idx > 1 else None
            normalized_input = normalize_task_input_for_learning_activity(
                task.input_data,
                task_index=idx,
                previous_artifact_location=previous.artifact_location if previous else None,
            )
            if normalized_input != task.input_data:
                task.input_data = self.style_rewrite(normalized_input, language)
        return tasks

    def ensure_sjm_task_anchors(
        self,
        tasks: list[PracticeTask],
        seed: ProjectSeed,
        language: str,
        sjm_override: str | None = None,
    ) -> list[PracticeTask]:
        """Deterministically keep SJM role/constraint anchors visible in task wording."""
        anchors = extract_sjm_task_anchors(sjm_override or seed.sjm or "")
        if not anchors or not tasks:
            return tasks

        required_count = min(2, len(tasks))
        if "заказчик" in anchors:
            target_anchor = "заказчик"
            anchor_sentence = "Заказчик остаётся главным адресатом результата: решение должно помогать согласовать следующий шаг."
        else:
            target_anchor = anchors[0]
            anchor_sentence = f"Сохрани якорь кейса: {', '.join(anchors[:2])}."

        for idx, task in enumerate(tasks):
            task_text = " ".join(
                [
                    task.situation or "",
                    task.constraints_or_risk or "",
                    task.goal or "",
                    task.expected_artifact or "",
                ]
            ).lower()
            if idx >= required_count or target_anchor in task_text:
                continue

            sentence = self.style_rewrite(anchor_sentence, language)
            task.situation = normalize_sentence(f"{task.situation or ''} {sentence}".strip())

            if target_anchor not in (task.expected_artifact or "").lower():
                recipient = "заказчика" if target_anchor == "заказчик" else target_anchor
                task.expected_artifact = normalize_sentence(
                    f"{task.expected_artifact or ''} Артефакт показывает решение для {recipient}.".strip()
                )

        return tasks
