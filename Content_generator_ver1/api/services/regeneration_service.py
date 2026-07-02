"""Application service for README regeneration workflows."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from api.db.generation_results_db import update_regeneration_result
from api.db.logging_db import write_log_async
from api.utils.logger import get_logger
from api.utils.result_cache import get_result
from content_gen.agents.content_editor import ContentEditorAgent
from content_gen.agents.regeneration import RegenerationAgent
from content_gen.regeneration_pipeline import build_regeneration_pipeline_input
from content_gen.repair.style_guard import StyleGuardRepair
from content_gen.llm.factory import create_llm_client
from content_gen.models.readme_document import ReadmeDocument
from content_gen.project_seed_provider import ProjectSeedProvider
from content_gen.renderers.toc import TOCRenderer
from content_gen.utils.latex_validator import build_latex_agent_hint, collect_latex_issues
from content_gen.utils.markdown_display_normalizer import (
    normalize_markdown_display_blocks,
    strip_protected_block_instruction_leaks,
)
from content_gen.utils.markdown_regeneration_guard import remove_adjacent_rewritten_paragraph_duplicates
from content_gen.utils.regeneration_scope import RegenerationChangeIntent
from content_gen.utils.rubric_export import convert_numpy_types, criteria_to_json
from content_gen.validators.rubric import RubricScorer
from utils.token_counter import count_tokens

logger = get_logger("regeneration")


@dataclass(frozen=True)
class RegenerationCommand:
    """Input contract for regeneration application workflow."""

    request_id: str
    user_id: str
    original_md: str
    comments: str
    language: str = "ru"
    original_request_id: str | None = None
    project_seed: dict[str, Any] | None = None
    curriculum_project: dict[str, Any] | None = None


@dataclass(frozen=True)
class RegenerationResultView:
    """Serializable regeneration result returned to the HTTP adapter."""

    request_id: str
    regenerated_md: str
    changes: list[str]
    rubric: dict[str, Any]
    text_stats: dict[str, Any]
    learning_outcomes: list[str]
    skills: list[str]
    seed_source: str
    learning_context_source: str
    accepted: bool = True
    warnings: list[str] = field(default_factory=list)
    rubric_regression: dict[str, Any] | None = None
    validation_report: dict[str, Any] | None = None


class RegenerationValidationError(Exception):
    """Raised when regenerated content fails deterministic validation."""

    def __init__(self, detail: str, *, status_code: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def calculate_text_stats(text: str) -> dict[str, int]:
    """Compute deterministic text statistics for reporting."""
    chars = len(text)
    words = len(text.split())
    tokens = count_tokens(text)
    return {
        "chars": chars,
        "words": words,
        "tokens": tokens,
    }


def _unique_non_empty(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if isinstance(item, str) and item.strip()))


def _learning_context_from_cache(cached_result: dict[str, Any] | None) -> tuple[list[str], list[str], str | None]:
    """Read already structured LO/skills from cache without LLM extraction."""
    if not cached_result:
        return [], [], None

    candidates: list[tuple[str, Any]] = [
        ("cache.project_seed_payload", cached_result.get("project_seed_payload")),
        ("cache.project_seed", cached_result.get("project_seed")),
        ("cache.regenerated", cached_result.get("regenerated")),
        ("cache.report_json", cached_result.get("report_json")),
        ("cache.root", cached_result),
    ]

    result = cached_result.get("result")
    spec = getattr(result, "spec", None) if result is not None else None
    if spec is not None:
        candidates.append(("cache.result.spec", spec.model_dump() if hasattr(spec, "model_dump") else spec))

    for source, candidate in candidates:
        if isinstance(candidate, dict):
            learning_outcomes = _unique_non_empty(
                ProjectSeedProvider._as_list(candidate.get("learning_outcomes"))
            )
            skills = _unique_non_empty(ProjectSeedProvider._as_list(candidate.get("skills")))
        else:
            learning_outcomes = _unique_non_empty(
                ProjectSeedProvider._as_list(getattr(candidate, "learning_outcomes", None))
            )
            skills = _unique_non_empty(
                ProjectSeedProvider._as_list(getattr(candidate, "skills", None))
            )
        if learning_outcomes or skills:
            return learning_outcomes, skills, source

    return [], [], None


def _learning_context_from_seed_and_cache(
    seed: Any,
    cached_result: dict[str, Any] | None,
) -> tuple[list[str], list[str], str]:
    """Prefer current project metadata; use cache only to fill missing structured fields."""
    learning_outcomes = _unique_non_empty(list(getattr(seed, "learning_outcomes", []) or []))
    skills = _unique_non_empty(list(getattr(seed, "skills", []) or []))
    source = "seed" if learning_outcomes or skills else "unavailable"

    cached_learning_outcomes, cached_skills, cached_source = _learning_context_from_cache(cached_result)
    if not learning_outcomes and cached_learning_outcomes:
        learning_outcomes = cached_learning_outcomes
        source = cached_source or source
    if not skills and cached_skills:
        skills = cached_skills
        source = cached_source or source

    return learning_outcomes, skills, source


def _rubric_failed_count(rubric_json: dict[str, Any] | None) -> int | None:
    """Count failed rubric items from the serialized rubric contract."""
    if not isinstance(rubric_json, dict):
        return None
    items = rubric_json.get("items")
    if not isinstance(items, list):
        return None

    failed = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        if score < 1:
            failed += 1
    return failed


def _failed_rubric_ids(rubric_json: dict[str, Any] | None) -> set[str]:
    """Return IDs of failed criteria for regression diagnostics."""
    if not isinstance(rubric_json, dict) or not isinstance(rubric_json.get("items"), list):
        return set()
    failed: set[str] = set()
    for item in rubric_json["items"]:
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        if score < 1 and item.get("id"):
            failed.add(str(item["id"]))
    return failed


def _failed_rubric_items(rubric_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return compact failed criteria with labels for user-facing warnings."""
    if not isinstance(rubric_json, dict) or not isinstance(rubric_json.get("items"), list):
        return []
    failed: list[dict[str, Any]] = []
    for item in rubric_json["items"]:
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        if score >= 1:
            continue
        item_id = str(item.get("id") or "").strip()
        failed.append(
            {
                "id": item_id,
                "title": str(item.get("title") or item.get("name") or item_id or "Критерий").strip(),
                "score": score,
                "evidence": str(
                    item.get("evidence")
                    or item.get("message")
                    or item.get("comment")
                    or item.get("description")
                    or ""
                ).strip(),
            }
        )
    return failed


