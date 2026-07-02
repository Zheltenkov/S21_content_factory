import pytest

from content_gen.agents.flow import (
    AgentFlowRunner,
    FlowDefinition,
    FlowEdgeConfig,
    FlowNodeConfig,
    FlowNodeOutput,
)
from content_gen.methodology.decision import MethodologyGateInterrupt
from content_gen.models.flow_state import ProjectFlowState


def make_flow_definition():
    nodes = [
        FlowNodeConfig(id="a", name="A", handler="start"),
        FlowNodeConfig(id="b", name="B", handler="middle"),
        FlowNodeConfig(id="c", name="C", handler="end"),
    ]
    edges = [
        FlowEdgeConfig(source="a", target="b"),
        FlowEdgeConfig(source="b", target="c"),
    ]
    return FlowDefinition(name="test", version="1", nodes=nodes, edges=edges)


def test_flow_runner_executes_in_topological_order():
    runner = AgentFlowRunner(make_flow_definition())
    context = {}
    order = []

    registry = {
        "start": lambda ctx: order.append("start") or FlowNodeOutput(updates={"value": 1}),
        "middle": lambda ctx: order.append("middle") or FlowNodeOutput(updates={"value": ctx["value"] + 1}),
        "end": lambda ctx: order.append("end") or FlowNodeOutput(updates={"value": ctx["value"] + 1}),
    }

    steps = runner.run(context, registry)
    assert context["value"] == 3
    assert order == ["start", "middle", "end"]
    assert [step.node_id for step in steps] == ["a", "b", "c"]


def test_flow_runner_raises_on_missing_handler():
    runner = AgentFlowRunner(make_flow_definition())
    with pytest.raises(RuntimeError):
        runner.run({}, registry={"start": lambda ctx: FlowNodeOutput()})


def test_flow_runner_stops_after_error_status():
    runner = AgentFlowRunner(make_flow_definition())
    context = {}
    order = []

    registry = {
        "start": lambda ctx: order.append("start") or FlowNodeOutput(updates={"value": 1}),
        "middle": lambda ctx: order.append("middle") or FlowNodeOutput(status="error", issues=["hard fail"]),
        "end": lambda ctx: order.append("end") or FlowNodeOutput(updates={"value": 3}),
    }

    steps = runner.run(context, registry)

    assert order == ["start", "middle"]
    assert [step.status for step in steps] == ["success", "error"]
    assert "value" in context and context["value"] == 1


def test_flow_runner_skips_node_when_run_if_condition_is_false():
    nodes = [
        FlowNodeConfig(id="a", name="A", handler="start"),
        FlowNodeConfig(id="b", name="B", handler="translate", conditions={"run_if": "target_language != 'ru'"}),
        FlowNodeConfig(id="c", name="C", handler="end"),
    ]
    edges = [
        FlowEdgeConfig(source="a", target="b"),
        FlowEdgeConfig(source="b", target="c"),
    ]
    runner = AgentFlowRunner(FlowDefinition(name="conditional", version="1", nodes=nodes, edges=edges))
    order = []
    context = {"target_language": "ru"}

    registry = {
        "start": lambda ctx: order.append("start") or FlowNodeOutput(),
        "translate": lambda ctx: order.append("translate") or FlowNodeOutput(),
        "end": lambda ctx: order.append("end") or FlowNodeOutput(),
    }

    steps = runner.run(context, registry)

    assert order == ["start", "end"]
    assert [step.status for step in steps] == ["success", "skipped", "success"]


def test_flow_runner_syncs_typed_state_from_context_and_updates():
    runner = AgentFlowRunner(make_flow_definition())
    state = ProjectFlowState.from_initial_input({"language": "ru"})
    context = state.to_context()

    def _middle(ctx):
        ctx["markdown"] = "# Test"
        return FlowNodeOutput(updates={"generate_bonus": True})

    registry = {
        "start": lambda ctx: FlowNodeOutput(updates={"target_language": "en"}),
        "middle": _middle,
        "end": lambda ctx: FlowNodeOutput(updates={"rubric_json": {"score": 1}}),
    }

    runner.run(context, registry)

    assert state.target_language == "en"
    assert state.generate_bonus is True
    assert state.markdown == "# Test"
    assert state.rubric_json == {"score": 1}


