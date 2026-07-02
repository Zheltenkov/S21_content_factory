from content_gen.methodology.decision import MethodologyGateDecision
from content_gen.methodology.models import StageRepairResult, StageReviewIssue, StageReviewResult
from content_gen.methodology.trace import MethodologyTraceRecorder
from content_gen.models.flow_state import ProjectFlowState
from content_gen.models.result import OrchestratorResult
from content_gen.models.schemas import Annotation, IntroSection, ProjectContextMeta, ProjectSeed, ProjectSpec


def _result() -> OrchestratorResult:
    seed = ProjectSeed(
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
    context = ProjectContextMeta(track="PjM", thematic_block="PjM", last_order=1)
    spec = ProjectSpec(
        language=seed.language,
        project_type=seed.project_type,
        thematic_block=seed.thematic_block,
        required_tools=[],
        title="Title",
        annotation=Annotation(text="Annotation", chars=10),
        intro=IntroSection(intro_text="Intro", instruction_text="Instruction"),
        context=context,
    )
    return OrchestratorResult(spec=spec, warnings=[], report_json={})


def test_trace_recorder_syncs_state_and_attaches_report_payload() -> None:
    recorder = MethodologyTraceRecorder()
    state = ProjectFlowState.from_initial_input({"language": "ru"})
    context = state.to_context()
    review = StageReviewResult(
        stage="practice",
        status="warning",
        issues=[StageReviewIssue(code="practice.tasks_missing", message="Missing tasks", severity="major")],
    )
    repair = StageRepairResult(
        stage="practice",
        status="applied",
        issue_codes=["practice.tasks_missing"],
        actions=["parsed tasks"],
        updated_fields=["practice_tasks"],
    )
    decision = MethodologyGateDecision(
        stage="practice",
        action="warn",
        status="warning",
        summary="Practice needs attention.",
        issues=[review.issues[0].model_dump()],
    )

    recorder.append_review(context, review)
    recorder.append_repair(context, repair)
    recorder.append_decision(context, decision)
    result = _result()
    flow_trace = [{"step_index": 0, "node_id": "practice"}]
    recorder.attach_to_result(result, context, flow_trace)

    assert state.methodology_reviews == [review]
    assert state.methodology_repairs == [repair]
    assert state.methodology_gate_decisions == [decision]
    assert result.flow_trace == flow_trace
    assert result.methodology_reviews[0]["stage"] == "practice"
    assert result.methodology_gate_decisions[0]["action"] == "warn"
    assert result.report_json["methodology_summary"]["severity_counts"]["major"] == 1
    assert result.report_json["methodology_repair_summary"]["updated_fields"]["practice_tasks"] == 1
    assert result.report_json["methodology_gate_summary"]["actions"]["warn"] == 1
