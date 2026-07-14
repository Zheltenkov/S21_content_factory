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
from dataclasses import dataclass

from .domain import CurriculumBlock, PlanNode, ProjectBlueprint

#: Fixed, inspectable policy-area registry. Keys match the artifact policy matrix
#: (slice 4). Order is significant: earlier areas win ties. Hints are casefolded
#: substrings matched against the project's aggregated skill text.
POLICY_AREA_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai_quality_safety", ("eval", "guardrail", "safety", "галлюцин", "порог качеств", "эскалац", "разметк качеств")),
    ("ai_automation", ("ai", "ии", "автоматиз", "workflow", "воркфлоу", "агент", "llm", "промпт", "нейросет", "пайплайн данн")),
    ("operations", ("эксплуат", "deploy", "деплой", "разверт", "развёрт", "мониторинг", "логир", "health", "backup", "runbook", "инцидент", "sre", "надежн", "надёжн")),
    ("engineering_discipline", ("ci", "cd", "git", "тест", "репозитор", "релиз", "сборк", "версион", "code review", "ревью кода", "пайплайн сборк")),
    ("marketing_sales", ("маркет", "продаж", "лендинг", "реклам", "трафик", "конверси", "воронк", "канал привлеч", "позиционир", "продуктов аналитик")),
    ("monetization", ("монетиз", "тариф", "unit econom", "юнит эконом", "ценообраз", "выручк", "revenue", "подписк", "прайс")),
    ("product_creation", ("прототип", "mvp", "приложен", "сервис", "созда", "разработ", "постро")),
)


#: A hint in a PRIMARY skill weighs this much more than one in supporting text
#: (tools / outcomes / secondary skills), so incidental keywords do not classify a project.
PRIMARY_NAME_WEIGHT = 4
PRIMARY_ACTION_WEIGHT = 3
ARTIFACT_WEIGHT = 2
SUPPORTING_WEIGHT = 1
#: Minimum weighted score to assign an area at all (a single primary hit = PRIMARY_WEIGHT
#: clears it; a lone supporting-text hit = 1 does not).
MIN_SCORE = 3
#: The best area must beat the runner-up by this margin to be confident, else it is a
#: low-confidence (ambiguous) guess routed to the methodologist worklist.
MARGIN = 2
#: A clearly dominant match: two primary hits (or one primary + strong support) with a gap.
HIGH_SCORE = 6
HIGH_MARGIN = 3


@dataclass(frozen=True)
class PolicyAreaResult:
    """The classifier's per-project verdict: a candidate area + how sure + why."""

    area: str
    confidence: str  # high | medium | low | none
    rationale: str


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", " ".join(text.split()).casefold().replace("ё", "е"))


def _contains_hint(text: str, hint: str) -> bool:
    """Match short technical tokens at word boundaries and stems as substrings."""

    if len(hint) <= 2 and hint.isalnum():
        return re.search(rf"(?<!\w){re.escape(hint)}(?!\w)", text) is not None
    return hint in text


def _matches(text: str, hints: tuple[str, ...]) -> list[str]:
    return [hint for hint in hints if _contains_hint(text, hint)]


def _primary_nodes(project: ProjectBlueprint) -> list[PlanNode]:
    primary = [occurrence.node for occurrence in project.primary_occurrences]
    return primary or project.unique_nodes


def classify_policy_area(project: ProjectBlueprint) -> PolicyAreaResult:
    """Weighted best-matching policy area with a confidence band and rationale.

    Primary skills dominate the score; a single incidental keyword in tools/outcomes weighs
    little. A clear, dominant winner is high/medium confidence (auto-accepted); a weak or
    near-tie match is a low-confidence guess (worklist); no keyword hit at all is "none".
    The candidate area is kept even when low-confidence so the methodologist can confirm or
    correct it in one step rather than starting from nothing.
    """
    nodes = project.unique_nodes
    if not nodes:
        return PolicyAreaResult("", "none", "нет навыков в проекте")
    primary_nodes = _primary_nodes(project)
    primary_ids = {node.tmp_id for node in primary_nodes}
    primary_text = _norm(" ".join(node.name for node in primary_nodes))
    action_text = _norm(
        " ".join(
            outcome
            for node in primary_nodes
            for outcome in (*node.outcomes_can, *node.outcomes_skills)
        )
    )
    supporting_text = _norm(
        " ".join(
            occurrence.node.name
            + " "
            + " ".join(
                (*occurrence.node.outcomes_can, *occurrence.node.outcomes_skills, *occurrence.node.tools)
            )
            for occurrence in project.occurrences
            if occurrence.node.tmp_id not in primary_ids and not occurrence.is_repeat
        )
    )
    artifact_text = ""
    if project.template_binding is not None and project.template_binding.source == "brief":
        artifact_text = _norm(f"{project.artifact_family} {project.artifact}")

    scored: list[tuple[int, str, list[str], list[str], list[str], list[str]]] = []
    for area, hints in POLICY_AREA_HINTS:
        primary_matched = _matches(primary_text, hints)
        action_matched = _matches(action_text, hints)
        artifact_matched = _matches(artifact_text, hints)
        supporting_matched = _matches(supporting_text, hints)
        score = (
            PRIMARY_NAME_WEIGHT * len(primary_matched)
            + PRIMARY_ACTION_WEIGHT * len(action_matched)
            + ARTIFACT_WEIGHT * len(artifact_matched)
            + SUPPORTING_WEIGHT * len(supporting_matched)
        )
        if score:
            scored.append(
                (score, area, primary_matched, action_matched, artifact_matched, supporting_matched)
            )
    if not scored:
        return PolicyAreaResult("", "none", "нет совпадений по ключевым словам")
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_area, primary_matched, action_matched, artifact_matched, supporting_matched = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    margin = best_score - second_score
    parts: list[str] = []
    if primary_matched:
        parts.append("основные навыки: " + ", ".join(primary_matched))
    if action_matched:
        parts.append("наблюдаемые действия: " + ", ".join(action_matched))
    if artifact_matched:
        parts.append("ожидаемый артефакт: " + ", ".join(artifact_matched))
    if supporting_matched:
        parts.append("контекст: " + ", ".join(supporting_matched))
    rationale = "; ".join(parts) or "слабое совпадение"
    if best_score >= HIGH_SCORE and margin >= HIGH_MARGIN:
        confidence = "high"
    elif best_score >= MIN_SCORE and margin >= MARGIN:
        confidence = "medium"
    else:
        confidence = "low"
        if margin < MARGIN and len(scored) > 1:
            rationale += f"; неоднозначно с «{scored[1][1]}»"
    return PolicyAreaResult(best_area, confidence, rationale)


def classify_project_type(project: ProjectBlueprint) -> str:
    """lab (single skill) | capstone (final integrative) | project (multi-skill)."""
    if project.project_kind == "capstone":
        return "capstone"
    if len(project.unique_nodes) <= 1:
        return "lab"
    return "project"


def classify_projects(blocks: list[CurriculumBlock]) -> None:
    """Assign project_type + policy_area (+ confidence/rationale) in place (post-grouping)."""
    for block in blocks:
        for project in block.projects:
            project.project_type = classify_project_type(project)
            project.policy_area_source = "auto"
            if project.project_type == "capstone":
                project.policy_area = "capstone"
                project.policy_area_confidence = "high"
                project.policy_area_rationale = "итоговый интеграционный проект программы"
            else:
                result = classify_policy_area(project)
                project.policy_area = result.area
                project.policy_area_confidence = result.confidence
                project.policy_area_rationale = result.rationale
