"""Deterministic spiral curriculum planner.

The planner intentionally does not call LLMs. It transforms accepted skills and
the prerequisite DAG into project blueprints that are denser and more
pedagogically useful than a one-skill-per-project topological walk.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import networkx as nx

from .. import config
from .archetype_classification import classify_activity_archetypes
from .artifact_policy import apply_artifact_contracts
from .domain import (
    BloomBucket,
    CurriculumBlock,
    OccurrenceRole,
    PlanNode,
    ProjectBlueprint,
    SkillOccurrence,
    TemplateBinding,
    TemplateSource,
)
from .edge_policy import CurriculumEdgeRole, curriculum_edge_role
from .journey import CurriculumDesignSpec, build_curriculum_design_spec
from .methodology_profile import MethodologyProfile
from .project_classification import classify_projects
from .title_policy import apply_title_policy

_DANGLING_TAIL_WORDS = {
    "и",
    "или",
    "в",
    "во",
    "на",
    "для",
    "по",
    "с",
    "со",
    "к",
    "ко",
    "о",
    "об",
    "от",
    "до",
    "из",
}


def _strip_dangling_tail(text: str) -> str:
    """Remove half-open clauses that appear after compacting generated labels."""
    cleaned = re.sub(r"\([^)]*$", "", text).strip(" .,-:;(")
    words = cleaned.split()
    while words and words[-1].casefold().strip(" .,-:;()") in _DANGLING_TAIL_WORDS:
        words.pop()
    return " ".join(words).strip(" .,-:;")


def _drop_latin_parenthetical_notes(text: str) -> str:
    """Remove English glossary notes from Russian curriculum labels."""
    return re.sub(r"\s*\([^)]*[A-Za-z][^)]*\)", "", text).strip()


def _limit_on_word_boundary(text: str, *, max_chars: int) -> str:
    """Shorten text without cutting words or leaving unfinished parentheses."""
    if len(text) <= max_chars:
        return _strip_dangling_tail(text) or text.strip(" .,-")
    limit = max(12, max_chars - 1)
    candidate = text[:limit].rstrip()
    boundary = candidate.rfind(" ")
    if boundary >= max(12, limit // 2):
        candidate = candidate[:boundary]
    candidate = _strip_dangling_tail(candidate)
    return f"{candidate}…" if candidate else "…"


def _dag_position(dag_payload: dict[str, Any]) -> dict[str, int]:
    return {
        str(item.get("id")): index
        for index, item in enumerate(dag_payload.get("order", []))
        if isinstance(item, dict) and item.get("id") is not None
    }


def _is_reliable_theme_edge(edge: dict[str, Any]) -> bool:
    relation_type = str(edge.get("relation_type") or "").casefold()
    if relation_type == "hard":
        return True
    try:
        confidence = float(edge.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return confidence >= config.TAU_EDGE_ACCEPT


def _direct_edge_pairs(
    dag_payload: dict[str, Any],
    *,
    roles: set[CurriculumEdgeRole] | None = None,
) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for edge in dag_payload.get("final_edges", []):
        if not isinstance(edge, dict):
            continue
        if roles is not None and curriculum_edge_role(edge) not in roles:
            continue
        src_id = str(edge.get("src_id") or "")
        dst_id = str(edge.get("dst_id") or "")
        if src_id and dst_id:
            pairs.add((src_id, dst_id))
    return pairs


def _has_direct_edge(node: PlanNode, project_nodes: list[PlanNode], direct_edges: set[tuple[str, str]]) -> bool:
    return any(
        (node.tmp_id, existing.tmp_id) in direct_edges or (existing.tmp_id, node.tmp_id) in direct_edges
        for existing in project_nodes
    )


def _compact_text(value: str, *, max_words: int = 6, max_chars: int = 72) -> str:
    """Keep generated curriculum labels compact and domain-neutral."""
    text = " ".join(str(value or "").replace("—", "-").split()).strip(" .,-")
    if not text:
        return "Общее"
    text = text.split(":", 1)[0].strip()
    text = _drop_latin_parenthetical_notes(text)
    words = text.split()
    shortened_by_words = False
    if len(words) > max_words:
        text = " ".join(words[:max_words])
        shortened_by_words = True
    text = _strip_dangling_tail(text)
    if len(text) > max_chars:
        text = _limit_on_word_boundary(text, max_chars=max_chars)
    elif shortened_by_words and text:
        text = f"{text}…"
    return text or "Общее"


def _full_text(value: str) -> str:
    """Normalize a curriculum label without shortening user-visible names."""
    text = " ".join(str(value or "").replace("—", "-").split()).strip(" .,-")
    if not text:
        return "Общее"
    return _drop_latin_parenthetical_notes(text) or "Общее"


def _project_theme_for(node: PlanNode) -> str:
    """Use catalog-derived semantics only: coverage area, skill group, then generic fallback."""
    return _full_text(node.block_key or node.group or "Общее")


_ARTIFACT_FAMILY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("analysis", ("анализ", "оцен", "исслед", "выяв", "диагност", "измер", "интерпрет", "сравн", "аудит")),
    ("document", ("документ", "опис", "оформ", "подготов", "состав", "регламент", "чек-лист", "шаблон", "гайд", "отчет", "отчёт")),
    ("configuration", ("настрой", "развер", "внедр", "интегр", "автоматиз", "конфиг", "подключ", "администр")),
    ("design", ("проектир", "модел", "планир", "определ", "формулир", "выбор", "специфиц", "приорит")),
    ("production", ("созда", "собир", "разработ", "реализ", "постро", "изготов", "код", "программ")),
)


_ARTIFACT_FAMILY_LABELS = {
    "analysis": "аналитический вывод",
    "document": "комплект документов",
    "configuration": "рабочая настройка",
    "design": "проектное решение",
    "production": "созданный продуктовый результат",
    "practice": "практический результат",
}


def _norm_text(value: object) -> str:
    text = str(value or "").casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я+ ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _artifact_family_for(node: PlanNode) -> str:
    """Classify a checked artifact type without using domain-specific templates."""
    text = " ".join(
        [
            node.name,
            node.group,
            node.block_key,
            " ".join(node.outcomes_know),
            " ".join(node.outcomes_can),
            " ".join(node.outcomes_skills),
            " ".join(node.tools),
        ]
    ).casefold().replace("ё", "е")
    for family, hints in _ARTIFACT_FAMILY_PATTERNS:
        if any(hint in text for hint in hints):
            return family
    if node.bloom >= 5:
        return "production"
    if node.bloom >= 4:
        return "design"
    return "practice"


def _artifact_key_for(node: PlanNode) -> str:
    return f"{_project_theme_for(node)}::{_artifact_family_for(node)}"


def _node_scope_text(node: PlanNode) -> str:
    return _norm_text(" ".join([node.name, node.group, node.block_key]))


def _template_scopes(template: dict[str, Any]) -> list[dict[str, Any]]:
    raw = template.get("scopes")
    return [scope for scope in raw if isinstance(scope, dict)] if isinstance(raw, list) else []


def _template_scope_overlap(node: PlanNode, template: dict[str, Any]) -> float:
    """Score semantic scope only; artifact-family policy is applied separately."""

    scopes = _template_scopes(template)
    if not scopes:
        return 0.1

    node_text = _node_scope_text(node)
    best = 0.0
    for scope in scopes:
        scope_type = str(scope.get("scope_type") or "").strip()
        if scope_type == "any":
            best = max(best, 0.6 * float(scope.get("weight", 1.0) or 1.0))
            continue
        scope_name = str(scope.get("normalized_scope_name") or scope.get("scope_name") or "").strip()
        normalized_scope = _norm_text(scope_name)
        if not normalized_scope:
            continue
        scope_tokens = set(normalized_scope.split())
        node_tokens = set(node_text.split())
        overlap = len(scope_tokens & node_tokens) / max(len(scope_tokens), 1)
        if normalized_scope in node_text:
            overlap = max(overlap, 1.0)
        best = max(best, overlap * float(scope.get("weight", 1.0) or 1.0))
    return best


def _template_scope_score(node: PlanNode, template: dict[str, Any]) -> float:
    """Strict node-level score retained for compatibility and focused tests."""

    family = str(template.get("artifact_family") or "").strip()
    if family and family != _artifact_family_for(node):
        return 0.0
    return _template_scope_overlap(node, template)


def _template_binding_for(template: dict[str, Any] | None) -> TemplateBinding | None:
    """Build a durable, version-snapshotted binding from a bound template dict."""
    code = str((template or {}).get("code") or "").strip()
    if not template or not code:
        return None
    source = str(template.get("source") or "").strip().casefold()
    resolved_source: TemplateSource = "brief" if source == "brief" else "global"
    return TemplateBinding(
        template_code=code,
        template_version=str(template.get("updated_at") or "").strip(),
        source=resolved_source,
        repeatable=bool(template.get("repeatable", False)),
    )


def _project_template_score(
    project: ProjectBlueprint,
    template: dict[str, Any],
    *,
    allow_brief_family_override: bool,
) -> float:
    """Score a template after project grouping, before spiral repetitions are added."""

    nodes = [item.node for item in project.primary_occurrences] or project.unique_nodes
    if not nodes:
        return 0.0
    template_family = str(template.get("artifact_family") or "").strip()
    family_matches = not template_family or template_family == project.artifact_family
    is_brief_template = str(template.get("source") or "").strip().casefold() == "brief"
    if not family_matches and not (allow_brief_family_override and is_brief_template):
        return 0.0
    return max(_template_scope_overlap(node, template) for node in nodes)


def _maximum_unique_template_matches(
    projects: list[ProjectBlueprint],
    templates: list[dict[str, Any]],
    *,
    project_indexes: list[int],
    template_indexes: list[int],
    allow_brief_family_override: bool,
) -> dict[int, int]:
    """Return a maximum-cardinality, maximum-score deterministic bipartite match."""

    graph = nx.Graph()
    for project_index in sorted(project_indexes):
        for template_index in sorted(template_indexes):
            template = templates[template_index]
            score = _project_template_score(
                projects[project_index],
                template,
                allow_brief_family_override=allow_brief_family_override,
            )
            if score < 0.5:
                continue
            # Semantic score dominates; priority and stable input order only break ties.
            priority = int(template.get("priority", 100) or 100)
            tie_break = max(0, 10_000 - priority) * 100 + max(0, len(templates) - template_index)
            weight = int(round(score * 10_000)) * 10_000_000 + tie_break
            graph.add_edge(("project", project_index), ("template", template_index), weight=weight)

    matching = nx.max_weight_matching(graph, maxcardinality=True, weight="weight")
    assignments: dict[int, int] = {}
    for left, right in matching:
        if left[0] == "template":
            left, right = right, left
        if left[0] == "project" and right[0] == "template":
            assignments[int(left[1])] = int(right[1])
    return assignments


def _best_repeatable_template(
    project: ProjectBlueprint,
    templates: list[dict[str, Any]],
    template_indexes: list[int],
    *,
    allow_brief_family_override: bool,
) -> int | None:
    scored: list[tuple[float, int, str, int]] = []
    for template_index in template_indexes:
        template = templates[template_index]
        score = _project_template_score(
            project,
            template,
            allow_brief_family_override=allow_brief_family_override,
        )
        if score < 0.5:
            continue
        scored.append(
            (
                score,
                -int(template.get("priority", 100) or 100),
                str(template.get("code") or ""),
                template_index,
            )
        )
    return max(scored)[3] if scored else None


def _apply_template_to_project(project: ProjectBlueprint, template: dict[str, Any]) -> None:
    nodes = [item.node for item in project.primary_occurrences] or project.unique_nodes
    template_family = str(template.get("artifact_family") or project.artifact_family).strip()
    if template_family:
        project.artifact_family = template_family
    template_artifact = _template_artifact_for(nodes, project.block_key, project.artifact_family, template)
    if template_artifact:
        project.artifact = template_artifact
    template_title = _template_title_for(nodes, project.block_key, project.artifact_family, template)
    if template_title:
        project.title = template_title
    enrichment = _template_enrichment_for(
        nodes,
        project.block_key,
        project.artifact_family,
        project.artifact,
        template,
    )
    project.enrichment.update({key: value for key, value in enrichment.items() if value})
    template_code = str(template.get("code") or "").strip()
    project.artifact_template_code = template_code
    project.template_binding = _template_binding_for(template)
    if template_code:
        project.artifact_key = f"{project.artifact_key}::template:{template_code}"


def _best_brief_template_for_node(
    node: PlanNode,
    templates: list[dict[str, Any]],
    available_indexes: set[int],
) -> int | None:
    scored: list[tuple[float, int, str, int]] = []
    for template_index in sorted(available_indexes):
        template = templates[template_index]
        if str(template.get("source") or "").casefold() != "brief" or bool(template.get("repeatable")):
            continue
        score = _template_scope_overlap(node, template)
        if score < 0.5:
            continue
        scored.append(
            (
                score,
                -int(template.get("priority", 100) or 100),
                str(template.get("code") or ""),
                template_index,
            )
        )
    return max(scored)[3] if scored else None


def _partition_projects_for_brief_template_coverage(
    projects: list[ProjectBlueprint],
    artifact_templates: list[dict[str, Any]],
) -> tuple[list[ProjectBlueprint], int]:
    """Split only projects containing two or more clear brief-template groups."""

    templates = [template for template in artifact_templates if isinstance(template, dict)]
    available_indexes = {
        index
        for index, template in enumerate(templates)
        if str(template.get("source") or "").casefold() == "brief" and not bool(template.get("repeatable"))
    }
    partitioned: list[ProjectBlueprint] = []
    split_count = 0
    for project in projects:
        primary = project.primary_occurrences
        if project.project_kind == "capstone" or len(primary) < 2 or len(available_indexes) < 2:
            partitioned.append(project)
            continue

        groups: dict[int | None, list[SkillOccurrence]] = {}
        for occurrence in primary:
            template_index = _best_brief_template_for_node(occurrence.node, templates, available_indexes)
            groups.setdefault(template_index, []).append(occurrence)
        matched_indexes = [index for index in groups if index is not None]
        if len(matched_indexes) < 2 or any(len(groups[index]) < 2 for index in matched_indexes):
            partitioned.append(project)
            continue

        ordered_groups = sorted(
            groups.items(),
            key=lambda item: min(primary.index(occurrence) for occurrence in item[1]),
        )
        split_count += len(ordered_groups) - 1
        for group_index, (template_index, occurrences) in enumerate(ordered_groups, start=1):
            nodes = [occurrence.node for occurrence in occurrences]
            artifact_family = _artifact_family_for(nodes[0])
            split_project = ProjectBlueprint(
                occurrences=list(occurrences),
                block_key=project.block_key,
                artifact=_artifact_for(nodes, project.block_key, artifact_family),
                artifact_key=f"{project.artifact_key}::coverage:{group_index}",
                artifact_family=artifact_family,
                title=_project_title_for(project.block_key, group_index, len(ordered_groups)),
                project_kind=project.project_kind,
            )
            if template_index is not None:
                _apply_template_to_project(split_project, templates[template_index])
                available_indexes.discard(template_index)
            partitioned.append(split_project)
    return partitioned, split_count


def _assign_templates_to_projects(
    projects: list[ProjectBlueprint],
    artifact_templates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind templates after grouping, honoring one-use and brief coverage semantics."""

    templates = [template for template in artifact_templates if isinstance(template, dict)]
    nonrepeatable = [index for index, template in enumerate(templates) if not bool(template.get("repeatable"))]
    repeatable = [index for index, template in enumerate(templates) if bool(template.get("repeatable"))]
    # Capstone has its own mandatory release/demo contract. Ordinary artifact
    # templates must not claim it merely because one repeated skill overlaps.
    project_indexes = [index for index, project in enumerate(projects) if project.project_kind != "capstone"]
    template_by_code = {
        str(template.get("code") or ""): index
        for index, template in enumerate(templates)
        if str(template.get("code") or "")
    }
    assignments = {
        project_index: template_by_code[project.artifact_template_code]
        for project_index, project in enumerate(projects)
        if project.artifact_template_code in template_by_code
    }
    prebound_projects = set(assignments)
    prebound_count = len(assignments)
    used_nonrepeatable = {
        template_index
        for template_index in assignments.values()
        if template_index in nonrepeatable
    }

    strict_assignments = _maximum_unique_template_matches(
        projects,
        templates,
        project_indexes=[index for index in project_indexes if index not in assignments],
        template_indexes=[index for index in nonrepeatable if index not in used_nonrepeatable],
        allow_brief_family_override=False,
    )
    assignments.update(strict_assignments)
    strict_count = len(strict_assignments)
    used_nonrepeatable.update(strict_assignments.values())

    remaining_projects = [index for index in project_indexes if index not in assignments]
    remaining_brief_templates = [
        index
        for index in nonrepeatable
        if index not in used_nonrepeatable and str(templates[index].get("source") or "").casefold() == "brief"
    ]
    coverage_assignments = _maximum_unique_template_matches(
        projects,
        templates,
        project_indexes=remaining_projects,
        template_indexes=remaining_brief_templates,
        allow_brief_family_override=True,
    )
    assignments.update(coverage_assignments)

    repeat_assignment_count = 0
    for project_index in project_indexes:
        if project_index in assignments:
            continue
        template_index = _best_repeatable_template(
            projects[project_index],
            templates,
            repeatable,
            allow_brief_family_override=False,
        )
        if template_index is None:
            template_index = _best_repeatable_template(
                projects[project_index],
                templates,
                [
                    index
                    for index in repeatable
                    if str(templates[index].get("source") or "").casefold() == "brief"
                ],
                allow_brief_family_override=True,
            )
        if template_index is not None:
            assignments[project_index] = template_index
            repeat_assignment_count += 1

    for project_index, template_index in sorted(assignments.items()):
        if project_index in prebound_projects:
            continue
        _apply_template_to_project(projects[project_index], templates[template_index])

    used_template_indexes = set(assignments.values())
    unused_codes = [
        str(template.get("code") or "")
        for index, template in enumerate(templates)
        if index not in used_template_indexes and str(template.get("code") or "")
    ]
    return {
        "db_template_count": len(templates),
        "db_template_project_count": len(assignments),
        "template_bound_project_count": len(assignments),
        "template_assignment_prebound_count": prebound_count,
        "template_assignment_strict_count": strict_count,
        "template_assignment_coverage_count": len(coverage_assignments),
        "template_repeat_assignment_count": repeat_assignment_count,
        "template_unused_count": len(unused_codes),
        "template_unused_codes": unused_codes,
        "template_unbound_project_count": len(projects) - len(assignments),
        "template_eligible_project_count": len(project_indexes),
    }


