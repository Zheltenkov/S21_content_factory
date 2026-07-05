"""Выборочная адъюдикация ложных срабатываний.

Модуль отделяет две разные задачи оценки качества:
gold-матчинг показывает, нашли ли мы размеченные ревьюером ошибки;
адъюдикация показывает, какая доля "ложных" находок на самом деле является
реальными дефектами, которые отсутствуют в неполной разметке.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Z_95 = 1.96
VALID_VERDICTS = {"real", "style", "wrong"}


@dataclass(frozen=True)
class CorpusTotals:
    """Итоги строгой оценки корпуса, нужные для пересчёта общей precision."""

    true_positive: int
    false_positive: int
    predicted_total: int | None = None


@dataclass(frozen=True)
class AdjudicationCandidate:
    """Одна находка, которую отдаём человеку на ручную проверку."""

    finding_id: str
    project: str
    criterion: str
    checker: str
    file_path: str
    line_range: str
    quote: str
    flagged_defect: str
    suggested_fix: str
    subtype: str


@dataclass(frozen=True)
class SamplingPlanItem:
    """План выборки для одного подтипа."""

    subtype: str
    population: int
    sample_size: int
    mode: str


@dataclass(frozen=True)
class AdjudicationSample:
    """Результат построения листа для ручной разметки."""

    checker: str
    candidates: list[AdjudicationCandidate]
    plan: list[SamplingPlanItem]


@dataclass(frozen=True)
class SubtypeScore:
    """Оценка precision внутри одного подтипа."""

    subtype: str
    population: int
    adjudicated: int
    real: int
    false_positive: int
    precision: float | None
    ci_low: float | None
    ci_high: float | None


@dataclass(frozen=True)
class AdjudicationScore:
    """Итог ручной адъюдикации с поправкой корпусной precision."""

    checker: str
    population_total: int
    adjudicated_total: int
    subtype_scores: list[SubtypeScore]
    stratified_precision: float | None
    stratified_margin_95: float | None
    estimated_true_false_positive: float | None
    original_micro_precision: float | None = None
    corrected_micro_precision: float | None = None
    corrected_false_positive: float | None = None
    warnings: list[str] = field(default_factory=list)


def build_adjudication_sample(
    fp_file: Path,
    *,
    checker: str,
    margin: float = 0.15,
    seed: int = 42,
    census_max: int = 10,
) -> AdjudicationSample:
    """Строит воспроизводимую стратифицированную выборку по подтипам чекера."""

    rows = _load_false_positive_rows(fp_file, checker)
    by_subtype: dict[str, list[AdjudicationCandidate]] = defaultdict(list)
    for row in rows:
        candidate = _candidate_from_false_positive_row(row)
        by_subtype[candidate.subtype].append(candidate)

    random_source = random.Random(seed)
    selected: list[AdjudicationCandidate] = []
    plan: list[SamplingPlanItem] = []
    for subtype, candidates in sorted(by_subtype.items(), key=lambda item: (-len(item[1]), item[0])):
        population = len(candidates)
        required = population if population <= census_max else sample_size(population, margin)
        chosen = candidates if required >= population else random_source.sample(candidates, required)
        selected.extend(chosen)
        plan.append(
            SamplingPlanItem(
                subtype=subtype,
                population=population,
                sample_size=len(chosen),
                mode="census" if len(chosen) >= population else "sample",
            )
        )

    random_source.shuffle(selected)
    return AdjudicationSample(checker=checker, candidates=selected, plan=plan)


def write_adjudication_sheet(sample: AdjudicationSample, out_path: Path) -> None:
    """Пишет CSV-лист, который можно отдать разметчику."""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "finding_id",
                "project",
                "criterion",
                "checker",
                "file",
                "line",
                "quote",
                "flagged_defect",
                "suggested_fix",
                "subtype_hidden",
                "verdict[real|style|wrong]",
                "note",
            ]
        )
        for item in sample.candidates:
            writer.writerow(
                [
                    item.finding_id,
                    item.project,
                    item.criterion,
                    item.checker,
                    item.file_path,
                    item.line_range,
                    item.quote,
                    item.flagged_defect,
                    item.suggested_fix,
                    item.subtype,
                    "",
                    "",
                ]
            )


def score_adjudication_sheet(
    sheet_file: Path,
    fp_file: Path,
    *,
    checker: str,
    corpus_totals: CorpusTotals | None = None,
) -> AdjudicationScore:
    """Считает precision по размеченному листу и, если возможно, корпусную поправку."""

    population = Counter(detect_issue_subtype(row["issue"]) for row in _load_false_positive_rows(fp_file, checker))
    population_total = sum(population.values())
    sheet_rows = _load_adjudication_sheet(sheet_file)
    verdict_column = _find_verdict_column(sheet_rows)

    by_subtype: dict[str, Counter[str]] = defaultdict(Counter)
    ignored_rows = 0
    for row in sheet_rows:
        verdict = (row.get(verdict_column) or "").strip().lower()
        if not verdict:
            continue
        if verdict not in VALID_VERDICTS:
            ignored_rows += 1
            continue
        subtype = (row.get("subtype_hidden") or "other").strip() or "other"
        by_subtype[subtype][verdict] += 1

    subtype_scores: list[SubtypeScore] = []
    weighted_precision = 0.0
    variance_sum = 0.0
    estimated_true_fp = 0.0
    missing_subtypes: list[str] = []
    adjudicated_total = 0

    for subtype, subtype_population in population.most_common():
        counts = by_subtype.get(subtype, Counter())
        real = counts["real"]
        adjudicated = real + counts["style"] + counts["wrong"]
        adjudicated_total += adjudicated
        if adjudicated == 0:
            missing_subtypes.append(subtype)
            subtype_scores.append(
                SubtypeScore(
                    subtype=subtype,
                    population=subtype_population,
                    adjudicated=0,
                    real=0,
                    false_positive=0,
                    precision=None,
                    ci_low=None,
                    ci_high=None,
                )
            )
            continue

        precision, ci_low, ci_high = wilson_interval(real, adjudicated)
        weight = subtype_population / population_total if population_total else 0.0
        weighted_precision += weight * precision
        # Если подтип размечен целиком, ошибки выборки внутри него нет.
        if adjudicated < subtype_population:
            variance_sum += (weight**2) * (precision * (1.0 - precision) / adjudicated)
        estimated_true_fp += subtype_population * (1.0 - precision)
        subtype_scores.append(
            SubtypeScore(
                subtype=subtype,
                population=subtype_population,
                adjudicated=adjudicated,
                real=real,
                false_positive=adjudicated - real,
                precision=precision,
                ci_low=ci_low,
                ci_high=ci_high,
            )
        )

    warnings: list[str] = []
    if ignored_rows:
        warnings.append(f"Проигнорированы строки с неизвестным verdict: {ignored_rows}.")
    if missing_subtypes:
        warnings.append("Не размечены подтипы: " + ", ".join(missing_subtypes) + ".")

    margin_95 = Z_95 * math.sqrt(variance_sum) if adjudicated_total and not missing_subtypes else None
    precision_value = weighted_precision if adjudicated_total and not missing_subtypes else None
    estimated_fp_value = estimated_true_fp if adjudicated_total and not missing_subtypes else None

    original_precision: float | None = None
    corrected_precision: float | None = None
    corrected_false_positive: float | None = None
    if corpus_totals and estimated_fp_value is not None:
        original_precision = _precision(corpus_totals.true_positive, corpus_totals.false_positive)
        other_false_positive = max(0.0, corpus_totals.false_positive - population_total)
        corrected_false_positive = other_false_positive + estimated_fp_value
        corrected_precision = _precision(corpus_totals.true_positive, corrected_false_positive)

    return AdjudicationScore(
        checker=checker,
        population_total=population_total,
        adjudicated_total=adjudicated_total,
        subtype_scores=subtype_scores,
        stratified_precision=precision_value,
        stratified_margin_95=margin_95,
        estimated_true_false_positive=estimated_fp_value,
        original_micro_precision=original_precision,
        corrected_micro_precision=corrected_precision,
        corrected_false_positive=corrected_false_positive,
        warnings=warnings,
    )


def infer_corpus_totals(fp_file: Path, aggregate_summary: Path | None = None) -> CorpusTotals | None:
    """Читает итоговые TP/FP из aggregate_summary.json рядом с выгрузкой."""

    summary_path = aggregate_summary or (fp_file.parent / "aggregate_summary.json")
    if not summary_path.exists():
        return None
    with summary_path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    try:
        return CorpusTotals(
            true_positive=int(data["true_positive"]),
            false_positive=int(data["false_positive"]),
            predicted_total=int(data["predicted_total"]) if data.get("predicted_total") is not None else None,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"Файл {summary_path} не похож на aggregate_summary.json.") from error


def format_sample_plan(sample: AdjudicationSample) -> str:
    """Формирует человекочитаемый план выборки."""

    lines = ["План выборки (подтип: N всего -> n в листе, режим):"]
    for item in sample.plan:
        lines.append(f"  {item.subtype:14s} {item.population:3d} -> {item.sample_size:3d}  ({item.mode})")
    lines.append(f"Итого в листе: {len(sample.candidates)} строк")
    return "\n".join(lines)


def format_score(score: AdjudicationScore) -> str:
    """Формирует текстовый отчёт по адъюдикации."""

    lines = [f"{'подтип':14s} {'N':>4} {'разм.':>5} {'real':>4} {'FP':>4} {'prec':>6}  95% CI"]
    for item in score.subtype_scores:
        if item.precision is None:
            lines.append(f"{item.subtype:14s} {item.population:4d} {'-':>5}  не размечено")
            continue
        lines.append(
            f"{item.subtype:14s} {item.population:4d} {item.adjudicated:5d} "
            f"{item.real:4d} {item.false_positive:4d} {item.precision:6.2f}  "
            f"[{item.ci_low:.2f}; {item.ci_high:.2f}]"
        )

    lines.append("")
    lines.append(f"Чекер {score.checker}:")
    if score.stratified_precision is None:
        lines.append("  precision не посчитана: разметьте хотя бы по одной строке каждого подтипа из листа.")
    else:
        margin = score.stratified_margin_95 or 0.0
        low = max(0.0, score.stratified_precision - margin)
        high = min(1.0, score.stratified_precision + margin)
        lines.append(
            f"  оценка precision (стратиф.): {score.stratified_precision:.3f}  "
            f"+/-{margin:.3f}  -> [{low:.3f}; {high:.3f}]"
        )
        lines.append(
            f"  настоящих FP оценочно: {score.estimated_true_false_positive:.0f} "
            f"из {score.population_total}"
        )

    if score.corrected_micro_precision is not None and score.corrected_false_positive is not None:
        lines.append("")
        lines.append("Корпусная micro precision:")
        lines.append(f"  как сейчас (все 'не в gold' = FP): {score.original_micro_precision:.3f}")
        lines.append(
            f"  с поправкой по этому чекеру:        {score.corrected_micro_precision:.3f}   "
            f"(FP -> {score.corrected_false_positive:.0f})"
        )

    for warning in score.warnings:
        lines.append(f"Предупреждение: {warning}")
    return "\n".join(lines)


def sample_size(population: int, margin: float, p: float = 0.5) -> int:
    """Размер выборки для доли с 95% доверием и поправкой на конечную совокупность."""

    if population <= 0:
        return 0
    if not 0 < margin < 1:
        raise ValueError("margin должен быть в интервале (0, 1).")
    n0 = (Z_95**2 * p * (1.0 - p)) / (margin**2)
    adjusted = n0 / (1.0 + (n0 - 1.0) / population)
    return min(population, math.ceil(adjusted))


def wilson_interval(successes: int, total: int) -> tuple[float, float, float]:
    """Интервал Уилсона для доли успехов."""

    if total <= 0:
        return 0.0, 0.0, 1.0
    p = successes / total
    denominator = 1.0 + Z_95**2 / total
    center = (p + Z_95**2 / (2.0 * total)) / denominator
    half_width = (Z_95 / denominator) * math.sqrt(p * (1.0 - p) / total + Z_95**2 / (4.0 * total**2))
    return p, max(0.0, center - half_width), min(1.0, center + half_width)


def detect_issue_subtype(issue_text: str) -> str:
    """Достаёт служебный подтип из последнего сегмента `quote | issue | fix | subtype`."""

    parts = [part.strip() for part in issue_text.split("|")]
    last = parts[-1] if parts else ""
    return last if re.fullmatch(r"[a-z_]+", last or "") else "other"


def split_issue_parts(issue_text: str) -> tuple[str, str, str, str]:
    """Разбирает строку найденной ошибки на цитату, дефект, правку и подтип."""

    parts = [part.strip() for part in issue_text.split("|")]
    subtype = detect_issue_subtype(issue_text)
    if subtype != "other" and parts:
        parts = parts[:-1]
    quote = parts[0] if parts else ""
    defect = parts[1] if len(parts) > 1 else ""
    suggested_fix = " | ".join(parts[2:]) if len(parts) > 2 else ""
    return quote, defect, suggested_fix, subtype


def _load_false_positive_rows(fp_file: Path, checker: str) -> list[dict[str, Any]]:
    with fp_file.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = []
        for row in csv.DictReader(stream):
            if row.get("checker") == checker:
                rows.append({"raw": row, "issue": _required_field(row, ("найденная ошибка", "found_issue", "issue"))})
        return rows


def _candidate_from_false_positive_row(row: dict[str, Any]) -> AdjudicationCandidate:
    raw = row["raw"]
    quote, defect, suggested_fix, subtype = split_issue_parts(row["issue"])
    location = _first_existing(raw, ("найденная строка/диапазон", "found_location", "location"))
    file_path, line_range = split_location(location)
    return AdjudicationCandidate(
        finding_id=_first_existing(raw, ("finding_id", "id")),
        project=_first_existing(raw, ("проект", "project")),
        criterion=_first_existing(raw, ("критерий_код", "criterion_code", "criterion")),
        checker=_first_existing(raw, ("checker",)),
        file_path=file_path,
        line_range=line_range,
        quote=quote,
        flagged_defect=defect,
        suggested_fix=suggested_fix,
        subtype=subtype,
    )


def split_location(value: str) -> tuple[str, str]:
    """Разделяет `README.md:10-12` на путь и строку, не ломая пустые значения."""

    if ":" not in value:
        return value, ""
    file_path, line_range = value.rsplit(":", 1)
    return file_path, line_range


def _load_adjudication_sheet(sheet_file: Path) -> list[dict[str, str]]:
    with sheet_file.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _find_verdict_column(rows: list[dict[str, str]]) -> str:
    if not rows:
        raise ValueError("Лист адъюдикации пуст.")
    for column in rows[0]:
        if column.startswith("verdict"):
            return column
    raise ValueError("В листе нет колонки verdict.")


def _first_existing(row: dict[str, str], aliases: Iterable[str]) -> str:
    for alias in aliases:
        value = row.get(alias)
        if value is not None:
            return value
    return ""


def _required_field(row: dict[str, str], aliases: Iterable[str]) -> str:
    value = _first_existing(row, aliases)
    if value:
        return value
    raise ValueError(f"Не найдена обязательная колонка: {', '.join(aliases)}.")


def _precision(true_positive: float, false_positive: float) -> float:
    denominator = true_positive + false_positive
    return true_positive / denominator if denominator else 0.0
