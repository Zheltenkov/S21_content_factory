"""Построение и использование curriculum-графа трека."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .models import CurriculumEntry

logger = logging.getLogger(__name__)

GRAPH_VERSION = "1.0.0"
GRAPH_DIR = Path(".curriculum_cache")


def _to_set(values: list[str]) -> set:
    return {v.strip().lower() for v in values or [] if v.strip()}


def _jaccard(a: list[str], b: list[str]) -> float:
    set_a = _to_set(a)
    set_b = _to_set(b)
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0
    return intersection / union


@dataclass
class CurriculumNode:
    node_id: str
    track: str
    order: int
    code: str
    code_name: str
    title: str
    skills: list[str] = field(default_factory=list)
    learning_outcomes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class CurriculumEdge:
    source: str
    target: str
    weight: float
    order_delta: int
    skill_overlap: float
    lo_overlap: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class CurriculumGraph:
    track: str
    nodes: dict[str, CurriculumNode] = field(default_factory=dict)
    adjacency: dict[str, list[CurriculumEdge]] = field(default_factory=dict)
    version: str = GRAPH_VERSION
    built_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict[str, object]:
        return {
            "track": self.track,
            "version": self.version,
            "built_at": self.built_at,
            "nodes": {node_id: node.to_dict() for node_id, node in self.nodes.items()},
            "adjacency": {
                node_id: [edge.to_dict() for edge in edges]
                for node_id, edges in self.adjacency.items()
            },
        }

    @staticmethod
    def from_dict(data: dict[str, object]) -> CurriculumGraph:
        graph = CurriculumGraph(track=data["track"])
        graph.version = data.get("version", GRAPH_VERSION)
        graph.built_at = data.get("built_at", datetime.utcnow().isoformat())
        nodes = data.get("nodes", {})
        adjacency = data.get("adjacency", {})
        for node_id, node_data in nodes.items():
            graph.nodes[node_id] = CurriculumNode(**node_data)
        for node_id, edges in adjacency.items():
            graph.adjacency[node_id] = [CurriculumEdge(**edge) for edge in edges]
        return graph


@dataclass
class CurriculumInsights:
    graph_available: bool
    progress_ratio: float = 0.0
    previous_nodes: list[dict[str, object]] = field(default_factory=list)
    next_nodes: list[dict[str, object]] = field(default_factory=list)
    skills_already: list[str] = field(default_factory=list)
    skills_to_prepare: list[str] = field(default_factory=list)
    level_adjust: int = 0
    task_adjust: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _graph_path(track: str) -> Path:
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    return GRAPH_DIR / f"{track}_curriculum.json"


def save_graph(graph: CurriculumGraph) -> None:
    path = _graph_path(graph.track)
    path.write_text(json.dumps(graph.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("💾 Curriculum graph saved: %s", path)


def load_graph(track: str) -> CurriculumGraph | None:
    path = _graph_path(track)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CurriculumGraph.from_dict(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("⚠️ Failed to load curriculum graph %s: %s", track, exc)
        return None


def build_graph(entries: list[CurriculumEntry]) -> CurriculumGraph:
    if not entries:
        raise ValueError("Нельзя построить curriculum-граф без записей")
    track = entries[0].track
    graph = CurriculumGraph(track=track)
    sorted_entries = sorted(entries, key=lambda e: e.order)

    for entry in sorted_entries:
        node_id = _node_id(entry)
        graph.nodes[node_id] = CurriculumNode(
            node_id=node_id,
            track=entry.track,
            order=entry.order,
            code=entry.code,
            code_name=entry.code_name,
            title=entry.title,
            skills=entry.skills or [],
            learning_outcomes=entry.learning_outcomes or [],
        )
        graph.adjacency[node_id] = []

    for i, src_entry in enumerate(sorted_entries):
        for dst_entry in sorted_entries[i + 1 :]:
            src_id = _node_id(src_entry)
            dst_id = _node_id(dst_entry)
            weight, skill_overlap, lo_overlap = _edge_weight(src_entry, dst_entry)
            if weight <= 0:
                continue
            edge = CurriculumEdge(
                source=src_id,
                target=dst_id,
                weight=weight,
                order_delta=max(1, dst_entry.order - src_entry.order),
                skill_overlap=skill_overlap,
                lo_overlap=lo_overlap,
            )
            graph.adjacency[src_id].append(edge)

    _validate_graph(graph)
    return graph


def _node_id(entry: CurriculumEntry) -> str:
    return f"{entry.track}:{entry.order}:{entry.code}"


def _edge_weight(src: CurriculumEntry, dst: CurriculumEntry) -> tuple[float, float, float]:
    skill_overlap = _jaccard(src.skills, dst.skills)
    lo_overlap = _jaccard(src.learning_outcomes, dst.learning_outcomes)
    order_delta = max(1, dst.order - src.order)
    order_factor = 1.0 / (1.0 + order_delta)
    weight = 0.6 * skill_overlap + 0.3 * lo_overlap + 0.1 * order_factor
    weight = round(weight, 4)
    return weight, skill_overlap, lo_overlap


def _validate_graph(graph: CurriculumGraph) -> None:
    indegree = {node_id: 0 for node_id in graph.nodes}
    for edges in graph.adjacency.values():
        for edge in edges:
            indegree[edge.target] += 1
            if graph.nodes[edge.source].order >= graph.nodes[edge.target].order:
                raise ValueError("Нарушен порядок: обнаружено обратное ребро")
    queue = [node_id for node_id, deg in indegree.items() if deg == 0]
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for edge in graph.adjacency.get(current, []):
            indegree[edge.target] -= 1
            if indegree[edge.target] == 0:
                queue.append(edge.target)
    if visited != len(graph.nodes):
        raise ValueError("Curriculum-граф содержит цикл или изолированные узлы без источника")


def build_and_save_graph(entries: list[CurriculumEntry]) -> CurriculumGraph:
    graph = build_graph(entries)
    save_graph(graph)
    return graph


def nearest_previous(graph: CurriculumGraph, order: int, skills: list[str], k: int = 3) -> list[CurriculumNode]:
    candidates = [node for node in graph.nodes.values() if node.order < order]
    return _rank_nodes(candidates, skills, k, current_order=order, reverse=True)


def recommended_next(graph: CurriculumGraph, order: int, skills: list[str], k: int = 3) -> list[CurriculumNode]:
    candidates = [node for node in graph.nodes.values() if node.order > order]
    return _rank_nodes(candidates, skills, k, current_order=order, reverse=False)


def _rank_nodes(
    candidates: list[CurriculumNode],
    skills: list[str],
    k: int,
    current_order: int,
    reverse: bool,
) -> list[CurriculumNode]:
    skills_set = _to_set(skills)
    ranked: list[tuple[float, CurriculumNode]] = []
    for node in candidates:
        overlap = _jaccard(list(skills_set), node.skills)
        distance_penalty = 1.0 / (1 + abs(node.order - current_order))
        score = 0.7 * overlap + 0.3 * distance_penalty
        ranked.append((score, node))
    ranked.sort(key=lambda x: x[0], reverse=True)
    nodes = [node for _, node in ranked[:k]]
    if reverse:
        nodes.sort(key=lambda n: n.order, reverse=True)
    else:
        nodes.sort(key=lambda n: n.order)
    return nodes


def analyze_curriculum_position(
    track: str,
    last_order: int | None,
    current_skills: list[str] | None = None,
) -> CurriculumInsights:
    graph = load_graph(track)
    if not graph or not graph.nodes:
        return CurriculumInsights(graph_available=False)

    current_skills = current_skills or []
    max_order = max(node.order for node in graph.nodes.values())
    order = last_order if last_order is not None else 0
    progress_ratio = order / max_order if max_order else 0.0

    previous_nodes = nearest_previous(graph, order or 1, current_skills, k=3)
    next_nodes = recommended_next(graph, order or 0, current_skills, k=3)

    covered_skills = _aggregate_skills(previous_nodes)
    upcoming_skills = _aggregate_skills(next_nodes)
    skills_to_prepare = [
        skill for skill in upcoming_skills
        if skill not in covered_skills
    ][:5]

    level_adjust = 0
    if progress_ratio >= 0.75:
        level_adjust = 1
    elif progress_ratio <= 0.25:
        level_adjust = -1

    task_adjust = 1 if len(skills_to_prepare) >= 3 else 0

    return CurriculumInsights(
        graph_available=True,
        progress_ratio=round(progress_ratio, 3),
        previous_nodes=[node.to_dict() for node in previous_nodes],
        next_nodes=[node.to_dict() for node in next_nodes],
        skills_already=covered_skills[:5],
        skills_to_prepare=skills_to_prepare,
        level_adjust=level_adjust,
        task_adjust=task_adjust,
    )


def _aggregate_skills(nodes: list[CurriculumNode]) -> list[str]:
    seen = []
    for node in nodes:
        for skill in node.skills:
            normalized = skill.strip()
            if normalized and normalized not in seen:
                seen.append(normalized)
    return seen