def _render_pattern(pattern: object, *, nodes: list[PlanNode], block_key: str, artifact_family: str, artifact: str = "") -> str:
    text = str(pattern or "").strip()
    if not text:
        return ""
    skills = ", ".join(node.name for node in nodes)
    first_skill = nodes[0].name if nodes else ""
    try:
        return text.format(
            theme=block_key,
            skills=skills,
            first_skill=first_skill,
            artifact=artifact,
            artifact_family=_ARTIFACT_FAMILY_LABELS.get(artifact_family, artifact_family),
        )
    except (KeyError, IndexError, ValueError):
        return text


def _template_artifact_for(nodes: list[PlanNode], block_key: str, artifact_family: str, template: dict[str, Any] | None) -> str:
    if not template:
        return ""
    rendered = _render_pattern(
        template.get("artifact_description"),
        nodes=nodes,
        block_key=block_key,
        artifact_family=artifact_family,
    )
    return rendered


def _template_title_for(nodes: list[PlanNode], block_key: str, artifact_family: str, template: dict[str, Any] | None) -> str:
    if not template:
        return ""
    pattern = str(template.get("project_name_pattern") or "").strip()
    rendered = _render_pattern(
        pattern,
        nodes=nodes,
        block_key=block_key,
        artifact_family=artifact_family,
    )
    # Project-name patterns can expand into long skill lists. If that happens,
    # use the accepted template title as the stable human-readable project name.
    if rendered and len(rendered) >= 8 and ("{" not in pattern or len(rendered) <= 72):
        return rendered
    title = _render_pattern(
        template.get("title"),
        nodes=nodes,
        block_key=block_key,
        artifact_family=artifact_family,
    )
    return title or rendered


