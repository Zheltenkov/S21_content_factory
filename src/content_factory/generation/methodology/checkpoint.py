"""Explicit human approval checkpoints for generated artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..utils.markdown_display_normalizer import normalize_markdown_display_blocks
from .decision import MethodologyGateInterrupt


class HumanApprovalCheckpoint(BaseModel):
    """A paused artifact that must be approved before the next flow node."""

    id: str
    stage: str
    node_id: str
    title: str
    summary: str
    resume_from_node: str
    allowed_targets: list[str] = Field(default_factory=list)
    artifact: dict[str, Any] = Field(default_factory=dict)
    artifact_hash: str = ""


class RequirementMatrixItem(BaseModel):
    """Strict UI contract for methodology requirement matrix rows."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    status: Literal["pass", "fail"]
    passed: bool
    evidence: str = Field(min_length=1, max_length=500)


class HumanApprovalCheckpointPolicy:
    """Deterministic policy for milestone approvals inside AgentFlow."""

    DEFAULT_CHECKPOINTS = {
        "task_planning",
        "title",
        "structure",
        "theory",
        "practice",
        "quality",
        "evaluation",
        "translation",
    }
    CHECKPOINT_NODE_MAP = {
        "context": "context",
        "task_planning": "task_planning",
        "title_annotation": "title",
        "skeleton": "structure",
        "theory": "theory",
        "practice": "practice",
        "global_quality": "quality",
        "evaluation": "evaluation",
        "translate": "translation",
        "finalize": "finalize",
    }

    def __init__(self, checkpoints: set[str] | None = None) -> None:
        self.checkpoints = checkpoints or set()

    @classmethod
    def from_env(cls, *, enabled_by_default: bool = False) -> "HumanApprovalCheckpointPolicy":
        raw_value = os.getenv("METHODOLOGY_HUMAN_CHECKPOINTS")
        if raw_value is None:
            raw_value = "all" if enabled_by_default else ""
        normalized = raw_value.strip().lower()
        if normalized in {"", "0", "false", "off", "none", "disabled"}:
            return cls(set())
        if normalized in {"1", "true", "on", "enabled", "all"}:
            return cls(set(cls.DEFAULT_CHECKPOINTS))
        checkpoints = {part.strip() for part in normalized.split(",") if part.strip()}
        if "all" in checkpoints:
            checkpoints.remove("all")
            checkpoints.update(cls.DEFAULT_CHECKPOINTS)
        if "annotation" in checkpoints:
            checkpoints.remove("annotation")
            checkpoints.add("title")
        return cls(checkpoints)

    def maybe_raise(self, node_id: str, context: dict[str, Any]) -> None:
        """Raise a controlled pause when the completed node produced a gated artifact."""
        checkpoint_id = self.CHECKPOINT_NODE_MAP.get(node_id)
        if not checkpoint_id or checkpoint_id not in self.checkpoints:
            return
        checkpoint = self._build_checkpoint(checkpoint_id, context)
        if checkpoint is None:
            return
        checkpoint.artifact_hash = _checkpoint_artifact_hash(checkpoint)
        checkpoint_payload = checkpoint.model_dump(mode="json")
        if _checkpoint_already_approved(context, checkpoint_payload):
            context["last_skipped_human_approval_checkpoint"] = checkpoint_payload
            return
        context["human_approval_checkpoint"] = checkpoint_payload
        context.setdefault("human_approval_checkpoints", []).append(checkpoint_payload)
        raise MethodologyGateInterrupt(
            checkpoint.summary,
            context={
                "phase": checkpoint.stage,
                "error_type": "HumanApprovalCheckpoint",
                "checkpoint": checkpoint_payload,
            },
        )

    def _build_checkpoint(
        self,
        checkpoint_id: str,
        context: dict[str, Any],
    ) -> HumanApprovalCheckpoint | None:
        builders = {
            "context": self._context_checkpoint,
            "task_planning": self._task_planning_checkpoint,
            "title": self._title_checkpoint,
            "structure": self._structure_checkpoint,
            "annotation": self._annotation_checkpoint,
            "theory": self._theory_checkpoint,
            "practice": self._practice_checkpoint,
            "quality": self._quality_checkpoint,
            "evaluation": self._evaluation_checkpoint,
            "translation": self._translation_checkpoint,
            "finalize": self._finalize_checkpoint,
        }
        builder = builders.get(checkpoint_id)
        if builder is None:
            return None
        return builder(context)

    @staticmethod
    def _context_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        seed = context.get("seed")
        context_meta = context.get("context_meta")
        context_analysis = context.get("context_analysis")
        context_bundle = context.get("context_bundle")
        artifact = {
            "title": _seed_title(context),
            "summary": "Генератор разобрал входные данные и подготовил контекст, который будет использоваться на следующих этапах.",
            "context_review": _context_review(seed, context_meta, context_analysis, context_bundle),
            "learning_outcomes": _compact_list(_get_value(seed, "learning_outcomes"), limit=10),
            "skills": _compact_list(_get_value(seed, "skills"), limit=10),
            "similar_projects": _similar_project_summaries(
                context.get("similar_projects") or _get_value(context_meta, "similar_projects"),
                limit=5,
            ),
            "warnings_count": len(context.get("warnings") or []),
        }
        return HumanApprovalCheckpoint(
            id="context",
            stage="context",
            node_id="context",
            title="Проверка контекста",
            summary="Контекст проекта готов. Подтвердите входные данные и curriculum-привязку перед планированием задач.",
            resume_from_node="task_planning",
            allowed_targets=["context", "seed", "curriculum_context", "storytelling"],
            artifact=artifact,
        )

    @staticmethod
    def _task_planning_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        seed = context.get("seed")
        context_meta = context.get("context_meta")
        context_analysis = context.get("context_analysis")
        context_bundle = context.get("context_bundle")
        evidence_specs = context.get("evidence_specs") or []
        artifact = {
            "title": _seed_title(context),
            "summary": "Генератор разобрал входные данные и подготовил план практики, сторителлинг и цепочку артефактов.",
            "context_review": _context_review(seed, context_meta, context_analysis, context_bundle),
            "planning_review": _planning_review(
                context.get("task_plan"),
                context.get("practice_plan_contract"),
                context.get("artifact_chain_plan"),
                evidence_specs,
                context.get("story_map_contract"),
            ),
            "learning_outcomes": _compact_list(_get_value(seed, "learning_outcomes"), limit=10),
            "skills": _compact_list(_get_value(seed, "skills"), limit=10),
            "similar_projects": _similar_project_summaries(
                context.get("similar_projects") or _get_value(context_meta, "similar_projects"),
                limit=5,
            ),
            "warnings_count": len(context.get("warnings") or []),
        }
        return HumanApprovalCheckpoint(
            id="task_planning",
            stage="task_planning",
            node_id="task_planning",
            title="Проверка замысла и плана",
            summary="Контекст и план задач готовы. Подтвердите интерпретацию проекта перед генерацией названия и README.",
            resume_from_node="title_annotation",
            allowed_targets=[
                "context",
                "seed",
                "curriculum_context",
                "storytelling",
                "task_planning",
                "task_plan",
                "practice_plan",
                "artifact_chain",
            ],
            artifact=artifact,
        )

    @staticmethod
    def _title_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        annotation_text = _annotation_text(context.get("annotation"))
        artifact = {
            "title": str(context.get("title") or ""),
            "annotation": annotation_text,
        }
        return HumanApprovalCheckpoint(
            id="title",
            stage="title",
            node_id="title_annotation",
            title="Проверка названия проекта",
            summary="Название и аннотация готовы. Подтвердите их перед сборкой структуры README.",
            resume_from_node="skeleton",
            allowed_targets=["title", "annotation"],
            artifact=artifact,
        )

    @staticmethod
    def _structure_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        markdown = normalize_markdown_display_blocks(str(context.get("markdown") or ""))
        artifact = {
            "title": str(context.get("title") or ""),
            "annotation": _annotation_text(context.get("annotation")),
            "summary": "Черновик структуры README готов: проверьте состав глав, частей и практических блоков.",
            "structure_outline": _markdown_outline(markdown),
            "requirements_matrix": _checkpoint_requirement_matrix(context, markdown, stage="structure"),
            "markdown_excerpt": markdown,
            "markdown_sections": _markdown_subsections(markdown, min_level=2),
        }
        return HumanApprovalCheckpoint(
            id="structure",
            stage="skeleton",
            node_id="skeleton",
            title="Проверка структуры README",
            summary="Черновик структуры готов и требует подтверждения перед генерацией теории.",
            resume_from_node="theory",
            allowed_targets=["title", "annotation", "chapter_1", "chapter_2", "chapter_3", "skeleton"],
            artifact=artifact,
        )

    @staticmethod
    def _annotation_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        artifact = {
            "title": str(context.get("title") or ""),
            "annotation": _annotation_text(context.get("annotation")),
        }
        return HumanApprovalCheckpoint(
            id="annotation",
            stage="annotation",
            node_id="skeleton",
            title="Проверка аннотации",
            summary="Аннотация готова и требует подтверждения методолога перед генерацией теории.",
            resume_from_node="theory",
            allowed_targets=["annotation"],
            artifact=artifact,
        )

    @staticmethod
    def _theory_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        theory_parts = [_part_summary(part) for part in context.get("theory_parts") or []]
        chapter_markdown = _markdown_section(str(context.get("markdown") or ""), "глава 2")
        if not chapter_markdown and theory_parts:
            chapter_markdown = _theory_parts_markdown(context.get("theory_parts") or [])
        chapter_markdown = normalize_markdown_display_blocks(chapter_markdown)
        artifact = {
            "title": str(context.get("title") or ""),
            "summary": f"Сгенерировано частей теории: {len(theory_parts)}",
            "theory_parts": theory_parts,
            "requirements_matrix": _checkpoint_requirement_matrix(
                context,
                str(context.get("markdown") or ""),
                stage="theory",
            ),
            "markdown_excerpt": chapter_markdown,
            "markdown_sections": _markdown_subsections(chapter_markdown),
        }
        return HumanApprovalCheckpoint(
            id="theory",
            stage="theory",
            node_id="theory",
            title="Проверка теории",
            summary="Глава 2 готова и требует подтверждения методолога перед генерацией практики.",
            resume_from_node="practice",
            allowed_targets=["chapter_2", "theory"],
            artifact=artifact,
        )

    @staticmethod
    def _practice_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        practice_tasks = [_task_summary(task) for task in context.get("practice_tasks") or []]
        bonus_tasks = [_task_summary(task, bonus=True) for task in context.get("bonus_tasks") or []]
        all_practice_tasks = practice_tasks + bonus_tasks
        markdown = str(context.get("markdown") or "")
        chapter_markdown = normalize_markdown_display_blocks(
            _markdown_section(markdown, "глава 3")
        )
        bonus_markdown = ""
        if re.search(r"^#{1,6}\s+Бонус\b", markdown, flags=re.IGNORECASE | re.MULTILINE):
            bonus_markdown = normalize_markdown_display_blocks(_markdown_section(markdown, "бонус"))
        if bonus_markdown and bonus_markdown not in chapter_markdown:
            chapter_markdown = "\n\n".join(part for part in [chapter_markdown, bonus_markdown] if part).strip()
        dataset_files = [
            {
                "path": str(item.get("path") or ""),
                "bytes": _data_size(item.get("data")),
            }
            for item in context.get("dataset_files") or []
            if isinstance(item, dict)
        ]
        artifact = {
            "title": str(context.get("title") or ""),
            "summary": f"Сгенерировано задач: {len(all_practice_tasks)}, materials-файлов: {len(dataset_files)}",
            "practice_tasks": all_practice_tasks,
            "dataset_files": dataset_files,
            "requirements_matrix": build_requirement_matrix(context, str(context.get("markdown") or "")),
            "markdown_excerpt": chapter_markdown,
            "markdown_sections": _markdown_subsections(chapter_markdown),
        }
        return HumanApprovalCheckpoint(
            id="practice",
            stage="practice",
            node_id="practice",
            title="Проверка практики и материалов",
            summary="Глава 3 и materials готовы и требуют подтверждения перед редакторской сборкой.",
            resume_from_node="global_quality",
            allowed_targets=["chapter_3", "practice", "dataset", "materials"],
            artifact=artifact,
        )

    @staticmethod
    def _quality_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        markdown = normalize_markdown_display_blocks(str(context.get("markdown") or ""))
        artifact = {
            "title": str(context.get("title") or ""),
            "summary": "README прошел глобальную редакторскую сборку.",
            "markdown_chars": len(markdown),
            "warnings_count": len(context.get("warnings") or []),
            "requirements_matrix": build_requirement_matrix(context, markdown),
            # Финальный checkpoint проверяет весь README, поэтому preview не должен
            # терять главу 3 после редакторской сборки.
            "markdown_excerpt": markdown,
            "markdown_sections": _markdown_subsections(markdown, min_level=2),
        }
        return HumanApprovalCheckpoint(
            id="quality",
            stage="final",
            node_id="global_quality",
            title="Проверка редакторской сборки",
            summary="Глобальная связность и редактура применены. Подтвердите перед финальной оценкой.",
            resume_from_node="evaluation",
            allowed_targets=["annotation", "chapter_1", "chapter_2", "chapter_3", "final"],
            artifact=artifact,
        )

    @staticmethod
    def _evaluation_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        rubric = context.get("rubric_json") or {}
        if not isinstance(rubric, dict):
            rubric = {}
        items = rubric.get("items") or []
        failed_count = 0
        if isinstance(items, list):
            failed_count = sum(1 for item in items if _rubric_item_failed(item))
        artifact = {
            "title": str(context.get("title") or ""),
            "summary": "Финальная оценка завершена. Подтвердите перед сборкой результата.",
            "rubric_score": rubric.get("score") or rubric.get("total_score") or rubric.get("percentage"),
            "rubric_failed_count": failed_count,
            "issues_count": len(context.get("issues") or []),
            "rubric": rubric,
        }
        return HumanApprovalCheckpoint(
            id="evaluation",
            stage="final",
            node_id="evaluation",
            title="Проверка финальной оценки",
            summary="Валидаторы завершили проверку. Методолог может подтвердить export или запросить точечные правки.",
            resume_from_node="finalize",
            allowed_targets=["annotation", "chapter_1", "chapter_2", "chapter_3", "final"],
            artifact=artifact,
        )

    @staticmethod
    def _translation_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        markdown = normalize_markdown_display_blocks(
            str(context.get("translated_markdown") or context.get("markdown") or "")
        )
        target_language = str(context.get("target_language") or _get_value(context.get("seed"), "language") or "").strip()
        artifact = {
            "title": str(context.get("title") or _seed_title(context)),
            "summary": f"Перевод готов: целевой язык {target_language or '-'}",
            "target_language": target_language,
            "markdown_chars": len(markdown),
            "requirements_matrix": build_requirement_matrix(context, markdown),
            "markdown_excerpt": markdown,
            "markdown_sections": _markdown_subsections(markdown, min_level=2),
        }
        return HumanApprovalCheckpoint(
            id="translation",
            stage="translation",
            node_id="translate",
            title="Проверка перевода README",
            summary="Перевод README готов. Подтвердите его перед финальной сборкой результата.",
            resume_from_node="finalize",
            allowed_targets=["translation", "annotation", "chapter_1", "chapter_2", "chapter_3", "final"],
            artifact=artifact,
        )

    @staticmethod
    def _finalize_checkpoint(context: dict[str, Any]) -> HumanApprovalCheckpoint:
        markdown = normalize_markdown_display_blocks(
            str(context.get("translated_markdown") or context.get("markdown") or "")
        )
        artifact = {
            "title": str(context.get("title") or _seed_title(context)),
            "summary": "Финальная сборка результата готова.",
            "markdown_chars": len(markdown),
            "assets_count": _assets_count(context.get("assets")),
            "project_spec_summary": _project_spec_summary(context.get("project_spec") or _get_value(context.get("result"), "spec")),
            "requirements_matrix": build_requirement_matrix(context, markdown),
            "markdown_excerpt": markdown,
            "markdown_sections": _markdown_subsections(markdown, min_level=2),
        }
        return HumanApprovalCheckpoint(
            id="finalize",
            stage="final",
            node_id="finalize",
            title="Проверка финального результата",
            summary="Финальный результат собран. Подтвердите завершение генерации и сохранение артефактов.",
            resume_from_node="completed",
            allowed_targets=["final", "export", "materials"],
            artifact=artifact,
        )


