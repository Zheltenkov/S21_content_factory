"""Общее для форматов Microsoft Office: внутри все они — zip с XML.

Текст в OOXML разбит на run: один абзац или одна ячейка может состоять из нескольких кусков с
разным оформлением. Перевод приходит одной строкой, поэтому его надо разложить обратно по тем же
run — иначе пропадёт жирность, курсив и прочее inline-оформление.
"""

from __future__ import annotations

import re

# Управляющие символы, которые ElementTree запишет, а Word и Excel потом сочтут файл повреждённым.
_INVALID_XML_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def strip_invalid_xml(text: str) -> str:
    """Убирает символы, недопустимые в XML 1.0."""
    return _INVALID_XML_RE.sub("", text or "")


def spread_over_runs(translated: str, original_parts: list[str]) -> list[str]:
    """Распределяет перевод по исходным run пропорционально их длине."""
    if not original_parts:
        return []
    if len(original_parts) == 1:
        return [translated]

    weights = [max(len(part or ""), 1) for part in original_parts]
    total = sum(weights) or len(original_parts)
    chunks: list[str] = []
    cursor = 0
    cumulative = 0
    for weight in weights[:-1]:
        cumulative += weight
        target = round(len(translated) * cumulative / total)
        cut = _cut_near_space(translated, target, cursor)
        chunks.append(translated[cursor:cut])
        cursor = cut
    chunks.append(translated[cursor:])
    return chunks


def _cut_near_space(text: str, target: int, start: int) -> int:
    """Граница куска около пробела, чтобы перевод не разрывался посреди слова."""
    if target <= start:
        return start
    if target >= len(text):
        return len(text)
    window = max(20, min(80, len(text) // 8))
    left = max(start, target - window)
    right = min(len(text), target + window)
    candidates = [left + m.end() for m in re.finditer(r"\s+", text[left:right])]
    if not candidates:
        return target
    return min(candidates, key=lambda cut: abs(cut - target))