def _template_enrichment_for(
    nodes: list[PlanNode],
    block_key: str,
    artifact_family: str,
    artifact: str,
    template: dict[str, Any] | None,
) -> dict[str, str]:
    if not template:
        return {}
    return {
        "materials": _render_pattern(template.get("materials_pattern"), nodes=nodes, block_key=block_key, artifact_family=artifact_family, artifact=artifact),
        "storytelling": _render_pattern(template.get("storytelling_pattern"), nodes=nodes, block_key=block_key, artifact_family=artifact_family, artifact=artifact),
        "validation_criteria": _render_pattern(template.get("validation_criteria"), nodes=nodes, block_key=block_key, artifact_family=artifact_family, artifact=artifact),
    }


def _artifact_for(nodes: list[PlanNode], block_key: str, artifact_family: str) -> str:
    theme = _compact_text(block_key, max_words=6, max_chars=72)
    family_label = _ARTIFACT_FAMILY_LABELS.get(artifact_family, _ARTIFACT_FAMILY_LABELS["practice"])
    if len(nodes) == 1:
        return f"Проверяемый артефакт ({family_label}) по навыку «{_compact_text(nodes[0].name, max_words=8, max_chars=90)}»"
    labels = [_compact_text(node.name, max_words=5, max_chars=56) for node in nodes[:3]]
    suffix = f" и ещё {len(nodes) - 3}" if len(nodes) > 3 else ""
    return f"Интегративный артефакт ({family_label}) по теме «{theme}»: {', '.join(labels)}{suffix}"