def build_requirement_matrix(context: dict[str, Any], markdown: str | None = None) -> list[dict[str, Any]]:
    """Build deterministic source/didactics pass-fail matrix for review UI."""
    markdown = normalize_markdown_display_blocks(str(markdown if markdown is not None else context.get("markdown") or ""))
    chapter_2 = _markdown_section(markdown, "глава 2")
    chapter_3 = _markdown_section(markdown, "глава 3")
    final_section = _final_section(markdown)
    dataset_files = [item for item in context.get("dataset_files") or [] if isinstance(item, dict)]
    theory_headers = re.findall(r"^###\s+(.+?)\s*$", chapter_2, flags=re.M)
    task_blocks = _practice_task_blocks(chapter_3)

    has_h1 = bool(re.search(r"^#\s+.+$", markdown, flags=re.M))
    h1 = re.search(r"^#\s+(.+?)\s*$", markdown, flags=re.M)
    h1_words = len(h1.group(1).split()) if h1 else 0
    has_toc = bool(re.search(r"^##\s+(?:Содержание|Оглавление|Content|Мазмун)\s*$", markdown, flags=re.M))
    has_chapters = all(
        re.search(rf"^##\s+Глава\s+{chapter}\b", markdown, flags=re.M)
        for chapter in ("1", "2", "3")
    )
    has_final = bool(final_section.strip())

    canonical_theory = bool(theory_headers) and all(re.match(r"2\.\d+\.", header) for header in theory_headers)
    legacy_theory_count = len(re.findall(r"^###\s+Часть\s+\d+\.", chapter_2, flags=re.M))

    canonical_tasks = bool(task_blocks) and all(
        re.match(r"###\s+Задание\s+\d+\.", block.strip()) for block in task_blocks
    )
    task_template_ok = bool(task_blocks) and all(
        _has_markdown_label(block, "Что нужно сделать")
        and _has_markdown_label(block, "Что должно получиться")
        and _has_markdown_label(block, "Формат сдачи")
        and _has_markdown_label(block, "Переход к следующему заданию")
        for block in task_blocks
    )
    p2p_ok = bool(task_blocks) and all(_task_has_p2p_outcomes(block) for block in task_blocks)
    story_chain_ok = (
        context.get("story_map_contract") is not None
        or context.get("practice_plan_contract") is not None
        or "Переход к следующему заданию" in chapter_3
    )
    final_ok = has_final and not re.search(r"следующ(?:ий|ем)\s+проект", final_section, flags=re.I)

    return [
        _matrix_item(
            "structure",
            "Структура README",
            has_h1 and 1 <= h1_words <= 3 and has_toc and has_chapters and has_final,
            f"H1={h1_words} слов, TOC={has_toc}, главы 1-3={has_chapters}, финал={has_final}",
        ),
        _matrix_item(
            "theory",
            "Формат теории",
            canonical_theory and legacy_theory_count == 0,
            f"canonical 2.N={len(theory_headers)}, legacy Часть={legacy_theory_count}",
        ),
        _matrix_item(
            "practice_template",
            "Шаблон практики",
            canonical_tasks and task_template_ok,
            f"заданий={len(task_blocks)}, canonical={canonical_tasks}, pdf_blocks={task_template_ok}",
        ),
        _matrix_item(
            "p2p",
            "P2P-проверяемость",
            p2p_ok,
            "в каждом задании есть 2+ наблюдаемых результата и путь/артефакт",
        ),
        _matrix_item(
            "story_chain",
            "Единая цепочка",
            story_chain_ok,
            "есть story/practice contract или публичные переходы между заданиями",
        ),
        _matrix_item(
            "materials",
            "Сгенерированные данные",
            bool(dataset_files) or bool(context.get("evidence_specs")) or bool(context.get("practice_tasks")),
            f"materials={len(dataset_files)}, evidence_specs={len(context.get('evidence_specs') or [])}",
        ),
        _matrix_item(
            "final_closure",
            "Финальное завершение",
            final_ok,
            "финальный раздел завершает текущий проект без анонса следующего",
        ),
    ]


