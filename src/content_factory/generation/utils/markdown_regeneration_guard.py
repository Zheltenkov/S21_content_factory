"""Deterministic guards for LLM-based README regeneration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class _MarkdownBlock:
    content: str
    separator: str


_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+")
_PUNCT_RE = re.compile(r"[^\w\sА-Яа-яЁё]", re.UNICODE)


def _split_markdown_blocks(markdown: str) -> list[_MarkdownBlock]:
    """Split Markdown into blank-line separated blocks while preserving separators."""
    parts = re.split(r"(\n{2,})", markdown or "")
    blocks: list[_MarkdownBlock] = []
    for index in range(0, len(parts), 2):
        blocks.append(
            _MarkdownBlock(
                content=parts[index],
                separator=parts[index + 1] if index + 1 < len(parts) else "",
            )
        )
    return blocks


def _normalize_prose(value: str) -> str:
    """Normalize prose enough to compare rewrites without depending on line wrapping."""
    text = (value or "").replace("ё", "е").replace("Ё", "Е").lower()
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = _PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_plain_prose(block: str) -> bool:
    """Keep the guard away from headings, lists, tables, code, formulas and markers."""
    stripped = (block or "").strip()
    normalized = _normalize_prose(stripped)
    if len(normalized) < 120:
        return False
    if len(_WORD_RE.findall(normalized)) < 14:
        return False
    if "```" in stripped or "$$" in stripped or "[[[BLOCK_" in stripped:
        return False
    first_line = stripped.splitlines()[0].lstrip()
    if first_line.startswith(("#", "-", "*", "+", ">", "|", "<!--")):
        return False
    if any(line.lstrip().startswith(("-", "*", "+", ">", "|")) for line in stripped.splitlines()):
        return False
    return True


def _similarity(left: str, right: str) -> float:
    """Return a conservative similarity score for normalized prose blocks."""
    left_norm = _normalize_prose(left)
    right_norm = _normalize_prose(right)
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def _original_prose_index(original_md: str) -> set[str]:
    """Build a lookup of paragraphs that already existed before regeneration."""
    return {
        _normalize_prose(block.content)
        for block in _split_markdown_blocks(original_md)
        if _looks_like_plain_prose(block.content)
    }


def remove_adjacent_rewritten_paragraph_duplicates(original_md: str, regenerated_md: str) -> str:
    """
    Remove adjacent near-duplicate paragraphs produced by LLM insert-instead-of-replace failures.

    The guard is intentionally narrow: it only compares neighboring prose paragraphs,
    ignores structured Markdown, and prefers the paragraph that did not exist in the
    original README. This fixes cases where the model inserts a new version above or
    below the old text instead of replacing the old paragraph.
    """
    blocks = _split_markdown_blocks(regenerated_md)
    if len(blocks) < 2:
        return regenerated_md or ""

    original_blocks = _original_prose_index(original_md)
    kept: list[_MarkdownBlock] = []
    index = 0

    while index < len(blocks):
        current = blocks[index]
        next_block = blocks[index + 1] if index + 1 < len(blocks) else None
        if (
            next_block is not None
            and _looks_like_plain_prose(current.content)
            and _looks_like_plain_prose(next_block.content)
            and _similarity(current.content, next_block.content) >= 0.88
        ):
            current_was_original = _normalize_prose(current.content) in original_blocks
            next_was_original = _normalize_prose(next_block.content) in original_blocks

            if current_was_original and next_was_original:
                kept.append(current)
                index += 1
                continue
            if current_was_original and not next_was_original:
                kept.append(next_block)
                index += 2
                continue
            kept.append(current)
            index += 2
            continue

        kept.append(current)
        index += 1

    return "".join(f"{block.content}{block.separator}" for block in kept)