def _project_title_for(block_key: str, chunk_index: int, chunk_count: int) -> str:
    suffix = f" {chunk_index}" if chunk_count > 1 else ""
    return f"{_full_text(block_key)}{suffix}"


def _ordered_nodes(nodes: list[PlanNode], dag_payload: dict[str, Any]) -> list[PlanNode]:
    position = _dag_position(dag_payload)
    return sorted(nodes, key=lambda item: (position.get(item.tmp_id, 10**9), item.bloom, item.name))


def _project_from_nodes(
    nodes: list[PlanNode],
    *,
    block_key: str,
    artifact: str,
    artifact_key: str,
    artifact_family: str,
    artifact_template_code: str,
    enrichment: dict[str, str],
    title: str,
    project_kind: str,
) -> ProjectBlueprint:
    return ProjectBlueprint(
        occurrences=[SkillOccurrence(item, role="primary", touch_index=1) for item in nodes],
        block_key=block_key,
        artifact=artifact,
        artifact_key=artifact_key,
        artifact_family=artifact_family,
        artifact_template_code=artifact_template_code,
        enrichment=enrichment,
        title=title,
        project_kind=project_kind,
    )


def _split_nodes_for_project(
    nodes: list[PlanNode],
    dag_payload: dict[str, Any],
    *,
    max_skills: int,
) -> list[list[PlanNode]]:
    """Split nodes without collapsing an accepted prerequisite into one project."""
    direct_edges = _direct_edge_pairs(dag_payload, roles={"required"})
    chunks: list[list[PlanNode]] = []
    current: list[PlanNode] = []
    for node in nodes:
        can_append = current and len(current) < max_skills and not _has_direct_edge(node, current, direct_edges)
        if can_append:
            current.append(node)
            continue
        if current:
            chunks.append(current)
        current = [node]
    if current:
        chunks.append(current)
    return chunks


