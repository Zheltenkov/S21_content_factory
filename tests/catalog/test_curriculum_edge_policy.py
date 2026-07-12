from content_factory.catalog.pipeline.curriculum import PlanNode, ProjectBlueprint, SkillOccurrence, planner
from content_factory.catalog.pipeline.curriculum.edge_policy import curriculum_edge_role
from content_factory.catalog.pipeline.curriculum.journey import _apply_dag_constraints
from content_factory.catalog.pipeline.models import PrereqEdge
from content_factory.catalog.pipeline.stage_catalog_to_dag import operational_edges


def _node(node_id: str) -> PlanNode:
    return PlanNode(node_id, node_id, "group", "stage", 3, (), (), (), ())


def test_legacy_edge_roles_are_normalized() -> None:
    assert curriculum_edge_role({"relation_type": "hard"}) == "required"
    assert curriculum_edge_role({"relation_type": "soft"}) == "recommended"
    assert curriculum_edge_role({"relation_type": "related"}) == "related"


def test_recommended_edge_does_not_fragment_integrative_project() -> None:
    chunks = planner._split_nodes_for_project(
        [_node("A"), _node("B")],
        {"final_edges": [{"src_id": "A", "dst_id": "B", "relation_type": "soft"}]},
        max_skills=4,
    )

    assert [[node.tmp_id for node in chunk] for chunk in chunks] == [["A", "B"]]


def test_required_edge_keeps_prerequisite_in_separate_project() -> None:
    chunks = planner._split_nodes_for_project(
        [_node("A"), _node("B")],
        {"final_edges": [{"src_id": "A", "dst_id": "B", "relation_type": "hard"}]},
        max_skills=4,
    )

    assert [[node.tmp_id for node in chunk] for chunk in chunks] == [["A"], ["B"]]


def test_conflicting_recommendations_do_not_split_existing_projects() -> None:
    nodes = {node_id: _node(node_id) for node_id in ("A", "B", "C", "D")}
    projects = [
        ProjectBlueprint(
            occurrences=[SkillOccurrence(nodes["A"], role="primary"), SkillOccurrence(nodes["D"], role="primary")],
            block_key="stage",
            artifact="artifact-1",
        ),
        ProjectBlueprint(
            occurrences=[SkillOccurrence(nodes["C"], role="primary"), SkillOccurrence(nodes["B"], role="primary")],
            block_key="stage",
            artifact="artifact-2",
        ),
    ]

    ordered = planner._reorder_projects_by_dag_edges(
        projects,
        {
            "final_edges": [
                {"src_id": "A", "dst_id": "B", "relation_type": "soft"},
                {"src_id": "C", "dst_id": "D", "relation_type": "soft"},
            ]
        },
    )

    assert len(ordered) == 2
    assert {frozenset(node.tmp_id for node in project.unique_nodes) for project in ordered} == {
        frozenset({"A", "D"}),
        frozenset({"B", "C"}),
    }


def test_related_edge_is_not_part_of_operational_dag() -> None:
    edges = [PrereqEdge(src="A", dst="B", relation_type="related", decision="accept")]

    assert operational_edges(edges) == []


def test_only_required_edge_can_move_skill_to_later_journey_stage() -> None:
    initial = {"A": 1, "B": 0}

    recommended, recommended_notes = _apply_dag_constraints(
        initial,
        {"final_edges": [{"src_id": "A", "dst_id": "B", "relation_type": "soft"}]},
    )
    required, required_notes = _apply_dag_constraints(
        initial,
        {"final_edges": [{"src_id": "A", "dst_id": "B", "relation_type": "hard"}]},
    )

    assert recommended == initial
    assert recommended_notes == ()
    assert required == {"A": 1, "B": 1}
    assert required_notes
