import shutil
from pathlib import Path

from content_gen.curriculum import graph
from content_gen.curriculum.models import CurriculumEntry


def _entry(order: int, skills: list[str]) -> CurriculumEntry:
    return CurriculumEntry(
        track="DS",
        order=order,
        code=f"DS{order}",
        code_name=f"DS{order}",
        title=f"Project {order}",
        skills=skills,
        learning_outcomes=[f"LO {order}"],
    )


def test_build_graph_preserves_curriculum_order():
    curriculum_graph = graph.build_graph([
        _entry(1, ["python"]),
        _entry(2, ["python", "pandas"]),
        _entry(3, ["pandas"]),
    ])

    assert len(curriculum_graph.nodes) == 3
    assert all(
        curriculum_graph.nodes[edge.source].order < curriculum_graph.nodes[edge.target].order
        for edges in curriculum_graph.adjacency.values()
        for edge in edges
    )


def test_analyze_curriculum_position_without_cache(monkeypatch):
    graph_dir = Path(".tmp/test-fixtures/curriculum-graph").resolve()
    shutil.rmtree(graph_dir, ignore_errors=True)
    graph_dir.mkdir(parents=True)
    monkeypatch.setattr(graph, "GRAPH_DIR", graph_dir)

    insights = graph.analyze_curriculum_position("missing", last_order=1, current_skills=["python"])

    assert insights.graph_available is False
