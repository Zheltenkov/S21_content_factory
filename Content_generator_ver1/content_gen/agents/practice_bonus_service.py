"""Bonus practice task generation service."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..config.loader import prompt_trace_kwargs
from ..models.schemas import PracticeTask, ProjectSeed
from .base.llm_client import LLMClientProtocol
from .practice_contracts import ensure_p2p_criteria, normalize_approach_bullets
from .practice_finalizer import PracticeTaskFinalizer
from .practice_parsing import extract_public_practice_fields, parse_approach_bullets, parse_p2p_criteria
from .practice_prompting import build_practice_repo_info
from .practice_repair import (
    fix_task_risk,
    fix_task_situation,
    infer_covered_outcomes,
    infer_theory_support,
    summarize_approach_to_limit,
    token_set,
)


StyleRewrite = Callable[[str, str], str]
ArtifactLocationBuilder = Callable[..., str]


class BonusPracticeService:
    """Generate and finalize optional bonus practice tasks."""

    def __init__(
        self,
        *,
        llm: LLMClientProtocol,
        config: Any,
        llm_kwargs: dict[str, Any],
        didactics_context: str,
        style_rewrite: StyleRewrite,
        artifact_location_for_task: ArtifactLocationBuilder,
        finalizer: PracticeTaskFinalizer,
    ) -> None:
        self.llm = llm
        self.config = config
        self.llm_kwargs = llm_kwargs
        self.didactics_context = didactics_context
        self.style_rewrite = style_rewrite
        self.artifact_location_for_task = artifact_location_for_task
        self.finalizer = finalizer

    def generate(self, seed: ProjectSeed, n_bonus: int = 1) -> list[PracticeTask]:
        """Generate optional bonus tasks and apply the same task contracts as main practice."""
        if n_bonus < 1:
            return []

        n_bonus = min(n_bonus, 2)
        system_prompt = self.config.get_prompt("system").format(language=seed.language)
        if self.didactics_context:
            system_prompt = f"{system_prompt}\n\n=== DIDACTICS CONTEXT ===\n{self.didactics_context}"

        user_prompt = self.config.get_prompt("bonus_template").format(
            n=n_bonus,
            i="{i}",
            required_tools=", ".join(seed.required_tools) if seed.required_tools else "—",
            bonus_wish=seed.bonus_wish or "—",
            project_description=seed.project_description,
            learning_outcomes="; ".join(seed.learning_outcomes),
            skills="; ".join(seed.skills),
            repo_info=build_practice_repo_info(seed),
        )
        if seed.reference_project_hint and seed.reference_project_hint.strip():
            user_prompt = (
                f"{user_prompt}\n\n=== ЭТАЛОН ИДЕАЛЬНОГО ПРОЕКТА ===\n"
                "Сохраняй уровень и качество эталона, но бонусные задания должны оставаться в рамках текущего проекта.\n\n"
                f"{seed.reference_project_hint.strip()}"
            )

        generation_kwargs = self.llm_kwargs.copy()
        generation_kwargs.setdefault("temperature", 0.2)
        generation_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system",
                "bonus_template",
                output_schema="BonusPracticeTask[]",
            )
        )
        markdown = self.llm.complete(system=system_prompt, user=user_prompt, **generation_kwargs)
        bonus_tasks = self._materialize_bonus_tasks(markdown, seed)
        return self.finalizer.finalize_bonus_tasks(bonus_tasks, seed, seed.language)

    def _materialize_bonus_tasks(self, markdown: str, seed: ProjectSeed) -> list[PracticeTask]:
        """Parse generated bonus markdown into typed tasks before finalization."""
        tasks: list[PracticeTask] = []
        rx_bonus = re.compile(r"^###\s+Бонусная\s+задача\s+(\d+)\*?\.\s*(.+?)\s*$", re.M)
        indices = [match.start() for match in rx_bonus.finditer(markdown)] + [len(markdown)]
        header_matches = list(rx_bonus.finditer(markdown))

        for idx, header in enumerate(header_matches):
            start = header.end()
            end = indices[idx + 1] if idx + 1 < len(indices) else len(markdown)
            block = markdown[start:end].strip()
            title = header.group(2).strip()
            tasks.append(self._materialize_bonus_task(block=block, title=title, task_index=idx, seed=seed))

        return tasks

    def _materialize_bonus_task(
        self,
        *,
        block: str,
        title: str,
        task_index: int,
        seed: ProjectSeed,
    ) -> PracticeTask:
        """Build one bonus PracticeTask from one generated markdown block."""
        fields = extract_public_practice_fields(block)
        situation = fields["situation"]
        constraints_or_risk = fields["constraints_or_risk"]
        input_data = fields["input_data"]
        goal = fields["goal"]
        approach = fields["approach"]
        result = fields["result"]
        p2p_criteria = parse_p2p_criteria(block)

        bullets = self._build_approach_bullets(approach, seed.language)
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

        artifact_location, result = self._extract_or_default_bonus_location(result, seed, task_index)
        theory_support = infer_theory_support(
            "",
            title,
            situation,
            constraints_or_risk,
            goal,
            input_data,
            " ".join(bullets),
        )
        bullets = normalize_approach_bullets(
            bullets,
            theory_support,
            seed.language,
            style_rewrite=self.style_rewrite,
            token_set=token_set,
        )
        p2p_criteria = ensure_p2p_criteria(
            p2p_criteria,
            artifact_location,
            result,
            theory_support,
            seed.language,
            style_rewrite=self.style_rewrite,
        )

        roles = ["лид", "исполнитель", "рецензент"] if seed.project_type == "group" else None
        return PracticeTask(
            title=title,
            situation=situation,
            constraints_or_risk=constraints_or_risk,
            input_data=self.style_rewrite(input_data, seed.language),
            goal=self.style_rewrite(goal, seed.language),
            approach_bullets=bullets,
            expected_artifact=self.style_rewrite(result, seed.language),
            artifact_location=artifact_location,
            p2p_criteria=p2p_criteria,
            covered_outcomes=infer_covered_outcomes(
                seed,
                title,
                situation,
                constraints_or_risk,
                goal,
                input_data,
                " ".join(bullets),
            ),
            theory_support=theory_support,
            group_roles=roles,
        )

    def _build_approach_bullets(self, approach: str, language: str) -> list[str]:
        """Parse and style bonus approach bullets."""
        styled_bullets: list[str] = []
        for bullet in parse_approach_bullets(approach):
            if not bullet:
                continue
            if "```" in bullet:
                styled_bullets.append(bullet.strip())
            else:
                styled_bullets.append(self.style_rewrite(bullet.strip(), language))
        return summarize_approach_to_limit(styled_bullets, language)

    def _extract_or_default_bonus_location(
        self,
        result: str,
        seed: ProjectSeed,
        task_index: int,
    ) -> tuple[str, str]:
        """Extract explicit bonus artifact location or use the canonical bonus path."""
        location_match = re.search(r"\((?:где найти|where):\s*([^)]+)\)", result, flags=re.I)
        if location_match:
            artifact_location = location_match.group(1).strip()
            cleaned_result = re.sub(
                r"\s*\((?:где найти|where):\s*([^)]+)\)\s*$",
                "",
                result,
            ).strip()
            return artifact_location, cleaned_result
        return self.artifact_location_for_task(seed, task_index, bonus=True), result
