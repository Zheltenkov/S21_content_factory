from types import SimpleNamespace

from content_gen.agents.flow import FlowNodeOutput
from content_gen.domain_contracts import SectionContextPolicy
from content_gen.flow_handlers import GenerationFlowHandlers
from content_gen.models.generation_context import ContextNodeResult, GenerationContext
from content_gen.models.phase_results import (
    ContextPhaseResult,
    EvaluationPhaseResult,
    PracticePhaseResult,
    QualityPhaseResult,
    TitleAnnotationPhaseResult,
    TheoryPhaseResult,
    TranslationPhaseResult,
)
from content_gen.models.readme_document import ReadmeDocument
from content_gen.node_services import (
    ContextNodeService,
    EvaluationNodeService,
    FinalizeNodeService,
    PracticeNodeService,
    QualityNodeService,
    SectionContextRecorder,
    TaskPlanningNodeService,
    TheoryNodeService,
    TitleAnnotationNodeService,
    TranslationNodeService,
)


def test_generation_context_from_flow_context_excludes_state() -> None:
    state = object()
    context = GenerationContext.from_flow_context(
        {
            "raw_input": {"language": "RU"},
            "track_files": ["track.xlsx"],
            "warnings": ["w"],
            "state": state,
        }
    )

    assert context.raw_input == {"language": "RU"}
    assert context.track_files == ["track.xlsx"]
    assert context.warnings == ["w"]
    assert not hasattr(context, "state")


def test_context_node_service_returns_typed_updates() -> None:
    seed = SimpleNamespace(bonus_wish=None)
    context_meta = SimpleNamespace(search_metrics={"previous_projects_count": 2, "reference_enabled": True})
    context_analysis = SimpleNamespace()
    context_bundle = SimpleNamespace()

    def build_context(raw_input, track_files):
        assert raw_input == {"language": "RU"}
        assert track_files == ["track.xlsx"]
        return ContextPhaseResult(
            seed=seed,
            context_meta=context_meta,
            context_analysis=context_analysis,
            context_bundle=context_bundle,
            similar_projects=["prev"],
            warnings=["warn"],
        )

    result = ContextNodeService(build_context).execute(
        GenerationContext(raw_input={"language": "RU"}, track_files=["track.xlsx"])
    )

    assert result.target_language == "ru"
    assert result.generate_bonus is False
    assert result.warnings == ["warn"]
    assert result.issues == ["warn"]
    assert result.updates()["seed"] is seed


def test_task_planning_service_syncs_runtime_contract_state() -> None:
    seed = SimpleNamespace(tasks_count=None, task_complexity=None)
    context_meta = SimpleNamespace()
    context_analysis = SimpleNamespace()
    story_map = SimpleNamespace()
    practice_plan = SimpleNamespace()
    artifact_chain = SimpleNamespace(evidence_specs=["e1"])
    runtime_state = SimpleNamespace()

    class Planner:
        def plan(self, _seed, _context_meta, _context_analysis):
            return SimpleNamespace(tasks_count=3, complexity="medium")

    class BlueprintPlanner:
        def build(self, _seed, task_plan, _context_meta, _context_bundle):
            assert task_plan.tasks_count == 3
            return story_map, practice_plan, artifact_chain

    service = TaskPlanningNodeService(Planner(), BlueprintPlanner(), runtime_state=runtime_state)
    result = service.execute(
        GenerationContext(seed=seed, context_meta=context_meta, context_analysis=context_analysis)
    )

    assert seed.tasks_count == 3
    assert seed.task_complexity == "medium"
    assert result.story_map_contract is story_map
    assert result.practice_plan_contract is practice_plan
    assert result.artifact_chain_plan is artifact_chain
    assert result.evidence_specs == ["e1"]
    assert runtime_state.story_map_contract is story_map
    assert runtime_state.practice_plan_contract is practice_plan
    assert runtime_state.artifact_chain_plan is artifact_chain


