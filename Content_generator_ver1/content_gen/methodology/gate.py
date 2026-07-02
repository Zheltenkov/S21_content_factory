"""Deterministic methodology checks for AgentFlow stages."""

from __future__ import annotations

import re
import time
from typing import Any

from content_gen.config.thresholds import THRESHOLDS
from content_gen.domain_contracts import LearningActivityContract, NarrativeContract, StaticInstructionLeakGuard
from content_gen.practice_contract import (
    extract_material_refs,
    find_non_raw_material_issues,
    find_solution_like_material_refs,
    task_uses_previous_artifact,
)

from .models import IssueSeverity, ReviewStatus, StageReviewIssue, StageReviewResult


class MethodologyGate:
    """Review flow stages as a methodologist would, using explicit contracts."""

    reviewed_stages = {
        "context",
        "task_planning",
        "skeleton",
        "theory",
        "practice",
        "dataset_generation",
        "evaluation",
        "finalize",
    }

    def review(self, stage: str, context: dict[str, Any]) -> StageReviewResult:
        """Run deterministic stage checks and return a typed review result."""
        start = time.perf_counter()
        issues: list[StageReviewIssue] = []
        metrics: dict[str, Any] = {}
        evidence: dict[str, Any] = {}

        if stage not in self.reviewed_stages:
            return StageReviewResult(
                stage=stage,
                status="skipped",
                duration_ms=self._elapsed_ms(start),
            )

        reviewer = getattr(self, f"_review_{stage}", self._review_default)
        reviewer(context, issues, metrics, evidence)
        status = self._status_from_issues(issues)
        repair_instructions = [
            issue.repair_hint
            for issue in issues
            if issue.repair_hint and issue.severity in {"major", "critical"}
        ]

        return StageReviewResult(
            stage=stage,
            status=status,
            issues=issues,
            repair_instructions=repair_instructions,
            human_review_required=any(issue.severity == "critical" for issue in issues),
            metrics=metrics,
            evidence=evidence,
            duration_ms=self._elapsed_ms(start),
        )

    def _review_context(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        metrics: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        seed = context.get("seed")
        context_meta = context.get("context_meta")
        context_analysis = context.get("context_analysis")
        context_bundle = context.get("context_bundle")

        metrics["has_seed"] = seed is not None
        metrics["has_context_meta"] = context_meta is not None
        metrics["has_context_analysis"] = context_analysis is not None
        metrics["has_context_bundle"] = context_bundle is not None

        if seed is None:
            self._add_issue(issues, "context.seed_missing", "ProjectSeed is missing after context stage.", "critical")
            return
        if context_meta is None:
            self._add_issue(issues, "context.meta_missing", "ProjectContextMeta is missing.", "critical")
        if context_analysis is None:
            self._add_issue(issues, "context.analysis_missing", "ContextAnalysisResult is missing.", "major")
        if context_bundle is None:
            self._add_issue(issues, "context.bundle_missing", "ProjectContextBundle is missing.", "major")

        learning_outcomes = list(getattr(seed, "learning_outcomes", []) or [])
        skills = list(getattr(seed, "skills", []) or [])
        curriculum_context = getattr(seed, "curriculum_context", None) or {}
        metrics["learning_outcomes_count"] = len(learning_outcomes)
        metrics["skills_count"] = len(skills)
        metrics["has_curriculum_context"] = bool(curriculum_context)
        narrative_contract = self._coerce_narrative_contract(seed, context_bundle)
        metrics["has_narrative_contract"] = narrative_contract is not None
        metrics["narrative_contract_actionable"] = bool(narrative_contract and narrative_contract.is_actionable)

        if not learning_outcomes:
            self._add_issue(
                issues,
                "context.learning_outcomes_empty",
                "Learning outcomes are empty.",
                "critical",
                "Fill learning_outcomes before generation or route the task to human review.",
            )
        if not skills:
            self._add_issue(
                issues,
                "context.skills_empty",
                "Skills are empty.",
                "major",
                "Add at least one explicit skill used by theory and practice stages.",
            )
        if not curriculum_context:
            self._add_issue(
                issues,
                "context.curriculum_context_missing",
                "Curriculum context is absent; continuity checks are weak.",
                "major",
                "Attach curriculum_context with previous/current project metadata.",
            )
        if curriculum_context and not (narrative_contract and narrative_contract.is_actionable):
            self._add_issue(
                issues,
                "context.narrative_contract_missing",
                "NarrativeContract is absent or not actionable.",
                "major",
                "Build NarrativeContract with student role, working case, product/project, constraints, data sources and artifact chain.",
            )

        evidence["title_seed"] = getattr(seed, "title_seed", None)
        evidence["thematic_block"] = getattr(seed, "thematic_block", None)
        if narrative_contract:
            evidence["narrative_contract"] = narrative_contract.model_dump()

    def _review_task_planning(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        metrics: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        seed = context.get("seed")
        task_plan = context.get("task_plan")
        if task_plan is None:
            self._add_issue(
                issues,
                "task_planning.plan_missing",
                "Task plan is missing.",
                "major",
                "Create a TaskPlan before skeleton/theory generation.",
            )
            return

        tasks_count = int(getattr(task_plan, "tasks_count", 0) or 0)
        min_tasks, max_tasks = THRESHOLDS["practice_tasks_recommend"]
        metrics["tasks_count"] = tasks_count
        metrics["complexity"] = getattr(task_plan, "complexity", None)
        evidence["rationale"] = getattr(task_plan, "rationale", "")
        story_map_contract = context.get("story_map_contract")
        practice_plan_contract = context.get("practice_plan_contract")
        artifact_chain_plan = context.get("artifact_chain_plan")
        practice_steps = list(getattr(practice_plan_contract, "steps", []) or [])
        if isinstance(practice_plan_contract, dict):
            practice_steps = list(practice_plan_contract.get("steps") or [])
        metrics["has_story_map_contract"] = story_map_contract is not None
        metrics["has_practice_plan_contract"] = practice_plan_contract is not None
        metrics["has_artifact_chain_plan"] = artifact_chain_plan is not None
        metrics["practice_plan_steps_count"] = len(practice_steps)

        if tasks_count < min_tasks or tasks_count > max_tasks:
            self._add_issue(
                issues,
                "task_planning.tasks_count_out_of_range",
                f"Task count {tasks_count} is outside recommended range {min_tasks}-{max_tasks}.",
                "major",
                "Adjust tasks_count to the configured didactic range.",
                {"min": min_tasks, "max": max_tasks, "actual": tasks_count},
            )
        if seed is not None and getattr(seed, "tasks_count", None) != tasks_count:
            self._add_issue(
                issues,
                "task_planning.seed_mismatch",
                "ProjectSeed.tasks_count does not match TaskPlan.tasks_count.",
                "minor",
            )
        if story_map_contract is None:
            self._add_issue(
                issues,
                "task_planning.story_map_missing",
                "StoryMapContract is missing before theory generation.",
                "major",
                "Build a story map from curriculum/SJM before Chapter 2.",
            )
        if practice_plan_contract is None:
            self._add_issue(
                issues,
                "task_planning.practice_plan_contract_missing",
                "PracticePlanContract is missing before theory generation.",
                "major",
                "Build PracticePlanContract before TheoryAgent so theory supports planned activity.",
            )
        elif tasks_count and len(practice_steps) != tasks_count:
            self._add_issue(
                issues,
                "task_planning.practice_plan_steps_mismatch",
                "PracticePlanContract step count does not match TaskPlan.",
                "major",
                "Regenerate PracticePlanContract from the final TaskPlan.",
                {"expected": tasks_count, "actual": len(practice_steps)},
            )
        if artifact_chain_plan is None:
            self._add_issue(
                issues,
                "task_planning.artifact_chain_plan_missing",
                "ArtifactChainPlan is missing before practice/theory generation.",
                "major",
                "Build raw input -> artifact chain before downstream agents.",
            )

    def _review_skeleton(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        metrics: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        markdown = str(context.get("markdown") or "")
        blueprint = context.get("blueprint")
        metrics["markdown_chars"] = len(markdown)
        metrics["has_blueprint"] = blueprint is not None
        evidence["title"] = context.get("title")

        if not markdown.strip():
            self._add_issue(issues, "skeleton.markdown_empty", "Skeleton markdown is empty.", "critical")
            return
        for chapter, code in (("Глава 1", "chapter_1"), ("Глава 2", "chapter_2"), ("Глава 3", "chapter_3")):
            if chapter not in markdown:
                self._add_issue(
                    issues,
                    f"skeleton.{code}_missing",
                    f"Skeleton does not contain {chapter}.",
                    "major",
                    "Regenerate skeleton with all required chapters.",
                )
        if blueprint is None:
            self._add_issue(
                issues,
                "skeleton.blueprint_missing",
                "Structured ProjectBlueprint is missing.",
                "major",
                "Return ProjectBlueprint from skeleton phase for downstream validation.",
            )

    def _review_theory(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        metrics: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        markdown = str(context.get("markdown") or "")
        theory_parts = list(context.get("theory_parts") or [])
        practice_plan_contract = context.get("practice_plan_contract")
        chapter_2 = self._extract_chapter(markdown, "2", "3")
        topic_text = self._topic_text(context.get("seed"))
        static_leaks = StaticInstructionLeakGuard().find_leaks(chapter_2, topic_text=topic_text)
        metrics["theory_parts_count"] = len(theory_parts)
        metrics["chapter_2_words"] = self._count_words(chapter_2)
        metrics["static_instruction_leaks_count"] = len(static_leaks)
        metrics["has_practice_plan_contract"] = practice_plan_contract is not None
        evidence["theory_part_titles"] = [getattr(part, "title", "") for part in theory_parts[:5]]

        if not chapter_2.strip():
            self._add_issue(issues, "theory.chapter_missing", "Chapter 2 is missing or empty.", "critical")
            return
        if not theory_parts:
            self._add_issue(
                issues,
                "theory.parts_missing",
                "Structured theory_parts are missing.",
                "major",
                "Return typed TheoryPart objects from theory generation.",
            )
        if theory_parts and not any(getattr(part, "example", "") for part in theory_parts):
            self._add_issue(
                issues,
                "theory.examples_missing",
                "Theory parts do not include examples.",
                "minor",
            )
        if self._count_words(chapter_2) < 150:
            self._add_issue(
                issues,
                "theory.too_short",
                "Chapter 2 is likely too short for methodology coverage.",
                "major",
                "Expand theory before practice generation.",
            )
        if static_leaks:
            self._add_issue(
                issues,
                "theory.static_instruction_leak",
                "Chapter 2 contains static instruction markers unrelated to the project topic.",
                "major",
                "Remove references to repository, P2P, submission rules or checking environment from theory.",
                {"markers": static_leaks},
            )
        if practice_plan_contract is None:
            self._add_issue(
                issues,
                "theory.practice_plan_contract_missing",
                "Theory was generated without a prior PracticePlanContract.",
                "major",
                "Pass PracticePlanContract into TheoryAgent and make Chapter 2 support planned activities.",
            )

    def _review_practice(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        metrics: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        markdown = str(context.get("markdown") or "")
        practice_tasks = list(context.get("practice_tasks") or [])
        task_plan = context.get("task_plan")
        chapter_3 = self._extract_chapter(markdown, "3", None)
        expected_count = getattr(task_plan, "tasks_count", None)
        metrics["practice_tasks_count"] = len(practice_tasks)
        metrics["expected_tasks_count"] = expected_count
        evidence["practice_titles"] = [getattr(task, "title", "") for task in practice_tasks[:8]]

        if not chapter_3.strip():
            self._add_issue(issues, "practice.chapter_missing", "Chapter 3 is missing or empty.", "critical")
            return
        if not practice_tasks:
            self._add_issue(
                issues,
                "practice.tasks_missing",
                "Structured practice_tasks are missing.",
                "major",
                "Return typed PracticeTask objects from practice phase.",
            )
        if expected_count is not None and len(practice_tasks) != int(expected_count):
            self._add_issue(
                issues,
                "practice.tasks_count_mismatch",
                "Practice task count does not match TaskPlan.",
                "major",
                "Repair practice section to match the planned task count.",
                {"expected": expected_count, "actual": len(practice_tasks)},
            )

        solution_material_refs: dict[int, list[str]] = {}
        non_raw_material_issues: dict[int, list[str]] = {}
        dependency_gaps: list[dict[str, str]] = []
        activity_contract = LearningActivityContract()
        for idx, task in enumerate(practice_tasks, 1):
            input_data = str(getattr(task, "input_data", "") or "")
            refs = find_solution_like_material_refs(input_data)
            if refs:
                solution_material_refs[idx] = refs

            previous = practice_tasks[idx - 2] if idx > 1 else None
            for contract_issue in activity_contract.check_task(task, task_index=idx, previous_task=previous):
                if contract_issue.code == "practice.non_raw_input_materials":
                    phrase_issues = [
                        issue for issue in contract_issue.details.get("material_issues", [])
                        if not str(issue).startswith("solution_like_ref:")
                    ]
                    if phrase_issues:
                        non_raw_material_issues[idx] = phrase_issues
                elif contract_issue.code == "practice.task_dependency_missing":
                    dependency_gaps.append({
                        "task": str(idx),
                        "previous_artifact": str(contract_issue.details.get("previous_artifact", "")),
                    })

            if idx > 1 and previous is not None:
                previous_location = str(getattr(previous, "artifact_location", "") or "")
                already_recorded = any(gap.get("task") == str(idx) for gap in dependency_gaps)
                if previous_location and not already_recorded and not task_uses_previous_artifact(task, previous):
                    dependency_gaps.append({
                        "task": str(idx),
                        "previous_artifact": previous_location,
                    })

        metrics["solution_like_material_refs_count"] = sum(len(refs) for refs in solution_material_refs.values())
        metrics["non_raw_material_issues_count"] = sum(len(items) for items in non_raw_material_issues.values())
        metrics["practice_dependency_gaps_count"] = len(dependency_gaps)
        if solution_material_refs:
            self._add_issue(
                issues,
                "practice.solution_materials_leak",
                "Practice input materials look like ready learner deliverables.",
                "major",
                "Replace solution-like materials with raw cases/notes or previous task artifacts.",
                {"refs_by_task": solution_material_refs},
            )
        if non_raw_material_issues:
            self._add_issue(
                issues,
                "practice.non_raw_input_materials",
                "Practice input materials are described as classified or solved educational drafts.",
                "major",
                "Replace them with raw evidence: notes, logs, interview fragments, requests, constraints or previous task artifacts.",
                {"issues_by_task": non_raw_material_issues},
            )
        if dependency_gaps:
            self._add_issue(
                issues,
                "practice.task_dependency_missing",
                "Practice tasks do not form a causal chain through previous task artifacts.",
                "major",
                "Make each next task consume the artifact produced by the previous task.",
                {"gaps": dependency_gaps},
            )

        critic_issues = context.get("practice_critic_issues") or []
        serious_critic = [
            issue for issue in critic_issues
            if isinstance(issue, dict) and str(issue.get("severity", "")).lower() in {"major", "critical", "hard"}
        ]
        metrics["practice_critic_serious_count"] = len(serious_critic)
        if serious_critic:
            self._add_issue(
                issues,
                "practice.critic_serious_findings",
                "PracticeCritic found serious methodology issues.",
                "major",
                "Use PracticeCritic findings as repair instructions before final export.",
                {"count": len(serious_critic)},
            )

    def _review_dataset_generation(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        metrics: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        practice_tasks = list(context.get("practice_tasks") or [])
        dataset_files = list(context.get("dataset_files") or [])
        evidence_specs = list(context.get("evidence_specs") or [])

        material_refs_by_task: dict[int, list[str]] = {}
        for idx, task in enumerate(practice_tasks, 1):
            refs = extract_material_refs(str(getattr(task, "input_data", "") or ""))
            if refs:
                material_refs_by_task[idx] = refs

        expected_refs = sorted({ref for refs in material_refs_by_task.values() for ref in refs})
        generated_paths = {
            str(file.get("path", "")).replace("\\", "/").lower()
            for file in dataset_files
            if isinstance(file, dict)
        }
        spec_paths = {
            self._evidence_spec_path(spec).replace("\\", "/").lower()
            for spec in evidence_specs
            if self._evidence_spec_path(spec)
        }

        missing_files = [
            ref for ref in expected_refs
            if ref.replace("\\", "/").lower() not in generated_paths
        ]
        missing_specs = [
            ref for ref in expected_refs
            if ref.replace("\\", "/").lower() not in spec_paths
        ]
        weak_specs = [
            self._evidence_spec_path(spec)
            for spec in evidence_specs
            if self._evidence_spec_path(spec)
            and not (
                self._evidence_spec_list(spec, "contains")
                and self._evidence_spec_list(spec, "excludes")
                and self._evidence_spec_list(spec, "student_must_derive")
            )
        ]
        solution_like_paths = [
            path for path in generated_paths
            if find_solution_like_material_refs(f"`{path}`")
        ]
        non_raw_content_issues: dict[str, list[str]] = {}
        for file in dataset_files:
            if not isinstance(file, dict):
                continue
            path = str(file.get("path", "") or "")
            data = file.get("data")
            if isinstance(data, bytes):
                text = data.decode("utf-8", errors="ignore")[:4000]
            else:
                text = str(data or "")[:4000]
            content_issues = [
                item for item in find_non_raw_material_issues(text)
                if item.startswith("solution_like_ref:")
            ]
            if content_issues:
                non_raw_content_issues[path] = content_issues

        metrics["material_refs_count"] = len(expected_refs)
        metrics["dataset_files_count"] = len(dataset_files)
        metrics["evidence_specs_count"] = len(evidence_specs)
        metrics["missing_dataset_files_count"] = len(missing_files)
        metrics["missing_evidence_specs_count"] = len(missing_specs)
        evidence["material_refs_by_task"] = material_refs_by_task
        evidence["dataset_file_paths"] = sorted(generated_paths)
        evidence["evidence_spec_paths"] = sorted(spec_paths)

        if missing_files:
            self._add_issue(
                issues,
                "dataset_generation.files_missing",
                "Some materials referenced by practice tasks were not generated.",
                "major",
                "Generate every referenced materials/* file or remove the reference from task input_data.",
                {"missing_files": missing_files},
            )
        if missing_specs:
            self._add_issue(
                issues,
                "dataset_generation.evidence_specs_missing",
                "Some materials references do not have EvidenceSpec contracts.",
                "major",
                "Attach EvidenceSpec for every materials/* file before dataset generation.",
                {"missing_specs": missing_specs},
            )
        if weak_specs:
            self._add_issue(
                issues,
                "dataset_generation.evidence_specs_weak",
                "Some EvidenceSpec contracts do not fully define contains/excludes/student_must_derive.",
                "minor",
                "Complete EvidenceSpec fields so materials remain raw evidence, not answer drafts.",
                {"weak_specs": weak_specs},
            )
        if solution_like_paths:
            self._add_issue(
                issues,
                "dataset_generation.solution_like_paths",
                "Generated material paths look like ready learner deliverables.",
                "major",
                "Rename materials to raw source/case/notes/log/interview files.",
                {"paths": solution_like_paths},
            )
        if non_raw_content_issues:
            self._add_issue(
                issues,
                "dataset_generation.non_raw_content",
                "Generated material content references solution-like materials.",
                "major",
                "Regenerate materials as raw evidence without ready deliverables.",
                {"issues_by_file": non_raw_content_issues},
            )

    def _review_evaluation(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        metrics: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        rubric = context.get("rubric_json")
        metrics["has_rubric"] = isinstance(rubric, dict)
        if not isinstance(rubric, dict) or not rubric:
            self._add_issue(
                issues,
                "evaluation.rubric_missing",
                "Rubric result is missing.",
                "critical",
                "Run final rubric evaluation before finalization.",
            )
        else:
            evidence["rubric_keys"] = sorted(rubric.keys())[:20]

    def _review_finalize(
        self,
        context: dict[str, Any],
        issues: list[StageReviewIssue],
        metrics: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        result = context.get("result")
        project_spec = context.get("project_spec")
        markdown = str(context.get("markdown") or "")
        metrics["has_result"] = result is not None
        metrics["has_project_spec"] = project_spec is not None
        metrics["markdown_chars"] = len(markdown)

        if result is None:
            self._add_issue(issues, "finalize.result_missing", "OrchestratorResult is missing.", "critical")
        if project_spec is None:
            self._add_issue(issues, "finalize.project_spec_missing", "ProjectSpec is missing.", "critical")
        if not markdown.strip():
            self._add_issue(issues, "finalize.markdown_missing", "Final markdown is missing.", "critical")
        if result is not None:
            report_json = getattr(result, "report_json", None)
            metrics["has_report_json"] = isinstance(report_json, dict)
            if not isinstance(report_json, dict):
                self._add_issue(issues, "finalize.report_missing", "report_json is missing.", "critical")
            else:
                evidence["report_keys"] = sorted(report_json.keys())[:30]

    def _review_default(
        self,
        _context: dict[str, Any],
        _issues: list[StageReviewIssue],
        _metrics: dict[str, Any],
        _evidence: dict[str, Any],
    ) -> None:
        return

    @staticmethod
    def _add_issue(
        issues: list[StageReviewIssue],
        code: str,
        message: str,
        severity: IssueSeverity,
        repair_hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        issues.append(
            StageReviewIssue(
                code=code,
                message=message,
                severity=severity,
                repair_hint=repair_hint,
                details=details or {},
            )
        )

    @staticmethod
    def _status_from_issues(issues: list[StageReviewIssue]) -> ReviewStatus:
        if not issues:
            return "passed"
        if any(issue.severity == "critical" for issue in issues):
            return "failed"
        return "warning"

    @staticmethod
    def _coerce_narrative_contract(seed: Any, context_bundle: Any) -> NarrativeContract | None:
        payload = None
        if context_bundle is not None:
            payload = getattr(context_bundle, "narrative_contract", None)
        if not payload and seed is not None:
            curriculum_context = getattr(seed, "curriculum_context", None) or {}
            if isinstance(curriculum_context, dict):
                payload = curriculum_context.get("narrative_contract")
        if isinstance(payload, NarrativeContract):
            return payload
        if isinstance(payload, dict) and payload:
            try:
                return NarrativeContract(**payload)
            except Exception:
                return None
        return None

    @staticmethod
    def _topic_text(seed: Any) -> str:
        if seed is None:
            return ""
        return " ".join([
            str(getattr(seed, "title_seed", "") or ""),
            str(getattr(seed, "project_description", "") or ""),
            " ".join(getattr(seed, "learning_outcomes", []) or []),
            " ".join(getattr(seed, "skills", []) or []),
        ])

    @staticmethod
    def _extract_chapter(markdown: str, chapter_number: str, next_chapter_number: str | None) -> str:
        next_pattern = rf"\n##\s+Глава\s+{next_chapter_number}\b" if next_chapter_number else r"\Z"
        match = re.search(
            rf"##\s+Глава\s+{chapter_number}[^\n]*\n(.*?)(?={next_pattern})",
            markdown,
            re.S,
        )
        return match.group(1).strip() if match else ""

    @staticmethod
    def _count_words(text: str) -> int:
        return len(re.findall(r"\b[\wА-Яа-яЁё-]+\b", text))

    @staticmethod
    def _evidence_spec_path(spec: Any) -> str:
        if isinstance(spec, dict):
            return str(spec.get("path", "") or "")
        return str(getattr(spec, "path", "") or "")

    @staticmethod
    def _evidence_spec_list(spec: Any, key: str) -> list[Any]:
        if isinstance(spec, dict):
            value = spec.get(key) or []
        else:
            value = getattr(spec, key, []) or []
        return value if isinstance(value, list) else []

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        return round((time.perf_counter() - start) * 1000, 2)
