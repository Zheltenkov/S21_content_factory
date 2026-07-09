"""Export files for audit corpus evaluation summaries."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from content_factory.audit.domain import CRITERION_LABELS, Criterion


def write_corpus_evaluation(summary: Any, output_dir: Path) -> None:
    """Записывает машинный и табличный отчёт оценки."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "corpus_evaluation.json").write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_matches_csv(summary.matches, output_dir / "corpus_evaluation_main.csv")
    _write_metrics_csv(summary, output_dir / "corpus_evaluation_by_criterion.csv")
    _write_overview_metrics_csv(summary, output_dir / "corpus_evaluation_overview_by_criterion.csv")
    _write_prediction_slice_metrics_csv(summary.checker_metrics, output_dir / "corpus_evaluation_by_checker.csv")
    _write_prediction_slice_metrics_csv(summary.checker_group_metrics, output_dir / "corpus_evaluation_by_checker_group.csv")
    _write_false_negative_analysis_csv(summary.false_negative_analysis, output_dir / "corpus_false_negative_analysis.csv")
    _write_detailed_false_positive_csv(summary.detailed_false_positive_items, output_dir / "corpus_false_positive_detailed.csv")
    _write_items_csv(summary.false_negative_items, output_dir / "corpus_false_negative.csv")
    _write_items_csv(summary.false_positive_items, output_dir / "corpus_false_positive.csv")



def _format_range(start: int | None, end: int | None) -> str:
    """Format a source line range for CSV exports."""

    if start is None:
        return ""
    if end is None or end == start:
        return str(start)
    return f"{start}-{end}"


def _format_prediction_range(item: Any) -> str:
    """Format a predicted item line range for CSV exports."""

    if getattr(item, "file_path", None):
        line_range = _format_range(getattr(item, "line_start", None), getattr(item, "line_end", None))
        return f"{item.file_path}:{line_range}" if line_range else str(item.file_path)
    return _format_range(getattr(item, "line_start", None), getattr(item, "line_end", None))


def _criterion_label(criterion_value: str) -> str:
    """Return a human-readable criterion label."""

    try:
        return CRITERION_LABELS[Criterion(criterion_value)]
    except ValueError:
        return criterion_value


def _write_metrics_csv(summary: Any, output_path: Path) -> None:
    """Пишет основные детальные метрики по критериям в CSV."""

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "criterion",
                "label",
                "gold_total",
                "predicted_total",
                "true_positive",
                "false_positive",
                "false_negative",
                "precision",
                "recall",
                "f1_score",
            ],
        )
        writer.writeheader()
        for item in summary.per_criterion:
            writer.writerow(item.model_dump(mode="json"))


def _write_overview_metrics_csv(summary: Any, output_path: Path) -> None:
    """Пишет старую обзорную метрику проект × критерий в отдельный CSV."""

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "criterion",
                "label",
                "gold_total",
                "predicted_total",
                "true_positive",
                "false_positive",
                "false_negative",
                "precision",
                "recall",
                "f1_score",
            ],
        )
        writer.writeheader()
        for item in summary.overview_per_criterion:
            writer.writerow(item.model_dump(mode="json"))


def _write_prediction_slice_metrics_csv(items: Sequence[Any], output_path: Path) -> None:
    """Пишет метрики по чекерам или группам чекеров."""

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "slice_name",
                "label",
                "predicted_total",
                "true_positive",
                "false_positive",
                "precision",
                "false_positive_share",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(item.model_dump(mode="json"))


def _write_matches_csv(items: Sequence[Any], output_path: Path) -> None:
    """Пишет главную таблицу построчного сопоставления."""

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "проект",
                "project_id",
                "критерий",
                "критерий_код",
                "строка/диапазон",
                "строка_excel",
                "текст эталонной ошибки",
                "найденная ошибка",
                "найденная строка/диапазон",
                "finding_id",
                "checker",
                "тип совпадения",
                "засчитано",
                "score",
                "причина, почему засчитали",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "проект": item.project,
                    "project_id": item.project_id,
                    "критерий": item.label,
                    "критерий_код": item.criterion,
                    "строка/диапазон": item.gold_line_range,
                    "строка_excel": item.gold_row_number,
                    "текст эталонной ошибки": item.gold_text,
                    "найденная ошибка": item.found_text,
                    "найденная строка/диапазон": item.found_line_range,
                    "finding_id": item.found_finding_id or "",
                    "checker": item.found_checker or "",
                    "тип совпадения": item.match_type,
                    "засчитано": "да" if item.counted else "нет",
                    "score": item.match_score,
                    "причина, почему засчитали": item.reason,
                }
            )


def _write_false_negative_analysis_csv(items: Sequence[Any], output_path: Path) -> None:
    """Пишет пропущенные ошибки с инженерной причиной пропуска."""

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "проект",
                "project_id",
                "критерий",
                "критерий_код",
                "строка/диапазон",
                "текст эталонной ошибки",
                "ближайшая находка",
                "nearest_checker",
                "nearest_match_type",
                "nearest_score",
                "reason_code",
                "причина пропуска",
                "следующий шаг",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "проект": item.project,
                    "project_id": item.project_id,
                    "критерий": item.label,
                    "критерий_код": item.criterion,
                    "строка/диапазон": item.gold_line_range,
                    "текст эталонной ошибки": item.gold_text,
                    "ближайшая находка": item.nearest_finding_id or "",
                    "nearest_checker": item.nearest_checker or "",
                    "nearest_match_type": item.nearest_match_type,
                    "nearest_score": item.nearest_score,
                    "reason_code": item.reason_code,
                    "причина пропуска": item.reason_label,
                    "следующий шаг": item.next_step,
                }
            )


def _write_detailed_false_positive_csv(items: Sequence[Any], output_path: Path) -> None:
    """Пишет детальные ложные срабатывания, которые не сопоставились с эталоном."""

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "проект",
                "project_id",
                "критерий",
                "критерий_код",
                "найденная строка/диапазон",
                "найденная ошибка",
                "finding_id",
                "checker",
            ],
        )
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "проект": item.project,
                    "project_id": item.project_id,
                    "критерий": _criterion_label(item.criterion),
                    "критерий_код": item.criterion,
                    "найденная строка/диапазон": _format_prediction_range(item),
                    "найденная ошибка": item.found_text,
                    "finding_id": item.finding_id,
                    "checker": item.checker_name,
                }
            )


def _write_items_csv(items: Sequence[Any], output_path: Path) -> None:
    """Пишет ошибки сравнения: ложные пропуски или ложные срабатывания."""

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["project_id", "criterion", "label"])
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    "project_id": item.project_id,
                    "criterion": item.criterion,
                    "label": _criterion_label(item.criterion),
                }
            )


