"""Conservative, deterministic classification of observable learner activity.

The activity axis is deliberately independent from the domain-oriented ``policy_area``.
Only a clearly dominant result is assigned automatically; weaker results remain visible as
suggestions for a methodologist. Generated titles, artifacts, and templates are excluded
from the evidence to prevent a circular dependency with artifact-contract selection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .domain import (
    ActivityArchetype,
    ActivityModifier,
    ArchetypeConfidence,
    CurriculumBlock,
    PlanNode,
    ProjectBlueprint,
)

CLASSIFICATION_VERSION = "activity-archetype/v1"

# Stems cover action verbs and the deverbal nouns used by the catalog. They are an
# inspectable classifier specification, not a claim of empirical cross-domain coverage.
ARCHETYPE_ACTION_HINTS: tuple[tuple[ActivityArchetype, tuple[str, ...]], ...] = (
    (
        "investigate",
        (
            "анализ",
            "исследован",
            "диагност",
            "наблюден",
            "сбор данн",
            "измерен",
            "сравнен",
            "выявлен",
            "интервью",
            "research",
            "investigat",
            "data analys",
        ),
    ),
    (
        "design",
        (
            "проектирован",
            "моделирован",
            "архитектур",
            "прототипирован",
            "планирован",
            "спецификац",
            "design",
            "architect",
            "modeling",
            "modelling",
        ),
    ),
    (
        "construct",
        (
            "разработ",
            "реализац",
            "создан",
            "сборк",
            "настройк",
            "конфигурац",
            "программирован",
            "implement",
            "build",
            "configur",
            "coding",
        ),
    ),
    (
        "operate",
        (
            "эксплуатац",
            "разверт",
            "развёрт",
            "деплой",
            "мониторинг",
            "сопровожд",
            "обслужив",
            "администр",
            "инцидент",
            "восстановлен",
            "operate",
            "deploy",
            "monitor",
            "maintain",
            "runbook",
        ),
    ),
    (
        "decide",
        (
            "приняти решен",
            "выбор решен",
            "приоритизац",
            "оценк вариант",
            "обоснован",
            "стратегическ решен",
            "decision",
            "prioritiz",
            "prioritis",
            "justify",
            "select option",
        ),
    ),
    (
        "perform",
        (
            "коммуникац",
            "презентац",
            "переговор",
            "консульт",
            "взаимодейств",
            "выступлен",
            "говорен",
            "аудирован",
            "письменн",
            "демонстрац навыка",
            "communicat",
            "present",
            "negotiat",
            "counsel",
            "perform",
        ),
    ),
)

ARCHETYPE_TOOL_HINTS: tuple[tuple[ActivityArchetype, tuple[str, ...]], ...] = (
    ("investigate", ("jupyter", "pandas", "опрос", "survey", "статист", "лабораторн оборудован")),
    ("design", ("figma", "uml", "bpmn", "cad", "сапр")),
    ("construct", ("ide", "framework", "фреймворк", "compiler", "компилятор")),
    ("operate", ("prometheus", "grafana", "kubernetes", "ansible", "terraform")),
    ("decide", ("decision matrix", "матрица решен")),
    ("perform", ("симулятор", "диктофон", "audio recorder", "видеозапись")),
)

HIGH_SCORE = 6
HIGH_MARGIN = 3
MEDIUM_SCORE = 3
MEDIUM_MARGIN = 2


@dataclass(frozen=True)
class ArchetypeResult:
    """Assigned verdict plus a preserved suggestion for ambiguous projects."""

    assigned: ActivityArchetype | None
    suggested: ActivityArchetype | None
    confidence: ArchetypeConfidence
    reasons: tuple[str, ...]
    modifiers: tuple[ActivityModifier, ...]
    version: str = CLASSIFICATION_VERSION


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").casefold().replace("ё", "е")).strip()


def _primary_nodes(project: ProjectBlueprint) -> list[PlanNode]:
    primary = [occurrence.node for occurrence in project.primary_occurrences]
    return primary or project.unique_nodes


def _matched_hints(text: str, hints: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(hint for hint in hints if hint in text)


def _bloom_priors(nodes: list[PlanNode]) -> tuple[ActivityArchetype, ...]:
    if not nodes:
        return ()
    bloom = round(sum(node.bloom for node in nodes) / len(nodes))
    if bloom == 3:
        return ("construct", "operate", "perform")
    if bloom == 4:
        return ("investigate", "decide")
    if bloom == 5:
        return ("design", "decide")
    if bloom >= 6:
        return ("design", "construct")
    return ()


def _experiment_modifier(text: str) -> bool:
    signal_pairs = (
        ("гипотез", "протокол"),
        ("hypothesis", "protocol"),
        ("контрольн", "измер"),
        ("control group", "measure"),
        ("эксперимент", "данн"),
        ("experiment", "data"),
    )
    return any(left in text and right in text for left, right in signal_pairs)


def classify_activity_archetype(project: ProjectBlueprint) -> ArchetypeResult:
    """Classify from source skills and outcomes, assigning only a high-confidence winner."""
    nodes = project.unique_nodes
    modifiers: list[ActivityModifier] = []
    if project.project_kind == "capstone":
        modifiers.append("integrative")
    if not nodes:
        return ArchetypeResult(None, None, "none", ("нет навыков в проекте",), tuple(modifiers))

    primary_nodes = _primary_nodes(project)
    primary_text = _norm(" ".join(node.name for node in primary_nodes))
    outcome_text = _norm(
        " ".join(
            outcome
            for node in nodes
            for outcome in (*node.outcomes_know, *node.outcomes_can, *node.outcomes_skills)
        )
    )
    supporting_text = _norm(" ".join(node.name for node in nodes if node not in primary_nodes))
    tools_text = _norm(" ".join(tool for node in nodes for tool in node.tools))
    source_text = " ".join((primary_text, outcome_text, supporting_text))
    if _experiment_modifier(source_text):
        modifiers.append("experiment")

    tool_hints = dict(ARCHETYPE_TOOL_HINTS)
    bloom_priors = set(_bloom_priors(primary_nodes))
    scored: list[tuple[int, ActivityArchetype, tuple[str, ...]]] = []
    for archetype, hints in ARCHETYPE_ACTION_HINTS:
        primary_matches = _matched_hints(primary_text, hints)
        outcome_matches = _matched_hints(outcome_text, hints)
        supporting_matches = _matched_hints(supporting_text, hints)
        tool_matches = _matched_hints(tools_text, tool_hints.get(archetype, ()))
        score = 2 * len(primary_matches) + 3 * len(outcome_matches) + len(supporting_matches) + len(tool_matches)
        reasons: list[str] = []
        if primary_matches:
            reasons.append("навыки: " + ", ".join(primary_matches))
        if outcome_matches:
            reasons.append("результаты: " + ", ".join(outcome_matches))
        if supporting_matches:
            reasons.append("поддерживающие навыки: " + ", ".join(supporting_matches))
        if tool_matches:
            reasons.append("инструменты: " + ", ".join(tool_matches))
        if archetype in bloom_priors:
            score += 1
            reasons.append("Bloom prior")
        if score:
            scored.append((score, archetype, tuple(reasons)))

    if not scored:
        return ArchetypeResult(
            None,
            None,
            "none",
            ("нет надёжных сигналов наблюдаемого действия",),
            tuple(modifiers),
        )

    order = {archetype: index for index, (archetype, _hints) in enumerate(ARCHETYPE_ACTION_HINTS)}
    scored.sort(key=lambda item: (-item[0], order[item[1]]))
    best_score, best_archetype, best_reasons = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0
    margin = best_score - second_score
    reasons = list(best_reasons)
    if len(scored) > 1 and margin < HIGH_MARGIN:
        reasons.append(f"альтернатива: {scored[1][1]} ({second_score})")
    reasons.append(f"score={best_score}; margin={margin}")

    if best_score >= HIGH_SCORE and margin >= HIGH_MARGIN:
        return ArchetypeResult(
            best_archetype,
            best_archetype,
            "high",
            tuple(reasons),
            tuple(modifiers),
        )
    confidence: ArchetypeConfidence = (
        "medium" if best_score >= MEDIUM_SCORE and margin >= MEDIUM_MARGIN else "low"
    )
    return ArchetypeResult(
        None,
        best_archetype,
        confidence,
        tuple(reasons),
        tuple(modifiers),
    )


def classify_activity_archetypes(blocks: list[CurriculumBlock]) -> None:
    """Populate the additive activity axis without touching legacy policy classification."""
    for block in blocks:
        for project in block.projects:
            if project.activity_archetype_source == "methodologist" and project.activity_archetype:
                continue
            result = classify_activity_archetype(project)
            project.activity_archetype = result.assigned or ""
            project.activity_archetype_suggestion = result.suggested or ""
            project.activity_archetype_confidence = result.confidence
            project.activity_archetype_reasons = result.reasons
            project.activity_archetype_modifiers = result.modifiers
            project.activity_archetype_source = "auto"
            project.activity_archetype_version = result.version