def _checkpoint_requirement_matrix(
    context: dict[str, Any],
    markdown: str | None,
    *,
    stage: str,
) -> list[dict[str, Any]]:
    matrix = build_requirement_matrix(context, markdown)
    if stage in {"structure", "theory"}:
        return [item for item in matrix if item.get("id") != "p2p"]
    return matrix


def _part_summary(part: Any) -> dict[str, Any]:
    title = _get_value(part, "title") or _get_value(part, "heading") or _get_value(part, "name")
    text = (
        _get_value(part, "text")
        or _get_value(part, "content")
        or _get_value(part, "body")
        or _get_value(part, "text_markdown")
        or ""
    )
    example = _get_value(part, "example") or ""
    combined_text = " ".join([str(text or ""), str(example or "")]).strip()
    return {
        "title": str(title or "Часть теории"),
        "words": len(combined_text.split()),
    }


def _task_summary(task: Any, *, bonus: bool = False) -> dict[str, Any]:
    title = _get_value(task, "title") or _get_value(task, "name") or _get_value(task, "task_title")
    objective = _get_value(task, "objective") or _get_value(task, "goal") or _get_value(task, "description")
    normalized_title = str(title or "Практическая задача")
    if bonus and "бонус" not in normalized_title.casefold():
        normalized_title = f"Бонусное задание: {normalized_title}"
    return {
        "title": normalized_title,
        "objective": _truncate_text(str(objective or ""), 220),
    }