def _pack_dynamic_artifact_projects(
    nodes: list[PlanNode],
    dag_payload: dict[str, Any],
    design_spec: CurriculumDesignSpec | None = None,
) -> tuple[list[ProjectBlueprint], dict[str, Any]]:
    max_skills = max(1, int(config.UP_MAX_SKILLS_PER_PROJECT))
    grouped: dict[str, list[PlanNode]] = {}
    group_order: list[str] = []
    group_meta: dict[str, tuple[str, str]] = {}
    stage_by_node = design_spec.node_stage if design_spec else {}
    stage_by_index = dict(enumerate(design_spec.stages)) if design_spec else {}
    for node in _ordered_nodes(nodes, dag_payload):
        stage_index = stage_by_node.get(node.tmp_id)
        stage = stage_by_index.get(stage_index) if stage_index is not None else None
        theme = stage.title if stage else _project_theme_for(node)
        dynamic_key = f"{stage.code}::{_artifact_family_for(node)}" if stage else _artifact_key_for(node)
        artifact_key = dynamic_key
        if artifact_key not in grouped:
            grouped[artifact_key] = []
            group_order.append(artifact_key)
            group_meta[artifact_key] = (theme, _artifact_family_for(node))
        grouped[artifact_key].append(node)

    projects: list[ProjectBlueprint] = []
    assignment: dict[str, str] = {}
    split_count = 0

    for artifact_key in group_order:
        block_key, artifact_family = group_meta[artifact_key]
        chunks = _split_nodes_for_project(grouped[artifact_key], dag_payload, max_skills=max_skills)
        split_count += max(0, len(chunks) - 1)
        for chunk_index, chunk in enumerate(chunks, start=1):
            artifact = _artifact_for(chunk, block_key, artifact_family)
            project = _project_from_nodes(
                chunk,
                block_key=block_key,
                artifact=artifact,
                artifact_key=artifact_key,
                artifact_family=artifact_family,
                artifact_template_code="",
                enrichment={},
                title=_project_title_for(block_key, chunk_index, len(chunks)),
                project_kind="dynamic_artifact",
            )
            projects.append(project)
            for node in chunk:
                assignment[node.tmp_id] = artifact_key

    meta = {
        "artifact_first": True,
        "artifact_template_count": 0,
        "artifact_project_count": len(projects),
        "artifact_split_count": split_count,
        "dynamic_group_count": len(group_order),
        "artifact_family_counts": dict(Counter(family for _theme, family in group_meta.values())),
        "unassigned_node_count": 0,
        "assignment": assignment,
    }
    return projects, meta


