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


def _project_text(nodes: list[PlanNode]) -> str:
    parts: list[str] = []
    for node in nodes:
        parts.extend([node.name, node.group, node.block_key, *node.outcomes_can, *node.outcomes_skills, *node.tools])
    text = " ".join(parts).casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def classify_policy_area(nodes: list[PlanNode]) -> str:
    """Return the best-matching policy-area key, or "" when nothing matches confidently."""
    if not nodes:
        return ""
    text = _project_text(nodes)
    best_area = ""
    best_score = 0
    for area, hints in POLICY_AREA_HINTS:
        score = sum(1 for hint in hints if hint in text)
        if score > best_score:
            best_area, best_score = area, score
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
                project.policy_area = classify_policy_area(project.unique_nodes)
