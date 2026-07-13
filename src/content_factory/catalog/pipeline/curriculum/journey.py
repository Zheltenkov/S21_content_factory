"""Domain-agnostic curriculum journey design above the prerequisite DAG.

The DAG answers which skills may precede other skills.  This module answers a
different question: which methodological stages form a coherent learner
journey.  The accepted brief supplies the intended coverage order; hard DAG
edges may move a skill later, but never silently reorder the brief itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from .. import language
from .brief_questions import BriefQuestion, classify_questions, count_blocking
from .domain import PlanNode
from .edge_policy import curriculum_edge_role

DESIGN_SPEC_VERSION = "curriculum-design:v2"
MAX_JOURNEY_STAGES = 7
MAX_OPEN_QUESTIONS = 8


@dataclass(frozen=True)
class CurriculumStageSpec:
    """One accepted methodological stage and the skills assigned to it."""

    code: str
    title: str
    goal: str
    coverage_areas: tuple[str, ...]
    node_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "title": self.title,
            "goal": self.goal,
            "coverage_areas": list(self.coverage_areas),
            "node_ids": list(self.node_ids),
        }


@dataclass(frozen=True)
class CurriculumDesignSpec:
    """Planning contract reviewed before projects are materialized as UP rows."""

    journey_type: str
    program_goal: str
    stages: tuple[CurriculumStageSpec, ...]
    capstone_required: bool
    capstone_title: str
    dag_fingerprint: str = ""
    open_questions: tuple[str, ...] = ()
    uncovered_required_areas: tuple[str, ...] = ()
    dag_adjustments: tuple[str, ...] = ()
    approved: bool = False
    version: str = DESIGN_SPEC_VERSION

    @property
    def node_stage(self) -> dict[str, int]:
        return {node_id: stage_index for stage_index, stage in enumerate(self.stages) for node_id in stage.node_ids}

    @property
    def journey_type_label(self) -> str:
        return {
            "product_lifecycle": "Жизненный цикл продукта",
            "research_cycle": "Исследовательский цикл",
            "technology_mastery": "Освоение технологии",
            "operations_process": "Операционный процесс",
            "compliance_readiness": "Готовность и соответствие требованиям",
            "professional_workflow": "Профессиональный рабочий процесс",
            "mixed": "Смешанный маршрут",
        }.get(self.journey_type, self.journey_type)

    @property
    def ready(self) -> bool:
        return self.approved and not self.uncovered_required_areas

    @property
    def design_hash(self) -> str:
        payload = {
            "version": self.version,
            "journey_type": self.journey_type,
            "program_goal": self.program_goal,
            "stages": [stage.as_dict() for stage in self.stages],
            "capstone_required": self.capstone_required,
            "capstone_title": self.capstone_title,
            "dag_fingerprint": self.dag_fingerprint,
            "open_questions": list(self.open_questions),
            "uncovered_required_areas": list(self.uncovered_required_areas),
            "dag_adjustments": list(self.dag_adjustments),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def brief_questions(self) -> tuple[BriefQuestion, ...]:
        """Open questions typed with a category + blocking decision (slice 7)."""
        return classify_questions(self.open_questions)

    @property
    def blocking_question_count(self) -> int:
        return count_blocking(self.brief_questions)

    @property
    def readiness_state(self) -> str:
        if self.uncovered_required_areas:
            return "blocked"
        if not self.approved:
            return "needs_review"
        if self.blocking_question_count:
            return "blocked_by_questions"
        if self.open_questions:
            return "ready_with_questions"
        return "ready"

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "design_hash": self.design_hash,
            "journey_type": self.journey_type,
            "journey_type_label": self.journey_type_label,
            "program_goal": self.program_goal,
            "stages": [stage.as_dict() for stage in self.stages],
            "capstone_required": self.capstone_required,
            "capstone_title": self.capstone_title,
            "dag_fingerprint": self.dag_fingerprint,
            "open_questions": list(self.open_questions),
            "brief_questions": [question.as_dict() for question in self.brief_questions],
            "blocking_question_count": self.blocking_question_count,
            "uncovered_required_areas": list(self.uncovered_required_areas),
            "dag_adjustments": list(self.dag_adjustments),
            "approved": self.approved,
            "ready": self.ready,
            "readiness_state": self.readiness_state,
        }


def build_curriculum_design_spec(
    context: dict[str, Any] | None,
    nodes: Iterable[PlanNode],
    dag_payload: dict[str, Any] | None = None,
) -> CurriculumDesignSpec:
    """Build or rehydrate a journey contract from brief facts and accepted skills."""

    data = context or {}
    node_list = list(nodes)
    accepted = data.get("curriculum_design_spec")
    accepted_spec = accepted if isinstance(accepted, dict) else {}
    raw_text = _text(data.get("raw_text"))
    program_goal = _text(accepted_spec.get("program_goal")) or _text(data.get("program_goal"))
    required_areas = _text_list(data.get("must_include_areas"))
    accepted_stages = accepted_spec.get("stages")

    if isinstance(accepted_stages, list) and accepted_stages:
        stage_seeds = _accepted_stage_seeds(accepted_stages)
        ordered_areas = [area for _title, _goal, areas in stage_seeds for area in areas]
    else:
        ordered_areas = _ordered_coverage_areas(required_areas, node_list, raw_text, dag_payload or {})
        stage_seeds = _stage_seeds(ordered_areas)

    stage_by_node = _assign_nodes_to_stages(node_list, stage_seeds)
    stage_by_node, adjustments = _apply_dag_constraints(stage_by_node, dag_payload or {})
    stages = _hydrate_stage_nodes(stage_seeds, node_list, stage_by_node)
    covered_areas = {
        _normalize(area) for area in required_areas if any(_areas_match(area, node.block_key) for node in node_list)
    }
    uncovered = tuple(area for area in required_areas if _normalize(area) not in covered_areas)
    open_questions = tuple(_text_list(accepted_spec.get("open_questions"))) or _extract_open_questions(raw_text)
    journey_type = _text(accepted_spec.get("journey_type")) or _detect_journey_type(data, raw_text)
    capstone_required = _as_bool(accepted_spec.get("capstone_required")) or _capstone_required(data, raw_text)
    capstone_title = _text(accepted_spec.get("capstone_title")) or _capstone_title(journey_type, raw_text)

    draft = CurriculumDesignSpec(
        journey_type=journey_type,
        program_goal=program_goal,
        stages=stages,
        capstone_required=capstone_required,
        capstone_title=capstone_title,
        dag_fingerprint=_dag_fingerprint(dag_payload or {}),
        open_questions=open_questions,
        uncovered_required_areas=uncovered,
        dag_adjustments=adjustments,
        approved=False,
        version=_text(accepted_spec.get("version")) or DESIGN_SPEC_VERSION,
    )
    approval_requested = _as_bool(accepted_spec.get("approved")) or _as_bool(data.get("curriculum_design_approved"))
    accepted_hash = _text(accepted_spec.get("design_hash"))
    if approval_requested and accepted_hash and accepted_hash == draft.design_hash:
        return replace(draft, approved=True)
    return draft


def approve_curriculum_design_spec(spec: CurriculumDesignSpec) -> CurriculumDesignSpec:
    """Return an immutable accepted snapshot for persistence in brief metadata."""

    return replace(spec, approved=True)


def _accepted_stage_seeds(value: list[Any]) -> list[tuple[str, str, tuple[str, ...]]]:
    seeds: list[tuple[str, str, tuple[str, ...]]] = []
    for index, raw_stage in enumerate(value, start=1):
        if not isinstance(raw_stage, dict):
            continue
        areas = tuple(_text_list(raw_stage.get("coverage_areas")))
        if not areas:
            continue
        title = _text(raw_stage.get("title")) or _stage_title(index, areas)
        goal = _text(raw_stage.get("goal")) or _stage_goal(areas)
        seeds.append((title, goal, areas))
    return seeds


def _stage_seeds(areas: list[str]) -> list[tuple[str, str, tuple[str, ...]]]:
    if not areas:
        return []
    stage_count = min(MAX_JOURNEY_STAGES, max(1, math.ceil(len(areas) / 2)))
    chunks = _balanced_partition(areas, stage_count)
    return [
        (_stage_title(index, tuple(chunk)), _stage_goal(tuple(chunk)), tuple(chunk))
        for index, chunk in enumerate(chunks, start=1)
    ]


def _balanced_partition(items: list[str], parts: int) -> list[list[str]]:
    quotient, remainder = divmod(len(items), parts)
    chunks: list[list[str]] = []
    offset = 0
    for index in range(parts):
        size = quotient + (1 if index < remainder else 0)
        chunks.append(items[offset : offset + size])
        offset += size
    return [chunk for chunk in chunks if chunk]


def _stage_title(index: int, areas: tuple[str, ...]) -> str:
    if not areas:
        return f"Этап {index}"
    localized = tuple(language.localize_area_label(area) or area for area in areas)
    if len(areas) == 1:
        return localized[0]
    return f"{localized[0]} → {localized[-1]}"


def _stage_goal(areas: tuple[str, ...]) -> str:
    localized = [language.localize_area_label(area) or area for area in areas]
    return "Связать в практическом результате: " + "; ".join(localized)


def _ordered_coverage_areas(
    required_areas: list[str],
    nodes: list[PlanNode],
    raw_text: str,
    dag_payload: dict[str, Any],
) -> list[str]:
    areas = list(dict.fromkeys(area for area in required_areas if area))
    node_areas = list(dict.fromkeys(node.block_key for node in nodes if node.block_key))
    if not areas:
        areas = node_areas
        positions = _dag_positions(dag_payload)
        areas.sort(
            key=lambda area: min(
                (positions.get(node.tmp_id, 10**9) for node in nodes if _areas_match(area, node.block_key)),
                default=10**9,
            )
        )
    else:
        for area in node_areas:
            if not any(_areas_match(area, known) for known in areas):
                areas.append(area)

    if raw_text and not required_areas:
        folded = _normalize(raw_text)
        areas.sort(key=lambda area: _text_position(folded, area))
    return areas


def _assign_nodes_to_stages(
    nodes: list[PlanNode],
    stage_seeds: list[tuple[str, str, tuple[str, ...]]],
) -> dict[str, int]:
    if not stage_seeds:
        return {node.tmp_id: 0 for node in nodes}
    result: dict[str, int] = {}
    for node in nodes:
        best_stage = 0
        best_score = -1.0
        for stage_index, (_title, _goal, areas) in enumerate(stage_seeds):
            score = max((_area_similarity(node.block_key, area) for area in areas), default=0.0)
            if score > best_score:
                best_stage = stage_index
                best_score = score
        result[node.tmp_id] = best_stage
    return result


def _apply_dag_constraints(
    stage_by_node: dict[str, int],
    dag_payload: dict[str, Any],
) -> tuple[dict[str, int], tuple[str, ...]]:
    adjusted = dict(stage_by_node)
    notes: list[str] = []
    operational_edges = [
        edge
        for edge in dag_payload.get("final_edges", [])
        if isinstance(edge, dict) and curriculum_edge_role(edge) == "required"
    ]
    for _ in range(max(1, len(adjusted))):
        changed = False
        for edge in operational_edges:
            src_id = str(edge.get("src_id") or "")
            dst_id = str(edge.get("dst_id") or "")
            if src_id not in adjusted or dst_id not in adjusted:
                continue
            if adjusted[dst_id] < adjusted[src_id]:
                old_stage = adjusted[dst_id]
                adjusted[dst_id] = adjusted[src_id]
                notes.append(f"{dst_id}: этап {old_stage + 1} → {adjusted[dst_id] + 1} по prerequisite {src_id}")
                changed = True
        if not changed:
            break
    return adjusted, tuple(dict.fromkeys(notes))


def _hydrate_stage_nodes(
    stage_seeds: list[tuple[str, str, tuple[str, ...]]],
    nodes: list[PlanNode],
    stage_by_node: dict[str, int],
) -> tuple[CurriculumStageSpec, ...]:
    stages: list[CurriculumStageSpec] = []
    for index, (title, goal, areas) in enumerate(stage_seeds, start=1):
        node_ids = tuple(node.tmp_id for node in nodes if stage_by_node.get(node.tmp_id) == index - 1)
        stages.append(
            CurriculumStageSpec(
                code=f"stage-{index:02d}",
                title=title,
                goal=goal,
                coverage_areas=areas,
                node_ids=node_ids,
            )
        )
    return tuple(stages)


def _detect_journey_type(context: dict[str, Any], raw_text: str) -> str:
    explicit = _text(context.get("journey_type"))
    if explicit:
        return explicit
    haystack = _normalize(" ".join([_text(context.get("program_goal")), _text(context.get("domain")), raw_text]))
    signals = {
        "product_lifecycle": ("продукт", "mvp", "рынок", "запуск", "product", "startup"),
        "research_cycle": ("исследован", "гипотез", "данн", "эксперимент", "research"),
        "technology_mastery": ("технолог", "разработ", "инженер", "programming", "framework"),
        "operations_process": ("операцион", "процесс", "регламент", "эксплуатац", "operations"),
        "compliance_readiness": ("соответств", "требован", "аудит", "безопасност", "compliance"),
    }
    scores = {kind: sum(1 for signal in kind_signals if signal in haystack) for kind, kind_signals in signals.items()}
    best_kind, best_score = max(scores.items(), key=lambda item: item[1])
    return best_kind if best_score >= 2 else "professional_workflow"


def _capstone_required(context: dict[str, Any], raw_text: str) -> bool:
    explicit = context.get("capstone_required")
    if explicit is not None:
        return _as_bool(explicit)
    haystack = _normalize(" ".join([_text(context.get("program_goal")), raw_text]))
    return any(signal in haystack for signal in ("демо день", "итогов", "финальн", "защит", "capstone", "портфолио"))


def _capstone_title(journey_type: str, raw_text: str) -> str:
    folded = _normalize(raw_text)
    if "демо день" in folded:
        return "Итоговый проект и демо-день"
    if journey_type == "research_cycle":
        return "Итоговое исследование и защита результатов"
    return "Итоговый интеграционный проект"


def _extract_open_questions(raw_text: str) -> tuple[str, ...]:
    questions: list[str] = []
    for raw_line in raw_text.splitlines():
        line = " ".join(raw_line.split()).strip(" |-")
        if "?" not in line:
            continue
        for fragment in re.findall(r"[^?]{4,240}\?", line):
            question = fragment.strip(" |-")
            if question and question not in questions:
                questions.append(question)
            if len(questions) >= MAX_OPEN_QUESTIONS:
                return tuple(questions)
    return tuple(questions)


def _area_similarity(left: str, right: str) -> float:
    left_norm = _normalize(left)
    right_norm = _normalize(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.9
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    return len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)


def _areas_match(left: str, right: str) -> bool:
    return _area_similarity(left, right) >= 0.5


def _dag_positions(dag_payload: dict[str, Any]) -> dict[str, int]:
    return {
        str(item.get("id")): index
        for index, item in enumerate(dag_payload.get("order", []))
        if isinstance(item, dict) and item.get("id") is not None
    }


def _dag_fingerprint(dag_payload: dict[str, Any]) -> str:
    """Hash operational order and edge roles for design-approval invalidation."""

    edges = [
        {
            "src_id": str(edge.get("src_id") or ""),
            "dst_id": str(edge.get("dst_id") or ""),
            "role": curriculum_edge_role(edge),
        }
        for edge in dag_payload.get("final_edges", [])
        if isinstance(edge, dict)
    ]
    order = [
        str(item.get("id") or "")
        for item in dag_payload.get("order", [])
        if isinstance(item, dict)
    ]
    payload = {"edges": sorted(edges, key=lambda item: (item["src_id"], item["dst_id"], item["role"])), "order": order}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_position(haystack: str, value: str) -> int:
    position = haystack.find(_normalize(value))
    return position if position >= 0 else 10**9


def _normalize(value: object) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я+ ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _text_list(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [text for item in value if (text := _text(item))]


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"1", "true", "yes", "approved", "accepted"}


__all__ = [
    "CurriculumDesignSpec",
    "CurriculumStageSpec",
    "approve_curriculum_design_spec",
    "build_curriculum_design_spec",
]
