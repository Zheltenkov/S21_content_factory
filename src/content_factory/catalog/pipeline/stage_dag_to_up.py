"""Стадия 4: accepted skills + DAG -> spiral curriculum plan draft.

The stage does not generate new skills and does not mutate the DAG. It builds a
deterministic curriculum layer above the DAG: accepted skills become project
occurrences, core threads can reappear later for reinforcement/assessment, and
the final payload remains compatible with the existing UI and CSV export.
"""
from __future__ import annotations

import re
from math import isfinite
from typing import Any

from content_factory.content_profile import infer_content_profile

from . import config, language
from .curriculum import CurriculumBlock, PlanNode, ProjectBlueprint, SkillOccurrence, build_curriculum_blocks
from .curriculum.edge_policy import curriculum_edge_role
from .curriculum.project_quality import report_only_quality_metrics
from .curriculum.publication_gate import evaluate_publication_gate
from .curriculum.workload import build_workload_contract
from .models import SkillCandidate

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

CSV_PRIMARY_HEADER = [
    "Тематический блок",
    "Цели блока",
    "№",
    "Название контентной единицы (проекта)",
    "Краткое описание",
    "Обр. результаты — что узнает (ЗНАТЬ)",
    "Обр. результаты — что умеет (УМЕТЬ)",
    "Обр. результаты — какой навык (НАВЫКИ)",
    "Необходимое ПО",
    "Доп. материалы для генерации",
    "Сторителлинг",
    "Формат",
    "Кол-во в группе",
    "Трудоёмкость, астр. часы",
    "Трудоёмкость, дни",
    "Общая трудоёмкость, дни",
    "XP за проект",
    "% прохождения проекта",
    "Количество p2p проверок",
    "Список навыков (развесовка)",
    "Название на платформе / Gitlab",
    "Ссылки на GitLab",
]

CSV_SECONDARY_HEADER = [""] * len(CSV_PRIMARY_HEADER)


def _strip_dangling_tail(text: str) -> str:
    """Remove unfinished fragments after title compaction."""
    cleaned = re.sub(r"\([^)]*$", "", text).strip(" .,-:;(")
    words = cleaned.split()
    while words and words[-1].casefold().strip(" .,-:;()") in _DANGLING_TAIL_WORDS:
        words.pop()
    return " ".join(words).strip(" .,-:;")


def _drop_latin_parenthetical_notes(text: str) -> str:
    """Remove English glossary notes from Russian curriculum titles."""
    return re.sub(r"\s*\([^)]*[A-Za-z][^)]*\)", "", text).strip()


