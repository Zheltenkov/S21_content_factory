from pydantic import BaseModel

from api.db.paused_generation_codec import hydrate_context, hydrate_steps, serialize_context, serialize_steps
from content_gen.agents.flow import FlowExecutionStep
from content_gen.agents.task_planner import TaskPlan
from content_gen.models.flow_state import ProjectFlowState
from content_gen.models.schemas import ProjectSeed
from content_gen.observability import UnifiedTraceSink


class BinaryPayload(BaseModel):
    name: str
    data: bytes


def test_paused_generation_codec_roundtrips_typed_context() -> None:
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
    task_plan = TaskPlan(
        tasks_count=3,
        complexity="medium",
        level_index=1,
        level_source="audience_only",
        rationale="rationale",
        explanation="explanation",
        curriculum_context={},
    )
    state = ProjectFlowState.from_initial_input({"language": "ru"})
    context = {
        "state": state,
        "seed": seed,
        "task_plan": task_plan,
        "dataset_files": [{"path": "materials/raw.md", "data": b"raw bytes"}],
    }

    hydrated = hydrate_context(serialize_context(context))

    assert isinstance(hydrated["seed"], ProjectSeed)
    assert isinstance(hydrated["task_plan"], TaskPlan)
    assert hydrated["task_plan"].tasks_count == 3
    assert hydrated["dataset_files"][0]["data"] == b"raw bytes"
    assert isinstance(hydrated["state"], ProjectFlowState)
    assert hydrated["state"].seed == hydrated["seed"]


def test_paused_generation_codec_serializes_pydantic_binary_payload_without_utf8_decode() -> None:
    png_header = b"\x89PNG\r\n\x1a\n"

    serialized = serialize_context({"asset": BinaryPayload(name="diagram.png", data=png_header)})
    hydrated = hydrate_context(serialized)

    assert serialized["asset"]["data"]["data"]["__paused_type__"] == "builtins:bytes"
    assert hydrated["asset"]["data"] == png_header


def test_paused_generation_codec_roundtrips_steps() -> None:
    steps = [
        FlowExecutionStep(
            node_id="context",
            node_name="Context",
            status="paused",
            duration_ms=12.3,
            issues=["needs review"],
        )
    ]

    hydrated = hydrate_steps(serialize_steps(steps))

    assert hydrated[0].node_id == "context"
    assert hydrated[0].status == "paused"
    assert hydrated[0].issues == ["needs review"]


def test_paused_generation_codec_hydrates_new_flow_step_type_name() -> None:
    hydrated = hydrate_context(
        {
            "step": {
                "__paused_type__": "content_gen.workflow.flow_runner:FlowExecutionStep",
                "data": {
                    "node_id": "practice",
                    "node_name": "Practice",
                    "status": "paused",
                    "duration_ms": 5.0,
                    "issues": ["checkpoint"],
                },
            }
        }
    )

    assert isinstance(hydrated["step"], FlowExecutionStep)
    assert hydrated["step"].node_id == "practice"


def test_paused_generation_codec_drops_runtime_observability_sink() -> None:
    serialized = serialize_context({"markdown": "# README", "observability_sink": object()})

    assert serialized == {"markdown": "# README"}


def test_paused_generation_codec_drops_observability_sink_nested_in_state() -> None:
    state = ProjectFlowState.from_initial_input({"language": "ru"})
    sink = UnifiedTraceSink(run_id="run-1", user_id="user-1")
    context = {"state": state, "markdown": "# README", "observability_sink": sink}
    state.sync_from_context(context)

    serialized = serialize_context(context)

    assert "observability_sink" not in state.__dict__
    assert "observability_sink" not in serialized
    assert "observability_sink" not in serialized["state"]["data"]
    assert serialized["markdown"] == "# README"


def test_paused_generation_codec_records_unknown_type_compatibility_event() -> None:
    hydrated = hydrate_context(
        {
            "legacy": {
                "__paused_type__": "old.module:Thing",
                "data": {"value": 42},
            }
        }
    )

    assert hydrated["legacy"] == {"value": 42}
    assert hydrated["compatibility_events"][0]["compatibility_type"] == "unknown_paused_type"
    assert hydrated["compatibility_events"][0]["metadata"] == {"type_name": "old.module:Thing"}


def test_paused_generation_codec_records_model_validation_compatibility_event() -> None:
    hydrated = hydrate_context(
        {
            "seed": {
                "__paused_type__": "content_gen.models.schemas:ProjectSeed",
                "data": {"language": "ru"},
            }
        }
    )

    assert hydrated["seed"] == {"language": "ru"}
    assert hydrated["compatibility_events"][0]["compatibility_type"] == "pydantic_model_validation_failed"
    assert hydrated["compatibility_events"][0]["metadata"] == {
        "type_name": "content_gen.models.schemas:ProjectSeed"
    }