def _reorder_projects_by_dag_edges(
    projects: list[ProjectBlueprint],
    dag_payload: dict[str, Any],
    design_spec: CurriculumDesignSpec | None = None,
) -> list[ProjectBlueprint]:
    if len(projects) <= 1:
        return projects
    required_edges = _direct_edge_pairs(dag_payload, roles={"required"})
    recommended_edges = _direct_edge_pairs(dag_payload, roles={"recommended"})

    def project_graph(items: list[ProjectBlueprint]) -> nx.DiGraph:
        project_by_node: dict[str, int] = {}
        for project_index, project in enumerate(items):
            for node in project.unique_nodes:
                project_by_node.setdefault(node.tmp_id, project_index)
        graph = nx.DiGraph()
        graph.add_nodes_from(range(len(items)))
        for src_id, dst_id in required_edges:
            src_project = project_by_node.get(src_id)
            dst_project = project_by_node.get(dst_id)
            if src_project is None or dst_project is None or src_project == dst_project:
                continue
            graph.add_edge(src_project, dst_project)
        return graph

    graph = project_graph(projects)
    while not nx.is_directed_acyclic_graph(graph):
        cyclic_indexes = {index for cycle in nx.simple_cycles(graph) for index in cycle}
        expanded: list[ProjectBlueprint] = []
        split_any = False
        for project_index, project in enumerate(projects):
            primary = project.primary_occurrences
            if project_index not in cyclic_indexes or len(primary) <= 1:
                expanded.append(project)
                continue
            split_any = True
            for occurrence in primary:
                expanded.append(
                    ProjectBlueprint(
                        occurrences=[occurrence],
                        block_key=project.block_key,
                        artifact=_artifact_for([occurrence.node], project.block_key, project.artifact_family),
                        artifact_key=f"{project.artifact_key}::{occurrence.node.tmp_id}",
                        artifact_family=project.artifact_family,
                        artifact_template_code=project.artifact_template_code,
                        enrichment=dict(project.enrichment),
                        title=f"{project.title}: {occurrence.node.name}",
                        project_kind=project.project_kind,
                    )
                )
        if not split_any:
            return projects
        projects = expanded
        graph = project_graph(projects)

    project_by_node = {
        node.tmp_id: project_index
        for project_index, project in enumerate(projects)
        for node in project.unique_nodes
    }
    for src_id, dst_id in recommended_edges:
        src_project = project_by_node.get(src_id)
        dst_project = project_by_node.get(dst_id)
        if src_project is None or dst_project is None or src_project == dst_project:
            continue
        if nx.has_path(graph, dst_project, src_project):
            continue
        graph.add_edge(src_project, dst_project)

    stage_by_node = design_spec.node_stage if design_spec else {}

    def project_sort_key(index: int) -> tuple[int, int]:
        stage_indexes = [stage_by_node[node.tmp_id] for node in projects[index].unique_nodes if node.tmp_id in stage_by_node]
        return (max(stage_indexes, default=10**9), index)

    ordered_indexes = list(nx.lexicographical_topological_sort(graph, key=project_sort_key))
    return [projects[index] for index in ordered_indexes]


def _blocks_from_projects(
    projects: list[ProjectBlueprint],
    design_spec: CurriculumDesignSpec | None = None,
) -> list[CurriculumBlock]:
    if design_spec and design_spec.stages:
        stage_by_node = design_spec.node_stage
        projects_by_stage: dict[int, list[ProjectBlueprint]] = {}
        for project in projects:
            stage_indexes = [stage_by_node[node.tmp_id] for node in project.unique_nodes if node.tmp_id in stage_by_node]
            projects_by_stage.setdefault(max(stage_indexes, default=0), []).append(project)

        staged_blocks: list[CurriculumBlock] = []
        chunk_size = max(1, int(config.UP_MAX_PROJECTS_PER_BLOCK))
        for stage_index, stage in enumerate(design_spec.stages):
            stage_projects = projects_by_stage.get(stage_index, [])
            for offset in range(0, len(stage_projects), chunk_size):
                chunk = stage_projects[offset : offset + chunk_size]
                part = offset // chunk_size + 1
                title = stage.title if len(stage_projects) <= chunk_size else f"{stage.title} · часть {part}"
                staged_blocks.append(
                    CurriculumBlock(
                        block_keys=stage.coverage_areas or _ordered_block_keys(chunk),
                        projects=chunk,
                        stage_code=stage.code,
                        title=title,
                        goal=stage.goal,
                    )
                )
        return staged_blocks

    blocks: list[CurriculumBlock] = []
    chunk_size = max(1, int(config.UP_MAX_PROJECTS_PER_BLOCK))
    for offset in range(0, len(projects), chunk_size):
        chunk = projects[offset : offset + chunk_size]
        blocks.append(CurriculumBlock(block_keys=_ordered_block_keys(chunk), projects=chunk))
    return blocks