def _seed_title(context: dict[str, Any]) -> str:
    seed = context.get("seed")
    return str(
        context.get("title")
        or _get_value(seed, "platform_name")
        or _get_value(seed, "title_seed")
        or _get_value(seed, "project_description")
        or ""
    ).strip()


def _context_review(seed: Any, context_meta: Any, context_analysis: Any, context_bundle: Any) -> dict[str, Any]:
    title = str(_get_value(seed, "title_seed") or _get_value(seed, "platform_name") or "").strip()
    project_description = _truncate_text(str(_get_value(seed, "project_description") or ""), 420)
    storytelling = _truncate_text(str(_get_value(seed, "sjm") or ""), 420)
    thematic_block = str(_get_value(seed, "thematic_block") or _get_value(context_meta, "thematic_block") or "").strip()
    direction = str(_get_value(seed, "direction") or _get_value(context_meta, "track") or "").strip()
    audience_level = str(_get_value(seed, "audience_level") or "").strip()
    context_text = _first_non_empty(
        _get_value(context_analysis, "context_summary"),
        _get_value(context_meta, "context_summary"),
        _get_value(context_bundle, "context_summary"),
    )
    narrative_anchor = _first_non_empty(
        _get_value(context_analysis, "narrative_anchor"),
        _get_value(context_meta, "narrative_anchor"),
        _get_value(context_bundle, "narrative_anchor"),
    )
    facts = [
        {"label": "Трек", "value": direction},
        {"label": "Блок программы", "value": thematic_block},
        {"label": "Формат проекта", "value": _project_type_label(str(_get_value(seed, "project_type") or ""))},
        {"label": "Уровень аудитории", "value": _audience_level_label(audience_level)},
        {"label": "Источник контекста", "value": str(_get_value(context_bundle, "context_source") or "").strip()},
    ]
    facts = [item for item in facts if item["value"]]
    will_use = [
        "тему и описание проекта как основной фокус README",
        "сторителлинг как связку между задачами, теорией и практикой",
        "образовательные результаты и навыки как ограничения для содержания",
        "контекст программы, чтобы проект не выпадал из соседних проектов трека",
    ]
    can_change = [
        "уточнить тему, описание проекта или акцент учебного кейса",
        "переформулировать сторителлинг и роль студента",
        "добавить или убрать образовательные результаты и навыки",
        "указать, что контекст программы интерпретирован неверно",
    ]
    return {
        "project_title": title,
        "project_description": project_description,
        "storytelling": storytelling,
        "program_context": _truncate_text(str(context_text or ""), 420),
        "narrative_anchor": _truncate_text(str(narrative_anchor or ""), 260),
        "facts": facts,
        "will_use": will_use,
        "can_change": can_change,
    }


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _project_type_label(value: str) -> str:
    labels = {
        "individual": "индивидуальный",
        "group": "групповой",
        "team": "командный",
    }
    return labels.get(value.lower(), value)


