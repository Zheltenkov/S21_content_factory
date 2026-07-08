"""Детерминированные evidence-сигналы для дидактической оси.

Служат двум целям: (1) фактура для промпта жюри (LLM видит объективные числа, а не
только текст), (2) основа mock-фолбэка судьи, когда LLM недоступен. Переиспользует
общий модуль `validators.integrity_signals` (единый источник правды по повторам,
таблицам, диаграммам), добавляя дидактико-специфичные `example_count`/`directive_hits`.
"""

from __future__ import annotations

import re
from typing import TypedDict

from ...validators.integrity_signals import (
    diagram_topic_matches,
    table_integrity,
    verbatim_repetition,
)

_DIRECTIVE_RE = re.compile(r"\b(сделай|нажми|введите|скопируй|выполни шаг)\b", re.IGNORECASE)
_EXAMPLE_MARKER_RE = re.compile(r"\*\*Пример")


class DidacticSignals(TypedDict):
    """Объективные сигналы по README для дидактической оси."""

    repetition_ratio: float
    near_dup: int
    broken_tables: int
    diagram_match_avg: float
    example_count: int
    directive_hits: int


def collect_signals(md: str) -> DidacticSignals:
    """Собрать evidence-сигналы по сырому markdown README."""
    repetition = verbatim_repetition(md)
    rep_ratio = float(repetition.details.get("repetition_ratio", 0.0))
    near_dup = int(repetition.details.get("near_dup", 0))

    table = table_integrity(md)
    broken_tables = len(table.details.get("merged_rows", [])) + int(table.details.get("col_issues", 0))

    jaccards = [jac for _, jac in diagram_topic_matches(md)]
    diagram_match_avg = round(sum(jaccards) / len(jaccards), 3) if jaccards else 1.0

    return DidacticSignals(
        repetition_ratio=rep_ratio,
        near_dup=near_dup,
        broken_tables=broken_tables,
        diagram_match_avg=diagram_match_avg,
        example_count=len(_EXAMPLE_MARKER_RE.findall(md)),
        directive_hits=len(_DIRECTIVE_RE.findall(md)),
    )
