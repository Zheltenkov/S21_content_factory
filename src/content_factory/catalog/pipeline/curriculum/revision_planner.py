"""Compose a proposed curriculum from accepted DAG work and an identified historical register."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any, Literal

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import config
from .domain import AcceptanceCriterion, ArtifactContract, CurriculumBlock, PlanNode, ProjectBlueprint
from .edge_policy import curriculum_edge_role
from .methodology_profile import MethodologyProfile
from .planner import build_curriculum_blocks
from .revision_contracts import RevisionPractice

Disposition = Literal["retain", "revise", "merge", "specialize", "omit", "hold"]
TeamMode = Literal["team", "individual", "unknown"]


class SourceWork(BaseModel):
    """Source facts are independent of the proposed title, scope and workload."""

    model_config = ConfigDict(str_strip_whitespace=True)
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    source_ref: str = ""
    source_hours: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    block: str = ""
    team_mode: TeamMode = "unknown"
    outcomes: list[str] = Field(default_factory=list)
    card_title: str = ""
    card_ref: str = ""
    review_note: str = ""

    @property
    def subject(self) -> str:
        # When there is no human title, prefer the first complete source topic to a platform code.
        if self.card_title:
            return self.card_title
        if "_" in self.title and self.description:
            subject = re.split(r"[.!?\n]", self.description.lstrip(" -"), maxsplit=1)[0].strip()
            if 4 <= len(subject) <= 95:
                return subject
        return self.title


class RevisionDecision(RevisionPractice):
    """An explicit proposal. Only an upstream methodologist decision can set approved."""

    model_config = ConfigDict(str_strip_whitespace=True)
    source_ids: list[str] = Field(min_length=1)
    disposition: Disposition
    rationale: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    approved: bool = Field(default=False, strict=True)
    title: str = ""
    participant_action: str = ""
    artifact: str = ""
    validation_criteria: str = ""
    target_team_mode: TeamMode | Literal[""] = ""
    lost_outcomes: list[str] = Field(default_factory=list)
    review_question: str = ""
    requirement_refs: list[str] = Field(default_factory=list)
    evidence: list[dict[str, str]] = Field(default_factory=list)


@dataclass
class WorkGroup:
    sources: list[SourceWork]
    decision: RevisionDecision | None = None
    recommendations: list[dict[str, str]] = field(default_factory=list)
    anchor: ProjectBlueprint | None = None
    matched_skill_terms: dict[str, list[str]] = field(default_factory=dict)


_TOKEN = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+(?:[+#][+#]?)?")
_STOP = frozenset(
    "работа работающий проект задача учебный участник программа результаты результат решение пример "
    "данные практический применять уметь знает понимает умеет знать навыки навыков "
    "который данный основа основные новое свой можно нужно должна должен использовать "
    "требуется основание требования методы умение работа работы работающих".split()
)


def _terms(text: str) -> set[str]:
    """Short shared Russian stems and whole foreign/tool names, never substrings of identifiers."""
    tokens = [item.casefold().replace("ё", "е") for item in _TOKEN.findall(text)]
    return {
        word[:6] if len(word) >= 7 and word.isascii() is False else word
        for word in tokens if len(word) >= 2 and not word.isdigit() and word not in _STOP
    }


def _stable_id(kind: str, keys: list[str]) -> str:
    body = json.dumps(sorted(keys), ensure_ascii=False, separators=(",", ":"))
    return f"{kind}-{hashlib.sha256(body.encode()).hexdigest()[:20]}"


def _source_groups(spec: dict[str, Any]) -> tuple[list[WorkGroup], list[str]]:
    sources: dict[str, SourceWork] = {}
    issues: list[str] = []
    for index, raw in enumerate(spec["source_curriculum"], 1):
        try:
            source = SourceWork.model_validate(raw)
        except ValidationError:
            issues.append(f"Некорректная карточка в исходном реестре, позиция {index}.")
            continue
        if source.source_id in sources:
            issues.append(f"Повтор идентификатора исходной работы: {source.source_id}.")
            continue
        sources[source.source_id] = source

    assigned: dict[str, WorkGroup] = {}
    for index, raw in enumerate(spec.get("revision_decisions") or [], 1):
        try:
            decision = RevisionDecision.model_validate(raw)
        except ValidationError:
            issues.append(f"Неполное предложение изменения, позиция {index}.")
            continue
        keys = decision.source_ids
        if len(set(keys)) != len(keys) or not set(keys) <= sources.keys() or set(keys) & assigned.keys():
            issues.append(f"Неизвестная или повторная исходная позиция в решении {index}.")
            continue
        members = [sources[key] for key in keys]
        if (decision.disposition == "merge") != (len(keys) > 1):
            issues.append(f"Число исходных позиций не соответствует решению {index}.")
            continue
        if decision.disposition == "omit" and not (decision.approved and decision.lost_outcomes):
            decision = decision.model_copy(update={
                "disposition": "hold", "review_question": decision.review_question or "Подтвердить исключение и утрачиваемый результат?",
            })
        if decision.disposition in {"revise", "merge", "specialize"} and not all(
            (decision.title, decision.participant_action, decision.artifact, decision.validation_criteria)
        ):
            issues.append(f"В решении {index} отсутствует проектный контракт.")
            continue
        modes = {source.team_mode for source in members} - {"unknown"}
        changes_mode = bool(modes and decision.target_team_mode and modes != {decision.target_team_mode})
        if (len(modes) > 1 or changes_mode) and not (
            decision.approved and decision.target_team_mode in {"team", "individual"}
        ):
            issues.append(f"Для изменения формы сдачи в решении {index} требуется подтверждение.")
            continue
        group = WorkGroup(members, decision)
        assigned.update(dict.fromkeys(keys, group))

    groups: list[WorkGroup] = []
    seen: set[str] = set()
    for key, source in sources.items():
        if key in seen:
            continue
        group = assigned.get(key) or WorkGroup([source])
        groups.append(group)
        seen.update(item.source_id for item in group.sources)
    return groups, issues


def _source_terms(group: WorkGroup) -> set[str]:
    parts = [f"{s.title} {s.card_title} {s.description} {' '.join(s.outcomes)}" for s in group.sources]
    if group.decision:
        parts += [group.decision.title, group.decision.participant_action, " ".join(group.decision.required_tools)]
    return _terms(" ".join(parts))


def _overlap(a: set[str], b: set[str], weights: dict[str, float]) -> float:
    """Require two supported terms or an exact distinctive named tool on both sides."""
    shared = a & b
    if len(shared) < 2 and not any(t.isascii() and len(t) >= 3 and weights.get(t, 0) > 1.4 for t in shared):
        return 0.0
    numerator = sum(weights.get(t, 1) ** 2 for t in sorted(shared))
    return numerator / math.sqrt(max(1, len(a) * len(b)))


def _research_updates(requirements: str) -> list[dict[str, str]]:
    """Parse cited research paragraphs, keeping the entire quote and its reference for review."""
    updates = []
    for paragraph in requirements.splitlines():
        match = re.match(r"\s*(?:\d+[.)]|[-*])\s*\*\*(.+?)\*\*\s*(.+)", paragraph)
        if not match:
            continue
        title, body = match.groups()
        parts = re.split(r"\b(?:Основание|Источники?)\s*[—:\-]\s*", body, maxsplit=1, flags=re.I)
        if len(parts) == 2 and all(part.strip() for part in parts):
            updates.append({"topic": title.strip(". "), "quote": parts[0].strip(), "source_ref": parts[1].strip()})
    return updates


def _bind_inputs(
    groups: list[WorkGroup], projects: list[ProjectBlueprint], updates: list[dict[str, str]]
) -> list[str]:
    """Bind explicit identifiers first, otherwise rank by shared distinctive vocabulary."""
    documents = [_source_terms(group) for group in groups]
    counts = Counter(term for doc in documents for term in doc)
    weights = {term: 1 + math.log((len(groups) + 1) / (count + 1)) for term, count in counts.items()}
    diagnostics: list[str] = []
    # A proposed existing final project may fulfil the integration slot in the accepted design.
    capstones = [p for p in projects if p.project_kind == "capstone"]
    nominated = [g for g in groups if g.decision and g.decision.is_capstone]
    for update in updates:
        quote = update["quote"]
        identifiers = {
            index for index, group in enumerate(groups) for source in group.sources
            for code in (source.source_id, source.title.split("_")[0])
            if code and re.search(rf"(?<!\w){re.escape(code)}(?!\w)", quote, re.I)
        }
        if identifiers:
            for index in sorted(identifiers):
                groups[index].recommendations.append({**update, "binding": "source_id"})
            continue
        terms = _terms(update["topic"] + " " + quote)
        ranked = sorted(
            ((_overlap(terms, doc, weights), i) for i, doc in enumerate(documents)), key=lambda pair: (-pair[0], pair[1])
        )
        if ranked and ranked[0][0] >= 0.5:
            groups[ranked[0][1]].recommendations.append({**update, "binding": "topic_proposal"})
        else:
            diagnostics.append(f"Методологу определить место требования «{update['topic']}». {update['source_ref']}")

    # Assign a complete accepted project only once. This preserves accepted template bindings,
    # primary occurrences and the original DAG packing, including required prerequisite splits.
    # Use the proposed practice to justify its accepted skills. A mention in the old syllabus,
    # a tool list or an optional route cannot establish mastery on the new core route.
    practice_docs = [
        _terms(" ".join((d.title, d.participant_action, d.artifact, d.validation_criteria)))
        if (d := group.decision) and d.disposition in {"revise", "merge", "specialize"} else doc
        for group, doc in zip(groups, documents, strict=True)
    ]
    connectors = set("на по из от при для под над без со во за об их его как что или где это все".split())
    # Inflected forms can survive the exact stop-word filter. Exclude their normalized
    # tokens too, so generic instructional vocabulary cannot establish subject mastery.
    connectors.update(word[:6] if len(word) >= 7 else word for word in _STOP)
    matches: list[tuple[float, int, int, dict[str, list[str]]]] = []
    for p, project in enumerate(projects):
        if project.project_kind == "capstone" and (len(nominated) != 1 or len(capstones) != 1):
            continue
        nodes = [o.node for o in project.primary_occurrences] or project.unique_nodes
        scopes = [
            (node.tmp_id, _terms(" ".join((node.name, *node.outcomes_can, *node.outcomes_skills))) - connectors,
             _terms(" ".join(node.tools)))
            for node in nodes
        ]
        for i, doc in enumerate(practice_docs):
            decision = groups[i].decision
            if project.project_kind == "capstone" and groups[i] is not nominated[0]:
                continue
            if decision and decision.disposition == "specialize":
                continue
            evidence: dict[str, list[str]] = {}
            scores: list[float] = []
            for node_id, scope, tools in scopes:
                shared = scope & doc
                if len(shared) < 2 or not shared - tools:
                    break
                score = _overlap(scope, doc, weights)
                if score < 0.5:
                    break
                scores.append(score)
                evidence[node_id] = sorted(shared)
            if scores and len(scores) == len(scopes):
                matches.append((min(scores), p, i, evidence))
    used: set[int] = set()
    for _, p, i, evidence in sorted(matches, key=lambda item: (
        projects[item[1]].project_kind != "capstone", -item[0], item[1], item[2],
    )):
        if p not in used and groups[i].anchor is None:
            groups[i].anchor = projects[p]
            groups[i].matched_skill_terms = evidence
            used.add(p)
    return diagnostics


def _action(source: SourceWork) -> str:
    # Prefer a concrete historical performance indicator to an invented skill.
    for outcome in source.outcomes:
        verb = re.match(r"(?:умеет|уметь|может)\s*[:—-]\s*(.+)", outcome, re.I)
        if verb:
            return verb[1][:1].upper() + verb[1][1:].rstrip(".") + "."
    topics = [s.strip(" -.;") for s in re.split(r"[.;\n]+", source.description) if s.strip(" -.;")]
    if len(topics) > 1:
        return f"Решить пример по теме «{topics[0]}» и показать на результате, как используется «{topics[1]}»."
    return f"Подготовить собственный пример по теме «{source.subject}», показать ход решения и обосновать выбор."


def _practice(group: WorkGroup) -> dict[str, Any]:
    source = group.sources[0]
    decision = group.decision
    ids = [item.source_id for item in group.sources]
    title = (decision.title if decision else "") or source.subject
    action = (decision.participant_action if decision else "") or _action(source)
    artifact = (decision.artifact if decision else "") or f"Разбор «{title}» с исходными условиями, ходом работы и полученным результатом."
    criteria = (decision.validation_criteria if decision else "") or (
        f"Проверяющий меняет одно исходное условие в работе «{title}». "
        "Участник показывает затронутый шаг, повторяет его и объясняет изменение результата."
    )
    mode = (decision.target_team_mode if decision and decision.target_team_mode else source.team_mode)
    if mode == "team":
        criteria += " Каждый участник предъявляет свою часть общей работы и объясняет, как команда её проверила."
    return {
        "revision_project_id": _stable_id("source", ids), "source_unit_ids": ids,
        "is_entry_project": bool(decision and decision.is_entry_project),
        "title": title, "participant_action": action, "artifact": artifact, "validation_criteria": criteria,
        "team_mode": mode, "route": "specialization" if decision and decision.disposition == "specialize" else "core",
        "choose_one_group": decision.choose_one_group if decision and decision.disposition == "specialize" else "",
        "group_size": decision.group_size if decision else None,
        "peer_reviews": decision.peer_reviews if decision else None,
        "acceptance_test": decision.acceptance_test.model_dump() if decision and decision.acceptance_test else None,
        "effort_hours": decision.effort_hours if decision and decision.effort_hours is not None else (
            sum(item.source_hours or 0 for item in group.sources) or None
        ),
        "hours_basis": "revision_estimate" if decision and decision.effort_hours is not None else "historical_reference",
        "source_titles": [item.title for item in group.sources],
        "source_refs": [item.source_ref for item in group.sources], "source_content": [item.description for item in group.sources],
        "source_outcomes": [outcome for item in group.sources for outcome in item.outcomes],
        "updated_practice": bool(decision and decision.disposition in {"revise", "merge", "specialize"}),
        "learning_outcomes": decision.learning_outcomes.model_dump() if decision else {},
        "required_tools": decision.required_tools if decision else [],
        "inputs": decision.inputs if decision else [], "case_context": decision.case_context if decision else "",
        "research_evidence": group.recommendations,
        "review_notes": [item.review_note for item in group.sources if item.review_note],
        "decision_ref": decision.source_ref if decision else "",
        "requirement_refs": decision.requirement_refs if decision else [],
        "rationale": decision.rationale if decision else "",
    }


def _dispositions(group: WorkGroup, target_id: str | None) -> list[dict[str, Any]]:
    d = group.decision
    return [{
        "source_id": s.source_id, "source_ref": s.source_ref,
        "disposition": d.disposition if d else "retain",
        "target_project_ids": [target_id] if target_id else [],
        "rationale": d.rationale if d else "Основа строки — прежний УП. Действие, сдачу и проверку ещё предстоит согласовать.",
        "decision_origin": ("methodologist" if d.approved else "proposal") if d else "historical_copy",
        "decision_approved": bool(d and d.approved), "decision_ref": d.source_ref if d else "",
        "prerequisite_source_ids": sorted(set(d.prerequisite_source_ids)) if d else [],
        "requirement_refs": d.requirement_refs if d else [],
        "evidence": d.evidence if d else [],
        "target_team_mode": d.target_team_mode if d else "",
        "lost_outcomes": d.lost_outcomes if d else [], "review_question": d.review_question if d else "",
    } for s in group.sources]


def _schedule_revision(
    entries: list[tuple[ProjectBlueprint, dict[str, Any], str, str]], dag_sequence: list[int],
    requested: dict[int, list[str]], errors: list[str], skill_edges: list[dict[str, Any]],
) -> tuple[list[int], dict[str, set[str]]]:
    """Schedule project deliverables and required skills; scaffold adjacency is not evidence of dependency."""
    schedule = nx.DiGraph()
    schedule.add_nodes_from(range(len(entries)))
    end = dag_sequence[-1] if dag_sequence and entries[dag_sequence[-1]][0].project_kind == "capstone" else None
    if end is not None:
        schedule.add_edges_from((i, end) for i in range(len(entries)) if i != end)

    # Resolve old IDs to surviving rows after merges, omissions and holds.
    rows = [row for _, row, _, _ in entries]
    starts = [i for i, row in enumerate(rows) if row.get("is_entry_project")]
    if len(starts) > 1:
        errors.append("Несколько работ назначены началом программы. Согласуйте одну вводную работу.")
    elif starts:
        start = starts[0]
        if rows[start]["route"] != "core":
            errors.append("Стартовая работа должна быть доступна всем участникам.")
        schedule.add_edges_from((start, i) for i in range(len(rows)) if i != start)
    source_rows = {old: i for i, row in enumerate(rows) for old in row["source_unit_ids"]}
    declared: set[tuple[int, int]] = set()
    for target, source_ids in requested.items():
        for old in sorted(set(source_ids)):
            origin = source_rows.get(old)
            label = ", ".join(rows[target]["source_unit_ids"])
            if origin is None:
                errors.append(f"{label}: для предшествующей работы {old} отсутствует конечный проект.")
            elif origin == target:
                errors.append(f"{label}: проект ссылается на себя через предшествующую работу {old}.")
            elif rows[origin]["route"] == "specialization" and rows[target]["route"] == "core":
                errors.append(f"{label}: основная работа зависит от специализации {old}.")
            elif rows[origin].get("choose_one_group") and rows[origin].get("choose_one_group") == rows[target].get("choose_one_group"):
                errors.append(f"{label}: требуются две взаимоисключающие работы одной специализации ({old}).")
            else:
                declared.add((origin, target))

    schedule.add_edges_from(sorted(declared))
    prerequisites: dict[str, set[str]] = {}
    for origin, target in declared:
        prerequisites.setdefault(rows[target]["revision_project_id"], set()).add(rows[origin]["revision_project_id"])

    # Several projects can introduce the same skill. Keep alternatives until an actual
    # earlier provider is known. An optional route cannot supply the whole cohort.
    required: dict[int, list[set[int]]] = {i: [] for i in range(len(entries))}
    preferred: dict[int, list[set[int]]] = {i: [] for i in range(len(entries))}
    primary = [{o.node.tmp_id for o in p.primary_occurrences} for p, *_ in entries]
    for edge in skill_edges:
        role = curriculum_edge_role(edge)
        if role == "related":
            continue
        src, dst = str(edge.get("src_id")), str(edge.get("dst_id"))
        for target in dag_sequence:
            if dst not in entries[target][0].node_ids or (role == "recommended" and src in primary[target]):
                continue
            providers = {i for i in dag_sequence if src in primary[i] and i != end and i != target
                         and rows[i]["route"] != "specialization"}
            if not providers:
                if role == "required":
                    errors.append(f"{dst}: для входного навыка {src} отсутствует вводящая работа общего маршрута.")
                continue
            if role == "recommended":
                preferred[target].append(providers)
            else:
                required[target].append(providers)
                if len(providers) == 1:
                    schedule.add_edge(next(iter(providers)), target)

    # A supplier inherits the historical priority of the work waiting for it.
    # Condensation also lets us place unrelated work correctly in a draft with a real cycle.
    priority = list(range(len(entries)))
    condensed = nx.condensation(schedule)
    for component in reversed(list(nx.topological_sort(condensed))):
        members = condensed.nodes[component]["members"]
        children = {child for i in members for child in schedule.successors(i) if child != end}
        earliest = min(priority[j] for j in members | children)
        for i in members:
            priority[i] = earliest
    done: set[int] = set()
    order: list[int] = []
    while len(done) < len(entries):
        remaining = set(schedule) - done
        waiting = {i: set(schedule.predecessors(i)) - done for i in remaining}
        for i in remaining:
            for options in required[i]:
                if options.isdisjoint(done):
                    waiting[i].update(options)
        ready = [i for i in remaining if not waiting[i]]
        if not ready:
            # Break a deadlock only to display the draft. Keep the actual links and a
            # concrete cycle so a reviewer can resolve the contradictory requirements.
            blocked = nx.DiGraph()
            blocked.add_edges_from((j, i) for i in sorted(remaining) for j in sorted(waiting[i]))
            components = nx.condensation(blocked)
            members = min((components.nodes[k]["members"] for k in components if not components.in_degree(k)), key=min)
            cycle = nx.find_cycle(blocked.subgraph(members))
            path = [cycle[0][0], *(b for _, b in cycle)]
            names = [rows[i].get("title") or entries[i][0].title or rows[i]["revision_project_id"] for i in path]
            errors.append("Цикл предшествования: " + " → ".join(names) + ".")
            ready = sorted(members)
        # Older decisions have no explicit entry role: retain the first source at entry if feasible.
        if not order and not starts and 0 in ready and rows[0]["source_unit_ids"]:
            chosen = 0
        else:
            chosen = min(ready, key=lambda i: (sum(opts.isdisjoint(done) for opts in preferred[i]), priority[i], i))
        for options in required[chosen]:
            if previous := options & done:
                origin = next(i for i in order if i in previous)
                prerequisites.setdefault(rows[chosen]["revision_project_id"], set()).add(rows[origin]["revision_project_id"])
        order.append(chosen)
        done.add(chosen)
    return order, prerequisites


def build_revision_blocks(
    nodes: list[PlanNode], dag_payload: dict[str, Any], spec: dict[str, Any], *, profile: MethodologyProfile
) -> tuple[list[CurriculumBlock], dict[str, Any]]:
    """Build the ordinary DAG projects and incorporate independent historical and revised work."""
    groups, decision_errors = _source_groups(spec)
    requirements = spec.get("requirements_text", "")
    context = {**spec, "raw_text": requirements} if requirements else spec
    templates = spec.get("artifact_templates")
    dag_blocks, meta = build_curriculum_blocks(nodes, dag_payload, templates if isinstance(templates, list) else [],
                                               planning_context=context, profile=profile)
    projects = [p for block in dag_blocks for p in block.projects]
    active = [g for g in groups if not g.decision or g.decision.disposition not in {"omit", "hold"}]
    nominations = [g for g in active if g.decision and g.decision.is_capstone]
    if len(nominations) > 1:
        decision_errors.append("Несколько прежних работ предложены для одной итоговой защиты. Уточните выбор.")
    if any(g.decision and g.decision.disposition == "specialize" for g in nominations):
        decision_errors.append("Для защиты всего потока указана работа специализации. Уточните маршрут защиты.")
    if nominations and not any(p.project_kind == "capstone" for p in projects):
        decision_errors.append("Предложена итоговая работа. Для её размещения согласуйте итоговую защиту в каркасе УП.")
    diagnostics = _bind_inputs(active, projects, _research_updates(requirements))
    if len(nominations) == 1 and any(p.project_kind == "capstone" for p in projects):
        anchor = nominations[0].anchor
        if not anchor or anchor.project_kind != "capstone":
            decision_errors.append("Содержание предложенной защиты не подтверждает все основные навыки итогового "
                                   "проекта. Проверьте задания защиты или состав навыков в каркасе.")

    entries: list[tuple[ProjectBlueprint, dict[str, Any], str, str]] = []
    dispositions: list[dict[str, Any]] = []
    by_project: dict[int, int] = {}
    requested: dict[int, list[str]] = {}
    for group in groups:
        if group.decision and group.decision.disposition in {"hold", "omit"}:
            dispositions.extend(_dispositions(group, None))
            continue
        row = _practice(group)
        block_title = group.sources[0].block or "Практика программы"
        p = group.anchor or ProjectBlueprint(
            occurrences=[], block_key=block_title, artifact=row["artifact"], artifact_key=row["revision_project_id"],
            title=row["title"], project_kind="source_curriculum",
        )
        if group.anchor:
            by_project[id(group.anchor)] = len(entries)
            row["accepted_skill_binding"] = {
                "basis": "nominated_capstone" if p.project_kind == "capstone" else "primary_skill_terms",
                "matched_terms": group.matched_skill_terms,
            }
        if group.decision and group.decision.prerequisite_source_ids:
            requested[len(entries)] = group.decision.prerequisite_source_ids
        entries.append((p, row, block_title, ""))
        dispositions.extend(_dispositions(group, row["revision_project_id"]))

    # Preserve all accepted projects without claiming that a historical project taught an unrelated skill.
    for block in dag_blocks:
        for p in block.projects:
            if id(p) in by_project:
                continue
            by_project[id(p)] = len(entries)
            action = " ".join(dict.fromkeys(
                outcome for n in p.unique_nodes for outcome in (n.outcomes_can or n.outcomes_skills)
            )) or f"Продемонстрировать выполнение действий: {', '.join(n.name for n in p.unique_nodes)}."
            entries.append((p, {
                "revision_project_id": _stable_id("dag", [p.project_kind, p.artifact_key, *p.node_ids]),
                "source_unit_ids": [], "participant_action": action,
                "route": "core" if p.template_binding else "needs_design", "hours_basis": "dag_estimate",
            }, block.title or p.block_key, block.goal))

    dag_sequence = [by_project[id(p)] for p in projects]
    skill_edges = [e for e in dag_payload.get("final_edges", []) if isinstance(e, dict)]
    order, prerequisites = _schedule_revision(entries, dag_sequence, requested, decision_errors, skill_edges)
    ordered = [entries[i] for i in order]
    blocks: list[CurriculumBlock] = []
    for key, chunk in groupby(ordered, key=lambda item: item[2]):
        items = list(chunk)
        blocks.append(CurriculumBlock(block_keys=(key,), title=key, goal=items[0][3], projects=[p for p, *_ in items]))
    row_specs = [row for _, row, _, _ in ordered]
    for proposal in row_specs:
        proposal["prerequisite_project_ids"] = sorted(prerequisites.get(proposal["revision_project_id"], set()))
    meta["artifact_project_count"] = len(row_specs)
    meta["template_unbound_project_count"] = len(row_specs) - meta.get("template_bound_project_count", 0)
    meta["revision"] = {
        "version": 2, "source_curriculum": spec["source_curriculum"], "dispositions": dispositions,
        "required_skill_edges": [[e.get("src_id"), e.get("dst_id")] for e in skill_edges
                                 if curriculum_edge_role(e) == "required"],
        "entry_project_ids": [r["revision_project_id"] for r in row_specs if r.get("is_entry_project")],
        "terminal_project_id": (entries[dag_sequence[-1]][1]["revision_project_id"]
                                if dag_sequence and entries[dag_sequence[-1]][0].project_kind == "capstone" else None),
        "row_specs": row_specs, "diagnostics": [*decision_errors, *diagnostics], "decision_errors": decision_errors,
    }
    return blocks, meta


def apply_revision_rows(rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    """Transfer proposal fields to standard UP rows before workload and aggregate computation."""
    for row, proposal in zip(rows, meta["revision"]["row_specs"], strict=True):
        for key in ("revision_project_id", "source_unit_ids", "participant_action", "route", "hours_basis", "prerequisite_project_ids"):
            row[key] = proposal[key]
        if not proposal["source_unit_ids"]:
            if proposal["route"] == "needs_design":
                skills = ", ".join(row.get("node_names") or [])
                row["project_name"] = row["platform_project_name"] = f"На проектирование: {skills}"
                # A skill gap is an input to design. Scaffolding for a possible project must
                # not publish demands such as a public demo or release into its review copy.
                row["project_summary"] = proposal["participant_action"]
                row.update(artifact="", validation_criteria="", storytelling="", artifact_contract={},
                           artifact_contract_sources=[], artifact_slot_sources={}, artifact_merge_diagnostics=[])
                row["materials"] = (
                    "Дополнительная заявка из графа навыков. Методологу подобрать работу и способ сдачи. "
                    "Предварительные часы заявки учтены отдельно от маршрута участника."
                )
            continue
        row["source_refs"] = proposal["source_refs"]
        row["research_evidence"] = proposal["research_evidence"]
        row["choose_one_group"] = proposal["choose_one_group"]
        row["peer_reviews"] = proposal["peer_reviews"]
        row["decision_ref"] = proposal["decision_ref"]
        row["requirement_refs"] = proposal["requirement_refs"]
        row["project_name"] = row["platform_project_name"] = proposal["title"]
        # Keep the explicit template/assessment floor when a source proposal shares its project.
        contracted = bool(row.get("template_binding") or (row.get("artifact_contract") and not proposal["updated_practice"]))
        test = proposal["acceptance_test"]
        if proposal["updated_practice"] and not contracted:
            # A catalog template selected for this project is authoritative. Generic profile
            # examples, such as a public product launch, are not evidence of client requirements.
            row["artifact_contract"] = None
            row["artifact_contract_sources"] = []
            row["artifact_slot_sources"] = {}
            if test:
                criterion = AcceptanceCriterion(proposal["title"], test["check"], test["expected_result"], proposal["artifact"])
                row["artifact_contract"] = ArtifactContract(
                    artifact_type="revision", policy_area=row.get("policy_area") or "",
                    deliverables=(proposal["artifact"],), evidence_requirements=(proposal["artifact"],),
                    acceptance_criteria=(criterion,), execution_environment=", ".join(proposal["required_tools"]),
                ).as_dict()
                row["artifact_contract_sources"] = ["draft"]
                row["artifact_slot_sources"] = {"artifact": ["draft"], "acceptance_criteria": ["draft"]}
        row["artifact"] = "\n".join(dict.fromkeys(filter(None, [
            proposal["artifact"], row.get("artifact") if contracted else "",
        ])))
        row["validation_criteria"] = "\n".join(dict.fromkeys(filter(None, [
            proposal["validation_criteria"], row.get("validation_criteria") if contracted else "",
            (f"{test['check']} → {test['expected_result']}" if test else ""),
        ])))
        row["project_summary"] = f"{proposal['participant_action']} На сдачу: {row['artifact']}"
        if proposal["effort_hours"] is not None:
            row["effort_hours"] = proposal["effort_hours"]
        mode = proposal["team_mode"]
        if mode in {"team", "individual"}:
            row["delivery_format"] = "групповой" if mode == "team" else "индивидуальный"
            row["group_size"] = (
                proposal["group_size"] if mode == "team" and proposal["group_size"]
                else config.UP_FORMAT_GROUP_SIZES[row["delivery_format"]][0]
            )
        row["required_tools"] = ", ".join(dict.fromkeys(
            [s for s in (row.get("required_tools") or "").split(", ") if s] + proposal["required_tools"]
        ))
        if not row.get("node_ids"):
            # Historical outcomes are attributed to the source and never promoted into the accepted DAG.
            outcomes = [] if proposal["updated_practice"] else proposal["source_outcomes"]
            row["outcomes_know"] = "\n".join(v for v in outcomes if re.match(r"(зна|поним)", v, re.I))
            row["outcomes_can"] = "\n".join(v for v in outcomes if not re.match(r"(зна|поним)", v, re.I)) or proposal["participant_action"]
            row["outcomes_skills"] = ""
            row["outcome_count"] = len(outcomes) or 1
            row["learning_outcomes"] = "\n".join(filter(None, (row["outcomes_know"], row["outcomes_can"])))
        elif proposal["updated_practice"]:
            # Keep the accepted skill outcomes alongside the new task-specific performance.
            current_actions = (row.get("outcomes_can") or "").splitlines()
            if proposal["participant_action"] not in current_actions:
                row["outcomes_can"] = "\n".join([*current_actions, proposal["participant_action"]])
                row["outcome_count"] += 1
                row["learning_outcomes"] = "\n".join(filter(None, (
                    row["outcomes_know"], row["outcomes_can"], row["outcomes_skills"],
                )))
        if any(proposal["learning_outcomes"].values()):
            combined: list[str] = []
            for bucket, items in proposal["learning_outcomes"].items():
                previous = row.get(f"outcomes_{bucket}") or ""
                # Preserve catalog outcomes alongside project-specific performance statements.
                values = (previous.splitlines() if row.get("node_ids") else []) + items
                values = list(dict.fromkeys(s.strip() for s in values if s.strip()))
                row[f"outcomes_{bucket}"] = "\n".join(values)
                combined.extend(values)
            row["learning_outcomes"] = "\n".join(combined)
            row["outcome_count"] = len(combined)
        row["materials"] = "\n".join(dict.fromkeys(filter(None, [
            (f"Выбрать один проект из группы «{proposal['choose_one_group']}»." if proposal["choose_one_group"]
             else "Маршрут: специализация. Часы учитываются отдельно." if proposal["route"] == "specialization" else ""),
            ("Для начала работы: " + "; ".join(proposal["inputs"]) if proposal["inputs"] else ""),
            *[f"Прежняя работа «{title}» [{ref}]. {content}" for title, ref, content in zip(
                proposal["source_titles"], proposal["source_refs"], proposal["source_content"], strict=True,
            )],
            (f"Основание решения: {proposal['rationale']} {proposal['decision_ref']}" if proposal["decision_ref"] else ""),
            ("Перенос из прежнего УП. Действие, сдача и проверка заполнены по умолчанию и требуют согласования."
             if not proposal["decision_ref"] else ""),
            *[f"Для обсуждения с методологом ({item['topic']}): {item['quote']} [{item['source_ref']}]" for item in proposal["research_evidence"]],
            row.get("materials") if contracted else "",
            *proposal["review_notes"], row["validation_criteria"],
        ])))
        row["storytelling"] = proposal["case_context"]