def test_task_planning_service_records_fallback_trace_when_planner_fails() -> None:
    seed = SimpleNamespace(tasks_count=None, task_complexity=None, title_seed="Проект")
    context_meta = SimpleNamespace()
    context_analysis = SimpleNamespace()

    class Planner:
        def plan(self, _seed, _context_meta, _context_analysis):
            raise RuntimeError("planner unavailable")

    class BlueprintPlanner:
        def build(self, _seed, _task_plan, _context_meta, _context_bundle):
            raise RuntimeError("contract unavailable")

    service = TaskPlanningNodeService(Planner(), BlueprintPlanner())
    result = service.execute(
        GenerationContext(seed=seed, context_meta=context_meta, context_analysis=context_analysis)
    )

    assert seed.tasks_count is not None
    assert result.fallback_traces[0]["fallback_type"] == "default_task_plan"
    assert result.fallback_traces[0]["quality_risk"] == "medium"
    assert result.fallback_traces[0]["visible_to_user"] is True
    assert result.fallback_traces[1]["fallback_type"] == "practice_plan_contract_unavailable"
    assert result.fallback_traces[1]["visible_to_user"] is True
    assert result.updates()["fallback_traces"] == result.fallback_traces


def test_title_annotation_service_returns_typed_updates() -> None:
    seed = SimpleNamespace()
    context_meta = SimpleNamespace()
    annotation = SimpleNamespace(summary="short")

    def build_title_annotation(_seed, _context_meta):
        return TitleAnnotationPhaseResult(title="Название", annotation=annotation)

    result = TitleAnnotationNodeService(build_title_annotation).execute(
        GenerationContext(seed=seed, context_meta=context_meta)
    )

    assert result.title == "Название"
    assert result.annotation is annotation
    assert result.updates() == {"title": "Название", "annotation": annotation}


def test_quality_service_wraps_markdown_update() -> None:
    seed = SimpleNamespace()

    def improve_quality(_seed, markdown, readme_document, story_map_contract):
        assert story_map_contract is None
        updated_markdown = markdown + "\nquality"
        return QualityPhaseResult(
            markdown=updated_markdown,
            readme_document=ReadmeDocument.from_markdown(updated_markdown),
        )

    result = QualityNodeService(improve_quality).execute(GenerationContext(seed=seed, markdown="# README"))

    assert result.markdown == "# README\nquality"
    assert result.readme_document is not None
    assert result.readme_document.to_markdown().strip() == "# README\n\nquality"
    assert result.updates()["markdown"] == "# README\nquality"
    assert result.updates()["readme_document"] is result.readme_document


def test_quality_service_merges_runtime_fallback_traces() -> None:
    seed = SimpleNamespace()
    runtime_state = SimpleNamespace(
        fallback_traces=[{"node": "quality", "fallback_type": "style_guard_markdown_boundary"}]
    )

    def improve_quality(_seed, markdown, readme_document, story_map_contract):
        return QualityPhaseResult(markdown=markdown, readme_document=readme_document)

    result = QualityNodeService(improve_quality, runtime_state=runtime_state).execute(
        GenerationContext(
            seed=seed,
            markdown="# README",
            fallback_traces=[{"node": "task_planning", "fallback_type": "default_task_plan"}],
        )
    )

    assert [(event["node"], event["fallback_type"]) for event in result.fallback_traces] == [
        ("task_planning", "default_task_plan"),
        ("quality", "style_guard_markdown_boundary"),
    ]
    assert all(event["trace_id"] for event in result.fallback_traces)
    assert all("visible_to_user" in event for event in result.fallback_traces)
    assert result.updates()["fallback_traces"] == result.fallback_traces


def test_quality_service_uses_typed_quality_result() -> None:
    seed = SimpleNamespace()
    readme_document = ReadmeDocument.from_markdown("# README\n\n## Заключение\n\nDone.")

    def improve_quality(_seed, markdown, readme_document, story_map_contract):
        assert markdown == "# README"
        assert readme_document.title == "README"
        assert story_map_contract == {"completion": "done"}
        return QualityPhaseResult(markdown=readme_document.to_markdown(), readme_document=readme_document)

    result = QualityNodeService(improve_quality).execute(
        GenerationContext(
            seed=seed,
            markdown="# README",
            readme_document=readme_document,
            story_map_contract={"completion": "done"},
        )
    )

    assert result.readme_document is readme_document
    assert result.markdown == readme_document.to_markdown()


