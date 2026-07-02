"""Командная строка для выборочной адъюдикации ложных срабатываний."""

from __future__ import annotations

import argparse
from pathlib import Path

from content_factory.audit.adjudication import (
    CorpusTotals,
    build_adjudication_sample,
    format_sample_plan,
    format_score,
    infer_corpus_totals,
    score_adjudication_sheet,
    write_adjudication_sheet,
)


def main(argv: list[str] | None = None) -> int:
    """Точка входа `audit-adjudication`."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "sample":
        return _sample(args)
    if args.command == "score":
        return _score(args)
    parser.error("Укажите команду: sample или score.")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    """Описывает команды построения листа и подсчёта результата."""

    parser = argparse.ArgumentParser(description="Адъюдикация ложных срабатываний чекеров.")
    subparsers = parser.add_subparsers(dest="command")

    sample_parser = subparsers.add_parser("sample", help="Собрать лист для ручной разметки.")
    sample_parser.add_argument("--input", required=True, type=Path, help="false_positive_detailed_all.csv.")
    sample_parser.add_argument("--checker", default="spelling_wording_checker", help="Чекер для выборки.")
    sample_parser.add_argument("--margin", type=float, default=0.15, help="Желаемая точность доли для крупных подтипов.")
    sample_parser.add_argument("--seed", type=int, default=42, help="Семя воспроизводимой выборки.")
    sample_parser.add_argument("--census-max", type=int, default=10, help="Подтипы до этого размера размечаются целиком.")
    sample_parser.add_argument("--out", default=Path("adjudication_sheet.csv"), type=Path, help="Куда записать лист.")

    score_parser = subparsers.add_parser("score", help="Посчитать precision по размеченному листу.")
    score_parser.add_argument("--sheet", required=True, type=Path, help="Заполненный лист адъюдикации.")
    score_parser.add_argument("--fp-file", required=True, type=Path, help="Исходный false_positive_detailed_all.csv.")
    score_parser.add_argument("--checker", default="spelling_wording_checker", help="Чекер, который оцениваем.")
    score_parser.add_argument(
        "--aggregate-summary",
        type=Path,
        default=None,
        help="aggregate_summary.json. Если не задан, ищется рядом с --fp-file.",
    )
    score_parser.add_argument("--corpus-tp", type=int, default=None, help="TP корпуса, если нет aggregate_summary.json.")
    score_parser.add_argument("--corpus-fp", type=int, default=None, help="FP корпуса, если нет aggregate_summary.json.")
    score_parser.add_argument("--corpus-predicted", type=int, default=None, help="Всего предсказаний корпуса.")
    return parser


def _sample(args: argparse.Namespace) -> int:
    """Строит лист для разметки."""

    sample = build_adjudication_sample(
        args.input,
        checker=args.checker,
        margin=args.margin,
        seed=args.seed,
        census_max=args.census_max,
    )
    write_adjudication_sheet(sample, args.out)
    print(format_sample_plan(sample))
    print(f"Лист записан: {args.out}")
    print("Размечайте verdict: real / style / wrong.")
    return 0


def _score(args: argparse.Namespace) -> int:
    """Считает результат размеченного листа."""

    corpus_totals = _resolve_corpus_totals(args)
    score = score_adjudication_sheet(args.sheet, args.fp_file, checker=args.checker, corpus_totals=corpus_totals)
    print(format_score(score))
    return 0


def _resolve_corpus_totals(args: argparse.Namespace) -> CorpusTotals | None:
    """Берёт корпусные итоги из JSON или явных параметров."""

    inferred = infer_corpus_totals(args.fp_file, args.aggregate_summary)
    if inferred is not None:
        return inferred
    if args.corpus_tp is not None and args.corpus_fp is not None:
        return CorpusTotals(
            true_positive=args.corpus_tp,
            false_positive=args.corpus_fp,
            predicted_total=args.corpus_predicted,
        )
    return None


if __name__ == "__main__":
    raise SystemExit(main())
