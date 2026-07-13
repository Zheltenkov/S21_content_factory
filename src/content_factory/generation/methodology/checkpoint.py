"""Explicit human approval checkpoints for generated artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from ..utils.markdown_display_normalizer import normalize_markdown_display_blocks
from .checkpoint_models import HumanApprovalCheckpoint
from .checkpoint_requirement_matrix import (
    _checkpoint_requirement_matrix,
    build_requirement_matrix,
)
from .checkpoint_summaries import (
    _assets_count,
    _compact_list,
    _context_review,
    _get_value,
    _part_summary,
    _planning_review,
    _project_spec_summary,
    _rubric_item_failed,
    _seed_title,
    _similar_project_summaries,
    _task_summary,
)
from .checkpoint_text import (
    _annotation_text,
    _data_size,
    _markdown_outline,
    _markdown_section,
    _markdown_subsections,
)
from .decision import MethodologyGateInterrupt


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
    def from_env(cls, *, enabled_by_default: bool = False) -> HumanApprovalCheckpointPolicy:
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
        _det = action.get("details")
        details = _det if isinstance(_det, dict) else {}
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

