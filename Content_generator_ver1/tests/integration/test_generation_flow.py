import pytest

from content_gen.agents.flow import FlowNodeOutput
from content_gen.agents.context_analysis import ContextAnalysisResult
from content_gen.agents.task_planner import TaskPlan
from content_gen.models.flow_state import ProjectBlueprint
from content_gen.models.schemas import (
    Annotation,
    IntroSection,
    PracticeTask,
    ProjectSeed,
    ProjectSpec,
    ProjectContextMeta,
    TheoryPart,
)
from content_gen.orchestrator import Orchestrator, OrchestratorResult
from content_gen.methodology.decision import MethodologyGateInterrupt


class DummyLLM:
    def complete(self, **kwargs):
        return "ok"


def build_seed():
    return ProjectSeed(
        language="ru",
        project_type="individual",
        thematic_block="PjM",
        audience_level="base",
        required_tools=[],
        title_seed="Test",
        project_description="Desc",
        learning_outcomes=["LO1"],
        skills=["Skill1"],
    )


def build_context_meta():
    return ProjectContextMeta(
        track="PjM",
        thematic_block="PjM",
        last_order=1,
        aligned_skills=["Skill1"],
        narrative_anchor="Anchor",
        similar_projects=[],
        search_metrics={
            "strategies": {
                "for_curriculum": {"score": 0.9},
                "for_theory": {"score": 0.8},
            }
        },
        context_summary="Summary",
        context_profiles_used={"default": {"version": "1.0"}},
        context_levels=[],
    )


def build_context_analysis():
    return ContextAnalysisResult(
        is_first_project=False,
        context_summary="ctx",
        narrative_anchor="anchor",
        similar_projects=[],
        relevant_chunks=[],
        skills_alignment={"intersection": ["Skill1"], "new": ["Skill2"]},
        learning_outcomes_alignment={"continuation": [], "new": ["LO2"]},
        tools_alignment={"used": [], "new": []},
        audience_level_match=True,
        metrics={"hits": 1},
    )


def test_orchestrator_flow_logs_versions(monkeypatch):
    seed = build_seed()
    context_meta = build_context_meta()
    context_analysis = build_context_analysis()
    task_plan = TaskPlan(
        tasks_count=3,
        complexity="medium",
        level_index=1,
        level_source="audience_only",
        rationale="rationale",
        explanation="expl",
        curriculum_context={"graph_available": True, "skills_to_prepare": ["Skill3"]},
    )
    practice_issues = [{"kind": "theory_alignment", "severity": "major"}]
    agent_versions = {"practice": "1.0.0"}

    spec = ProjectSpec(
        language=seed.language,
        project_type=seed.project_type,
        thematic_block=seed.thematic_block,
        required_tools=[],
        title="Title",
        annotation=Annotation(text="Ann", chars=3),
        intro=IntroSection(intro_text="Intro", instruction_text="Instr"),
        theory=[],
        practice=[],
        bonus=None,
        context=context_meta,
        toc_md=None,
    )

    def registry(_self):
        def intent(ctx):
            updates = {
                "seed": seed,
                "context_meta": context_meta,
                "context_analysis": context_analysis,
                "warnings": ["w1"],
                "generate_bonus": False,
            }
            return FlowNodeOutput(updates=updates)

        def planning(ctx):
            ctx["task_plan"] = task_plan
            return FlowNodeOutput()

        def title_annotation(ctx):
            ctx["title"] = "Title"
            ctx["annotation"] = Annotation(text="Ann", chars=3)
            return FlowNodeOutput()

        def skeleton(ctx):
            ctx["markdown"] = "# Title\n"
            return FlowNodeOutput()

        def identity(_):
            return FlowNodeOutput()

        def practice(ctx):
            ctx["practice_critic_issues"] = practice_issues
            return FlowNodeOutput()

        def evaluation(ctx):
            ctx["rubric_json"] = {"score": 5}
            return FlowNodeOutput()

        def finalize(ctx):
            result = OrchestratorResult(
                spec=spec,
                warnings=ctx.get("warnings", []),
                report_json={
                    "task_plan": ctx["task_plan"].as_dict(),
                    "context": ctx["context_meta"].model_dump(),
                    "practice_critic_issues": practice_issues,
                    "context_analysis": {"metrics": {"levels": ["project", "chapter"]}},
                    "agent_config_versions": agent_versions,
                },
                assets=None,
                practice_critic_issues=practice_issues,
                agent_config_versions=agent_versions,
            )
            return FlowNodeOutput(updates={"result": result})

        return {
            "context": intent,
            "task_planning": planning,
            "title_annotation": title_annotation,
            "skeleton": skeleton,
            "theory": identity,
            "practice": practice,
            "global_quality": identity,
            "translate": identity,
            "evaluation": evaluation,
            "finalize": finalize,
        }

    orchestrator = Orchestrator(DummyLLM())
    monkeypatch.setattr(orchestrator.flow_handlers, "registry", lambda: registry(orchestrator))
    result = orchestrator.run_v2(raw_input={}, track_files=None)

    assert result.agent_config_versions == agent_versions
    assert result.practice_critic_issues == practice_issues
    assert result.report_json["task_plan"]["curriculum_context"]["graph_available"] is True
    assert "for_curriculum" in result.report_json["context"]["search_metrics"]["strategies"]
    assert result.flow_trace and result.flow_trace[0]["node_id"] == "context"
    assert result.methodology_reviews
    assert result.report_json["methodology_summary"]["total_reviews"] == len(result.methodology_reviews)