def test_evaluation_service_serializes_issues() -> None:
    seed = SimpleNamespace()

    class Issue:
        def __init__(self) -> None:
            self.severity = "soft"
            self.message = "warning"

    def evaluate(_seed, markdown, readme_document):
        assert markdown == "# README"
        assert readme_document.title == "README"
        return EvaluationPhaseResult(
            rubric_json={"passed": True},
            issues=[Issue()],
            readme_document=readme_document,
        )

    result = EvaluationNodeService(evaluate, lambda issues: [issue.__dict__ for issue in issues]).execute(
        GenerationContext(seed=seed, markdown="# README")
    )

    assert result.rubric_json == {"passed": True}
    assert result.serialized_issues == [{"severity": "soft", "message": "warning"}]
    assert result.updates() == {"rubric_json": {"passed": True}}


def test_evaluation_service_accepts_typed_phase_result() -> None:
    seed = SimpleNamespace()
    readme_document = ReadmeDocument.from_markdown("# README\n\nBody.")

    def evaluate(_seed, markdown, readme_document):
        assert markdown == "# README"
        assert readme_document.title == "README"
        return EvaluationPhaseResult(
            rubric_json={"passed": True},
            issues=["typed issue"],
            readme_document=readme_document,
        )

    result = EvaluationNodeService(evaluate, lambda issues: list(issues)).execute(
        GenerationContext(seed=seed, markdown="# README", readme_document=readme_document)
    )

    assert result.rubric_json == {"passed": True}
    assert result.serialized_issues == ["typed issue"]


def test_translation_service_normalizes_target_language() -> None:
    seed = SimpleNamespace(language="EN")

    def translate(_seed, markdown, target_language, readme_document):
        assert markdown == "# README"
        assert target_language == "en"
        assert readme_document.title == "README"
        return TranslationPhaseResult(
            markdown=markdown,
            translated_markdown="# TRANSLATED",
            readme_document=readme_document,
            translated_readme_document=ReadmeDocument.from_markdown("# TRANSLATED"),
        )

    result = TranslationNodeService(translate).execute(
        GenerationContext(seed=seed, markdown="# README", target_language=" EN ")
    )

    assert result.target_language == "en"
    assert result.updates()["translated_markdown"] == "# TRANSLATED"


def test_translation_service_accepts_typed_phase_result() -> None:
    seed = SimpleNamespace(language="RU")
    readme_document = ReadmeDocument.from_markdown("# README\n\nBody.")

    def translate(_seed, markdown, target_language, readme_document):
        assert markdown == "# README"
        assert target_language == "en"
        return TranslationPhaseResult(
            markdown=markdown,
            translated_markdown="# TRANSLATED",
            readme_document=readme_document,
            translated_readme_document=ReadmeDocument.from_markdown("# TRANSLATED"),
        )

    result = TranslationNodeService(translate).execute(
        GenerationContext(seed=seed, markdown="# README", target_language="en", readme_document=readme_document)
    )

    assert result.readme_document is readme_document
    assert result.translated_markdown == "# TRANSLATED"


def test_translation_service_records_missing_target_language_fallback() -> None:
    seed = SimpleNamespace(language="RU", title_seed="Проект")

    def translate(_seed, markdown, target_language, readme_document):
        assert target_language == "ru"
        return TranslationPhaseResult(
            markdown=markdown,
            translated_markdown=markdown,
            readme_document=readme_document,
            translated_readme_document=readme_document,
        )

    result = TranslationNodeService(translate).execute(
        GenerationContext(seed=seed, markdown="# README")
    )

    assert result.target_language == "ru"
    assert result.fallback_traces[0]["node"] == "translation"
    assert result.fallback_traces[0]["fallback_type"] == "missing_target_language"
    assert result.fallback_traces[0]["visible_to_user"] is True
    assert result.fallback_traces[0]["trace_id"]
    assert result.updates()["fallback_traces"] == result.fallback_traces