def _audience_level_label(value: str) -> str:
    labels = {
        "base": "базовый",
        "basic": "базовый",
        "middle": "средний",
        "advanced": "продвинутый",
    }
    return labels.get(value.lower(), value)


def _similar_project_summaries(value: Any, *, limit: int = 5) -> list[str]:
    items = _compact_list(value, limit=limit)
    summaries: list[str] = []
    for item in items:
        if isinstance(item, dict):
            title = item.get("title") or item.get("name") or item.get("project") or item.get("value")
            if title:
                summaries.append(str(title))
                continue
        summaries.append(str(item))
    return summaries


def _planning_review(
    task_plan: Any,
    practice_plan: Any,
    artifact_chain: Any,
    evidence_specs: Any,
    story_map: Any,
) -> dict[str, Any]:
    task_count = _get_value(task_plan, "tasks_count") or _get_value(practice_plan, "task_count")
    complexity = str(_get_value(task_plan, "complexity") or "").strip()
    explanation = _first_non_empty(
        _get_value(task_plan, "explanation"),
        _get_value(task_plan, "rationale"),
        _get_value(practice_plan, "project_goal"),
    )
    resolved_story_map = story_map or _get_value(practice_plan, "story_map")
    facts = [
        {"label": "Количество задач", "value": str(task_count) if task_count else ""},
        {"label": "Сложность", "value": _complexity_label(complexity)},
        {"label": "Основание", "value": str(_get_value(task_plan, "level_source") or "").strip()},
        {"label": "Материалы", "value": str(len(_evidence_summaries(evidence_specs or _get_value(artifact_chain, "evidence_specs"))))},
    ]
    facts = [item for item in facts if item["value"]]
    return {
        "facts": facts,
        "explanation": _truncate_text(str(explanation or ""), 520),
        "story": _story_map_summary(resolved_story_map),
        "task_flow": _practice_step_summaries(practice_plan, artifact_chain, limit=8),
        "evidence": _evidence_summaries(evidence_specs or _get_value(artifact_chain, "evidence_specs"), limit=6),
        "will_use": [
            "количество задач и уровень сложности при генерации практического блока",
            "цепочку артефактов, чтобы задания зависели друг от друга, а не были разрозненными",
            "исходные материалы как raw evidence, из которых студент должен вывести решение",
            "план задач как ограничение для теоретических разделов главы 2",
        ],
        "can_change": [
            "изменить количество задач или ожидаемую сложность",
            "перестроить последовательность задач и зависимость артефактов",
            "уточнить, какие материалы нужны студенту на входе",
            "попросить усилить или ослабить связь со сторителлингом",
        ],
    }


