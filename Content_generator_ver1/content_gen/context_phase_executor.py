"""Context phase executor for seed and curriculum context assembly."""

from __future__ import annotations

import logging
from typing import Any

from .agents.intent_mapper import IntentMapper
from .agents.context_analysis import ContextAnalysisResult
from .curriculum.models import CurriculumEntry
from .domain_contracts import build_narrative_contract
from .generation_runtime import GenerationRuntimeContainer
from .models.flow_state import ProjectContextBundle
from .models.phase_results import ContextPhaseResult
from .models.schemas import ProjectContextMeta, ProjectSeed

logger = logging.getLogger("content_gen.context_phase_executor")


def _safe_int(value: Any) -> int | None:
    """Convert value to int when possible."""
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_list(values: Any) -> list[str]:
    """Normalize strings into a deduplicated list."""
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _collect_project_values(projects: list[dict[str, Any]], keys: list[str]) -> list[str]:
    """Collect list-like fields from curriculum project snapshots."""
    collected: list[str] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        for key in keys:
            value = project.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        cleaned = item.strip()
                        if cleaned and cleaned not in collected:
                            collected.append(cleaned)
            elif isinstance(value, str):
                cleaned = value.strip()
                if cleaned and cleaned not in collected:
                    collected.append(cleaned)
    return collected


def _build_context_summary(seed: ProjectSeed, curriculum_ctx: dict[str, Any], prev_projects: list[dict[str, Any]]) -> str:
    """Compose a short context summary for downstream generators."""
    parts: list[str] = []
    block_name = curriculum_ctx.get("block_name")
    if block_name:
        parts.append(f"Проект относится к блоку «{block_name}».")

    current_description = curriculum_ctx.get("current_project_description") or seed.project_description
    if current_description:
        parts.append(f"Текущий проект фокусируется на: {current_description}.")

    storytelling_type = str(getattr(seed, "storytelling_type", None) or curriculum_ctx.get("storytelling_type") or "sjm")
    story = "" if storytelling_type == "none" else (getattr(seed, "sjm", None) or curriculum_ctx.get("sjm_context") or "").strip()
    if story:
        parts.append(f"Рабочий кейс проекта: {story[:280]}.")

    if prev_projects:
        prev_titles = [p.get("title", "").strip() for p in prev_projects[-2:] if isinstance(p, dict) and p.get("title")]
        if prev_titles:
            parts.append(f"До него студенты уже прошли: {', '.join(prev_titles)}.")

    next_projects = curriculum_ctx.get("next_projects", [])
    if isinstance(next_projects, list) and next_projects:
        next_titles = [p.get("title", "").strip() for p in next_projects[:2] if isinstance(p, dict) and p.get("title")]
        if next_titles:
            parts.append(f"Следующие темы пока не раскрываем заранее: {', '.join(next_titles)}.")

    if not parts:
        return "Контекст проекта формируется по данным учебного плана."
    return " ".join(parts[:4])


def _build_narrative_anchor(
    seed: ProjectSeed,
    prev_projects: list[dict[str, Any]],
    skills_intersection: list[str],
    skills_new: list[str],
) -> str:
    """Build continuity bridge from previous project to the current one."""
    if not prev_projects:
        return ""

    last_project = prev_projects[-1]
    last_title = last_project.get("title", "предыдущего проекта")

    anchor_parts = [f"Проект продолжает линию после «{last_title}»."]
    if skills_intersection:
        anchor_parts.append(f"Студенты уже опираются на навыки: {', '.join(skills_intersection[:3])}.")
    if skills_new:
        anchor_parts.append(
            "Теперь фокус смещается на текущий рабочий кейс, цепочку артефактов и проверяемый результат."
        )
    elif seed.learning_outcomes:
        anchor_parts.append("Новый проект развивает текущую тему блока без выхода за его границы.")
    return " ".join(anchor_parts)


