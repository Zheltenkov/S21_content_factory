"""Deterministic requirement-matrix builder for methodology checkpoints.

The source/didactics pass-fail matrix shown in the review UI: ``build_requirement_matrix``
(public, re-exported from the package ``__init__``) plus the stage-filtered
``_checkpoint_requirement_matrix`` and the ``_matrix_item`` row factory. Depends only on
the text-util + models leaves and the markdown normalizer, so it stays a leaf under the
checkpoint policy. ``checkpoint`` re-imports the two the policy calls.
"""

from __future__ import annotations

import re
from typing import Any

from ..utils.markdown_display_normalizer import normalize_markdown_display_blocks
from .checkpoint_models import RequirementMatrixItem
from .checkpoint_text import (
    _final_section,
    _has_markdown_label,
    _markdown_section,
    _practice_task_blocks,
    _task_has_p2p_outcomes,
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


def _matrix_item(item_id: str, title: str, passed: bool, evidence: str) -> dict[str, Any]:
    return RequirementMatrixItem(
        id=item_id,
        title=title,
        status="pass" if passed else "fail",
        passed=bool(passed),
        evidence=evidence,
    ).model_dump(mode="json")
