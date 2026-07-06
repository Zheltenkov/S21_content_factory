"""Детерминированные сигналы целостности сгенерированного README.

Общий модуль структурной оси v2: чистые функции без LLM, портированные из
прототипа `прототип/structural_criteria_v2.ipynb` (ячейка `run_new`). Ловят
дефекты, которых нет ни в `RubricScorer`, ни в audit-движке: слитые/рассогласованные
таблицы, дословные повторы («болванки» из шаблона), диаграммы не по теме раздела,
оборванные кавычки, множественные идентификаторы проекта.

Используется как `RubricScorer` (через `integrity_checker.py`), так и (в будущем)
mock-судьёй дидактики — чтобы не считать повторы/таблицы/диаграммы в трёх копиях.
Пороги — в `config.thresholds.THRESHOLDS` (провизорны, калибруются на корпусе).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, cast

from ..config.thresholds import THRESHOLDS

__all__ = [
    "IntegritySignal",
    "table_integrity",
    "verbatim_repetition",
    "diagram_topic_match",
    "diagram_topic_matches",
    "orphaned_quotes",
    "project_id_unity",
    "all_integrity_signals",
]


@dataclass(frozen=True)
class IntegritySignal:
    """Результат одной проверки целостности.

    `passed=False` — обнаружен дефект. `comments` — человекочитаемое пояснение,
    `details` — технические данные для отладки/отчёта методолога.
    """

    id: str
    title: str
    passed: bool
    comments: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


# --- вспомогательные функции (парити с прототипом) ---

def _clean_prose(text: str) -> str:
    """Убирает code/mermaid, таблицы, html, markdown-разметку — оставляет прозу."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^\|.*\|.*$", " ", text, flags=re.M)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#*_>`\[\]()]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 3]


# --- N.1 целостность таблиц ---

def table_integrity(md: str) -> IntegritySignal:
    """Строки таблиц не слиты с прозой + согласованность числа колонок."""
    long_rows = [ln[:90] for ln in md.splitlines() if ln.count("|") >= 2 and len(ln) > 200]

    def _flush(block: list[str]) -> int:
        rows = [r for r in block if "|" in r and not re.match(r"^\s*\|?[\s:|-]+\|?\s*$", r)]
        counts = [r.count("|") for r in rows]
        return 1 if counts and (max(counts) - min(counts) > 1) else 0

    col_issue = 0
    cur: list[str] = []
    for ln in md.splitlines():
        if ln.strip().startswith("|"):
            cur.append(ln)
        else:
            if cur:
                col_issue += _flush(cur)
                cur = []
    if cur:
        col_issue += _flush(cur)

    bad = len(long_rows) + col_issue
    comments = (
        []
        if bad == 0
        else [f"{len(long_rows)} строк слиты с прозой, {col_issue} таблиц с рассогласованием колонок"]
    )
    return IntegritySignal(
        id="N.1",
        title="Целостность таблиц",
        passed=bad == 0,
        comments=comments,
        details={"merged_rows": long_rows[:3], "col_issues": col_issue},
    )


# --- N.2 дословные повторы (template bleed) ---

def _repetition_ratio(text: str, n: int = 8) -> float:
    w = re.findall(r"\w+", text.lower())
    grams = [" ".join(w[i:i + n]) for i in range(len(w) - n + 1)]
    if not grams:
        return 0.0
    counts = Counter(grams)
    return sum(v for v in counts.values() if v > 1) / len(grams)


def verbatim_repetition(md: str) -> IntegritySignal:
    """Дословные повторы 8-грамм + почти-дубли предложений (Jaccard >= 0.7)."""
    sents = _sentences(_clean_prose(md))
    token_sets = [set(re.findall(r"\w+", s.lower())) for s in sents]
    ndup = 0
    examples: list[str] = []
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            a, b = token_sets[i], token_sets[j]
            if a and b and len(a & b) / len(a | b) >= 0.7:
                ndup += 1
                if len(examples) < 2:
                    examples.append(sents[i][:70])

    rr = round(_repetition_ratio(md), 3)
    rr_max = cast(float, THRESHOLDS["repetition_ratio_max"])
    ndup_max = cast(int, THRESHOLDS["near_dup_max"])
    fail = rr > rr_max or ndup > ndup_max
    comments = (
        [f"repetition_ratio={rr} (макс {rr_max}), почти-дублей={ndup} (макс {ndup_max})"]
        if fail
        else []
    )
    return IntegritySignal(
        id="N.2",
        title="Нет дословных повторов/болванок",
        passed=not fail,
        comments=comments,
        details={"repetition_ratio": rr, "near_dup": ndup, "examples": examples},
    )