def _pack_dynamic_artifact_blocks(
    nodes: list[PlanNode],
    dag_payload: dict[str, Any],
    artifact_templates: list[dict[str, Any]] | None = None,
    design_spec: CurriculumDesignSpec | None = None,
) -> tuple[list[CurriculumBlock], dict[str, Any]]:
    projects, meta = _pack_dynamic_artifact_projects(nodes, dag_payload, design_spec)
    projects, coverage_split_count = _partition_projects_for_brief_template_coverage(
        projects,
        artifact_templates or [],
    )
    meta["template_coverage_split_count"] = coverage_split_count
    meta["artifact_project_count"] = len(projects)
    meta["artifact_split_count"] = int(meta.get("artifact_split_count", 0) or 0) + coverage_split_count
    projects = _reorder_projects_by_dag_edges(projects, dag_payload, design_spec)
    blocks = _blocks_from_projects(projects, design_spec)
    if design_spec and design_spec.capstone_required:
        capstone = _capstone_project(nodes, design_spec)
        if capstone is not None:
            blocks.append(
                CurriculumBlock(
                    block_keys=("Итоговая интеграция",),
                    projects=[capstone],
                    stage_code="capstone",
                    title="Итоговая интеграция",
                    goal="Объединить результаты программы, предъявить итоговый артефакт и обосновать принятые решения.",
                )
            )
            meta["capstone_project_count"] = 1
    all_projects = [project for block in blocks for project in block.projects]
    meta.update(_assign_templates_to_projects(all_projects, artifact_templates or []))
    meta["artifact_family_counts"] = dict(Counter(project.artifact_family for project in all_projects))
    return blocks, meta


def _capstone_project(nodes: list[PlanNode], design_spec: CurriculumDesignSpec) -> ProjectBlueprint | None:
    by_id = {node.tmp_id: node for node in nodes}
    candidates: list[PlanNode] = []
    for stage in design_spec.stages:
        stage_nodes = [by_id[node_id] for node_id in stage.node_ids if node_id in by_id]
        if stage_nodes:
            candidates.append(max(stage_nodes, key=lambda node: (node.bloom, node.name)))
    max_skills = max(1, int(config.UP_MAX_SKILLS_PER_PROJECT))
    if len(candidates) > max_skills:
        indexes = [round(index * (len(candidates) - 1) / (max_skills - 1)) for index in range(max_skills)] if max_skills > 1 else [len(candidates) - 1]
        candidates = [candidates[index] for index in dict.fromkeys(indexes)]
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return None
    artifact = "Итоговый интеграционный артефакт, объединяющий результаты ключевых этапов программы"
    return ProjectBlueprint(
        occurrences=[
            SkillOccurrence(node=node, role="assessment", touch_index=2, bloom_bucket="skills")
            for node in candidates
        ],
        block_key="Итоговая интеграция",
        artifact=artifact,
        artifact_key="capstone::integration",
        artifact_family="production",
        title=design_spec.capstone_title,
        project_kind="capstone",
    )


def _flatten_projects(blocks: list[CurriculumBlock]) -> list[ProjectBlueprint]:
    return [project for block in blocks for project in block.projects]


def _ordered_block_keys(projects: list[ProjectBlueprint]) -> tuple[str, ...]:
    keys: list[str] = []
    for project in projects:
        if project.block_key and project.block_key not in keys:
            keys.append(project.block_key)
    return tuple(keys) or ("Общее",)


def _primary_project_index(projects: list[ProjectBlueprint]) -> dict[str, int]:
    index: dict[str, int] = {}
    for project_index, project in enumerate(projects):
        for occurrence in project.primary_occurrences:
            index.setdefault(occurrence.node.tmp_id, project_index)
    return index


def _centrality_scores(nodes: list[PlanNode], dag_payload: dict[str, Any]) -> dict[str, float]:
    by_id = {node.tmp_id: node for node in nodes}
    degree: Counter[str] = Counter()
    reliable_degree: Counter[str] = Counter()
    for edge in dag_payload.get("final_edges", []):
        if not isinstance(edge, dict):
            continue
        src_id = str(edge.get("src_id") or "")
        dst_id = str(edge.get("dst_id") or "")
        if src_id in by_id and dst_id in by_id:
            degree[src_id] += 1
            degree[dst_id] += 1
            if _is_reliable_theme_edge(edge):
                reliable_degree[src_id] += 1
                reliable_degree[dst_id] += 1
    block_frequency = Counter(node.block_key for node in nodes)
    return {
        node.tmp_id: float(reliable_degree[node.tmp_id] * 2 + degree[node.tmp_id] + min(block_frequency[node.block_key], 3) * 0.25)
        for node in nodes
    }


