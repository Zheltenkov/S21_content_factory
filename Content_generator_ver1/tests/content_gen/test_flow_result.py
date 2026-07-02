import pytest

from content_gen.agents.flow import FlowExecutionStep
from content_gen.exceptions import ContentGenerationError
from content_gen.flow_result import FlowResultFinalizer
from content_gen.methodology.trace import MethodologyTraceRecorder
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
    context = ProjectContextMeta(track="PjM", thematic_block="PjM")
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


def test_flow_result_finalizer_attaches_flow_trace() -> None:
    finalizer = FlowResultFinalizer(MethodologyTraceRecorder())
    result = _result()
    steps = [FlowExecutionStep(node_id="context", node_name="Context", status="success", duration_ms=1.2)]

    finalized = finalizer.finalize({"result": result}, steps)

    assert finalized is result
    assert result.flow_trace == [{"step_index": 0, "node_id": "context", "node_name": "Context", "status": "success", "duration_ms": 1.2, "issues": []}]
    assert result.report_json["flow_trace"][0]["node_id"] == "context"


def test_flow_result_finalizer_reports_error_step_without_result() -> None:
    finalizer = FlowResultFinalizer(MethodologyTraceRecorder())
    steps = [
        FlowExecutionStep(
            node_id="practice",
            node_name="Practice",
            status="error",
            duration_ms=2.0,
            issues=["hard fail"],
        )
    ]

    with pytest.raises(ContentGenerationError) as exc_info:
        finalizer.finalize({}, steps)

    assert "Flow остановлен на узле 'Practice': hard fail" in str(exc_info.value)
    assert exc_info.value.context["phase"] == "practice"


def test_flow_result_finalizer_reports_missing_result_without_error_step() -> None:
    finalizer = FlowResultFinalizer(MethodologyTraceRecorder())

    with pytest.raises(ContentGenerationError) as exc_info:
        finalizer.finalize({}, [])

    assert "Flow завершился без результата" in str(exc_info.value)
    assert exc_info.value.context["error_type"] == "MissingResult"