def _extract_cached_rubric(cached_result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Prefer the rubric that matches the latest visible README state."""
    if not isinstance(cached_result, dict):
        return None
    candidates = [
        (cached_result.get("regenerated") or {}).get("rubric"),
        cached_result.get("rubric"),
        (cached_result.get("report_json") or {}).get("rubric"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("items"), list):
            return candidate
    return None


def _rubric_regression_details(
    baseline_rubric: dict[str, Any] | None,
    regenerated_rubric: dict[str, Any],
    *,
    change_intent: RegenerationChangeIntent = "local_section_edit",
) -> dict[str, Any] | None:
    """Describe rubric regression without forcing HTTP error handling."""
    baseline_failed = _rubric_failed_count(baseline_rubric)
    regenerated_failed = _rubric_failed_count(regenerated_rubric)
    if baseline_failed is None or regenerated_failed is None:
        return None
    if regenerated_failed <= baseline_failed:
        return None

    baseline_failed_ids = _failed_rubric_ids(baseline_rubric)
    failed_items = _failed_rubric_items(regenerated_rubric)
    new_failed = [item for item in failed_items if item["id"] and item["id"] not in baseline_failed_ids]
    new_failed_text = ", ".join(
        f"{item['id']} {item['title']}".strip()
        for item in new_failed[:8]
    )
    if change_intent == "structural_document_edit":
        message = (
            "Структурная перегенерация не применена: результат ухудшил rubric "
            f"(было непройдено {baseline_failed}, стало {regenerated_failed}). "
            "Производные изменения оглавления и outline разрешены, но обязательные главы 1-3 "
            "и их базовые критерии должны сохраниться."
        )
    else:
        message = (
            "Перегенерация не применена: результат ухудшил rubric "
            f"(было непройдено {baseline_failed}, стало {regenerated_failed})."
        )
    if new_failed_text:
        message += f" Новые непройденные критерии: {new_failed_text}."
    message += " Уточните запрос правки и запустите перегенерацию еще раз."
    return {
        "baseline_failed": baseline_failed,
        "regenerated_failed": regenerated_failed,
        "change_intent": change_intent,
        "new_failed": new_failed,
        "failed": failed_items,
        "message": message,
    }


def _raise_if_rubric_regressed(
    baseline_rubric: dict[str, Any] | None,
    regenerated_rubric: dict[str, Any],
    *,
    change_intent: RegenerationChangeIntent = "local_section_edit",
) -> None:
    """Reject regenerated content that increases the number of failed criteria."""
    details = _rubric_regression_details(baseline_rubric, regenerated_rubric, change_intent=change_intent)
    if details is None:
        return
    raise RegenerationValidationError(
        str(details["message"]),
        status_code=422,
    )


def _refresh_toc_for_structural_regeneration(markdown: str, language: str) -> tuple[str, bool]:
    """Rebuild the TOC after document-level structure changes."""
    document = ReadmeDocument.from_markdown(markdown)
    renderer = TOCRenderer()
    toc = renderer.build_document(document, language=language)
    updated = renderer.inject_document(document, toc.toc_md, language=language).to_markdown()
    return updated, updated.strip() != (markdown or "").strip()


class RegenerationService:
    """Coordinate regeneration agents, validation, scoring and persistence."""

    def __init__(
        self,
        *,
        llm_factory: Callable[[], Any] | None = None,
        cache_getter: Callable[[str], dict[str, Any] | None] = get_result,
        db_updater: Callable[..., Any] = update_regeneration_result,
        log_writer: Callable[..., Any] = write_log_async,
    ) -> None:
        self._llm_factory = llm_factory or (
            lambda: create_llm_client(default_role="repair", enable_cache=True, enable_batching=True)
        )
        self._cache_getter = cache_getter
        self._db_updater = db_updater
        self._log_writer = log_writer

    async def regenerate(self, command: RegenerationCommand) -> RegenerationResultView:
        """Run the full regeneration application workflow."""
        await self._log_start(command)

        llm_client = self._llm_factory()
        configure_context = getattr(llm_client, "configure_run_context", None)
        if callable(configure_context):
            configure_context(user_id=command.user_id, run_id=command.request_id)
        regen_agent = RegenerationAgent(llm_client)

        original_cached = self._load_original_cached(command.original_request_id)
        seed_result = ProjectSeedProvider.build_for_regeneration(
            language=command.language,
            project_seed=command.project_seed,
            curriculum_project=command.curriculum_project,
            cached_result=original_cached,
        )
        seed = seed_result.seed
        if seed_result.warnings:
            logger.warning("⚠️ Предупреждения восстановления ProjectSeed: %s", "; ".join(seed_result.warnings[:3]))
        logger.info(
            "📌 ProjectSeed для перегенерации: source=%s, title='%s', LO=%s, skills=%s",
            seed_result.source,
            seed.title_seed,
            len(seed.learning_outcomes),
            len(seed.skills),
        )
        pipeline_input = build_regeneration_pipeline_input(
            original_md=command.original_md,
            comments=command.comments,
            language=command.language,
        )
        logger.info(
            "📐 Regeneration intent: %s, selected_sections=%s",
            pipeline_input.change_intent,
            len(pipeline_input.selected_sections),
        )

        result = await asyncio.to_thread(
            regen_agent.regenerate,
            original_md=command.original_md,
            comments=command.comments,
            language=command.language,
        )
        regenerated_md = result.regenerated_md
        changes = list(result.changes)
        validation_report = result.validation_report or {}

        await self._validate_latex(command, regenerated_md)
        regenerated_md = await self._apply_quality_checks(
            llm_client=llm_client,
            markdown=regenerated_md,
            seed=seed,
            language=command.language,
            allow_llm_rewrites=not pipeline_input.selected_sections and not pipeline_input.is_structural,
        )
        deduped_md = remove_adjacent_rewritten_paragraph_duplicates(command.original_md, regenerated_md)
        if deduped_md != regenerated_md:
            logger.info("✅ Удалены дублирующие old/new абзацы после quality checks")
            changes.append("Удалены дублирующие старые абзацы после финальных проверок")
            regenerated_md = deduped_md
        if pipeline_input.is_structural:
            refreshed_md, toc_changed = _refresh_toc_for_structural_regeneration(regenerated_md, command.language)
            if toc_changed:
                logger.info("✅ Оглавление пересобрано после структурной перегенерации")
                changes.append("Оглавление обновлено по фактической структуре README")
                validation_report = dict(validation_report)
                validation_report["toc_refreshed"] = True
                regenerated_md = refreshed_md

        learning_outcomes, skills, learning_context_source = await self._resolve_learning_context(
            seed=seed,
            cached_result=original_cached,
        )

        rubric_json = await self._score_rubric(
            llm_client=llm_client,
            markdown=regenerated_md,
            language=command.language,
            learning_outcomes=learning_outcomes,
        )
        baseline_rubric = await self._baseline_rubric_for_regression_guard(
            llm_client=llm_client,
            cached_result=original_cached,
            original_md=command.original_md,
            language=command.language,
            learning_outcomes=learning_outcomes,
        )
        text_stats = calculate_text_stats(regenerated_md)
        rubric_regression = _rubric_regression_details(
            baseline_rubric,
            rubric_json,
            change_intent=pipeline_input.change_intent,
        )
        if rubric_regression is not None:
            warning = str(rubric_regression["message"])
            logger.warning("⚠️ %s", warning)
            await self._log_writer(
                request_id=command.request_id,
                level="WARNING",
                message=warning,
                user_id=command.user_id,
                phase="regeneration_rubric_warning",
                metadata={"rubric_regression": rubric_regression},
            )
            return RegenerationResultView(
                request_id=command.request_id,
                regenerated_md=regenerated_md,
                changes=changes,
                rubric=convert_numpy_types(rubric_json),
                text_stats=text_stats,
                learning_outcomes=learning_outcomes,
                skills=skills,
                seed_source=seed_result.source,
                learning_context_source=learning_context_source,
                accepted=False,
                warnings=[warning],
                rubric_regression=convert_numpy_types(rubric_regression),
                validation_report=convert_numpy_types(validation_report),
            )

        await self._persist_regeneration(
            command=command,
            cached_result=original_cached,
            regenerated_md=regenerated_md,
            changes=changes,
            rubric_json=rubric_json,
            text_stats=text_stats,
            learning_outcomes=learning_outcomes,
            skills=skills,
            seed_source=seed_result.source,
            learning_context_source=learning_context_source,
            accepted=True,
            warnings=[],
            rubric_regression=None,
            validation_report=validation_report,
        )
        await self._log_success(
            command=command,
            regenerated_md=regenerated_md,
            changes_count=len(changes),
            learning_outcomes=learning_outcomes,
            skills=skills,
            seed_source=seed_result.source,
            learning_context_source=learning_context_source,
            validation_report=convert_numpy_types(validation_report),
        )

        return RegenerationResultView(
            request_id=command.request_id,
            regenerated_md=regenerated_md,
            changes=changes,
            rubric=convert_numpy_types(rubric_json),
            text_stats=text_stats,
            learning_outcomes=learning_outcomes,
            skills=skills,
            seed_source=seed_result.source,
            learning_context_source=learning_context_source,
        )

    def _load_original_cached(self, original_request_id: str | None) -> dict[str, Any] | None:
        if not original_request_id:
            return None
        return self._cache_getter(original_request_id)

    async def _validate_latex(self, command: RegenerationCommand, markdown: str) -> None:
        latex_issues = collect_latex_issues(markdown)
        if not latex_issues:
            return

        issue_text = "; ".join(latex_issues[:3])
        agent_hint = build_latex_agent_hint(latex_issues)
        logger.error("❌ Ошибка LaTeX при перегенерации: %s", issue_text)
        await self._log_writer(
            request_id=command.request_id,
            level="ERROR",
            message="Перегенерация остановлена: найдены ошибки LaTeX",
            user_id=command.user_id,
            phase="regeneration_validation",
            metadata={"issues": latex_issues[:5], "agent_hint": agent_hint},
        )
        raise RegenerationValidationError(
            f"Найдены проблемы с LaTeX формулами: {issue_text}. "
            f"Подсказка для агента: {agent_hint}",
            status_code=422,
        )

    async def _apply_quality_checks(
        self,
        *,
        llm_client: Any,
        markdown: str,
        seed: Any,
        language: str,
        allow_llm_rewrites: bool = True,
    ) -> str:
        logger.info("🔄 Применение проверок качества к перегенерированному контенту")

        if allow_llm_rewrites:
            try:
                content_editor = ContentEditorAgent(llm_client)
                markdown = await asyncio.to_thread(
                    content_editor.ensure_global_coherence,
                    markdown,
                    seed,
                )
                logger.info("✅ ContentEditor.ensure_global_coherence применён")
            except Exception as exc:
                logger.warning("⚠️ Ошибка при применении ContentEditor: %s", exc)

            try:
                style_guard = StyleGuardRepair()
                issues_style = await asyncio.to_thread(style_guard.lint, markdown, language)
                if issues_style:
                    logger.info("🔄 Найдено %s проблем стиля, применяем исправления", len(issues_style))
                    markdown = await asyncio.to_thread(style_guard.rewrite, markdown, language)
                    logger.info("✅ StyleGuardRepair применён")
                else:
                    logger.info("✅ Проблем стиля не найдено")
            except Exception as exc:
                logger.warning("⚠️ Ошибка при применении StyleGuard: %s", exc)

        else:
            logger.info("🔒 Перегенерация с ограниченным scope: глобальные LLM quality rewrites пропущены")
        return strip_protected_block_instruction_leaks(normalize_markdown_display_blocks(markdown))

    async def _resolve_learning_context(
        self,
        *,
        seed: Any,
        cached_result: dict[str, Any] | None,
    ) -> tuple[list[str], list[str], str]:
        learning_outcomes, skills, learning_context_source = _learning_context_from_seed_and_cache(seed, cached_result)
        logger.info(
            "📚 Контекст ЗУНов для перегенерации: source=%s, LO=%s, skills=%s",
            learning_context_source,
            len(learning_outcomes),
            len(skills),
        )

        if not learning_outcomes and not skills:
            logger.warning(
                "⚠️ Структурированный контекст ЗУНов для перегенерации отсутствует; "
                "рубрика будет рассчитана без LO/skills."
            )
        return learning_outcomes, skills, learning_context_source

    async def _baseline_rubric_for_regression_guard(
        self,
        *,
        llm_client: Any,
        cached_result: dict[str, Any] | None,
        original_md: str,
        language: str,
        learning_outcomes: list[str],
    ) -> dict[str, Any] | None:
        """Resolve the rubric baseline used to prevent worse regenerated output."""
        cached_rubric = _extract_cached_rubric(cached_result)
        if cached_rubric is not None:
            return cached_rubric

        if not (original_md or "").strip():
            return None

        try:
            logger.info("🔄 Baseline rubric отсутствует в кэше; пересчитываем по исходному README")
            return await self._score_rubric(
                llm_client=llm_client,
                markdown=original_md,
                language=language,
                learning_outcomes=learning_outcomes,
            )
        except Exception as exc:
            logger.warning("⚠️ Не удалось рассчитать baseline rubric для regression guard: %s", exc)
            return None

    async def _score_rubric(
        self,
        *,
        llm_client: Any,
        markdown: str,
        language: str,
        learning_outcomes: list[str],
    ) -> dict[str, Any]:
        rubric_scorer = RubricScorer(language=language, llm_client=llm_client)
        rubric_result = await asyncio.to_thread(
            rubric_scorer.score,
            markdown,
            learning_outcomes=learning_outcomes if learning_outcomes else None,
        )
        return criteria_to_json(rubric_result)

    async def _persist_regeneration(
        self,
        *,
        command: RegenerationCommand,
        cached_result: dict[str, Any] | None,
        regenerated_md: str,
        changes: list[str],
        rubric_json: dict[str, Any],
        text_stats: dict[str, Any],
        learning_outcomes: list[str],
        skills: list[str],
        seed_source: str,
        learning_context_source: str,
        accepted: bool = True,
        warnings: list[str] | None = None,
        rubric_regression: dict[str, Any] | None = None,
        validation_report: dict[str, Any] | None = None,
    ) -> None:
        if not command.original_request_id:
            return

        if cached_result is None:
            cached_result = self._cache_getter(command.original_request_id)
        if cached_result:
            cached_result["regenerated"] = {
                "regenerated_md": regenerated_md,
                "changes": changes,
                "rubric": rubric_json,
                "text_stats": text_stats,
                "learning_outcomes": learning_outcomes,
                "skills": skills,
                "seed_source": seed_source,
                "learning_context_source": learning_context_source,
                "accepted": accepted,
                "warnings": warnings or [],
                "rubric_regression": rubric_regression,
                "validation_report": validation_report or {},
                "comments": command.comments,
                "original_md": command.original_md,
            }

        try:
            await asyncio.to_thread(
                self._db_updater,
                command.original_request_id,
                regenerated_md,
                rubric_json,
                command.comments,
                changes,
                command.original_md,
            )
        except Exception as db_err:
            logger.warning("⚠️ Не удалось сохранить данные перегенерации в БД: %s", db_err)

    async def _log_start(self, command: RegenerationCommand) -> None:
        logger.info("🔄 Начало перегенерации контента для пользователя %s", command.user_id)
        await self._log_writer(
            request_id=command.request_id,
            level="INFO",
            message="Начало перегенерации контента",
            user_id=command.user_id,
            phase="regeneration",
            metadata={
                "comments_length": len(command.comments or ""),
                "original_md_length": len(command.original_md or ""),
            },
        )

    async def _log_success(
        self,
        *,
        command: RegenerationCommand,
        regenerated_md: str,
        changes_count: int,
        learning_outcomes: list[str],
        skills: list[str],
        seed_source: str,
        learning_context_source: str,
        validation_report: dict[str, Any] | None = None,
    ) -> None:
        logger.info(
            "✅ Перегенерация завершена успешно: markdown=%s символов, изменений=%s, LO=%s, Skills=%s",
            len(regenerated_md),
            changes_count,
            len(learning_outcomes),
            len(skills),
        )
        await self._log_writer(
            request_id=command.request_id,
            level="INFO",
            message="Перегенерация контента завершена успешно",
            user_id=command.user_id,
            phase="regeneration_complete",
            metadata={
                "markdown_length": len(regenerated_md),
                "changes_count": changes_count,
                "learning_outcomes_count": len(learning_outcomes),
                "skills_count": len(skills),
                "seed_source": seed_source,
                "learning_context_source": learning_context_source,
                "validation_report": validation_report or {},
            },
        )
