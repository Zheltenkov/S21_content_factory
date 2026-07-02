"""Typed node services for the content generation flow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .agents.task_planner import TaskPlan, TaskPlanner
from .config.thresholds import THRESHOLDS
from .domain_contracts import SectionContextPolicy
from .models.generation_context import (
    ContextNodeResult,
    EvaluationNodeResult,
    FinalizeNodeResult,
    GenerationContext,
    PracticeNodeResult,
    QualityNodeResult,
    SkeletonNodeResult,
    TaskPlanningNodeResult,
    TheoryNodeResult,
    TitleAnnotationNodeResult,
    TranslationNodeResult,
)
from .models.phase_results import (
    ContextPhaseResult,
    EvaluationPhaseResult,
    PracticePhaseResult,
    QualityPhaseResult,
    SkeletonPhaseResult,
    StructurePhaseResult,
    TheoryPhaseResult,
    TitleAnnotationPhaseResult,
    TranslationPhaseResult,
)
from .models.readme_document import ReadmeDocument
from .observability import FallbackTraceEvent, normalize_fallback_trace_event
from .project_planning import ProjectBlueprintPlanner

logger = logging.getLogger("content_gen.node_services")

IssueSerializer = Callable[[list[Any]], list[Any]]
IssuePredicate = Callable[[list[Any]], bool]
IssueMessages = Callable[[list[Any]], list[str]]
JsonSafe = Callable[[Any], Any]


def _runtime_attr(runtime_state: Any | None, name: str, default: Any) -> Any:
    if runtime_state is None:
        return default
    return getattr(runtime_state, name, default)


def _merge_runtime_fallback_traces(flow_context: dict[str, Any], runtime_state: Any | None) -> None:
    """Copy runtime fallback traces into the flow context report payload."""
    runtime_traces = list(_runtime_attr(runtime_state, "fallback_traces", []) or [])
    if not runtime_traces:
        return
    existing = flow_context.setdefault("fallback_traces", [])
    for event in runtime_traces:
        normalized = normalize_fallback_trace_event(event).model_dump(mode="json")
        if normalized not in existing:
            existing.append(normalized)


def _hard_issues(issues: list[Any]) -> list[Any]:
    """Return validator issues marked as hard by legacy validators."""
    return [
        issue
        for issue in issues
        if (isinstance(issue, dict) and issue.get("severity") == "hard")
        or getattr(issue, "severity", None) == "hard"
    ]


def _non_blocking_quality_warnings(
    *,
    label: str,
    issues: list[Any],
    existing_warnings: list[str],
    issue_messages: IssueMessages,
) -> list[str]:
    """Represent content-quality failures as warnings instead of flow errors."""
    warnings = list(existing_warnings or [])
    hard = _hard_issues(issues)
    if not hard:
        return warnings

    messages = issue_messages(hard)[:3]
    details = "; ".join(messages) if messages else f"{len(hard)} замечаний"
    warnings.append(
        f"⚠️ {label}: найдены замечания качества. "
        f"Генерация продолжена, проверь критерии и запроси правки при необходимости: {details}"
    )
    return warnings


class SectionContextRecorder:
    """Build and store schema-filtered context for section-scoped nodes."""

    def record(self, context: dict[str, Any], policy: SectionContextPolicy) -> dict[str, Any]:
        """Store the schema-filtered context that a section is allowed to consume/report."""
        seed = context.get("seed")
        payload = self._section_payload(context)
        topic_text = " ".join(
            [
                str(getattr(seed, "title_seed", "") or ""),
                str(getattr(seed, "project_description", "") or ""),
                " ".join(getattr(seed, "learning_outcomes", []) or []) if seed else "",
                " ".join(getattr(seed, "skills", []) or []) if seed else "",
            ]
        )
        filtered = policy.filter_context_payload(payload, topic_text=topic_text)
        context.setdefault("section_contexts", {})[policy.section] = filtered
        return filtered

    def _section_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        seed = context.get("seed")
        context_bundle = context.get("context_bundle")
        curriculum_context = getattr(seed, "curriculum_context", None) if seed is not None else None
        narrative_contract = None
        if context_bundle is not None:
            narrative_contract = getattr(context_bundle, "narrative_contract", None)
        if not narrative_contract and isinstance(curriculum_context, dict):
            narrative_contract = curriculum_context.get("narrative_contract")

        return {
            "curriculum_context": self.json_safe(curriculum_context),
            "narrative_contract": self.json_safe(narrative_contract),
            "sjm_context": self.json_safe(getattr(seed, "sjm", "") if seed else ""),
            "storytelling_type": self.json_safe(getattr(seed, "storytelling_type", "sjm") if seed else "sjm"),
            "learning_outcomes": self.json_safe(getattr(seed, "learning_outcomes", []) if seed else []),
            "skills": self.json_safe(getattr(seed, "skills", []) if seed else []),
            "required_tools": self.json_safe(getattr(seed, "required_tools", []) if seed else []),
            "project_description": self.json_safe(getattr(seed, "project_description", "") if seed else ""),
            "context_summary": self.json_safe(getattr(context.get("context_analysis"), "context_summary", "")),
            "narrative_anchor": self.json_safe(getattr(context.get("context_analysis"), "narrative_anchor", "")),
            "instruction_text": self.json_safe(getattr(context.get("intro_section"), "instruction_text", "")),
            "theory_summary": self.json_safe(context.get("theory_summary", "")),
            "story_map_contract": self.json_safe(context.get("story_map_contract")),
            "practice_plan_contract": self.json_safe(context.get("practice_plan_contract")),
            "artifact_chain_plan": self.json_safe(context.get("artifact_chain_plan")),
            "evidence_specs": self.json_safe(context.get("evidence_specs", [])),
            "dataset_files": self.json_safe(context.get("dataset_files", [])),
            "practice_tasks": self.json_safe(context.get("practice_tasks", [])),
            "theory_parts": self.json_safe(context.get("theory_parts", [])),
            "rubric_json": self.json_safe(context.get("rubric_json", {})),
            "warnings": self.json_safe(context.get("warnings", [])),
            "issues": self.json_safe(context.get("issues", [])),
            "markdown": self.json_safe(context.get("markdown", "")),
        }

    @classmethod
    def json_safe(cls, value: Any) -> Any:
        """Convert flow artifacts into JSON-compatible values for section context."""
        if value is None:
            return None
        if isinstance(value, bytes):
            return f"<bytes:{len(value)}>"
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [cls.json_safe(item) for item in value]
        if isinstance(value, tuple):
            return [cls.json_safe(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls.json_safe(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            return cls.json_safe(value.model_dump())
        if hasattr(value, "as_dict"):
            return cls.json_safe(value.as_dict())
        if hasattr(value, "__dict__"):
            return cls.json_safe(value.__dict__)
        return str(value)


class ContextNodeService:
    """Build the initial curriculum-aware generation context."""

    def __init__(self, build_context: Callable[[dict[str, Any], list[str]], ContextPhaseResult]) -> None:
        self.build_context = build_context

    def execute(self, context: GenerationContext) -> ContextNodeResult:
        raw_input = context.raw_input
        target_language = raw_input.get("language", "ru")
        target_language = str(target_language) if not isinstance(target_language, str) else target_language
        target_language = target_language.lower().strip() if isinstance(target_language, str) else target_language

        logger.info("Context is assembled from curriculum input without retrieval search")
        logger.info(
            "Context language from raw_input: '%s' -> target_language='%s'",
            raw_input.get("language"),
            target_language,
        )

        phase_result = self.build_context(
            raw_input,
            context.track_files,
        )
        warnings = list(phase_result.warnings or [])

        logger.info(
            "Context target_language='%s', previous_projects=%s, reference=%s",
            target_language,
            phase_result.context_meta.search_metrics.get("previous_projects_count", 0),
            phase_result.context_meta.search_metrics.get("reference_enabled", False),
        )

        return ContextNodeResult(
            seed=phase_result.seed,
            target_language=target_language,
            generate_bonus=phase_result.seed.bonus_wish is not None,
            context_meta=phase_result.context_meta,
            context_analysis=phase_result.context_analysis,
            context_bundle=phase_result.context_bundle,
            similar_projects=list(phase_result.similar_projects or []),
            warnings=warnings,
            issues=warnings,
        )


class TitleAnnotationNodeService:
    """Generate title and annotation as an isolated node service."""

    def __init__(self, build_title_annotation: Callable[[Any, Any], TitleAnnotationPhaseResult]) -> None:
        self.build_title_annotation = build_title_annotation

    def execute(self, context: GenerationContext) -> TitleAnnotationNodeResult:
        seed = context.require("seed")
        context_meta = context.require("context_meta")
        result = self.build_title_annotation(seed, context_meta)
        return TitleAnnotationNodeResult(title=result.title, annotation=result.annotation)


class TaskPlanningNodeService:
    """Plan practice scope and deterministic generation contracts."""

    def __init__(
        self,
        task_planner: TaskPlanner,
        project_blueprint_planner: ProjectBlueprintPlanner | None = None,
        runtime_state: Any | None = None,
    ) -> None:
        self.task_planner = task_planner
        self.project_blueprint_planner = project_blueprint_planner or ProjectBlueprintPlanner()
        self.runtime_state = runtime_state

    def execute(self, context: GenerationContext) -> TaskPlanningNodeResult:
        seed = context.require("seed")
        context_meta = context.require("context_meta")
        context_analysis = context.require("context_analysis")
        warnings: list[str] = []
        fallback_traces: list[dict[str, Any]] = list(context.fallback_traces or [])
        task_plan: TaskPlan | None = None

        try:
            task_plan = self.task_planner.plan(seed, context_meta, context_analysis)
            seed.tasks_count = task_plan.tasks_count
            seed.task_complexity = task_plan.complexity
            warnings.append(f"ℹ️ План практики готов: {task_plan.tasks_count} задач ({task_plan.complexity}).")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Practice plan fallback used: %s", exc)
            if not seed.tasks_count:
                seed.tasks_count = THRESHOLDS["practice_tasks_recommend"][0]
                seed.task_complexity = "medium"
            fallback_traces.append(
                FallbackTraceEvent.from_fallback(
                    node="task_planning",
                    fallback_type="default_task_plan",
                    reason=str(exc),
                    quality_risk="medium",
                    visible_to_user=True,
                    inputs={
                        "title_seed": getattr(seed, "title_seed", None),
                        "tasks_count": getattr(seed, "tasks_count", None),
                        "task_complexity": getattr(seed, "task_complexity", None),
                    },
                    trace={
                        "resolved_tasks_count": getattr(seed, "tasks_count", None),
                        "resolved_task_complexity": getattr(seed, "task_complexity", None),
                    },
                ).model_dump(mode="json")
            )
            warnings.append("⚠️ Использован дефолтный план практики")

        story_map_contract = None
        practice_plan_contract = None
        artifact_chain_plan = None
        evidence_specs: list[Any] = []
        try:
            story_map_contract, practice_plan_contract, artifact_chain_plan = self.project_blueprint_planner.build(
                seed,
                task_plan,
                context_meta,
                context.context_bundle,
            )
            evidence_specs = list(getattr(artifact_chain_plan, "evidence_specs", []) or [])
            self._sync_runtime_contract_state(
                story_map_contract,
                practice_plan_contract,
                artifact_chain_plan,
                evidence_specs,
            )
            warnings.append("ℹ️ Контракт учебной деятельности готов до генерации теории.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PracticePlanContract fallback skipped: %s", exc)
            fallback_traces.append(
                FallbackTraceEvent.from_fallback(
                    node="task_planning",
                    fallback_type="practice_plan_contract_unavailable",
                    reason=str(exc),
                    quality_risk="medium",
                    visible_to_user=True,
                    inputs={
                        "title_seed": getattr(seed, "title_seed", None),
                        "task_plan": getattr(task_plan, "as_dict", lambda: None)(),
                    },
                    trace={"contract_builder": type(self.project_blueprint_planner).__name__},
                ).model_dump(mode="json")
            )
            warnings.append(f"⚠️ Не удалось построить PracticePlanContract: {exc}")

        return TaskPlanningNodeResult(
            seed=seed,
            task_plan=task_plan,
            story_map_contract=story_map_contract,
            practice_plan_contract=practice_plan_contract,
            artifact_chain_plan=artifact_chain_plan,
            evidence_specs=evidence_specs,
            warnings=warnings,
            fallback_traces=fallback_traces,
            issues=warnings,
        )

    def _sync_runtime_contract_state(
        self,
        story_map_contract: Any,
        practice_plan_contract: Any,
        artifact_chain_plan: Any,
        evidence_specs: list[Any],
    ) -> None:
        if self.runtime_state is None:
            return
        self.runtime_state.story_map_contract = story_map_contract
        self.runtime_state.practice_plan_contract = practice_plan_contract
        self.runtime_state.artifact_chain_plan = artifact_chain_plan
        self.runtime_state.evidence_specs = evidence_specs


class QualityNodeService:
    """Run the global quality pass over the generated README."""

    def __init__(self, improve_quality: Callable[..., QualityPhaseResult], runtime_state: Any | None = None) -> None:
        self.improve_quality = improve_quality
        self.runtime_state = runtime_state

    def execute(self, context: GenerationContext) -> QualityNodeResult:
        seed = context.require("seed")
        markdown = context.require("markdown")
        source_document = ReadmeDocument.from_value(context.readme_document, fallback_markdown=markdown)
        result = self.improve_quality(
            seed,
            markdown,
            readme_document=source_document,
            story_map_contract=context.story_map_contract,
        )
        return QualityNodeResult(
            markdown=result.markdown,
            readme_document=result.readme_document,
            fallback_traces=self._merged_fallback_traces(context),
        )

    def _merged_fallback_traces(self, context: GenerationContext) -> list[dict[str, Any]]:
        """Merge incoming and runtime fallback traces without duplicating events."""
        traces = [
            normalize_fallback_trace_event(event).model_dump(mode="json")
            for event in list(context.fallback_traces or [])
        ]
        for event in list(_runtime_attr(self.runtime_state, "fallback_traces", []) or []):
            normalized = normalize_fallback_trace_event(event).model_dump(mode="json")
            if normalized not in traces:
                traces.append(normalized)
        return traces


class EvaluationNodeService:
    """Run final rubric evaluation and expose serialized issues."""

    def __init__(
        self,
        evaluate: Callable[..., EvaluationPhaseResult],
        serialize_issues: IssueSerializer,
    ) -> None:
        self.evaluate = evaluate
        self.serialize_issues = serialize_issues

    def execute(self, context: GenerationContext) -> EvaluationNodeResult:
        seed = context.require("seed")
        markdown = context.require("markdown")
        source_document = ReadmeDocument.from_value(context.readme_document, fallback_markdown=markdown)
        result = self._call_evaluation(seed, markdown, source_document)
        return EvaluationNodeResult(
            rubric_json=result.rubric_json,
            serialized_issues=self.serialize_issues(result.issues),
        )

    def _call_evaluation(
        self,
        seed: Any,
        markdown: str,
        readme_document: ReadmeDocument,
    ) -> EvaluationPhaseResult:
        """Call typed evaluation implementations."""
        return self.evaluate(seed, markdown, readme_document=readme_document)


class TranslationNodeService:
    """Translate the final README when the target language requires it."""

    def __init__(self, translate: Callable[..., TranslationPhaseResult]) -> None:
        self.translate = translate

    def execute(self, context: GenerationContext, target_language: str | None = None) -> TranslationNodeResult:
        seed = context.require("seed")
        markdown = context.require("markdown")
        raw_target_language = target_language or context.target_language
        fallback_traces = list(context.fallback_traces or [])
        if raw_target_language is None:
            fallback_traces.append(
                FallbackTraceEvent.from_fallback(
                    node="translation",
                    fallback_type="missing_target_language",
                    reason="target_language is absent; using ru",
                    quality_risk="low",
                    visible_to_user=True,
                    inputs={
                        "seed_language": getattr(seed, "language", None),
                        "title_seed": getattr(seed, "title_seed", None),
                    },
                    trace={"resolved_target_language": "ru"},
                ).model_dump(mode="json")
            )
        resolved_language = self._normalize_target_language(raw_target_language, seed)
        source_document = ReadmeDocument.from_value(context.readme_document, fallback_markdown=markdown)
        result = self._call_translation(seed, markdown, resolved_language, source_document)
        return TranslationNodeResult(
            markdown=result.markdown,
            translated_markdown=result.translated_markdown,
            seed=seed,
            target_language=resolved_language,
            readme_document=result.readme_document,
            fallback_traces=fallback_traces,
        )

    def _call_translation(
        self,
        seed: Any,
        markdown: str,
        target_language: str,
        readme_document: ReadmeDocument,
    ) -> TranslationPhaseResult:
        """Call typed translation implementations."""
        return self.translate(seed, markdown, target_language, readme_document=readme_document)

    @staticmethod
    def _normalize_target_language(target_language: Any, seed: Any) -> str:
        if target_language is None:
            logger.error(
                "target_language is absent in context; seed.language=%r. Using 'ru' fallback.",
                getattr(seed, "language", None),
            )
            return "ru"
        if not isinstance(target_language, str):
            target_language = str(target_language)
        return target_language.lower().strip()


class PracticeNodeService:
    """Generate practice tasks and synchronize flow/runtime artifacts."""

    def __init__(
        self,
        generate_practice: Callable[..., PracticePhaseResult],
        section_context_recorder: SectionContextRecorder,
        serialize_issues: IssueSerializer,
        has_hard_issues: IssuePredicate,
        issue_messages: IssueMessages,
        runtime_state: Any | None = None,
    ) -> None:
        self.generate_practice = generate_practice
        self.section_context_recorder = section_context_recorder
        self.serialize_issues = serialize_issues
        self.has_hard_issues = has_hard_issues
        self.issue_messages = issue_messages
        self.runtime_state = runtime_state

    def execute(self, context: GenerationContext, flow_context: dict[str, Any]) -> PracticeNodeResult:
        seed = context.require("seed")
        markdown = context.require("markdown")
        self._hydrate_contracts(flow_context)
        filtered_context = self.section_context_recorder.record(flow_context, SectionContextPolicy.for_practice())
        phase_result = self.generate_practice(
            seed,
            markdown,
            context.generate_bonus,
            practice_plan_contract=flow_context.get("practice_plan_contract"),
            artifact_chain_plan=flow_context.get("artifact_chain_plan"),
            section_context=filtered_context,
        )
        md = phase_result.markdown
        readme_document = phase_result.readme_document
        practice_tasks = phase_result.practice_tasks
        bonus_tasks = list(getattr(phase_result, "bonus_tasks", []) or [])
        practice_issues = phase_result.issues
        practice_warnings = phase_result.warnings

        artifact_chain_plan = phase_result.artifact_chain_plan or _runtime_attr(
            self.runtime_state,
            "artifact_chain_plan",
            context.artifact_chain_plan,
        )
        evidence_specs = list(
            phase_result.evidence_specs
            or _runtime_attr(self.runtime_state, "evidence_specs", context.evidence_specs)
            or []
        )
        dataset_files = list(phase_result.dataset_files or _runtime_attr(self.runtime_state, "dataset_files", []) or [])
        blueprint = self._update_blueprint_task_maps(context.blueprint, practice_tasks)
        serialized_issues = self.serialize_issues(practice_issues)
        practice_warnings = _non_blocking_quality_warnings(
            label="PracticeChecks",
            issues=practice_issues,
            existing_warnings=list(practice_warnings or []),
            issue_messages=self.issue_messages,
        )

        flow_context.update(
            {
                "artifact_chain_plan": artifact_chain_plan,
                "evidence_specs": evidence_specs,
                "dataset_files": dataset_files,
                "practice_tasks": practice_tasks,
                "bonus_tasks": bonus_tasks,
            }
        )
        _merge_runtime_fallback_traces(flow_context, self.runtime_state)
        self.section_context_recorder.record(flow_context, SectionContextPolicy.for_practice())
        self.section_context_recorder.record(flow_context, SectionContextPolicy.for_dataset())
        flow_context.setdefault("issues", []).extend(serialized_issues)
        flow_context.setdefault("warnings", []).extend(practice_warnings)

        return PracticeNodeResult(
            markdown=md,
            readme_document=readme_document or ReadmeDocument.from_markdown(md),
            practice_critic_issues=list(
                phase_result.practice_critic_issues
                or _runtime_attr(self.runtime_state, "practice_critic_issues", [])
                or []
            ),
            practice_tasks=list(practice_tasks or []),
            bonus_tasks=bonus_tasks,
            blueprint=blueprint,
            artifact_chain_plan=artifact_chain_plan,
            evidence_specs=evidence_specs,
            dataset_files=dataset_files,
            section_contexts=flow_context.get("section_contexts", {}),
            warnings=list(practice_warnings or []),
            serialized_issues=serialized_issues,
            issues=list(practice_warnings or []),
            status="success",
        )

    def _hydrate_contracts(self, flow_context: dict[str, Any]) -> None:
        flow_context.setdefault("story_map_contract", _runtime_attr(self.runtime_state, "story_map_contract", None))
        flow_context.setdefault(
            "practice_plan_contract",
            _runtime_attr(self.runtime_state, "practice_plan_contract", None),
        )
        flow_context.setdefault("artifact_chain_plan", _runtime_attr(self.runtime_state, "artifact_chain_plan", None))
        flow_context.setdefault("evidence_specs", list(_runtime_attr(self.runtime_state, "evidence_specs", []) or []))

    @staticmethod
    def _update_blueprint_task_maps(blueprint: Any | None, practice_tasks: list[Any]) -> Any | None:
        if blueprint is None:
            return None
        lo_task_map: dict[str, list[int]] = {}
        theory_task_map: dict[str, list[int]] = {}
        for idx, task in enumerate(practice_tasks, 1):
            for outcome in getattr(task, "covered_outcomes", []) or []:
                lo_task_map.setdefault(outcome, []).append(idx)
            for topic in getattr(task, "theory_support", []) or []:
                theory_task_map.setdefault(topic, []).append(idx)
        blueprint.lo_task_map = lo_task_map
        blueprint.theory_task_map = theory_task_map
        return blueprint


class FinalizeNodeService:
    """Assemble the final orchestrator result through a typed node service."""

    def __init__(
        self,
        result_assembler: Any,
        section_context_recorder: SectionContextRecorder,
        runtime_state: Any | None = None,
    ) -> None:
        self.result_assembler = result_assembler
        self.section_context_recorder = section_context_recorder
        self.runtime_state = runtime_state

    def execute(self, flow_context: dict[str, Any]) -> FinalizeNodeResult:
        dataset_files = list(flow_context.get("dataset_files") or _runtime_attr(self.runtime_state, "dataset_files", []) or [])
        flow_context["dataset_files"] = dataset_files
        self.section_context_recorder.record(flow_context, SectionContextPolicy.for_finalize())
        finalized = self.result_assembler.assemble(
            flow_context,
            dataset_files=dataset_files,
        )
        result = FinalizeNodeResult(
            result=finalized.result,
            project_spec=finalized.project_spec,
            markdown=finalized.markdown,
            readme_document=self._readme_document_from_finalized(finalized, flow_context),
            translated_markdown=finalized.translated_markdown,
            assets_binary=finalized.assets_binary,
            section_contexts=flow_context.get("section_contexts", {}),
            issues=list(finalized.step_warnings or []),
        )
        flow_context.update(result.updates())
        return result

    @staticmethod
    def _readme_document_from_finalized(finalized: Any, flow_context: dict[str, Any]) -> ReadmeDocument:
        """Use typed final assembly output and keep simple test doubles compatible."""
        return ReadmeDocument.from_value(
            getattr(finalized, "readme_document", None) or flow_context.get("readme_document"),
            fallback_markdown=getattr(finalized, "markdown", ""),
        )


class SkeletonNodeService:
    """Generate the README structure after title/annotation approval."""

    def __init__(
        self,
        build_skeleton: Callable[..., SkeletonPhaseResult],
        build_structure: Callable[..., StructurePhaseResult],
        serialize_issues: IssueSerializer,
        has_hard_issues: IssuePredicate,
        issue_messages: IssueMessages,
        json_safe: JsonSafe,
    ) -> None:
        self.build_skeleton = build_skeleton
        self.build_structure = build_structure
        self.serialize_issues = serialize_issues
        self.has_hard_issues = has_hard_issues
        self.issue_messages = issue_messages
        self.json_safe = json_safe

    def execute(self, context: GenerationContext) -> SkeletonNodeResult:
        seed = context.require("seed")
        context_meta = context.require("context_meta")
        title = context.title
        annotation = context.annotation

        if title and annotation:
            structure_result = self.build_structure(
                seed,
                context_meta,
                context.generate_bonus,
                str(title),
                annotation,
            )
        else:
            skeleton_result = self.build_skeleton(
                seed,
                context_meta,
                context.generate_bonus,
            )
            title = skeleton_result.title
            annotation = skeleton_result.annotation
            structure_result = StructurePhaseResult(
                markdown=skeleton_result.markdown,
                preflight_result=skeleton_result.preflight_result,
                intro_section=skeleton_result.intro_section,
                blueprint=skeleton_result.blueprint,
            )

        md = structure_result.markdown
        preflight_result = structure_result.preflight_result
        intro_section = structure_result.intro_section
        blueprint = structure_result.blueprint

        warnings: list[str] = []
        serialized_issues: list[Any] = []
        status = "success"
        if not preflight_result.passed:
            warn = f"⚠️ Structural Preflight: {len(preflight_result.hard_issues)} HARD проблем"
            warnings.append(warn)
            serialized_issues.extend(self.serialize_issues(preflight_result.hard_issues))
            status = "error"

        if blueprint is not None:
            if context.story_map_contract is not None:
                blueprint.story_map_contract = self.json_safe(context.story_map_contract)
            if context.practice_plan_contract is not None:
                blueprint.practice_plan_contract = self.json_safe(context.practice_plan_contract)

        flow_issues = self.issue_messages(preflight_result.hard_issues) if status == "error" else warnings
        return SkeletonNodeResult(
            markdown=md,
            readme_document=ReadmeDocument.from_markdown(md),
            title=str(title),
            annotation=annotation,
            intro_section=intro_section,
            blueprint=blueprint,
            warnings=warnings,
            serialized_issues=serialized_issues,
            issues=flow_issues,
            status=status,
        )


class TheoryNodeService:
    """Generate Chapter 2 theory with section-scoped context and typed output."""

    def __init__(
        self,
        generate_theory: Callable[..., TheoryPhaseResult],
        section_context_recorder: SectionContextRecorder,
        serialize_issues: IssueSerializer,
        has_hard_issues: IssuePredicate,
        issue_messages: IssueMessages,
        runtime_state: Any | None = None,
    ) -> None:
        self.generate_theory = generate_theory
        self.section_context_recorder = section_context_recorder
        self.serialize_issues = serialize_issues
        self.has_hard_issues = has_hard_issues
        self.issue_messages = issue_messages
        self.runtime_state = runtime_state

    def execute(self, context: GenerationContext, flow_context: dict[str, Any]) -> TheoryNodeResult:
        seed = context.require("seed")
        context_meta = context.require("context_meta")
        markdown = context.require("markdown")
        filtered_context = self.section_context_recorder.record(flow_context, SectionContextPolicy.for_theory())
        phase_result = self.generate_theory(
            seed,
            context_meta,
            markdown,
            practice_plan_contract=context.practice_plan_contract,
            section_context=filtered_context,
        )
        md = phase_result.markdown
        readme_document = phase_result.readme_document
        theory_parts = phase_result.theory_parts
        theory_issues = phase_result.issues
        theory_warnings = phase_result.warnings

        serialized_issues = self.serialize_issues(theory_issues)
        theory_warnings = _non_blocking_quality_warnings(
            label="TheoryChecks",
            issues=theory_issues,
            existing_warnings=list(theory_warnings or []),
            issue_messages=self.issue_messages,
        )
        flow_issues = list(theory_warnings)
        _merge_runtime_fallback_traces(flow_context, self.runtime_state)

        return TheoryNodeResult(
            markdown=md,
            readme_document=readme_document or ReadmeDocument.from_markdown(md),
            theory_parts=list(theory_parts or []),
            warnings=list(theory_warnings or []),
            serialized_issues=serialized_issues,
            issues=flow_issues,
            status="success",
        )
