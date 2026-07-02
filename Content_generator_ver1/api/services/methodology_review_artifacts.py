"""Pure artifact helpers for methodology review UI payloads."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from content_gen.methodology import build_requirement_matrix
from content_gen.models.readme_document import ReadmeDocument
from content_gen.utils.markdown_display_normalizer import normalize_markdown_display_blocks
from content_gen.utils.rubric_export import criteria_to_json
from content_gen.validators.rubric import RubricScorer

JsonDict = dict[str, Any]


def methodology_human_review_enabled(
    project_seed_payload: JsonDict,
    context: JsonDict | None = None,
) -> bool:
    """Return whether a request should pause for human methodologist approval."""
    raw_value = project_seed_payload.get("methodology_human_review")
    if raw_value is not None:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
        return bool(raw_value)

    if context:
        if context.get("methodology_human_review_enabled") is True:
            return True
        if context.get("human_approval_checkpoint") or context.get("human_approval_checkpoints"):
            return True
    return False


def annotation_text_from_context(context: JsonDict) -> str:
    """Extract annotation text from dict or typed annotation object."""
    annotation = context.get("annotation")
    if isinstance(annotation, dict):
        return str(annotation.get("text") or "")
    if hasattr(annotation, "text"):
        return str(annotation.text or "")
    return ""


def context_preview_markdown(context: JsonDict) -> str:
    """Build the most useful README preview from a paused flow context."""
    markdown = normalize_markdown_display_blocks(str(context.get("markdown") or "")).strip()
    if markdown:
        return markdown

    title = str(context.get("title") or "").strip()
    annotation = annotation_text_from_context(context).strip()
    blocks: list[str] = []
    if title:
        blocks.append(f"# {title}")
    if annotation:
        blocks.append(annotation)
    return "\n\n".join(blocks).strip()


def markdown_outline(markdown: str) -> list[JsonDict]:
    """Return a serializable Markdown heading outline."""
    return [
        {"level": len(match.group(1)), "title": match.group(2).strip()}
        for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*$", markdown or "", flags=re.MULTILINE)
    ]


def markdown_section(markdown: str, marker: str) -> str:
    """Return a section whose heading title contains marker."""
    return ReadmeDocument.from_markdown(markdown).section_markdown_by_title_fragment(marker).strip()


def markdown_subsections(markdown: str, *, min_level: int = 3) -> list[dict[str, str]]:
    """Split Markdown into tab-ready sections by heading level."""
    return ReadmeDocument.from_markdown(markdown).markdown_subsections(min_level=min_level)


def checkpoint_payload_hash(checkpoint: JsonDict) -> str:
    """Hash checkpoint payload excluding previous artifact hash."""
    payload = {key: value for key, value in checkpoint.items() if key != "artifact_hash"}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def is_final_checkpoint_payload(checkpoint: Any) -> bool:
    """Return whether checkpoint payload represents the final README review."""
    if not isinstance(checkpoint, dict):
        return False
    stage = str(checkpoint.get("stage") or "").lower()
    checkpoint_id = str(checkpoint.get("id") or "").lower()
    node_id = str(checkpoint.get("node_id") or "").lower()
    return stage == "final" or checkpoint_id in {"quality", "evaluation", "finalize"} or node_id in {
        "global_quality",
        "evaluation",
        "finalize",
    }


def _rubric_markdown_hash(markdown: str) -> str:
    """Stable hash for detecting whether cached rubric still matches Markdown."""
    return hashlib.sha256((markdown or "").encode("utf-8")).hexdigest()[:16]


def _rubric_item_failed(item: Any) -> bool:
    """Return whether a serialized rubric item should count as failed."""
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


def _rubric_failed_count(rubric: JsonDict) -> int | None:
    """Count failed criteria in a serialized rubric contract."""
    items = rubric.get("items")
    if not isinstance(items, list):
        return None
    return sum(1 for item in items if _rubric_item_failed(item))


def _rubric_score_value(rubric: JsonDict) -> Any:
    """Return the best compact score value for methodology review metadata."""
    for key in ("score", "total_score", "percentage"):
        if rubric.get(key) is not None:
            return rubric.get(key)
    total = rubric.get("total")
    max_score = rubric.get("max_score")
    if total is not None and max_score is not None:
        return f"{total} / {max_score}"
    return None


def _get_value(value: Any, field: str) -> Any:
    """Read a field from dict-like or object-like values."""
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _learning_outcomes_from_context(context: JsonDict) -> list[str]:
    """Extract learning outcomes used by rubric validation."""
    seed = context.get("seed") or {}
    raw = (
        context.get("learning_outcomes")
        or _get_value(seed, "learning_outcomes")
        or context.get("educational_results")
        or []
    )
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _score_rubric_for_markdown(context: JsonDict, markdown: str) -> JsonDict:
    """Re-run deterministic rubric scoring for a changed README preview."""
    seed = context.get("seed") or {}
    language = str(context.get("language") or _get_value(seed, "language") or "ru")
    report = RubricScorer(language=language, llm_client=None).score_document(
        ReadmeDocument.from_markdown(markdown),
        learning_outcomes=_learning_outcomes_from_context(context),
        use_cache=False,
    )
    rubric = criteria_to_json(report)
    rubric["source_markdown_hash"] = _rubric_markdown_hash(markdown)
    return rubric


def _refresh_rubric_artifact(context: JsonDict, artifact: JsonDict, markdown: str) -> None:
    """Keep evaluation rubric aligned with the latest preview Markdown."""
    current_hash = _rubric_markdown_hash(markdown)
    rubric = context.get("rubric_json") if isinstance(context.get("rubric_json"), dict) else {}
    rubric_hash = rubric.get("source_markdown_hash") if isinstance(rubric, dict) else None
    if not rubric or (rubric_hash and rubric_hash != current_hash):
        try:
            rubric = _score_rubric_for_markdown(context, markdown)
            context["rubric_json"] = rubric
        except Exception as exc:  # noqa: BLE001
            artifact["rubric_refresh_error"] = str(exc)
            rubric = rubric if isinstance(rubric, dict) else {}

    if not rubric:
        return
    if not rubric.get("source_markdown_hash"):
        rubric = dict(rubric)
        rubric["source_markdown_hash"] = current_hash
        context["rubric_json"] = rubric
    artifact.pop("rubric_refresh_error", None)
    artifact["rubric"] = rubric
    artifact["rubric_score"] = _rubric_score_value(rubric)
    artifact["rubric_failed_count"] = _rubric_failed_count(rubric)
    artifact["issues_count"] = len(context.get("issues") or [])
    artifact["rubric_source_hash"] = current_hash


def refresh_checkpoint_artifact(context: JsonDict) -> None:
    """Keep the visible human-review artifact aligned with context Markdown."""
    checkpoint = context.get("human_approval_checkpoint")
    if not isinstance(checkpoint, dict):
        return

    markdown = context_preview_markdown(context)
    artifact = dict(checkpoint.get("artifact") or {})
    stage = str(checkpoint.get("stage") or checkpoint.get("id") or "")
    title = str(context.get("title") or artifact.get("title") or "").strip()
    if title:
        artifact["title"] = title

    if stage == "context":
        artifact["summary"] = artifact.get("summary") or "Входные данные и curriculum/context слой подготовлены."
    elif stage == "task_planning":
        artifact["summary"] = artifact.get("summary") or "План задач, сторителлинг и цепочка артефактов подготовлены."
    elif stage in {"title", "annotation"}:
        artifact["annotation"] = annotation_text_from_context(context)
    elif stage == "skeleton":
        artifact["markdown_excerpt"] = markdown
        artifact["structure_outline"] = markdown_outline(markdown)
        artifact["requirements_matrix"] = build_requirement_matrix(context, markdown)
        artifact["markdown_sections"] = markdown_subsections(markdown, min_level=2)
    elif stage == "theory":
        chapter = markdown_section(markdown, "глава 2") or markdown
        artifact["markdown_excerpt"] = normalize_markdown_display_blocks(chapter)
        artifact["requirements_matrix"] = build_requirement_matrix(context, markdown)
        artifact["markdown_sections"] = markdown_subsections(chapter)
    elif stage == "practice":
        chapter = markdown_section(markdown, "глава 3") or markdown
        artifact["markdown_excerpt"] = normalize_markdown_display_blocks(chapter)
        artifact["requirements_matrix"] = build_requirement_matrix(context, markdown)
        artifact["markdown_sections"] = markdown_subsections(chapter)
        artifact["dataset_files"] = [
            {"path": str(item.get("path") or ""), "bytes": len(item.get("data") or b"")}
            for item in context.get("dataset_files") or []
            if isinstance(item, dict)
        ]
    elif stage == "translation":
        translated_markdown = normalize_markdown_display_blocks(
            str(context.get("translated_markdown") or context.get("markdown") or "")
        )
        artifact["markdown_excerpt"] = translated_markdown
        artifact["markdown_chars"] = len(translated_markdown)
        artifact["requirements_matrix"] = build_requirement_matrix(context, translated_markdown)
        artifact["markdown_sections"] = markdown_subsections(translated_markdown, min_level=2)
    elif is_final_checkpoint_payload(checkpoint):
        artifact["markdown_excerpt"] = markdown
        artifact["requirements_matrix"] = build_requirement_matrix(context, markdown)
        artifact["markdown_sections"] = markdown_subsections(markdown, min_level=2)
        if checkpoint.get("id") == "evaluation" or "rubric" in artifact or context.get("rubric_json"):
            _refresh_rubric_artifact(context, artifact, markdown)
    else:
        artifact["markdown_excerpt"] = markdown[:5000]

    checkpoint["artifact"] = artifact
    checkpoint["artifact_hash"] = checkpoint_payload_hash(checkpoint)
    context["human_approval_checkpoint"] = checkpoint