def test_flow_runner_calls_stage_review_hook_after_updates():
    reviews = []

    def review_hook(node, context, output):
        reviews.append((node.id, context.get("value"), output.status))
        context.setdefault("methodology_reviews", []).append({"stage": node.id})
        return [f"reviewed:{node.id}"]

    runner = AgentFlowRunner(make_flow_definition(), stage_review_hook=review_hook)
    context = {}
    registry = {
        "start": lambda ctx: FlowNodeOutput(updates={"value": 1}),
        "middle": lambda ctx: FlowNodeOutput(updates={"value": ctx["value"] + 1}),
        "end": lambda ctx: FlowNodeOutput(updates={"value": ctx["value"] + 1}),
    }

    steps = runner.run(context, registry)

    assert reviews == [("a", 1, "success"), ("b", 2, "success"), ("c", 3, "success")]
    assert context["methodology_reviews"] == [{"stage": "a"}, {"stage": "b"}, {"stage": "c"}]
    assert steps[0].issues == ["reviewed:a"]


def test_flow_runner_captures_pause_state_for_resume():
    runner = AgentFlowRunner(make_flow_definition())
    context = {}
    order = []

    def review_hook(node, _context, _output):
        if node.id == "a":
            raise MethodologyGateInterrupt(
                "pause",
                context={"error_type": "MethodologyGatePause", "phase": node.id},
            )
        return []

    registry = {
        "start": lambda ctx: order.append("start") or FlowNodeOutput(updates={"value": 1}),
        "middle": lambda ctx: order.append("middle") or FlowNodeOutput(updates={"value": ctx["value"] + 1}),
        "end": lambda ctx: order.append("end") or FlowNodeOutput(updates={"value": ctx["value"] + 1}),
    }
    runner.stage_review_hook = review_hook

    with pytest.raises(MethodologyGateInterrupt) as exc_info:
        runner.run(context, registry)

    interrupt = exc_info.value
    assert interrupt.flow_context is context
    assert interrupt.resume_from_index == 1
    assert [step.status for step in interrupt.flow_steps] == ["paused"]
    assert context["value"] == 1
    assert order == ["start"]


def test_flow_runner_resumes_from_saved_index_without_rerunning_previous_node():
    runner = AgentFlowRunner(make_flow_definition())
    context = {"value": 1}
    previous_steps = []
    order = []
    registry = {
        "start": lambda ctx: order.append("start") or FlowNodeOutput(updates={"value": 99}),
        "middle": lambda ctx: order.append("middle") or FlowNodeOutput(updates={"value": ctx["value"] + 1}),
        "end": lambda ctx: order.append("end") or FlowNodeOutput(updates={"value": ctx["value"] + 1}),
    }

    steps = runner.run(context, registry, start_index=1, previous_steps=previous_steps)

    assert order == ["middle", "end"]
    assert context["value"] == 3
    assert [step.node_id for step in steps] == ["b", "c"]


def test_flow_runner_emits_workflow_node_and_checkpoint_hooks():
    started = []
    checkpoints = []
    runner = AgentFlowRunner(
        make_flow_definition(),
        workflow_node_started_hook=started.append,
        workflow_checkpoint_hook=checkpoints.append,
    )
    context = {}
    registry = {
        "start": lambda ctx: FlowNodeOutput(updates={"value": "alpha"}),
        "middle": lambda ctx: FlowNodeOutput(status="skipped", issues=["manual skip"]),
        "end": lambda ctx: FlowNodeOutput(updates={"items": [1, 2, 3]}),
    }

    steps = runner.run(context, registry)

    assert [item["node_id"] for item in started] == ["a", "b", "c"]
    assert [item["status"] for item in checkpoints] == ["success", "skipped", "success"]
    assert [item["checkpoint_index"] for item in checkpoints] == [1, 2, 3]
    assert checkpoints[0]["input_hash"]
    assert checkpoints[0]["output_artifact"]["value"]["chars"] == 5
    assert checkpoints[0]["context_snapshot"]["value"] == "alpha"
    assert [step.status for step in steps] == ["success", "skipped", "success"]


def test_flow_runner_detects_cycles():
    nodes = [
        FlowNodeConfig(id="x", name="X", handler="x"),
        FlowNodeConfig(id="y", name="Y", handler="y"),
    ]
    edges = [
        FlowEdgeConfig(source="x", target="y"),
        FlowEdgeConfig(source="y", target="x"),
    ]
    definition = FlowDefinition(name="cyclic", version="1", nodes=nodes, edges=edges)
    with pytest.raises(RuntimeError):
        AgentFlowRunner(definition)


def test_flow_runner_rejects_edge_with_unknown_node_id():
    nodes = [
        FlowNodeConfig(id="context", name="Context", handler="context"),
        FlowNodeConfig(id="task_planning", name="Task Planning", handler="task_planning"),
    ]
    edges = [
        FlowEdgeConfig(source="init", target="task_planning"),
    ]

    definition = FlowDefinition(name="invalid", version="1", nodes=nodes, edges=edges)

    with pytest.raises(RuntimeError, match="edge source 'init'"):
        AgentFlowRunner(definition)
