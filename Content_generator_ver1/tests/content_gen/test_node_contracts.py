import pytest

from content_gen.workflow.flow_runner import AgentFlowRunner, FlowNodeOutput, load_flow_definition
from content_gen.node_contracts import (
    load_model_roles,
    load_node_contracts,
    validate_contract_hardening,
    validate_contracts_against_flow,
)


def test_node_contracts_cover_executable_flow() -> None:
    flow = load_flow_definition("content_generation")
    contracts = load_node_contracts()

    errors = validate_contracts_against_flow(flow.nodes, contracts)

    assert errors == []


def test_node_contracts_use_known_model_roles() -> None:
    roles = load_model_roles()
    contracts = load_node_contracts()

    unknown_roles = {
        node_id: contract.model_role
        for node_id, contract in contracts.items()
        if contract.model_role not in roles
    }

    assert unknown_roles == {}


def test_node_contracts_pass_static_hardening_checks() -> None:
    flow = load_flow_definition("content_generation")
    contracts = load_node_contracts()
    roles = load_model_roles()

    errors = validate_contract_hardening(flow_nodes=flow.nodes, contracts=contracts, model_roles=roles)

    assert errors == []


@pytest.mark.parametrize(
    "node_id",
    [
        "context",
        "task_planning",
        "title_annotation",
        "skeleton",
        "theory",
        "practice",
        "global_quality",
        "evaluation",
        "translate",
        "finalize",
    ],
)
def test_node_contracts_have_operational_policy(node_id: str) -> None:
    contract = load_node_contracts()[node_id]

    assert contract.prompt_id
    assert contract.prompt_version
    assert contract.validators
    assert contract.repair_policy
    assert contract.fallback_policy
    assert contract.fallback_events
    assert contract.observability_tags


def test_flow_trace_includes_node_contract_metadata() -> None:
    flow = load_flow_definition("content_generation")
    context = {
        "seed": object(),
        "context_meta": object(),
        "context_analysis": object(),
        "target_language": "ru",
    }

    runner = AgentFlowRunner(flow)
    steps = runner.run(
        context,
        registry={
            "context": lambda ctx: FlowNodeOutput(updates={"seed": "s"}),
            "task_planning": lambda ctx: FlowNodeOutput(updates={"task_plan": "p"}),
            "title_annotation": lambda ctx: FlowNodeOutput(updates={"title": "t", "annotation": "a"}),
            "skeleton": lambda ctx: FlowNodeOutput(updates={"markdown": "# T"}),
            "theory": lambda ctx: FlowNodeOutput(updates={"markdown": "# T", "theory_parts": []}),
            "practice": lambda ctx: FlowNodeOutput(updates={"markdown": "# T", "practice_tasks": []}),
            "global_quality": lambda ctx: FlowNodeOutput(updates={"markdown": "# T"}),
            "evaluation": lambda ctx: FlowNodeOutput(updates={"rubric_json": {}}),
            "translate": lambda ctx: FlowNodeOutput(updates={"translated_markdown": "# T"}),
            "finalize": lambda ctx: FlowNodeOutput(updates={"result": "ok"}),
        },
    )

    assert steps[-1].node_id == "finalize"
    traces = context["node_traces"]
    first_trace = traces[0]
    assert first_trace["metadata"]["node_contract_id"] == "context"
    assert first_trace["metadata"]["model_role"] == "planner"
    assert first_trace["metadata"]["observability_tags"]
    assert first_trace["prompt_version"] == "1.0.0"
