"""Materialize public practice markdown blocks into typed PracticeTask objects."""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import Any, Protocol

from ..config.thresholds import CODE_EXAMPLE_CONFIG
from ..models.schemas import PracticeTask, ProjectSeed
from .practice_contracts import ensure_p2p_criteria, fix_result_artifact, normalize_approach_bullets
from .practice_parsing import extract_public_practice_fields, parse_approach_bullets, parse_p2p_criteria
from .practice_repair import (
    fix_goal_active_form,
    fix_task_risk,
    fix_task_situation,
    infer_covered_outcomes,
    infer_theory_support,
    is_programming_topic,
    summarize_approach_to_limit,
    token_set,
)


ArtifactLocationBuilder = Callable[[ProjectSeed, int], str]
StyleRewrite = Callable[[str, str], str]


class CodeTaskAgentProtocol(Protocol):
    """Minimal contract for optional code subtask generation."""

    def generate(
        self,
        *,
        topic: str,
        skills: list[str],
        seed: ProjectSeed,
        context: str,
    ) -> Any:
        """Generate optional code tasks for one practice task."""


class PracticeTaskMaterializer:
    """Build one typed PracticeTask from one generated markdown task block."""

    def __init__(
        self,
        *,
        style_rewrite: StyleRewrite,
        artifact_location_for_task: ArtifactLocationBuilder,
        code_agent: CodeTaskAgentProtocol | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.style_rewrite = style_rewrite
        self.artifact_location_for_task = artifact_location_for_task
        self.code_agent = code_agent
        self.logger = logger or logging.getLogger("content_gen.agents.practice_materializer")

    def materialize(
        self,
        *,
        block: str,
        title: str,
        task_index: int,
        seed: ProjectSeed,
        theory_summary: str,
    ) -> PracticeTask:
        """Parse, repair, enrich and return one PracticeTask."""
        fields = extract_public_practice_fields(block)
        situation = fields["situation"]
        constraints_or_risk = fields["constraints_or_risk"]
        input_data = fields["input_data"]
        goal = fields["goal"]
        approach = fields["approach"]
        result = fields["result"]
        p2p_criteria = parse_p2p_criteria(block)

        goal = fix_goal_active_form(goal, seed.language)
        situation = fix_task_situation(
            situation,
            input_data,
            goal,
            seed.language,
            style_rewrite=self.style_rewrite,
        )
        constraints_or_risk = fix_task_risk(
            constraints_or_risk,
            situation,
            goal,
            seed.language,
            style_rewrite=self.style_rewrite,
        )
        result, artifact_location = fix_result_artifact(
            result,
            seed,
            task_index,
            self.artifact_location_for_task,
        )

        bullets = self._build_approach_bullets(approach, seed.language)
        roles = self._build_group_roles(seed)
        enhanced_approach = self._append_code_tasks(
            bullets,
            title=title,
            seed=seed,
            goal=goal,
            input_data=input_data,
        )

        theory_support = infer_theory_support(
            theory_summary,
            title,
            situation,
            constraints_or_risk,
            goal,
            input_data,
            " ".join(enhanced_approach),
        )
        enhanced_approach = normalize_approach_bullets(
            enhanced_approach,
            theory_support,
            seed.language,
            style_rewrite=self.style_rewrite,
            token_set=token_set,
        )
        covered_outcomes = infer_covered_outcomes(
            seed,
            title,
            situation,
            constraints_or_risk,
            goal,
            input_data,
            " ".join(enhanced_approach),
        )
        theory_support = infer_theory_support(
            theory_summary,
            title,
            situation,
            constraints_or_risk,
            goal,
            input_data,
            " ".join(enhanced_approach),
        )
        p2p_criteria = ensure_p2p_criteria(
            p2p_criteria,
            artifact_location,
            result,
            theory_support,
            seed.language,
            style_rewrite=self.style_rewrite,
        )

        return PracticeTask(
            title=title,
            situation=situation,
            constraints_or_risk=constraints_or_risk,
            input_data=self.style_rewrite(input_data, seed.language),
            goal=self.style_rewrite(goal, seed.language),
            approach_bullets=enhanced_approach,
            expected_artifact=self.style_rewrite(result, seed.language),
            artifact_location=artifact_location,
            p2p_criteria=p2p_criteria,
            covered_outcomes=covered_outcomes,
            theory_support=theory_support,
            group_roles=roles,
        )

    def _build_approach_bullets(self, approach: str, language: str) -> list[str]:
        """Parse approach markdown and apply style normalization to non-code bullets."""
        styled_bullets: list[str] = []
        for bullet in parse_approach_bullets(approach):
            if not bullet:
                continue
            if "```" in bullet:
                styled_bullets.append(bullet.strip())
            else:
                styled_bullets.append(self.style_rewrite(bullet.strip(), language))
        return summarize_approach_to_limit(styled_bullets, language)

    @staticmethod
    def _build_group_roles(seed: ProjectSeed) -> list[str] | None:
        """Build deterministic group roles from project type and group size."""
        if seed.project_type != "group" or not seed.group_size:
            return None
        if seed.group_size == 2:
            return ["лид", "исполнитель"]
        if seed.group_size == 3:
            return ["лид", "исполнитель", "рецензент"]

        roles = ["лид"] + [f"исполнитель {idx + 1}" for idx in range(seed.group_size - 2)] + ["рецензент"]
        if len(roles) > seed.group_size:
            roles = roles[: seed.group_size]
        return roles

    def _append_code_tasks(
        self,
        bullets: list[str],
        *,
        title: str,
        seed: ProjectSeed,
        goal: str,
        input_data: str,
    ) -> list[str]:
        """Generate and append optional code subtasks when the project contract allows it."""
        enhanced_approach = bullets.copy()
        code_tasks = self._generate_code_tasks(title=title, seed=seed, goal=goal, input_data=input_data)
        if not code_tasks:
            return enhanced_approach

        code_tasks_text = "\n".join(
            [
                f"- {task.title} ({task.difficulty}): {task.hint or 'Используй заготовку кода с TODO комментариями'}"
                for task in code_tasks[:2]
            ]
        )
        if code_tasks_text:
            enhanced_approach.append(f"**Задания на программирование:**\n{code_tasks_text}")
        return enhanced_approach

    def _generate_code_tasks(
        self,
        *,
        title: str,
        seed: ProjectSeed,
        goal: str,
        input_data: str,
    ) -> list[Any]:
        """Call the optional code agent with the narrow context for this task."""
        if (
            not self.code_agent
            or not is_programming_topic(seed)
            or not CODE_EXAMPLE_CONFIG["enable_code_tasks_in_practice"]
        ):
            return []

        try:
            code_result = self.code_agent.generate(
                topic=title,
                skills=seed.skills or [],
                seed=seed,
                context=f"{goal}\n{input_data}",
            )
            if code_result and code_result.tasks:
                max_tasks = CODE_EXAMPLE_CONFIG.get("max_code_tasks_per_practice", 3)
                return list(code_result.tasks[:max_tasks])
        except Exception as exc:  # noqa: BLE001
            print(
                f"  ⚠️ Ошибка генерации CodeTask для задачи '{title}': {str(exc)}",
                file=sys.stderr,
                flush=True,
            )
        return []