def test_orchestrator_pauses_for_planning_checkpoint_when_ui_callback_enabled(monkeypatch):
    seed = build_seed()
    context_meta = build_context_meta()
    context_analysis = build_context_analysis()

    def registry(_self):
        def intent(ctx):
            return FlowNodeOutput(
                updates={
                    "seed": seed,
                    "context_meta": context_meta,
                    "context_analysis": context_analysis,
                    "warnings": [],
                    "generate_bonus": False,
                }
            )

        def planning(ctx):
            return FlowNodeOutput()

        def title_annotation(ctx):
            return FlowNodeOutput(
                updates={
                    "title": "Title",
                    "annotation": Annotation(text="Annotation", chars=10),
                }
            )

        def skeleton(ctx):
            return FlowNodeOutput(
                updates={
                    "markdown": "# Title\n\nAnnotation\n\n## Глава 1. Введение и инструкция\n\n",
                    "title": "Title",
                    "annotation": Annotation(text="Annotation", chars=10),
                    "intro_section": IntroSection(intro_text="", instruction_text=""),
                }
            )

        return {
            "context": intent,
            "task_planning": planning,
            "title_annotation": title_annotation,
            "skeleton": skeleton,
            "theory": lambda _ctx: FlowNodeOutput(),
            "practice": lambda _ctx: FlowNodeOutput(),
            "global_quality": lambda _ctx: FlowNodeOutput(),
            "translate": lambda _ctx: FlowNodeOutput(),
            "evaluation": lambda _ctx: FlowNodeOutput(),
            "finalize": lambda _ctx: FlowNodeOutput(),
        }

    monkeypatch.delenv("METHODOLOGY_HUMAN_CHECKPOINTS", raising=False)
    orchestrator = Orchestrator(DummyLLM(), methodology_progress_callback=lambda _payload: None)
    monkeypatch.setattr(orchestrator.flow_handlers, "registry", lambda: registry(orchestrator))

    with pytest.raises(MethodologyGateInterrupt) as exc_info:
        orchestrator.run_v2(raw_input={}, track_files=None)

    assert exc_info.value.context["error_type"] == "HumanApprovalCheckpoint"
    assert exc_info.value.context["checkpoint"]["id"] == "task_planning"
    assert exc_info.value.context["checkpoint"]["resume_from_node"] == "title_annotation"
    assert exc_info.value.resume_from_index == 2


def test_finalize_prefers_structured_artifacts(monkeypatch):
    orchestrator = Orchestrator(DummyLLM())
    seed = build_seed()
    context_meta = build_context_meta()
    context_analysis = build_context_analysis()

    def unexpected_title_annotation(*_args, **_kwargs):
        raise AssertionError("title_annot.generate should not be called when structured artifacts are present")

    monkeypatch.setattr(orchestrator.title_annot, "generate", unexpected_title_annotation)

    context = {
        "seed": seed,
        "context_meta": context_meta,
        "context_analysis": context_analysis,
        "rubric_json": {"score": 5},
        "warnings": [],
        "issues": [],
        "markdown": "# Markdown title\n\n## Глава 1. Введение и инструкция\n\n### Введение\n\nЧерновик\n",
        "target_language": "ru",
        "title": "Structured Title",
        "annotation": Annotation(text="Structured annotation", chars=21),
        "intro_section": IntroSection(intro_text="Structured intro", instruction_text="Structured instruction"),
        "theory_parts": [
            TheoryPart(
                title="Theory title",
                body="Theory body",
                example="Theory example",
                bridge_questions=["Question?"],
            )
        ],
        "practice_tasks": [
            PracticeTask(
                title="Practice title",
                input_data="Repo",
                goal="Build artifact",
                approach_bullets=["Step 1"],
                expected_artifact="README",
                artifact_location="project/README.md",
            )
        ],
        "blueprint": ProjectBlueprint(
            language="ru",
            has_bonus=False,
            section_order=["title", "annotation", "toc", "intro", "theory", "practice"],
            chapter_titles={"intro": "## Глава 1. Введение и инструкция"},
            intro_subsections=["Введение", "Инструкция"],
            planned_tasks_count=3,
            planned_task_complexity="medium",
        ),
    }

    output = orchestrator.flow_handlers.node_finalize(context)

    assert output.status == "success"
    assert context["result"].spec.title == "Structured Title"
    assert context["result"].spec.annotation.text == "Structured annotation"
    assert context["result"].spec.intro.intro_text == "Structured intro"
    assert context["result"].spec.theory[0].title == "Theory title"
    assert context["result"].spec.practice[0].title == "Practice title"
    assert context["project_spec"].practice[0].artifact_location == "project/README.md"
    assert context["result"].report_json["blueprint"]["planned_tasks_count"] == 3