def _limit_on_word_boundary(text: str, *, max_chars: int) -> str:
    """Shorten labels without cutting a word or leaving an open parenthesis."""
    if len(text) <= max_chars:
        return _strip_dangling_tail(text) or text.strip(" .,-")
    limit = max(12, max_chars - 1)
    candidate = text[:limit].rstrip()
    boundary = candidate.rfind(" ")
    if boundary >= max(12, limit // 2):
        candidate = candidate[:boundary]
    candidate = _strip_dangling_tail(candidate)
    return f"{candidate}…" if candidate else "…"


def _display_name(candidate: SkillCandidate) -> str:
    # Для сматченных сущностей используем каноническое имя каталога.
    if candidate.canonical_name and candidate.resolution in {"matched", "alias", "fuzzy"}:
        return language.localize_skill_label(candidate.canonical_name)
    return language.localize_skill_label(candidate.name)


def _display_group(candidate: SkillCandidate) -> str:
    # Coverage area лучше подходит для тематического блока, чем сырая skill-group.
    value = candidate.coverage_area or candidate.canonical_group or candidate.group or "Общее"
    return language.localize_area_label(value) or language.localize_group_label(value) or "Общее"


def _node_from_candidate(candidate: SkillCandidate) -> PlanNode:
    # Для ЗУН раскладываем индикаторы строго по Bloom-бакетам F/G/H.
    outcomes_know: list[str] = []
    outcomes_can: list[str] = []
    outcomes_skills: list[str] = []
    for indicator in candidate.indicators:
        text = indicator.text.strip()
        if not text:
            continue
        if indicator.bloom in config.UP_BLOOM_KNOW:
            outcomes_know.append(text)
        elif indicator.bloom in config.UP_BLOOM_CAN:
            outcomes_can.append(text)
        else:
            outcomes_skills.append(text)
    if not outcomes_know and not outcomes_can and not outcomes_skills:
        if candidate.bloom <= 2:
            outcomes_know.append(_display_name(candidate))
        elif candidate.bloom <= 4:
            outcomes_can.append(_display_name(candidate))
        else:
            outcomes_skills.append(_display_name(candidate))
    tools = tuple(sorted({tool.strip() for tool in candidate.tools if tool.strip()}))
    return PlanNode(
        tmp_id=candidate.tmp_id,
        name=_display_name(candidate),
        group=candidate.canonical_group or candidate.group or "Без группы",
        block_key=_display_group(candidate),
        bloom=candidate.bloom,
        outcomes_know=tuple(dict.fromkeys(outcomes_know)),
        outcomes_can=tuple(dict.fromkeys(outcomes_can)),
        outcomes_skills=tuple(dict.fromkeys(outcomes_skills)),
        tools=tools,
    )


def build_plan_nodes(candidates: list[SkillCandidate]) -> list[PlanNode]:
    """Expose the normalized planner-node contract to design approval flows."""

    return [_node_from_candidate(candidate) for candidate in candidates]


def _audience_label(spec: dict[str, Any] | None) -> str:
    seniority = str((spec or {}).get("seniority") or "").casefold()
    mapping = {
        "junior": "Начальный",
        "junior+": "Начальный",
        "middle": "Средний",
        "senior": "Продвинутый",
        "lead": "Продвинутый",
        "начинающий": "Начальный",
        "базовый": "Начальный",
    }
    return mapping.get(seniority, "Начальный")


def _snap_hours(raw_hours: float) -> int:
    return min(config.UP_HOUR_BANDS, key=lambda band: abs(band - raw_hours))


def _estimate_project_hours(nodes: list[PlanNode]) -> int:
    # В baseline считаем часы от числа навыков и верхнего Bloom в проекте.
    max_bloom = max((node.bloom for node in nodes), default=2)
    raw_hours = 6 + 3 * len(nodes) + 2 * max(0, max_bloom - 2)
    return _snap_hours(raw_hours)


def _compact_label(value: str, *, max_words: int = 5, max_chars: int = 56) -> str:
    """Return a short Russian UI/CSV label without losing technical terms."""
    text = language.localize_area_label(value) or language.localize_skill_label(value) or value
    text = re.sub(r"\s+", " ", text.replace("—", "-")).strip(" .,-")
    if not text:
        return "Общее"
    # Long clarifications after colon are useful in coverage audit, but too noisy as block titles.
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


def _clean_project_title(value: str) -> str:
    title = re.sub(r"^\s*(Практический\s+проект|Проект)\s*:\s*", "", value or "", flags=re.IGNORECASE).strip()
    title = language.localize_area_label(title) or language.localize_skill_label(title) or title
    title = re.sub(r"\s+", " ", title.replace("—", "-")).strip(" .,-")
    if not title:
        return "Проект"
    return _drop_latin_parenthetical_notes(title) or "Проект"


def _join_limited(values: list[str], *, limit: int = 3) -> str:
    labels = [_compact_label(value, max_words=5, max_chars=48) for value in values if value]
    unique = list(dict.fromkeys(labels))
    if len(unique) <= limit:
        return ", ".join(unique)
    return ", ".join(unique[:limit]) + f" и ещё {len(unique) - limit}"


def _block_title(block_index: int, block_keys: list[str]) -> str:
    labels = [_compact_label(value, max_words=3, max_chars=34) for value in block_keys if value]
    unique = list(dict.fromkeys(labels))
    theme = unique[0] if unique else "Общее"
    return f"Блок {block_index}. {theme}"


def _block_goal(nodes: list[PlanNode]) -> str:
    names = _join_limited([node.name for node in nodes], limit=4)
    return f"Сформировать практику: {names}" if names else "Сформировать практический результат блока."


def _project_name(project: ProjectBlueprint, block_index: int, project_index: int, block_key: str = "") -> str:
    # Название должно опираться на данные проекта, а не на доменно-локальные keyword-шаблоны.
    nodes = project.unique_nodes
    if project.title:
        return _clean_project_title(project.title)
    if len(nodes) == 1:
        return _clean_project_title(nodes[0].name)
    anchor = nodes[-1].name
    return _clean_project_title(anchor)


def _cap_title_length(text: str, *, limit: int = 72) -> str:
    """Trim to ``limit`` chars on a word boundary (no mid-word cut)."""
    value = str(text or "").strip(" .,-:;")
    if len(value) <= limit:
        return value
    clipped = value[:limit].rstrip()
    boundary = clipped.rfind(" ")
    if boundary >= limit // 2:
        clipped = clipped[:boundary]
    return clipped.rstrip(" .,-:;")


def _deduplicate_project_name(name: str, project: ProjectBlueprint, seen: set[str], row_number: int) -> str:
    """Disambiguate duplicate titles by semantic refinement, bounded to the title length."""

    normalized = name.casefold().strip()
    if normalized not in seen:
        seen.add(normalized)
        return name
    # Prefer a distinguishing skill (semantic), fall back to a compact positional marker
    # (never "вариант N"); keep every candidate within the title length limit.
    anchor = _clean_project_title(project.unique_nodes[-1].name) if project.unique_nodes else ""
    if anchor and anchor.casefold() not in normalized:
        candidate = _cap_title_length(f"{name}: {anchor}")
    else:
        candidate = _cap_title_length(name, limit=66) + f" ({row_number})"
    while candidate.casefold().strip() in seen:
        candidate = _cap_title_length(name, limit=66) + f" ({row_number})"
        row_number += 1
    seen.add(candidate.casefold().strip())
    return candidate


def _project_summary(project: ProjectBlueprint, role: str) -> str:
    nodes = project.unique_nodes
    names = ", ".join(node.name for node in nodes)
    artifact = project.artifact.strip()
    if artifact:
        return (
            f"Практический проект, в котором участник в роли «{role}» собирает проверяемый артефакт: "
            f"{artifact}. Навыки проекта: {names}."
        )
    return (
        f"Практический проект, в котором участник в роли «{role}» применяет навыки {names} "
        "и собирает проверяемый промежуточный результат."
    )


def _project_storytelling(project: ProjectBlueprint, role: str, block_key: str) -> str:
    nodes = project.unique_nodes
    names = ", ".join(node.name for node in nodes)
    artifact = project.artifact.strip() or "проверяемый результат проекта"
    return (
        f"Ты работаешь как {role} и решаешь учебный кейс по теме «{block_key}». "
        f"Нужно применить навыки {names}, собрать артефакт «{artifact}» и защитить результат."
    )


def _localized_role(value: str) -> str:
    """Normalize common brief role labels before they reach Russian UP rows."""

    normalized = value.strip()
    aliases = {
        "beginning technological entrepreneur": "начинающий технологический предприниматель",
        "technological entrepreneur": "технологический предприниматель",
    }
    return aliases.get(normalized.lower(), normalized or "участник программы")


def _project_assessment_criteria(project: ProjectBlueprint) -> str:
    if project.enrichment.get("validation_criteria"):
        return project.enrichment["validation_criteria"]
    artifact = project.artifact.strip() or "проверяемый результат"
    skills = ", ".join(node.name for node in project.unique_nodes)
    return (
        f"Критерии проверки: артефакт «{artifact}» создан и предъявлен; "
        f"в решении явно применены навыки: {skills}; результат можно проверить по заявленным ЗУН."
    )


def _project_materials(project: ProjectBlueprint) -> str:
    if project.enrichment.get("materials"):
        criteria = _project_assessment_criteria(project)
        materials = project.enrichment["materials"]
        return materials if criteria and criteria in materials else "\n".join(item for item in [materials, criteria] if item)
    nodes = project.unique_nodes
    tools = sorted({tool for node in nodes for tool in node.tools})
    lines = [
        f"Описание артефакта: {project.artifact.strip() or 'проверяемый результат проекта'}.",
        f"Опорные навыки: {', '.join(node.name for node in nodes)}.",
        _project_assessment_criteria(project),
    ]
    if tools:
        lines.insert(2, f"Инструменты: {', '.join(tools)}.")
    return "\n".join(lines)


def enrich_curriculum_row(row: dict[str, Any], project: ProjectBlueprint, spec: dict[str, Any] | None, block_key: str) -> None:
    """Enrich a curriculum row without mutating DAG identity fields."""
    protected_node_ids = list(row.get("node_ids") or [])
    protected_node_names = list(row.get("node_names") or [])
    role = _localized_role(str((spec or {}).get("role") or "участник программы"))
    project_name = _project_name(project, int(row.get("block_index", 0) or 0), int(row.get("project_index_in_block", 0) or 0), block_key)
    row.update(
        {
            "project_name": project_name,
            "project_summary": _project_summary(project, role),
            "materials": _project_materials(project),
            "storytelling": project.enrichment.get("storytelling") or _project_storytelling(project, role, block_key),
            "platform_project_name": project_name,
            "validation_criteria": _project_assessment_criteria(project),
        }
    )
    profile_decision = infer_content_profile(
        direction=str((spec or {}).get("direction_code") or (spec or {}).get("direction") or ""),
        thematic_block=block_key,
        title=project_name,
        description=str(row.get("project_summary") or ""),
        skills=[node.name for node in project.unique_nodes],
        required_tools=[tool for node in project.unique_nodes for tool in node.tools],
        learning_outcomes=[
            str(row.get("outcomes_know") or ""),
            str(row.get("outcomes_can") or ""),
            str(row.get("outcomes_skills") or ""),
        ],
        artifact=project.artifact,
    )
    row["project_content_type"] = profile_decision.profile
    row["content_profile_decision"] = profile_decision.model_dump(mode="json")
    row["node_ids"] = protected_node_ids
    row["node_names"] = protected_node_names


def _occurrence_totals(blocks: list[CurriculumBlock]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for block in blocks:
        for project in block.projects:
            for occurrence in project.occurrences:
                totals[occurrence.node.tmp_id] = totals.get(occurrence.node.tmp_id, 0) + 1
    return totals


def _allows_outcome_bucket(occurrence: SkillOccurrence, bucket: str, total_occurrences: int) -> bool:
    if bucket in {"know", "can"}:
        return True
    if bucket != "skills":
        return False
    if occurrence.role == "assessment" or occurrence.bloom_bucket == "skills":
        return True
    # Если высокого повторного касания не будет, единственная строка должна
    # сохранить терминальный результат. У повторяемых нитей "владеть"
    # раскрывается позже.
    return total_occurrences <= 1 and occurrence.node.bloom >= 5


def _preferred_outcome_bucket(occurrence: SkillOccurrence, total_occurrences: int) -> str:
    if occurrence.bloom_bucket == "know" or occurrence.node.bloom <= 2:
        return "know"
    if _allows_outcome_bucket(occurrence, "skills", total_occurrences):
        if occurrence.bloom_bucket == "skills" or occurrence.role == "assessment" or occurrence.node.bloom >= 5:
            return "skills"
    return "can"


def _occurrence_outcome_sources(occurrence: SkillOccurrence, occurrence_totals: dict[str, int] | None = None) -> tuple[str, tuple[str, ...]]:
    node = occurrence.node
    total_occurrences = int((occurrence_totals or {}).get(node.tmp_id, 1) or 1)
    bucket = _preferred_outcome_bucket(occurrence, total_occurrences)
    if bucket == "know":
        return "know", node.outcomes_know or (f"Объясняет назначение навыка «{node.name}» в рабочем контексте.",)
    if bucket == "skills":
        return "skills", node.outcomes_skills or node.outcomes_can or (f"Интегрирует навык «{node.name}» в проверяемый артефакт.",)
    return "can", node.outcomes_can or node.outcomes_know or (f"Применяет навык «{node.name}» для решения проектной задачи.",)


def _fallback_outcome(occurrence: SkillOccurrence, occurrence_totals: dict[str, int] | None = None) -> tuple[str, str]:
    node = occurrence.node
    total_occurrences = int((occurrence_totals or {}).get(node.tmp_id, 1) or 1)
    bucket = _preferred_outcome_bucket(occurrence, total_occurrences)
    if bucket == "skills":
        return "skills", f"Защищает результат, демонстрируя владение навыком «{node.name}»."
    if occurrence.role == "reinforcement":
        return "can", f"Повторно применяет навык «{node.name}» в более сложном сценарии."
    if bucket == "know":
        return "know", f"Понимает ключевые принципы навыка «{node.name}»."
    return "can", f"Применяет навык «{node.name}» в практическом задании."


def _project_allowed_buckets(project: ProjectBlueprint, occurrence_totals: dict[str, int] | None = None) -> set[str]:
    allowed = {"know", "can"}
    for occurrence in project.occurrences:
        total_occurrences = int((occurrence_totals or {}).get(occurrence.node.tmp_id, 1) or 1)
        if _allows_outcome_bucket(occurrence, "skills", total_occurrences):
            allowed.add("skills")
            break
    return allowed


def _project_outcomes(project: ProjectBlueprint, occurrence_totals: dict[str, int] | None = None) -> tuple[str, str, str, int]:
    buckets: dict[str, list[str]] = {"know": [], "can": [], "skills": []}
    max_outcomes = max(1, int(config.UP_TARGET_OUTCOMES_MAX))
    min_outcomes = max(1, min(int(config.UP_TARGET_OUTCOMES_MIN), max_outcomes))
    allowed_buckets = _project_allowed_buckets(project, occurrence_totals)

    for occurrence in project.occurrences:
        bucket, outcomes = _occurrence_outcome_sources(occurrence, occurrence_totals)
        if bucket not in allowed_buckets:
            continue
        for outcome in outcomes:
            text = outcome.strip()
            if text and text not in buckets[bucket] and sum(len(items) for items in buckets.values()) < max_outcomes:
                buckets[bucket].append(text)

    for occurrence in project.occurrences:
        if sum(len(items) for items in buckets.values()) >= min_outcomes:
            break
        bucket, outcome = _fallback_outcome(occurrence, occurrence_totals)
        if bucket not in allowed_buckets:
            continue
        if outcome not in buckets[bucket]:
            buckets[bucket].append(outcome)

    anchor = project.unique_nodes[-1].name if project.unique_nodes else "проектный навык"
    completion_fallbacks = [
        ("know", f"Описывает контекст применения навыка «{anchor}»."),
        ("can", f"Применяет навык «{anchor}» при создании проектного артефакта."),
        ("can", f"Обосновывает выбранный способ работы с темой «{project.block_key}»."),
        ("skills", f"Оформляет и защищает проверяемый результат по теме «{project.block_key}»."),
    ]
    for bucket, outcome in completion_fallbacks:
        if sum(len(items) for items in buckets.values()) >= min_outcomes:
            break
        if bucket not in allowed_buckets:
            continue
        if outcome not in buckets[bucket]:
            buckets[bucket].append(outcome)

    return (
        "\n".join(buckets["know"]),
        "\n".join(buckets["can"]),
        "\n".join(buckets["skills"]),
        sum(len(items) for items in buckets.values()),
    )


def _project_skill_list(project: ProjectBlueprint) -> str:
    labels: list[str] = []
    for occurrence in project.occurrences:
        suffix = ""
        if occurrence.role == "reinforcement":
            suffix = " (закрепление)"
        elif occurrence.role == "assessment":
            suffix = " (контроль/владение)"
        label = occurrence.node.name + suffix
        if label not in labels:
            labels.append(label)
    return ", ".join(labels)


def _weighted_skill_list(project: ProjectBlueprint) -> str:
    nodes = project.unique_nodes
    if not nodes:
        return ""
    base_weight = round(100 / len(nodes))
    weights = [base_weight] * len(nodes)
    delta = 100 - sum(weights)
    if weights:
        weights[-1] += delta
    return ", ".join(f"{node.name}: {weight}%" for node, weight in zip(nodes, weights, strict=False))


def _default_group_size(delivery_format: str) -> int:
    bounds = config.UP_FORMAT_GROUP_SIZES.get(delivery_format, (1, 1))
    return int(bounds[0])


def _target_total_hours(spec: dict[str, Any] | None) -> float | None:
    raw = (spec or {}).get("target_total_hours")
    if raw in (None, ""):
        return None
    try:
        value = float(raw or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _scale_hours_to_target(rows: list[dict[str, Any]], spec: dict[str, Any] | None) -> None:
    target_hours = _target_total_hours(spec)
    current_hours = sum(float(row.get("effort_hours", 0) or 0) for row in rows)
    if not rows or not target_hours or current_hours <= 0:
        return
    factor = target_hours / current_hours
    # Small deltas are noise from rounding. Large deltas mean the brief contains
    # an explicit workload contract and the plan must respect it.
    if 0.9 <= factor <= 1.1:
        return
    for row in rows:
        raw = float(row.get("effort_hours", 0) or 0) * factor
        row["effort_hours"] = max(4, int(round(raw / 2.0) * 2))
    rounded_total = sum(float(row.get("effort_hours", 0) or 0) for row in rows)
    delta = int(round(target_hours - rounded_total))
    if delta and rows:
        rows[-1]["effort_hours"] = max(4, int(float(rows[-1].get("effort_hours", 0) or 0) + delta))


def _fill_effort_columns(rows: list[dict[str, Any]]) -> None:
    total_hours = sum(float(row.get("effort_hours", 0) or 0) for row in rows)
    cumulative_days = 0.0
    for row in rows:
        effort_hours = float(row.get("effort_hours", 0) or 0)
        effort_days = round(effort_hours / config.UP_HOURS_PER_DAY, 2) if config.UP_HOURS_PER_DAY else 0.0
        cumulative_days = round(cumulative_days + effort_days, 2)
        row["effort_days"] = effort_days
        row["cumulative_days"] = cumulative_days
        row["xp"] = int(round(effort_hours * config.UP_XP_PER_HOUR))
        row["completion_percent"] = round((sum(float(item.get("effort_hours", 0) or 0) for item in rows[: int(row["row_number"])]) / total_hours) * 100, 1) if total_hours else ""
        row["p2p_checks"] = 1 if effort_hours >= 12 else 0


def _format_rows(blocks: list[CurriculumBlock], spec: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    row_number = 0
    seen_project_names: set[str] = set()
    occurrence_totals = _occurrence_totals(blocks)
    for block_index, block in enumerate(blocks, start=1):
        all_block_nodes = [node for project in block.projects for node in project.unique_nodes]
        block_keys = list(dict.fromkeys([project.block_key for project in block.projects if project.block_key]))
        if not block_keys:
            block_keys = list(dict.fromkeys([node.block_key for node in all_block_nodes]))
        block_title = block.title or _block_title(block_index, block_keys)
        block_goal = block.goal or _block_goal(all_block_nodes)
        for project_index, project in enumerate(block.projects, start=1):
            row_number += 1
            project_nodes = project.unique_nodes
            effort_hours = _estimate_project_hours(project_nodes)
            required_tools = ", ".join(sorted({tool for node in project_nodes for tool in node.tools}))
            outcomes_know, outcomes_can, outcomes_skills, outcome_count = _project_outcomes(project, occurrence_totals)
            block_key = project.block_key or (project_nodes[0].block_key if project_nodes else "Общее")
            delivery_format = config.UP_DEFAULT_FORMAT
            row = {
                "block_index": block_index,
                "row_number": row_number,
                "project_index_in_block": project_index,
                "block_title": block_title if project_index == 1 else "",
                "block_goal": block_goal,
                "project_name": "",
                "project_summary": "",
                "outcomes_know": outcomes_know,
                "outcomes_can": outcomes_can,
                "outcomes_skills": outcomes_skills,
                "learning_outcomes": "\n".join(item for item in [outcomes_know, outcomes_can, outcomes_skills] if item),
                "skills_list": _project_skill_list(project),
                "node_ids": project.node_ids,
                "node_names": [node.name for node in project_nodes],
                "occurrence_count": len(project.occurrences),
                "primary_skill_count": len(project.primary_occurrences),
                "repeat_skill_count": len([occurrence for occurrence in project.occurrences if occurrence.is_repeat]),
                "outcome_count": outcome_count,
                "artifact": project.artifact,
                "artifact_key": project.artifact_key,
                "artifact_family": project.artifact_family,
                "artifact_template_code": project.artifact_template_code,
                "template_binding": project.template_binding.as_dict() if project.template_binding else None,
                "project_type": project.project_type,
                "policy_area": project.policy_area,
                "policy_area_confidence": project.policy_area_confidence,
                "policy_area_rationale": project.policy_area_rationale,
                "policy_area_source": project.policy_area_source,
                "audience_level": _audience_label(spec),
                "required_tools": required_tools,
                "materials": "",
                "storytelling": "",
                "delivery_format": delivery_format,
                "group_size": _default_group_size(delivery_format),
                "effort_hours": effort_hours,
                "effort_days": "",
                "cumulative_days": "",
                "xp": "",
                "completion_percent": "",
                "p2p_checks": "",
                "weighted_skills": _weighted_skill_list(project),
                "platform_project_name": "",
                "artifact_links": "",
            }
            enrich_curriculum_row(row, project, spec, block_key)
            row["project_name"] = _deduplicate_project_name(
                str(row.get("project_name") or "Проект"),
                project,
                seen_project_names,
                row_number,
            )
            row["platform_project_name"] = row["project_name"]
            rows.append(row)
    _scale_hours_to_target(rows, spec)
    _fill_effort_columns(rows)
    return rows


def _plan_report(rows: list[dict[str, Any]], dag_payload: dict[str, Any]) -> dict[str, Any]:
    # Проверяем порядок по стабильным tmp_id, а не по отображаемым именам.
    position_by_node: dict[str, tuple[int, int]] = {}
    row_by_node: dict[str, int] = {}
    for row in rows:
        row_number = int(row.get("row_number", 0) or 0)
        node_ids = row.get("node_ids") or []
        for skill_index, node_id in enumerate(node_ids):
            node_key = str(node_id)
            position_by_node.setdefault(node_key, (row_number, skill_index))
            row_by_node.setdefault(node_key, row_number)
    broken_order: list[str] = []
    recommended_order_notes: list[str] = []
    project_violations: list[str] = []
    for edge in dag_payload.get("final_edges", []):
        if not isinstance(edge, dict):
            continue
        src_id = str(edge.get("src_id") or "")
        dst_id = str(edge.get("dst_id") or "")
        src = str(edge.get("src") or src_id)
        dst = str(edge.get("dst") or dst_id)
        if not src_id or not dst_id or src_id not in position_by_node or dst_id not in position_by_node:
            continue
        edge_role = curriculum_edge_role(edge)
        if row_by_node[src_id] == row_by_node[dst_id]:
            if edge_role == "required":
                project_violations.append(f"{src} -> {dst}")
            continue
        if edge_role == "required" and position_by_node[src_id] >= position_by_node[dst_id]:
            broken_order.append(f"{src} -> {dst}")
        elif edge_role == "recommended" and position_by_node[src_id] >= position_by_node[dst_id]:
            recommended_order_notes.append(f"{src} -> {dst}")
    return {
        "coverage_ok": not broken_order and not project_violations,
        "order_violations": broken_order,
        "recommended_order_notes": recommended_order_notes,
        "project_violations": project_violations,
    }


def _quality_metrics(rows: list[dict[str, Any]], planner_meta: dict[str, Any]) -> dict[str, Any]:
    project_count = len(rows)
    if not project_count:
        return {
            "avg_skills_per_project": 0.0,
            "avg_outcomes_per_project": 0.0,
            "single_skill_project_count": 0,
            "avg_primary_skills_per_project": 0.0,
            "avg_repeat_skills_per_project": 0.0,
            "overloaded_project_count": 0,
            "enriched_project_count": 0,
            "enrichment_completeness_pct": 0.0,
            "artifact_field_count": 0,
            "validation_criteria_count": 0,
            "core_thread_count": 0,
            "repeated_thread_count": 0,
            "spiral_enabled": bool(config.UP_SPIRAL_ENABLED),
            "artifact_first": bool(planner_meta.get("artifact_first", False)),
            "artifact_project_count": int(planner_meta.get("artifact_project_count", 0) or 0),
            "db_template_count": int(planner_meta.get("db_template_count", 0) or 0),
            "db_template_project_count": int(planner_meta.get("db_template_project_count", 0) or 0),
            "template_bound_project_count": int(planner_meta.get("template_bound_project_count", 0) or 0),
            "template_unused_count": int(planner_meta.get("template_unused_count", 0) or 0),
            "template_unbound_project_count": int(planner_meta.get("template_unbound_project_count", 0) or 0),
            "unassigned_node_count": int(planner_meta.get("unassigned_node_count", 0) or 0),
            "journey_stage_count": 0,
            "uncovered_required_area_count": 0,
            "capstone_required": False,
            "capstone_present": False,
            "design_readiness_state": "not_available",
            "blocking_question_count": 0,
            "dag_wave_count": int(planner_meta.get("dag_wave_count", 0) or 0),
            "up_block_count": 0,
            "target_skills_per_project": [config.UP_TARGET_SKILLS_MIN, config.UP_TARGET_SKILLS_MAX],
            "target_outcomes_per_project": [config.UP_TARGET_OUTCOMES_MIN, config.UP_TARGET_OUTCOMES_MAX],
            **report_only_quality_metrics(rows),
        }
    skill_counts = [len(row.get("node_ids") or []) for row in rows]
    primary_skill_counts = [int(row.get("primary_skill_count", len(row.get("node_ids") or [])) or 0) for row in rows]
    repeat_skill_counts = [int(row.get("repeat_skill_count", 0) or 0) for row in rows]
    outcome_counts = [int(row.get("outcome_count", 0) or 0) for row in rows]
    enriched_project_count = 0
    artifact_field_count = 0
    validation_criteria_count = 0
    for row in rows:
        has_artifact = bool(str(row.get("artifact") or "").strip())
        has_validation = bool(str(row.get("validation_criteria") or "").strip())
        artifact_field_count += int(has_artifact)
        validation_criteria_count += int(has_validation)
        enriched_project_count += int(
            all(
                str(row.get(field) or "").strip()
                for field in (
                    "project_summary",
                    "artifact",
                    "materials",
                    "storytelling",
                    "validation_criteria",
                    "delivery_format",
                )
            )
        )
    overloaded = [
        row
        for row in rows
        if len(row.get("node_ids") or []) > config.UP_TARGET_SKILLS_MAX
        or int(row.get("outcome_count", 0) or 0) > config.UP_TARGET_OUTCOMES_MAX
    ]
    return {
        "avg_skills_per_project": round(sum(skill_counts) / project_count, 2),
        "avg_primary_skills_per_project": round(sum(primary_skill_counts) / project_count, 2),
        "avg_repeat_skills_per_project": round(sum(repeat_skill_counts) / project_count, 2),
        "avg_outcomes_per_project": round(sum(outcome_counts) / project_count, 2),
        "single_skill_project_count": sum(1 for count in skill_counts if count <= 1),
        "overloaded_project_count": len(overloaded),
        "enriched_project_count": enriched_project_count,
        "enrichment_completeness_pct": round(enriched_project_count / project_count * 100, 1),
        "artifact_field_count": artifact_field_count,
        "validation_criteria_count": validation_criteria_count,
        "core_thread_count": len(planner_meta.get("core_thread_ids") or []),
        "repeated_thread_count": int(planner_meta.get("repeated_thread_count", 0) or 0),
        "spiral_enabled": bool(config.UP_SPIRAL_ENABLED),
        "artifact_first": bool(planner_meta.get("artifact_first", False)),
        "artifact_project_count": int(planner_meta.get("artifact_project_count", 0) or 0),
        "db_template_count": int(planner_meta.get("db_template_count", 0) or 0),
        "db_template_project_count": int(planner_meta.get("db_template_project_count", 0) or 0),
        "template_bound_project_count": int(planner_meta.get("template_bound_project_count", 0) or 0),
        "template_unused_count": int(planner_meta.get("template_unused_count", 0) or 0),
        "template_unbound_project_count": int(planner_meta.get("template_unbound_project_count", 0) or 0),
        "unassigned_node_count": int(planner_meta.get("unassigned_node_count", 0) or 0),
        "journey_stage_count": len((planner_meta.get("design_spec") or {}).get("stages") or []),
        "uncovered_required_area_count": len(
            (planner_meta.get("design_spec") or {}).get("uncovered_required_areas") or []
        ),
        "capstone_required": bool((planner_meta.get("design_spec") or {}).get("capstone_required", False)),
        "capstone_present": bool(int(planner_meta.get("capstone_project_count", 0) or 0)),
        "design_readiness_state": str(
            (planner_meta.get("design_spec") or {}).get("readiness_state") or "not_available"
        ),
        "blocking_question_count": int(
            (planner_meta.get("design_spec") or {}).get("blocking_question_count", 0) or 0
        ),
        "target_skills_per_project": [config.UP_TARGET_SKILLS_MIN, config.UP_TARGET_SKILLS_MAX],
        "target_outcomes_per_project": [config.UP_TARGET_OUTCOMES_MIN, config.UP_TARGET_OUTCOMES_MAX],
        **report_only_quality_metrics(rows),
    }


def run(spec: dict[str, Any] | None, candidates: list[SkillCandidate], dag_payload: dict[str, Any]) -> dict[str, Any]:
    # Планировщик работает только по фактически принятым узлам DAG.
    if not candidates or not dag_payload.get("order"):
        return {
            "status": "deferred",
            "message": "Черновик УП пока не строится: нет принятых навыков с валидным DAG.",
            "title": "Черновик учебного плана",
            "audience_level": _audience_label(spec),
            "source_policy": "accepted_only",
            "summary": {
                "blocks": 0,
                "projects": 0,
                "total_hours": 0,
                "total_days": 0,
                "workload": build_workload_contract(0, spec, default_hours_per_week=config.UP_HOURS_PER_WEEK).as_dict(),
                "total_xp": 0,
            },
            "rows": [],
            "blocks": [],
            "csv_primary_header": CSV_PRIMARY_HEADER,
            "csv_secondary_header": CSV_SECONDARY_HEADER,
            "report": {
                "coverage_ok": False,
                "order_violations": [],
                "project_violations": [],
                "quality_metrics": _quality_metrics([], {}),
                "publication_gate": evaluate_publication_gate(_quality_metrics([], {})).as_dict(),
            },
        }

    nodes = build_plan_nodes(candidates)
    artifact_templates = (spec or {}).get("artifact_templates")
    if not isinstance(artifact_templates, list):
        artifact_templates = []
    blocks, planner_meta = build_curriculum_blocks(nodes, dag_payload, artifact_templates, planning_context=spec)
    rows = _format_rows(blocks, spec)
    total_hours = sum(float(row.get("effort_hours", 0) or 0) for row in rows)
    total_days = sum(float(row.get("effort_days", 0) or 0) for row in rows)
    total_xp = sum(int(row.get("xp", 0) or 0) for row in rows)
    report = _plan_report(rows, dag_payload)
    report["quality_metrics"] = _quality_metrics(rows, planner_meta)
    dag_waves = dag_payload.get("visual_waves") or dag_payload.get("waves") or []
    report["quality_metrics"]["dag_wave_count"] = len(dag_waves) if isinstance(dag_waves, list) else 0
    report["quality_metrics"]["up_block_count"] = len(blocks)
    report["publication_gate"] = evaluate_publication_gate(report["quality_metrics"]).as_dict()
    report["planner_meta"] = planner_meta
    design_spec = planner_meta.get("design_spec") if isinstance(planner_meta.get("design_spec"), dict) else {}
    is_invalid = bool(report["order_violations"] or report.get("project_violations"))

    # Для UI держим и блочное представление, и плоские CSV-совместимые строки.
    block_payloads: list[dict[str, Any]] = []
    rows_by_block: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_block.setdefault(int(row["block_index"]), []).append(row)
    for block_index, block_rows in rows_by_block.items():
        total_block_hours = sum(float(row.get("effort_hours", 0) or 0) for row in block_rows)
        total_block_days = sum(float(row.get("effort_days", 0) or 0) for row in block_rows)
        block_payloads.append(
            {
                "block_index": block_index,
                "title": str(block_rows[0].get("block_title") or f"Блок {block_index}"),
                "goal": str(block_rows[0].get("block_goal") or ""),
                "project_count": len(block_rows),
                "total_hours": total_block_hours,
                "total_days": round(total_block_days, 2),
                "rows": block_rows,
            }
        )

    return {
        "status": "invalid" if is_invalid else "built",
        "message": (
            "Черновик УП невалиден: найдены нарушения порядка DAG. Нужна перенарезка проектов или правка DAG."
            if is_invalid
            else "Черновик УП построен детерминированно по принятым skills и текущему DAG."
        ),
        "title": "Черновик учебного плана",
        "audience_level": _audience_label(spec),
        "source_policy": "accepted_only",
        "planner_meta": planner_meta,
        "design_spec": design_spec,
        "summary": {
            "blocks": len(block_payloads),
            "projects": len(rows),
            "total_hours": int(total_hours) if isfinite(total_hours) else 0,
            # total_days is retained for DB/CSV compatibility (deprecated); the canonical
            # workload figure is the derived weeks/months in "workload" below.
            "total_days": round(total_days, 2) if isfinite(total_days) else 0.0,
            "workload": build_workload_contract(
                total_hours if isfinite(total_hours) else 0,
                spec,
                default_hours_per_week=config.UP_HOURS_PER_WEEK,
            ).as_dict(),
            "total_xp": int(total_xp),
            "avg_skills_per_project": report["quality_metrics"]["avg_skills_per_project"],
            "avg_outcomes_per_project": report["quality_metrics"]["avg_outcomes_per_project"],
            "repeated_thread_count": report["quality_metrics"]["repeated_thread_count"],
        },
        "rows": rows,
        "blocks": block_payloads,
        "csv_primary_header": CSV_PRIMARY_HEADER,
        "csv_secondary_header": CSV_SECONDARY_HEADER,
        "report": report,
    }