def _select_core_threads(nodes: list[PlanNode], dag_payload: dict[str, Any]) -> list[PlanNode]:
    if not config.UP_SPIRAL_ENABLED:
        return []
    scores = _centrality_scores(nodes, dag_payload)
    candidates = [node for node in nodes if scores.get(node.tmp_id, 0.0) > 0.0]
    if len(nodes) >= config.UP_CORE_THREAD_MIN and len(candidates) < config.UP_CORE_THREAD_MIN:
        candidates = nodes[: config.UP_CORE_THREAD_MIN]
    ordered = sorted(candidates, key=lambda node: (-scores.get(node.tmp_id, 0.0), node.bloom, node.name))
    return ordered[: max(0, int(config.UP_CORE_THREAD_MAX))]


def _target_repeat_indexes(first_index: int, project_count: int, occurrence_count: int) -> list[int]:
    if project_count <= 2 or occurrence_count <= 1:
        return []
    targets: list[int] = []
    # Expanding gaps in project units. This approximates spaced repetition while
    # staying deterministic and independent of calendar dates.
    gap = max(2, int(config.UP_SPIRAL_MIN_GAP))
    cursor = first_index
    for _touch in range(2, occurrence_count + 1):
        cursor += gap
        if cursor >= project_count:
            cursor = project_count - 1
        if cursor > first_index and cursor not in targets:
            targets.append(cursor)
        gap += max(1, int(config.UP_SPIRAL_GAP_GROWTH))
    return targets


def _bucket_for_repeat(touch_index: int, total_occurrences: int) -> BloomBucket:
    if touch_index <= 1:
        return "can"
    if touch_index >= total_occurrences:
        return "skills"
    return "can"


def _add_spiral_occurrences(blocks: list[CurriculumBlock], nodes: list[PlanNode], dag_payload: dict[str, Any]) -> set[str]:
    projects = _flatten_projects(blocks)
    if len(projects) < 3:
        return set()
    direct_edges = _direct_edge_pairs(dag_payload, roles={"required"})
    primary_index = _primary_project_index(projects)
    repeated_threads: set[str] = set()
    max_skills = max(1, int(config.UP_MAX_SKILLS_PER_PROJECT))

    for node in _select_core_threads(nodes, dag_payload):
        first_index = primary_index.get(node.tmp_id)
        if first_index is None:
            continue
        desired = min(max(1, int(config.UP_MAX_THREAD_OCCURRENCES)), max(1, len(projects) // 3 + 1))
        desired = max(int(config.UP_MIN_THREAD_OCCURRENCES), desired)
        desired = min(desired, len(projects))
        targets = _target_repeat_indexes(first_index, len(projects), desired)
        total_occurrences = 1 + len(targets)
        for touch_offset, target_index in enumerate(targets, start=2):
            project = projects[target_index]
            existing_nodes = project.unique_nodes
            if node.tmp_id in {item.tmp_id for item in existing_nodes}:
                continue
            if len(existing_nodes) >= max_skills:
                continue
            if _has_direct_edge(node, existing_nodes, direct_edges):
                continue
            role: OccurrenceRole = "assessment" if touch_offset == total_occurrences else "reinforcement"
            project.occurrences.append(
                SkillOccurrence(
                    node=node,
                    role=role,
                    touch_index=touch_offset,
                    bloom_bucket=_bucket_for_repeat(touch_offset, total_occurrences),
                )
            )
            repeated_threads.add(node.tmp_id)
    return repeated_threads


def build_curriculum_blocks(
    nodes: list[PlanNode],
    dag_payload: dict[str, Any],
    artifact_templates: list[dict[str, Any]] | None = None,
    planning_context: dict[str, Any] | None = None,
    *,
    profile: MethodologyProfile,
) -> tuple[list[CurriculumBlock], dict[str, Any]]:
    """Build project blocks and return planner metadata."""
    journey_enabled = bool((planning_context or {}).get("must_include_areas") or (planning_context or {}).get("curriculum_design_spec"))
    design_spec = build_curriculum_design_spec(planning_context, nodes, dag_payload) if journey_enabled else None
    blocks, artifact_meta = _pack_dynamic_artifact_blocks(nodes, dag_payload, artifact_templates, design_spec)
    core_threads = _select_core_threads(nodes, dag_payload)
    repeated_threads = _add_spiral_occurrences(blocks, nodes, dag_payload)
    classify_projects(blocks)
    classify_activity_archetypes(blocks)
    apply_artifact_contracts(blocks, profile=profile)
    apply_title_policy(blocks)
    meta = {
        **artifact_meta,
        "artifact_match_count": 0,
        "core_thread_ids": [node.tmp_id for node in core_threads],
        "core_thread_names": [node.name for node in core_threads],
        "repeated_thread_ids": sorted(repeated_threads),
        "repeated_thread_count": len(repeated_threads),
        "design_spec": design_spec.as_dict() if design_spec else {},
    }
    return blocks, meta