def test_practice_service_uses_runtime_state_side_effects() -> None:
    seed = SimpleNamespace(
        title_seed="Проект",
        project_description="Описание",
        learning_outcomes=["LO1"],
        skills=["Skill"],
        required_tools=[],
        curriculum_context={},
    )
    blueprint = SimpleNamespace()
    practice_plan = SimpleNamespace()
    artifact_chain = SimpleNamespace(evidence_specs=["e1"])
    runtime_state = SimpleNamespace(
        story_map_contract=SimpleNamespace(),
        practice_plan_contract=practice_plan,
        artifact_chain_plan=artifact_chain,
        evidence_specs=["e1"],
        dataset_files=[{"path": "data.csv"}],
        practice_critic_issues=[{"message": "critic"}],
        fallback_traces=[{"node": "practice", "fallback_type": "practice_critic_json_object_recovery"}],
    )
    task = SimpleNamespace(covered_outcomes=["LO1"], theory_support=["2.1"])

    class Issue:
        def __init__(self) -> None:
            self.severity = "soft"
            self.message = "practice note"

    def generate_practice(_seed, markdown, _generate_bonus, practice_plan_contract, artifact_chain_plan, section_context):
        assert markdown == "# README"
        assert practice_plan_contract is practice_plan
        assert artifact_chain_plan is artifact_chain
        assert "project_description" in section_context
        updated_markdown = "# README\npractice"
        return PracticePhaseResult(
            markdown=updated_markdown,
            readme_document=ReadmeDocument.from_markdown(updated_markdown),
            practice_tasks=[task],
            issues=[Issue()],
            warnings=["warn"],
        )

    service = PracticeNodeService(
        generate_practice,
        SectionContextRecorder(),
        lambda issues: [issue.__dict__ for issue in issues],
        lambda _issues: False,
        lambda issues: [issue.message for issue in issues],
        runtime_state=runtime_state,
    )
    flow_context = {"seed": seed, "markdown": "# README", "issues": [], "warnings": []}
    result = service.execute(
        GenerationContext(seed=seed, markdown="# README", blueprint=blueprint),
        flow_context,
    )

    assert result.markdown == "# README\npractice"
    assert result.practice_tasks == [task]
    assert result.dataset_files == [{"path": "data.csv"}]
    assert result.practice_critic_issues == [{"message": "critic"}]
    assert blueprint.lo_task_map == {"LO1": [1]}
    assert blueprint.theory_task_map == {"2.1": [1]}
    assert flow_context["issues"] == [{"severity": "soft", "message": "practice note"}]
    assert flow_context["warnings"] == ["warn"]
    assert flow_context["fallback_traces"][0]["node"] == "practice"
    assert flow_context["fallback_traces"][0]["fallback_type"] == "practice_critic_json_object_recovery"
    assert flow_context["fallback_traces"][0]["trace_id"]
    assert "practice" in result.section_contexts
    assert "dataset" in result.section_contexts


def test_theory_service_uses_typed_phase_readme_document() -> None:
    seed = SimpleNamespace(
        title_seed="Проект",
        project_description="Описание",
        learning_outcomes=["LO1"],
        skills=["Skill"],
        required_tools=[],
        curriculum_context={},
    )
    context_meta = SimpleNamespace()
    readme_document = ReadmeDocument.from_markdown("# README\n\n## Глава 2. Теория\n\nTyped theory.")
    part = SimpleNamespace(title="Theory")

    def generate_theory(_seed, _context_meta, markdown, practice_plan_contract, section_context):
        assert markdown == "# README"
        assert practice_plan_contract == {"plan": True}
        assert "project_description" in section_context
        return TheoryPhaseResult(
            markdown=readme_document.to_markdown(),
            readme_document=readme_document,
            theory_parts=[part],
            issues=[],
            warnings=["typed theory"],
        )

    service = TheoryNodeService(
        generate_theory,
        SectionContextRecorder(),
        lambda issues: [str(issue) for issue in issues],
        lambda _issues: False,
        lambda issues: [str(issue) for issue in issues],
    )
    flow_context = {"seed": seed, "context_meta": context_meta, "markdown": "# README", "warnings": [], "issues": []}

    result = service.execute(
        GenerationContext(
            seed=seed,
            context_meta=context_meta,
            markdown="# README",
            practice_plan_contract={"plan": True},
        ),
        flow_context,
    )

    assert result.readme_document is readme_document
    assert result.theory_parts == [part]
    assert result.warnings == ["typed theory"]