def _complexity_label(value: str) -> str:
    labels = {
        "low": "низкая",
        "easy": "низкая",
        "medium": "средняя",
        "middle": "средняя",
        "high": "высокая",
        "advanced": "высокая",
    }
    return labels.get(value.lower(), value)


def _story_map_summary(story_map: Any) -> dict[str, str]:
    if story_map is None:
        return {}
    return {
        "role": _truncate_text(str(_get_value(story_map, "student_role") or ""), 180),
        "case": _truncate_text(str(_get_value(story_map, "working_case") or ""), 240),
        "tension": _truncate_text(str(_get_value(story_map, "central_tension") or ""), 240),
        "completion": _truncate_text(str(_get_value(story_map, "completion") or ""), 240),
    }


def _practice_step_summaries(practice_plan: Any, artifact_chain: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    practice_steps = _as_list(_get_value(practice_plan, "steps"))
    artifact_steps = _as_list(_get_value(artifact_chain, "steps"))
    rows: list[dict[str, Any]] = []
    max_len = max(len(practice_steps), len(artifact_steps))
    for index in range(min(max_len, limit)):
        practice_step = practice_steps[index] if index < len(practice_steps) else None
        artifact_step = artifact_steps[index] if index < len(artifact_steps) else None
        task_index = _get_value(practice_step, "task_index") or _get_value(artifact_step, "task_index") or index + 1
        title = _first_non_empty(
            _get_value(practice_step, "title_hint"),
            _get_value(practice_step, "title"),
            f"Задача {task_index}",
        )
        artifact = _first_non_empty(
            _get_value(practice_step, "artifact_location"),
            _get_value(artifact_step, "artifact_location"),
        )
        depends_on = _first_non_empty(
            _get_value(practice_step, "depends_on"),
            _get_value(artifact_step, "depends_on"),
        )
        rows.append(
            {
                "index": task_index,
                "title": _truncate_text(str(title), 180),
                "artifact": _truncate_text(str(artifact), 220),
                "depends_on": _truncate_text(str(depends_on), 220),
                "focus": _truncate_text(_join_first(_get_value(practice_step, "p2p_focus"), limit=2), 220),
            }
        )
    return rows


def _evidence_summaries(value: Any, *, limit: int = 6) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in _as_list(value)[:limit]:
        contains = _join_first(_get_value(item, "contains"), limit=2)
        rows.append(
            {
                "path": str(_get_value(item, "path") or _get_value(item, "name") or item or "").strip(),
                "kind": str(_get_value(item, "evidence_type") or _get_value(item, "kind") or "").strip(),
                "contains": _truncate_text(contains, 240),
            }
        )
    return [row for row in rows if row["path"]]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _join_first(value: Any, *, limit: int = 3) -> str:
    items = _as_list(value)
    if not items:
        return ""
    return "; ".join(str(item) for item in items[:limit] if item)


def _seed_summary(seed: Any) -> dict[str, Any]:
    return {
        "language": str(_get_value(seed, "language") or ""),
        "project_type": str(_get_value(seed, "project_type") or ""),
        "direction": str(_get_value(seed, "direction") or ""),
        "thematic_block": str(_get_value(seed, "thematic_block") or ""),
        "audience_level": str(_get_value(seed, "audience_level") or ""),
        "project_description": _truncate_text(str(_get_value(seed, "project_description") or ""), 320),
        "storytelling": _truncate_text(str(_get_value(seed, "sjm") or ""), 320),
    }


def _context_summary(context_meta: Any, context_analysis: Any, context_bundle: Any) -> dict[str, Any]:
    return {
        "context_meta": _compact_object(
            context_meta,
            keys=["track", "thematic_block", "last_order", "narrative_anchor", "context_summary"],
        ),
        "context_analysis": _compact_object(
            context_analysis,
            keys=["context_summary", "narrative_anchor", "audience_level_match"],
        ),
        "context_bundle": _compact_object(
            context_bundle,
            keys=["context_source", "context_summary", "narrative_anchor", "previous_projects_count"],
        ),
    }


def _task_plan_summary(task_plan: Any) -> dict[str, Any]:
    if task_plan is None:
        return {}
    return {
        "tasks_count": _get_value(task_plan, "tasks_count"),
        "complexity": _get_value(task_plan, "complexity"),
        "level_index": _get_value(task_plan, "level_index"),
        "level_source": _get_value(task_plan, "level_source"),
        "rationale": _truncate_text(str(_get_value(task_plan, "rationale") or ""), 320),
        "explanation": _truncate_text(str(_get_value(task_plan, "explanation") or ""), 420),
    }


def _contract_summary(value: Any) -> Any:
    return _compact_value(value, text_limit=260, max_items=8)


def _compact_list(value: Any, *, limit: int = 8) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [_truncate_text(str(value), 260)]
    if not isinstance(value, list):
        value = list(value) if isinstance(value, (tuple, set)) else [value]
    return [_compact_value(item, text_limit=260, max_items=6) for item in value[:limit]]


def _compact_object(value: Any, *, keys: list[str]) -> dict[str, Any]:
    if value is None:
        return {}
    payload = value.model_dump(exclude_none=True, mode="json") if isinstance(value, BaseModel) else value
    if not isinstance(payload, dict):
        return {"value": _compact_value(payload)}
    return {
        key: _compact_value(payload.get(key), text_limit=260, max_items=6)
        for key in keys
        if payload.get(key) not in (None, "", [], {})
    }


def _compact_value(value: Any, *, text_limit: int = 260, max_items: int = 8) -> Any:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return _compact_value(value.model_dump(exclude_none=True, mode="json"), text_limit=text_limit, max_items=max_items)
    if isinstance(value, dict):
        items = list(value.items())[:max_items]
        compact = {
            str(key): _compact_value(item_value, text_limit=text_limit, max_items=max_items)
            for key, item_value in items
        }
        if len(value) > len(items):
            compact["_truncated_keys"] = len(value) - len(items)
        return compact
    if isinstance(value, list):
        result = [_compact_value(item, text_limit=text_limit, max_items=max_items) for item in value[:max_items]]
        if len(value) > len(result):
            result.append({"_truncated_items": len(value) - len(result)})
        return result
    if isinstance(value, (tuple, set)):
        return _compact_value(list(value), text_limit=text_limit, max_items=max_items)
    if isinstance(value, (int, float, bool)):
        return value
    return _truncate_text(str(value), text_limit)


def _assets_count(assets: Any) -> int:
    if assets is None:
        return 0
    if isinstance(assets, dict):
        return len(assets)
    if isinstance(assets, (list, tuple, set)):
        return len(assets)
    return 1


def _project_spec_summary(spec: Any) -> dict[str, Any]:
    if spec is None:
        return {}
    return {
        "title": str(_get_value(spec, "title") or ""),
        "theory_count": len(_get_value(spec, "theory") or []),
        "practice_count": len(_get_value(spec, "practice") or []),
        "language": str(_get_value(spec, "language") or ""),
    }


def _rubric_item_failed(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("passed") is False:
        return True
    if "score" not in item:
        return False
    try:
        return float(item.get("score") or 0) != 1.0
    except (TypeError, ValueError):
        return True


def _get_value(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _markdown_section(markdown: str, heading_marker: str, limit: int = 1800) -> str:
    if not markdown:
        return ""
    marker = heading_marker.lower()
    lines = markdown.splitlines()
    start_index = None
    start_level = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        title = stripped.lstrip("#").strip().lower()
        if marker in title:
            start_index = index
            start_level = len(stripped) - len(stripped.lstrip("#"))
            break
    if start_index is None:
        return _truncate_text(markdown, limit)
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        if level <= start_level:
            end_index = index
            break
    return "\n".join(lines[start_index:end_index]).strip()


def _annotation_text(annotation: Any) -> str:
    if isinstance(annotation, dict):
        return str(annotation.get("text") or "")
    if hasattr(annotation, "text"):
        return str(annotation.text or "")
    return ""


def _checkpoint_artifact_hash(checkpoint: HumanApprovalCheckpoint) -> str:
    payload = checkpoint.model_dump(mode="json", exclude={"artifact_hash"})
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _checkpoint_already_approved(context: dict[str, Any], checkpoint: dict[str, Any]) -> bool:
    checkpoint_id = str(checkpoint.get("id") or "")
    checkpoint_hash = str(checkpoint.get("artifact_hash") or "")
    if not checkpoint_id or not checkpoint_hash:
        return False

    for action in reversed(list(context.get("methodology_review_actions") or [])):
        if not isinstance(action, dict) or action.get("action") != "approved":
            continue
        details = action.get("details") if isinstance(action.get("details"), dict) else {}
        if (
            str(details.get("checkpoint_id") or "") == checkpoint_id
            and str(details.get("checkpoint_hash") or "") == checkpoint_hash
        ):
            return True
        approved_checkpoint = details.get("checkpoint")
        if isinstance(approved_checkpoint, dict) and (
            str(approved_checkpoint.get("id") or "") == checkpoint_id
            and str(approved_checkpoint.get("artifact_hash") or "") == checkpoint_hash
        ):
            return True
    return False


def _theory_parts_markdown(parts: list[Any]) -> str:
    blocks = ["## Глава 2. Теоретический блок"]
    for index, part in enumerate(parts, 1):
        title = _get_value(part, "title") or _get_value(part, "heading") or f"Часть {index}"
        body = (
            _get_value(part, "text")
            or _get_value(part, "content")
            or _get_value(part, "body")
            or _get_value(part, "text_markdown")
            or ""
        )
        example = _get_value(part, "example") or ""
        blocks.append(f"### 2.{index}. {title}".strip())
        if body:
            blocks.append(str(body).strip())
        if example:
            blocks.append(str(example).strip())
    return "\n\n".join(block for block in blocks if block).strip()


def _markdown_outline(markdown: str) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", str(markdown or ""), flags=re.MULTILINE):
        outline.append(
            {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
            }
        )
    return outline


def _markdown_subsections(section_markdown: str, *, min_level: int = 3) -> list[dict[str, str]]:
    """Split a chapter into addressable markdown subsections for review UI."""
    if not section_markdown:
        return []

    matches = [
        match
        for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", section_markdown, flags=re.MULTILINE)
        if len(match.group(1)) >= min_level
    ]
    sections: list[dict[str, str]] = []
    used_ids: set[str] = set()
    for index, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()
        end = len(section_markdown)
        for next_match in matches[index + 1 :]:
            if len(next_match.group(1)) <= level:
                end = next_match.start()
                break
        section_id = _unique_section_id(_slug_text(title), used_ids)
        sections.append(
            {
                "id": section_id,
                "title": title,
                "markdown": section_markdown[match.start() : end].strip(),
            }
        )
    return sections


def _slug_text(value: str) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-zа-яё0-9]+", "_", text, flags=re.I)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:80] or "section"


def _unique_section_id(base: str, used_ids: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used_ids.add(candidate)
    return candidate


def _truncate_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}..."


def _matrix_item(item_id: str, title: str, passed: bool, evidence: str) -> dict[str, Any]:
    return RequirementMatrixItem(
        id=item_id,
        title=title,
        status="pass" if passed else "fail",
        passed=bool(passed),
        evidence=evidence,
    ).model_dump(mode="json")


def _has_markdown_label(text: str, label: str) -> bool:
    return bool(re.search(rf"\*\*{re.escape(label)}:?\*\*", text, flags=re.I))


def _practice_task_blocks(chapter_3: str) -> list[str]:
    raw = re.split(r"(?=^###\s+(?:Задание|Задача)\s+\d+\.)", chapter_3, flags=re.M)
    return [chunk.strip() for chunk in raw if chunk.strip().startswith("###")]


def _task_has_p2p_outcomes(task_block: str) -> bool:
    result_match = re.search(
        r"\*\*(?:Что должно получиться|Критерии проверки.*?):?\*\*\s*(.+?)(?=\n\*\*|\n###|\Z)",
        task_block,
        flags=re.S | re.I,
    )
    if not result_match:
        return False
    checklist = [
        line
        for line in result_match.group(1).splitlines()
        if re.match(r"^\s*[-*]\s*(?:\[[ xX]?\]\s*)?.{10,}", line)
    ]
    has_location = bool(re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./{}:\-]+\.[A-Za-z0-9]+", task_block))
    has_artifact = bool(re.search(r"\b(файл|документ|таблиц|схем|артефакт|отчет|отчёт|README|Markdown)\b", task_block, re.I))
    return len(checklist) >= 2 and (has_location or has_artifact)


def _final_section(markdown: str) -> str:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, flags=re.M))
    for index, match in enumerate(matches):
        title = match.group(1).strip().lower()
        if not re.search(r"(заключение|итог проекта|финал проекта|завершение проекта)", title, flags=re.I):
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        return markdown[match.start():end].strip()
    return ""


def _data_size(data: Any) -> int:
    if data is None:
        return 0
    if isinstance(data, bytes):
        return len(data)
    return len(str(data).encode("utf-8"))