def _execute_context_phase(
    orchestrator,
    raw_input: dict[str, Any],
    track_files: list[str] = None
) -> ContextPhaseResult:
    """Build seed and curriculum-aware context without retrieval."""
    orchestrator.intent = IntentMapper()
    logger.info("🔄 Phase 0 | IntentMapper + curriculum context")

    seed, intent_warnings = orchestrator.intent.map(raw_input)
    original_language = seed.language

    if original_language != "ru":
        logger.info(f"ℹ️ Целевой язык: {original_language}. Генерация будет на русском, перевод в конце.")
        seed.language = "ru"

    warnings = intent_warnings.messages
    curriculum_ctx = seed.curriculum_context or {}

    # Данные из УП имеют явный приоритет как методологический контракт проекта.
    # Форма может передать их напрямую, но при восстановлении из sessionStorage
    # они также доступны внутри curriculum_context.
    if curriculum_ctx:
        if curriculum_ctx.get("storytelling_type"):
            seed.storytelling_type = ProjectSeed._normalize_storytelling_type(curriculum_ctx.get("storytelling_type"))
        if not seed.sjm and curriculum_ctx.get("sjm_context"):
            seed.sjm = str(curriculum_ctx["sjm_context"]).strip() or None
        if (
            (not seed.audience_level or str(seed.audience_level).lower() in {"base", "beginner", "beginner_plus"})
            and curriculum_ctx.get("current_project_audience_level")
        ):
            seed.audience_level = str(curriculum_ctx["current_project_audience_level"]).strip()
        if not seed.required_tools and curriculum_ctx.get("current_project_required_tools"):
            seed.required_tools = _normalize_list(curriculum_ctx["current_project_required_tools"])
        if not getattr(seed, "required_software", None) and curriculum_ctx.get("current_project_required_software"):
            seed.required_software = _normalize_list(curriculum_ctx["current_project_required_software"])

    previous_projects = curriculum_ctx.get("previous_projects", [])
    if not isinstance(previous_projects, list):
        previous_projects = []

    narrative_contract = build_narrative_contract(seed, curriculum_ctx, previous_projects)
    curriculum_ctx = {**curriculum_ctx, "narrative_contract": narrative_contract.model_dump()}
    seed.curriculum_context = curriculum_ctx

    current_project_order = _safe_int(curriculum_ctx.get("current_project_order"))
    previous_orders = [
        order
        for order in (_safe_int(project.get("order")) for project in previous_projects if isinstance(project, dict))
        if order is not None
    ]
    last_order = max(previous_orders, default=0)
    if last_order == 0 and current_project_order and current_project_order > 1:
        last_order = current_project_order - 1
    if last_order == 0 and seed.last_known_order:
        last_order = seed.last_known_order

    previous_skills = _collect_project_values(previous_projects, ["skills", "current_project_skills"])
    previous_outcomes = _collect_project_values(previous_projects, ["learning_outcomes"])
    previous_tools = _collect_project_values(previous_projects, ["required_tools", "tools"])

    seed_skills = _normalize_list(seed.skills)
    seed_outcomes = _normalize_list(seed.learning_outcomes)
    seed_tools = _normalize_list(seed.required_tools)

    skills_intersection = [skill for skill in seed_skills if skill in previous_skills]
    skills_new = [skill for skill in seed_skills if skill not in previous_skills]
    outcomes_continuation = [item for item in seed_outcomes if item in previous_outcomes]
    outcomes_new = [item for item in seed_outcomes if item not in previous_outcomes]
    tools_used = [tool for tool in seed_tools if tool in previous_tools]
    tools_new = [tool for tool in seed_tools if tool not in previous_tools]

    is_first_project = not previous_projects and last_order == 0
    context_summary = _build_context_summary(seed, curriculum_ctx, previous_projects)
    narrative_anchor = _build_narrative_anchor(seed, previous_projects, skills_intersection, skills_new)

    thematic_block = seed.thematic_block or curriculum_ctx.get("block_name") or seed.direction or "unknown"
    track = seed.direction or seed.thematic_block or thematic_block
    reference_enabled = bool((seed.reference_project_hint or "").strip() or (seed.reference_practice_hint or "").strip())

    context_meta = ProjectContextMeta(
        track=track,
        thematic_block=thematic_block,
        last_order=last_order,
        aligned_skills=skills_intersection[:5],
        narrative_anchor=narrative_anchor,
        similar_projects=[],
        search_metrics={
            "context_source": "curriculum_only",
            "previous_projects_count": len(previous_projects),
            "reference_enabled": reference_enabled,
        },
        context_summary=context_summary,
        context_profiles_used={
            "mode": "curriculum_only",
            "reference_project_hint": bool((seed.reference_project_hint or "").strip()),
            "reference_practice_hint": bool((seed.reference_practice_hint or "").strip()),
            "narrative_contract": narrative_contract.model_dump(),
        },
        context_levels=[
            {
                "level": "curriculum",
                "block_name": curriculum_ctx.get("block_name"),
                "current_project_order": current_project_order,
            }
        ],
    )

    context_analysis = ContextAnalysisResult(
        is_first_project=is_first_project,
        context_summary=context_summary,
        narrative_anchor=narrative_anchor,
        similar_projects=[],
        relevant_chunks=[],
        skills_alignment={
            "intersection": skills_intersection,
            "new": skills_new,
        },
        learning_outcomes_alignment={
            "continuation": outcomes_continuation,
            "new": outcomes_new,
        },
        tools_alignment={
            "used": tools_used,
            "new": tools_new,
        },
        audience_level_match=True,
        metrics={
            "context_source": "curriculum_only",
            "previous_projects_count": len(previous_projects),
            "current_project_order": current_project_order,
            "reference_enabled": reference_enabled,
        },
    )

    context_bundle = ProjectContextBundle(
        context_source="curriculum_only",
        thematic_block=thematic_block,
        current_project_order=current_project_order,
        previous_projects_count=len(previous_projects),
        is_first_project=is_first_project,
        reference_enabled=reference_enabled,
        context_summary=context_summary,
        narrative_anchor=narrative_anchor,
        narrative_contract=narrative_contract.model_dump(),
        aligned_skills=skills_intersection,
        new_skills=skills_new,
        continued_learning_outcomes=outcomes_continuation,
        new_learning_outcomes=outcomes_new,
        used_tools=tools_used,
        new_tools=tools_new,
    )

    similar_projects: list[CurriculumEntry] = []
    if curriculum_ctx:
        warnings.append("ℹ️ Контекст проекта собран из учебного плана без retrieval-поиска.")
    else:
        warnings.append("⚠️ Контекст УП не передан: генерация опирается только на входное описание проекта.")

    if reference_enabled:
        warnings.append("ℹ️ Эталонный reference подключен как ориентир по структуре и качеству, но не как источник фактов.")

    if is_first_project:
        warnings.append(f"ℹ️ В тематическом блоке {thematic_block} нет предыдущих проектов.")
        logger.info(f"ℹ️ Первый проект в тематическом блоке {thematic_block}")
    elif skills_new:
        warnings.append(f"ℹ️ Новые навыки проекта: {', '.join(skills_new[:3])}")

    logger.info(
        "✅ Контекст проекта подготовлен: previous_projects=%s, last_order=%s, reference=%s",
        len(previous_projects),
        last_order,
        reference_enabled,
    )

    return ContextPhaseResult(
        seed=seed,
        context_meta=context_meta,
        context_analysis=context_analysis,
        context_bundle=context_bundle,
        similar_projects=list(similar_projects or []),
        warnings=warnings,
    )


class ContextPhaseExecutor:
    """Execute Phase 0 against an explicit runtime dependency container."""

    def __init__(self, runtime: GenerationRuntimeContainer) -> None:
        self.runtime = runtime

    def execute(
        self,
        raw_input: dict[str, Any],
        track_files: list[str] | None = None,
    ) -> ContextPhaseResult:
        return _execute_context_phase(self.runtime, raw_input, track_files)