def test_theory_service_keeps_quality_issues_non_blocking() -> None:
    seed = SimpleNamespace(
        title_seed="Проект",
        project_description="Описание",
        learning_outcomes=["LO1"],
        skills=["Skill"],
        required_tools=[],
        curriculum_context={},
    )
    context_meta = SimpleNamespace()
    readme_document = ReadmeDocument.from_markdown("# README\n\n## Глава 2. Теория\n\nTyped theory.")
    part = SimpleNamespace(title="Theory")

    class Issue:
        def __init__(self) -> None:
            self.severity = "hard"
            self.message = "Раздел 2.3: длина 313 слов (ожидается 110-312)"

    def generate_theory(_seed, _context_meta, markdown, practice_plan_contract, section_context):
        return TheoryPhaseResult(
            markdown=readme_document.to_markdown(),
            readme_document=readme_document,
            theory_parts=[part],
            issues=[Issue()],
            warnings=[],
        )

    service = TheoryNodeService(
        generate_theory,
        SectionContextRecorder(),
        lambda issues: [issue.__dict__ for issue in issues],
        lambda _issues: True,
        lambda issues: [issue.message for issue in issues],
    )

    result = service.execute(
        GenerationContext(seed=seed, context_meta=context_meta, markdown="# README"),
        {"seed": seed, "context_meta": context_meta, "markdown": "# README", "warnings": [], "issues": []},
    )

    assert result.status == "success"
    assert result.serialized_issues == [{"severity": "hard", "message": "Раздел 2.3: длина 313 слов (ожидается 110-312)"}]
    assert any("Генерация продолжена" in warning for warning in result.warnings)
    assert result.issues == result.warnings


def test_practice_service_uses_typed_phase_readme_document() -> None:
    seed = SimpleNamespace(
        title_seed="Проект",
        project_description="Описание",
        learning_outcomes=["LO1"],
        skills=["Skill"],
        required_tools=[],
        curriculum_context={},
    )
    readme_document = ReadmeDocument.from_markdown("# README\n\n## Глава 3. Практика\n\nTyped practice.")
    task = SimpleNamespace(covered_outcomes=[], theory_support=[])

    def generate_practice(_seed, markdown, _generate_bonus, practice_plan_contract, artifact_chain_plan, section_context):
        assert markdown == "# README"
        assert practice_plan_contract == {"plan": True}
        assert artifact_chain_plan == {"chain": True}
        assert "project_description" in section_context
        return PracticePhaseResult(
            markdown=readme_document.to_markdown(),
            readme_document=readme_document,
            practice_tasks=[task],
            issues=[],
            warnings=["typed practice"],
            artifact_chain_plan={"chain": "updated"},
            evidence_specs=["evidence"],
            dataset_files=[{"path": "typed.csv"}],
            practice_critic_issues=[{"message": "typed critic"}],
        )

    service = PracticeNodeService(
        generate_practice,
        SectionContextRecorder(),
        lambda issues: [str(issue) for issue in issues],
        lambda _issues: False,
        lambda issues: [str(issue) for issue in issues],
    )
    flow_context = {
        "seed": seed,
        "markdown": "# README",
        "practice_plan_contract": {"plan": True},
        "artifact_chain_plan": {"chain": True},
        "issues": [],
        "warnings": [],
    }

    result = service.execute(
        GenerationContext(seed=seed, markdown="# README", generate_bonus=False),
        flow_context,
    )

    assert result.readme_document is readme_document
    assert result.practice_tasks == [task]
    assert result.warnings == ["typed practice"]
    assert result.artifact_chain_plan == {"chain": "updated"}
    assert result.evidence_specs == ["evidence"]
    assert result.dataset_files == [{"path": "typed.csv"}]
    assert result.practice_critic_issues == [{"message": "typed critic"}]


