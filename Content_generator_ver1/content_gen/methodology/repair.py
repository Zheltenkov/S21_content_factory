"""Bounded deterministic repairs for methodology gate findings."""

from __future__ import annotations

import re
import time
from typing import Any

from content_gen.config.thresholds import THRESHOLDS
from content_gen.models.flow_state import ProjectBlueprint
from content_gen.models.schemas import PracticeTask, TheoryPart
from content_gen.recovery import ModelOutputNormalizer

from .models import StageRepairResult, StageReviewIssue, StageReviewResult


class MethodologyRepairController:
    """Apply safe post-stage repairs without rerunning agent generation."""

    REPAIRABLE_ISSUES = {
        "task_planning.tasks_count_out_of_range",
        "task_planning.seed_mismatch",
        "skeleton.blueprint_missing",
        "theory.parts_missing",
        "practice.tasks_missing",
        "practice.tasks_count_mismatch",
    }

    def repair(
        self,
        stage: str,
        context: dict[str, Any],
        review: StageReviewResult,
    ) -> StageRepairResult | None:
        """Run at most one deterministic repair attempt for a reviewed stage."""
        if review.status in {"passed", "skipped"}:
            return None

        attempted = context.setdefault("methodology_repair_attempted", set())
        if stage in attempted:
            return StageRepairResult(
                stage=stage,
                status="skipped",
                issue_codes=[issue.code for issue in review.issues],
                skipped_reason="repair already attempted for this stage",
            )
        attempted.add(stage)

        actionable = [issue for issue in review.issues if issue.code in self.REPAIRABLE_ISSUES]
        if not actionable:
            if not review.repair_instructions:
                return None
            return StageRepairResult(
                stage=stage,
                status="skipped",
                issue_codes=[issue.code for issue in review.issues],
                skipped_reason="no deterministic repair registered for these issue codes",
            )

        start = time.perf_counter()
        try:
            actions: list[str] = []
            updated_fields: list[str] = []
            warnings: list[str] = []
            metrics: dict[str, Any] = {"allowed_issue_codes": [issue.code for issue in actionable]}

            if stage == "task_planning":
                self._repair_task_planning(context, actionable, actions, updated_fields, warnings, metrics)
            elif stage == "skeleton":
                self._repair_skeleton(context, actionable, actions, updated_fields, warnings, metrics)
            elif stage == "theory":
                self._repair_theory(context, actionable, actions, updated_fields, warnings, metrics)
            elif stage == "practice":
                self._repair_practice(context, actionable, actions, updated_fields, warnings, metrics)
            else:
                warnings.append(f"No repair handler for stage '{stage}'.")

            status = "applied" if updated_fields else "skipped"
            skipped_reason = None if updated_fields else "handler found no safe deterministic update"
            return StageRepairResult(
                stage=stage,
                status=status,
                issue_codes=[issue.code for issue in actionable],
                actions=actions,
                updated_fields=updated_fields,
                warnings=warnings,
                skipped_reason=skipped_reason,
                metrics=metrics,
                duration_ms=self._elapsed_ms(start),
            )
        except Exception as exc:  # noqa: BLE001
            return StageRepairResult(
                stage=stage,
                status="failed",
                issue_codes=[issue.code for issue in actionable],
                warnings=[str(exc)],
                duration_ms=self._elapsed_ms(start),
            )

    def _repair_task_planning(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        actions: list[str],
        updated_fields: list[str],
        warnings: list[str],
        metrics: dict[str, Any],
    ) -> None:
        seed = context.get("seed")
        task_plan = context.get("task_plan")
        if task_plan is None:
            warnings.append("TaskPlan is absent; deterministic repair cannot create a plan.")
            return

        issue_codes = {issue.code for issue in issues}
        min_tasks, max_tasks = THRESHOLDS["practice_tasks_recommend"]
        current_count = int(getattr(task_plan, "tasks_count", 0) or 0)
        metrics["task_plan_tasks_before"] = current_count

        if "task_planning.tasks_count_out_of_range" in issue_codes:
            repaired_count = max(min_tasks, min(max_tasks, current_count or min_tasks))
            if repaired_count != current_count:
                setattr(task_plan, "tasks_count", repaired_count)
                updated_fields.append("task_plan.tasks_count")
                actions.append(f"clamped TaskPlan.tasks_count from {current_count} to {repaired_count}")

        repaired_count = int(getattr(task_plan, "tasks_count", current_count) or current_count or min_tasks)
        if seed is not None and getattr(seed, "tasks_count", None) != repaired_count:
            setattr(seed, "tasks_count", repaired_count)
            updated_fields.append("seed.tasks_count")
            actions.append(f"synced ProjectSeed.tasks_count to {repaired_count}")

        complexity = getattr(task_plan, "complexity", None)
        if seed is not None and complexity and getattr(seed, "task_complexity", None) != complexity:
            setattr(seed, "task_complexity", complexity)
            updated_fields.append("seed.task_complexity")
            actions.append(f"synced ProjectSeed.task_complexity to {complexity}")

        context["task_plan"] = task_plan
        if seed is not None:
            context["seed"] = seed
        metrics["task_plan_tasks_after"] = repaired_count

    def _repair_skeleton(
        self,
        context: dict[str, Any],
        _issues: list[StageReviewIssue],
        actions: list[str],
        updated_fields: list[str],
        warnings: list[str],
        metrics: dict[str, Any],
    ) -> None:
        if context.get("blueprint") is not None:
            return

        markdown = str(context.get("markdown") or "")
        if not markdown.strip():
            warnings.append("Markdown is empty; ProjectBlueprint cannot be inferred.")
            return

        seed = context.get("seed")
        task_plan = context.get("task_plan")
        chapter_titles = self._extract_chapter_titles(markdown)
        has_bonus = bool(getattr(seed, "bonus_wish", None)) or bool(re.search(r"^##\s+Бонус\b", markdown, re.M))
        section_order = ["title", "annotation", "toc", "intro", "theory", "practice"]
        if has_bonus:
            section_order.append("bonus")

        blueprint = ProjectBlueprint(
            language=self._as_text(getattr(seed, "language", "ru")),
            has_bonus=has_bonus,
            section_order=section_order,
            chapter_titles=chapter_titles,
            intro_subsections=self._extract_intro_subsections(markdown),
            planned_tasks_count=self._optional_int(
                getattr(task_plan, "tasks_count", None) or getattr(seed, "tasks_count", None)
            ),
            planned_task_complexity=(
                getattr(task_plan, "complexity", None) or getattr(seed, "task_complexity", None)
            ),
        )
        context["blueprint"] = blueprint
        updated_fields.append("blueprint")
        actions.append("inferred ProjectBlueprint from skeleton markdown and planning context")
        metrics["chapter_titles_count"] = len(chapter_titles)

    def _repair_theory(
        self,
        context: dict[str, Any],
        _issues: list[StageReviewIssue],
        actions: list[str],
        updated_fields: list[str],
        warnings: list[str],
        metrics: dict[str, Any],
    ) -> None:
        if context.get("theory_parts"):
            return

        markdown = str(context.get("markdown") or "")
        theory_parts = self.parse_theory_parts(markdown)
        metrics["parsed_theory_parts_count"] = len(theory_parts)
        if not theory_parts:
            warnings.append("No theory parts could be parsed from Chapter 2.")
            return

        context["theory_parts"] = theory_parts
        updated_fields.append("theory_parts")
        actions.append(f"parsed {len(theory_parts)} TheoryPart objects from Chapter 2 markdown")

    def _repair_practice(
        self,
        context: dict[str, Any],
        _issues: list[StageReviewIssue],
        actions: list[str],
        updated_fields: list[str],
        warnings: list[str],
        metrics: dict[str, Any],
    ) -> None:
        if context.get("practice_tasks"):
            warnings.append("Practice tasks already exist; deterministic repair will not add or delete tasks.")
            return

        markdown = str(context.get("markdown") or "")
        practice_tasks = self.parse_practice_tasks(markdown)
        metrics["parsed_practice_tasks_count"] = len(practice_tasks)
        if not practice_tasks:
            warnings.append("No practice tasks could be parsed from Chapter 3.")
            return

        context["practice_tasks"] = practice_tasks
        updated_fields.append("practice_tasks")
        actions.append(f"parsed {len(practice_tasks)} PracticeTask objects from Chapter 3 markdown")

    @classmethod
    def parse_theory_parts(cls, markdown: str) -> list[TheoryPart]:
        """Extract typed theory parts from a generated markdown chapter."""
        normalized_markdown = ModelOutputNormalizer().normalize_theory_markdown(markdown).markdown
        chapter_2 = cls._extract_chapter(normalized_markdown, "2", "3")
        if not chapter_2:
            return []

        parts: list[TheoryPart] = []
        pattern = r"^###\s+2\.\d+\.\s+(.+?)\s*\n+(.*?)(?=^###\s+2\.\d+\.|\Z)"
        for match in re.finditer(pattern, chapter_2, re.S | re.M):
            title = match.group(1).strip()
            body = match.group(2).strip()
            if title and body:
                parts.append(TheoryPart(title=title, body=body, example="", bridge_questions=[]))
        return parts

    @classmethod
    def parse_practice_tasks(cls, markdown: str) -> list[PracticeTask]:
        """Extract typed practice tasks from a generated markdown chapter."""
        normalized_markdown = ModelOutputNormalizer().normalize_practice_markdown(markdown).markdown
        chapter_3 = cls._extract_chapter(normalized_markdown, "3", None)
        if not chapter_3:
            return []
        chapter_3 = re.split(r"^##\s+Бонус\b", chapter_3, maxsplit=1, flags=re.M)[0]

        tasks: list[PracticeTask] = []
        pattern = r"^###\s+Задани(?:е|я)\s+\d+\.\s+(.+?)\s*\n+(.*?)(?=^###\s+Задани(?:е|я)\s+\d+\.|\Z)"
        for match in re.finditer(pattern, chapter_3, re.S | re.M):
            title = match.group(1).strip()
            body = match.group(2).strip()
            if not title:
                continue
            goal = cls._extract_goal_from_body(body) or cls._first_paragraph(body) or f"Выполнить задание: {title}"
            tasks.append(
                PracticeTask(
                    title=title,
                    input_data="",
                    goal=goal,
                    approach_bullets=[],
                    expected_artifact=cls._extract_expected_artifact(body),
                    artifact_location=cls._extract_artifact_location(body),
                )
            )
        return tasks

    @staticmethod
    def _extract_chapter(markdown: str, chapter_number: str, next_chapter_number: str | None) -> str:
        next_pattern = rf"^##\s+Глава\s+{next_chapter_number}\b" if next_chapter_number else r"\Z"
        match = re.search(
            rf"^##\s+Глава\s+{chapter_number}[^\n]*\n(.*?)(?={next_pattern})",
            markdown,
            re.S | re.M,
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_chapter_titles(markdown: str) -> dict[str, str]:
        titles: dict[str, str] = {}
        for chapter_number, key in (("1", "intro"), ("2", "theory"), ("3", "practice")):
            match = re.search(rf"^##\s+Глава\s+{chapter_number}[^\n]*", markdown, re.M)
            if match:
                titles[key] = match.group(0).strip()
        bonus = re.search(r"^##\s+Бонус[^\n]*", markdown, re.M)
        if bonus:
            titles["bonus"] = bonus.group(0).strip()
        return titles

    @classmethod
    def _extract_intro_subsections(cls, markdown: str) -> list[str]:
        chapter_1 = cls._extract_chapter(markdown, "1", "2")
        return [match.group(1).strip() for match in re.finditer(r"^###\s+(.+?)\s*$", chapter_1, re.M)]

    @staticmethod
    def _first_paragraph(text: str) -> str:
        for part in re.split(r"\n\s*\n", text):
            cleaned = re.sub(r"^\s*[-*]\s+", "", part.strip())
            if cleaned and not cleaned.startswith("#"):
                return re.sub(r"\s+", " ", cleaned)[:500]
        return ""

    @staticmethod
    def _extract_expected_artifact(text: str) -> str:
        match = re.search(
            r"(?:\*\*Что должно получиться:?\*\*|\*\*Ожидаемый результат:?\*\*|ожидаемый\s+артефакт|артефакт)\s*[:\-]?\s*(.+)",
            text,
            re.I | re.S,
        )
        if match:
            return match.group(1).strip()[:300]
        return "README or project artifact"

    @staticmethod
    def _extract_artifact_location(text: str) -> str:
        match = re.search(r"([A-Za-z0-9_.\-]+/[A-Za-z0-9_./{}:\-]+\.[A-Za-z0-9]+)", text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_goal_from_body(text: str) -> str:
        match = re.search(r"(?:\*\*Цель:?\*\*|(?:^|\n)\s*Цель:)\s*(.+?)(?=\n\*\*|\n\s*Подход:|\Z)", text, re.S | re.I)
        return re.sub(r"\s+", " ", match.group(1)).strip()[:500] if match else ""

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_text(value: Any) -> str:
        enum_value = getattr(value, "value", value)
        return str(enum_value or "ru")

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        return round((time.perf_counter() - start) * 1000, 2)
