"""Explicit, deterministic project classification (contract epic, slice 3).

Anti-fragility foundation for the artifact policy registry (slice 4): instead of matching
each project to an artifact/template by lexical similarity *after the fact*, assign every
project a ``project_type`` (lab | project | capstone) and a ``policy_area`` (a key into a
small, fixed, inspectable registry) at grouping time. The classification is stored on the
``ProjectBlueprint`` and surfaced in the UP rows so it can be reviewed.

Key rule: when no policy area matches confidently, ``policy_area`` stays ``""`` — the
project is *visibly unclassified* (counted, draft-only later), never given a silent
generic artifact. That is the difference between a detected area and a guessed one.

Pure leaf: depends only on the domain dataclasses + stdlib.
"""

from __future__ import annotations

import re

from .domain import CurriculumBlock, PlanNode, ProjectBlueprint

#: Fixed, inspectable policy-area registry. Keys match the artifact policy matrix
#: (slice 4). Order is significant: earlier areas win ties. Hints are casefolded
#: substrings matched against the project's aggregated skill text.
POLICY_AREA_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai_quality_safety", ("eval", "guardrail", "safety", "галлюцин", "порог качеств", "эскалац", "разметк качеств")),
    ("ai_automation", ("ai", "ии", "автоматиз", "workflow", "воркфлоу", "агент", "llm", "промпт", "нейросет", "пайплайн данн")),
    ("operations", ("эксплуат", "deploy", "деплой", "разверт", "развёрт", "мониторинг", "логир", "health", "backup", "runbook", "инцидент", "sre", "надежн", "надёжн")),
    ("engineering_discipline", ("ci", "cd", "git", "тест", "репозитор", "релиз", "сборк", "версион", "code review", "ревью кода", "пайплайн сборк")),
    ("marketing_sales", ("маркет", "продаж", "лендинг", "реклам", "трафик", "конверси", "воронк", "канал привлеч", "продуктов аналитик")),
    ("monetization", ("монетиз", "тариф", "unit econom", "юнит эконом", "ценообраз", "выручк", "revenue", "подписк", "прайс")),
    ("product_creation", ("прототип", "mvp", "продукт", "приложен", "сервис", "созда", "разработ", "постро", "запуск продукт")),
)


#: A hint in a PRIMARY skill weighs this much more than one in supporting text
#: (tools / outcomes / secondary skills), so incidental keywords do not classify a project.
PRIMARY_WEIGHT = 3
#: Minimum weighted score to assign an area at all (a single primary hit = PRIMARY_WEIGHT
#: clears it; a lone supporting-text hit = 1 does not).
MIN_SCORE = 3
#: The best area must beat the runner-up by this margin, else the project is ambiguous → "".
MARGIN = 2


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", " ".join(text.split()).casefold().replace("ё", "е"))


def _primary_nodes(project: ProjectBlueprint) -> list[PlanNode]:
    primary = [occurrence.node for occurrence in project.primary_occurrences]
    return primary or project.unique_nodes


def classify_policy_area(project: ProjectBlueprint) -> str:
    """Weighted best-matching policy area, or "" when weak or ambiguous.

    Primary skills dominate; a single incidental keyword in tools/outcomes/supporting skills
    is not enough (MIN_SCORE), and a near-tie between two areas stays unclassified (MARGIN).
    Unclassified is the safe outcome — the gate flags it for a methodologist.
    """
    nodes = project.unique_nodes
    if not nodes:
        return ""
    primary_ids = {node.tmp_id for node in _primary_nodes(project)}
    primary_text = _norm(" ".join(node.name + " " + node.group for node in nodes if node.tmp_id in primary_ids))
    supporting_text = _norm(
        " ".join(
            node.block_key
            + " "
            + " ".join([*node.outcomes_can, *node.outcomes_skills, *node.tools])
            + ("" if node.tmp_id in primary_ids else " " + node.name + " " + node.group)
            for node in nodes
        )
    )
    scores: list[tuple[int, str]] = []
    for area, hints in POLICY_AREA_HINTS:
        primary_hits = sum(1 for hint in hints if hint in primary_text)
        supporting_hits = sum(1 for hint in hints if hint in supporting_text)
        score = PRIMARY_WEIGHT * primary_hits + supporting_hits
        if score:
            scores.append((score, area))
    if not scores:
        return ""
    scores.sort(reverse=True)
    best_score, best_area = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0
    if best_score < MIN_SCORE or best_score - second_score < MARGIN:
        return ""
    return best_area


def classify_project_type(project: ProjectBlueprint) -> str:
    """lab (single skill) | capstone (final integrative) | project (multi-skill)."""
    if project.project_kind == "capstone":
        return "capstone"
    if len(project.unique_nodes) <= 1:
        return "lab"
    return "project"


def classify_projects(blocks: list[CurriculumBlock]) -> None:
    """Assign project_type + policy_area to every project in place (post-grouping pass)."""
    for block in blocks:
        for project in block.projects:
            project.project_type = classify_project_type(project)
            if project.project_type == "capstone":
                project.policy_area = "capstone"
            else:
                project.policy_area = classify_policy_area(project)