# --- N.3 диаграмма <-> тема раздела ---

def _nearest_heading(md: str, idx: int) -> str | None:
    heading = None
    for m in re.finditer(r"^#{2,3}\s+(.+)$", md[:idx], re.M):
        heading = m.group(1)
    return heading


def diagram_topic_matches(md: str) -> list[tuple[str | None, float]]:
    """Per-diagram (ближайший заголовок, Jaccard токенов) для каждой mermaid-диаграммы."""
    matches: list[tuple[str | None, float]] = []
    for m in re.finditer(r"```mermaid(.*?)```", md, flags=re.S):
        head = _nearest_heading(md, m.start())
        nodes = set(re.findall(r"[А-Яа-яЁё]{4,}", m.group(1).lower()))
        head_words = set(re.findall(r"[А-Яа-яЁё]{4,}", (head or "").lower()))
        union = nodes | head_words
        jac = len(nodes & head_words) / len(union) if union else 1.0
        matches.append((head, round(jac, 3)))
    return matches


def diagram_topic_match(md: str) -> IntegritySignal:
    """Токены mermaid-диаграммы пересекаются с ближайшим заголовком (Jaccard)."""
    min_jac = cast(float, THRESHOLDS["diagram_topic_min"])
    mismatched = [(head, jac) for head, jac in diagram_topic_matches(md) if jac < min_jac]

    comments = (
        []
        if not mismatched
        else [f"{len(mismatched)} диаграмм вне темы (напр. '{mismatched[0][0]}' match={mismatched[0][1]})"]
    )
    return IntegritySignal(
        id="N.3",
        title="Диаграммы соответствуют теме раздела",
        passed=not mismatched,
        comments=comments,
        details={
            "mismatched": [{"heading": h, "jaccard": j} for h, j in mismatched[:3]],
            "note": "грубая эвристика по токенам; пограничные случаи -> на дидактическую ось",
        },
    )


# --- N.4 оборванные фразы / кавычки ---

def orphaned_quotes(md: str) -> IntegritySignal:
    """Баланс «» и отсутствие осиротевших закрывающих кавычек."""
    op, cl = md.count("«"), md.count("»")
    orphan = 0
    for m in re.finditer("»", md):
        if "«" not in md[max(0, m.start() - 200):m.start()]:
            orphan += 1
    bad = (op != cl) or orphan > 0
    comments = [f"«={op}, »={cl}, осиротевших »={orphan}"] if bad else []
    return IntegritySignal(
        id="N.4",
        title="Нет оборванных фраз/кавычек",
        passed=not bad,
        comments=comments,
        details={"open": op, "close": cl, "orphan": orphan},
    )


# --- N.5 единство идентификатора проекта ---

def project_id_unity(md: str) -> IntegritySignal:
    """В теле README единственный идентификатор проекта вида `PjM15_PublicSpeaking`."""
    ids = sorted(set(re.findall(r"\b[A-Za-z]{2,}\d+_[A-Za-z0-9_]+\b", md)))
    passed = len(ids) <= 1
    comments = [] if passed else [f"в теле несколько id: {ids}"]
    return IntegritySignal(
        id="N.5",
        title="Единый идентификатор проекта",
        passed=passed,
        comments=comments,
        details={"ids": ids},
    )


def all_integrity_signals(md: str) -> list[IntegritySignal]:
    """Все проверки целостности над полным markdown README."""
    return [
        table_integrity(md),
        verbatim_repetition(md),
        diagram_topic_match(md),
        orphaned_quotes(md),
        project_id_unity(md),
    ]