def test_practice_service_keeps_quality_issues_non_blocking() -> None:
    seed = SimpleNamespace(
        title_seed="Проект",
        project_description="Описание",
        learning_outcomes=["LO1"],
        skills=["Skill"],
        required_tools=[],
        curriculum_context={},
    )
    readme_document = ReadmeDocument.from_markdown("# README\n\n## Глава 3. Практика\n\nTyped practice.")

    class Issue:
        def __init__(self) -> None:
            self.severity = "hard"
            self.message = "Задание 1: не хватает ожидаемого результата"

    def generate_practice(_seed, markdown, _generate_bonus, practice_plan_contract, artifact_chain_plan, section_context):
        return PracticePhaseResult(
            markdown=readme_document.to_markdown(),
            readme_document=readme_document,
            practice_tasks=[],
            issues=[Issue()],
            warnings=[],
        )

    service = PracticeNodeService(
        generate_practice,
        SectionContextRecorder(),
        lambda issues: [issue.__dict__ for issue in issues],
        lambda _issues: True,
        lambda issues: [issue.message for issue in issues],
    )
    flow_context = {"seed": seed, "markdown": "# README", "issues": [], "warnings": []}

    result = service.execute(GenerationContext(seed=seed, markdown="# README"), flow_context)

    assert result.status == "success"
    assert flow_context["issues"] == [{"severity": "hard", "message": "Задание 1: не хватает ожидаемого результата"}]
    assert any("Генерация продолжена" in warning for warning in flow_context["warnings"])
    assert result.issues == result.warnings


def test_finalize_service_prefers_resumed_context_dataset_files() -> None:
    captured = {}

    class Finalized:
        result = object()
        project_spec = object()
        markdown = "# final"
        readme_document = ReadmeDocument.from_markdown("# final")
        translated_markdown = None
        assets_binary = {}
        step_warnings = ["final warn"]

    class ResultAssembler:
        def assemble(self, context, dataset_files):
            captured["context"] = context
            captured["dataset_files"] = dataset_files
            return Finalized()

    runtime_state = SimpleNamespace(dataset_files=[{"path": "stale.csv"}])
    dataset_files = [{"path": "resumed.csv"}]
    context = {
        "seed": SimpleNamespace(
            title_seed="Проект",
            project_description="Описание",
            learning_outcomes=[],
            skills=[],
            required_tools=[],
            curriculum_context={},
        ),
        "dataset_files": dataset_files,
        "section_contexts": {},
    }

    result = FinalizeNodeService(ResultAssembler(), SectionContextRecorder(), runtime_state=runtime_state).execute(context)

    assert captured["dataset_files"] == dataset_files
    assert result.markdown == "# final"
    assert result.issues == ["final warn"]
    assert "finalize" in result.section_contexts
    assert context["markdown"] == "# final"


def test_flow_handler_uses_injected_context_service() -> None:
    seed = SimpleNamespace(bonus_wish=None)
    context_meta = SimpleNamespace()
    context_analysis = SimpleNamespace()
    context_bundle = SimpleNamespace()

    class Service:
        def execute(self, context):
            assert isinstance(context, GenerationContext)
            return ContextNodeResult(
                seed=seed,
                target_language="ru",
                generate_bonus=False,
                context_meta=context_meta,
                context_analysis=context_analysis,
                context_bundle=context_bundle,
                similar_projects=[],
                warnings=["typed warning"],
                issues=["typed warning"],
            )

    handlers = GenerationFlowHandlers(
        task_planner=object(),
        result_assembler=object(),
        log_phase=lambda _phase, _message: None,
        context_service=Service(),
    )
    context = {"raw_input": {"language": "ru"}, "track_files": [], "warnings": []}

    output = handlers.node_context(context)

    assert isinstance(output, FlowNodeOutput)
    assert output.updates["seed"] is seed
    assert output.issues == ["typed warning"]
    assert context["warnings"] == ["typed warning"]


def test_section_context_recorder_filters_theory_context() -> None:
    recorder = SectionContextRecorder()
    context = {
        "seed": SimpleNamespace(
            title_seed="Проект",
            project_description="Описание",
            learning_outcomes=["LO"],
            skills=["Skill"],
            required_tools=[],
            curriculum_context={"narrative_contract": {"case": "x"}},
        ),
        "context_analysis": SimpleNamespace(context_summary="summary", narrative_anchor="anchor"),
        "markdown": "# README",
        "instruction_text": "Нельзя попадать в theory context",
    }

    filtered = recorder.record(context, policy=SectionContextPolicy.for_theory())

    assert context["section_contexts"]["theory"] == filtered
    assert "project_description" in filtered
    assert "instruction_text" not in filtered
