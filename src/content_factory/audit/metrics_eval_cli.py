"""Командная строка для оценки аудита на корпусе `metrics`."""

from __future__ import annotations

import argparse
from pathlib import Path

from content_factory.audit.cli import DEFAULT_OPENROUTER_FACT_MODEL, DEFAULT_OPENROUTER_MODEL, DEFAULT_OPENROUTER_TECH_MODEL
from content_factory.audit.corpus_evaluation import evaluate_corpus_report, write_corpus_evaluation
from content_factory.audit.domain import AuditSettings
from content_factory.audit.env import get_env_value, load_env_file
from content_factory.audit.exporters import write_report
from content_factory.audit.orchestrator import AuditRunner


def main(argv: list[str] | None = None) -> int:
    """Запускает аудит корпуса и считает precision/recall/F1 по Excel-разметке."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    metrics_dir = args.metrics_dir.expanduser().resolve()
    gold_xlsx = args.gold_xlsx.expanduser().resolve() if args.gold_xlsx else _find_gold_xlsx(metrics_dir)
    output_dir = args.output.expanduser().resolve()
    audit_output_dir = output_dir / "audit_report"

    env_file_values = load_env_file(Path(".env"))
    from content_factory.audit import aligner as _aligner

    provider_url = _aligner.PROVIDER_URLS[args.provider]
    if args.provider == "polza":
        provider_key = get_env_value(("POLZA_AI_API_KEY", "POLZA_API_KEY"), env_file_values)
    else:
        provider_key = get_env_value(("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), env_file_values)
    settings = AuditSettings(
        input_path=metrics_dir,
        output_path=audit_output_dir,
        allow_network=not args.skip_network,
        use_model=args.use_model,
        include_unknown=not args.hide_unknown,
        expected_languages=args.expected_languages if args.expected_languages is not None else ("RUS", "ENG", "UZ", "TG"),
        max_file_bytes=args.max_file_bytes,
        link_timeout_seconds=args.link_timeout,
        min_image_width=args.min_image_width,
        min_image_height=args.min_image_height,
        openrouter_api_key=provider_key,
        openrouter_base_url=provider_url,
        lean_checkers=args.lean,
        openrouter_model=args.openrouter_model
        or get_env_value(("OPENROUTER_MODEL", "OPEN_ROUTER_MODEL"), env_file_values)
        or DEFAULT_OPENROUTER_MODEL,
        openrouter_fact_model=args.openrouter_fact_model
        or get_env_value(("OPENROUTER_FACT_MODEL", "OPEN_ROUTER_FACT_MODEL"), env_file_values)
        or DEFAULT_OPENROUTER_FACT_MODEL,
        openrouter_tech_model=args.openrouter_tech_model
        or get_env_value(("OPENROUTER_TECH_MODEL", "OPEN_ROUTER_TECH_MODEL"), env_file_values)
        or DEFAULT_OPENROUTER_TECH_MODEL,
    )

    judge_backend = args.provider if args.judge_backend in ("openrouter", "polza") else args.judge_backend
    report = AuditRunner(settings).run()
    write_report(report, audit_output_dir)
    summary = evaluate_corpus_report(
        report,
        gold_xlsx,
        matcher=args.matcher,
        judge_backend=judge_backend,
        judge_model=args.judge_model,
        judge_api_key=settings.openrouter_api_key,
        judge_topk=args.judge_topk,
        judge_cache_path=str(args.judge_cache) if args.judge_cache else None,
        defects_only=args.defects_only,
        confidence_floor=args.confidence_floor,
        mirror_dedupe=args.mirror_dedupe,
        cap_repetitive=args.cap_repetitive,
    )
    write_corpus_evaluation(summary, output_dir)
    _print_summary(summary, output_dir)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Описывает параметры пакетной оценки."""

    parser = argparse.ArgumentParser(description="Оценка аудита на папке metrics и Excel-разметке.")
    parser.add_argument("--metrics-dir", type=Path, default=Path("metrics"), help="Папка с тестовыми проектами и Excel.")
    parser.add_argument("--gold-xlsx", type=Path, default=None, help="Excel-файл с эталонными ошибками.")
    parser.add_argument("--output", type=Path, default=Path(".tmp") / "metrics_evaluation", help="Папка результата.")
    parser.add_argument("--skip-network", action="store_true", help="Не проверять внешние ссылки по сети.")
    parser.add_argument("--use-model", action="store_true", help="Включить модельные проверки через OpenRouter.")
    parser.add_argument("--hide-unknown", action="store_true", help="Исключить находки 'нужна проверка' из оценки.")
    parser.add_argument("--expected-languages", default=None, help="Ожидаемые языки через запятую.")
    parser.add_argument("--max-file-bytes", type=int, default=2_000_000, help="Максимальный размер текстового файла.")
    parser.add_argument("--link-timeout", type=float, default=8.0, help="Таймаут проверки ссылки.")
    parser.add_argument("--min-image-width", type=int, default=640, help="Минимальная ширина изображения.")
    parser.add_argument("--min-image-height", type=int, default=360, help="Минимальная высота изображения.")
    parser.add_argument("--openrouter-model", default=None, help="Модель OpenRouter для общих модельных проверок.")
    parser.add_argument("--openrouter-fact-model", default=None, help="Модель OpenRouter для фактологической проверки.")
    parser.add_argument("--openrouter-tech-model", default=None, help="Модель OpenRouter для проверки технологий.")
    parser.add_argument(
        "--matcher",
        choices=["strict", "anchor_judge"],
        default="strict",
        help="Стратегия сопоставления эталона и находок: strict (по строке/тексту) или anchor_judge (якоря + судья).",
    )
    parser.add_argument(
        "--provider",
        choices=["openrouter", "polza"],
        default="openrouter",
        help="Провайдер LLM для аудита и судьи: openrouter или polza (OpenAI-совместимый).",
    )
    parser.add_argument(
        "--judge-backend",
        choices=["offline", "openrouter", "polza"],
        default="offline",
        help="Бэкенд судьи для matcher=anchor_judge: offline (без сети), openrouter или polza.",
    )
    parser.add_argument("--judge-model", default=None, help="Модель OpenRouter для судьи (по умолчанию qwen coder).")
    parser.add_argument("--judge-topk", type=int, default=6, help="Сколько кандидатов на эталонный кейс отдавать судье.")
    parser.add_argument("--judge-cache", type=Path, default=None, help="Путь к JSON-кэшу решений судьи.")
    parser.add_argument("--defects-only", action="store_true", help="Считать recall только по дефектам, без субъективных мнений.")
    parser.add_argument("--confidence-floor", type=float, default=0.0, help="Отсекать находки ниже этой уверенности (precision).")
    parser.add_argument("--mirror-dedupe", action="store_true", help="Схлопывать дубли зеркальных файлов RU/EN и README/check-list.")
    parser.add_argument("--cap-repetitive", type=int, default=0, help="Лимит однотипных readability-придирок на единицу (0 = без лимита).")
    parser.add_argument("--lean", action="store_true", help="Экономный режим: без дорогого Perplexity-фактчека и нулевых по точности правил.")
    return parser


def _find_gold_xlsx(metrics_dir: Path) -> Path:
    """Находит файл разметки, предпочитая очищенный атомарный (xlsx или csv)."""

    candidates = sorted(metrics_dir.glob("*.xlsx")) + sorted(metrics_dir.glob("*.csv"))
    if not candidates:
        raise FileNotFoundError(f"В папке {metrics_dir} не найден файл разметки (.xlsx/.csv).")
    for keyword in ("очищ", "атомар", "atomic", "clean"):
        for f in candidates:
            if keyword in f.name.lower():
                return f
    gold = [f for f in candidates if "проект" in f.name.lower() or "gold" in f.name.lower()]
    if gold:
        return gold[0]
    return candidates[0]


def _print_summary(summary, output_dir: Path) -> None:
    """Печатает короткие итоговые метрики."""

    print("Main detailed metric:")
    print(f"Gold cases: {summary.gold_total}")
    print(f"Predicted cases in gold scope: {summary.predicted_total}")
    print(f"TP/FP/FN: {summary.true_positive}/{summary.false_positive}/{summary.false_negative}")
    print(f"Precision: {summary.precision}")
    print(f"Recall: {summary.recall}")
    print(f"F1-score: {summary.f1_score}")
    if summary.actionable_metrics is not None:
        print(
            "Actionable precision: "
            f"{summary.actionable_metrics.precision} "
            f"({summary.actionable_metrics.true_positive}/{summary.actionable_metrics.predicted_total})"
        )
    if summary.cost_quality is not None:
        print(
            "Cost per gold TP: "
            f"{summary.cost_quality.cost_per_gold_true_positive}; "
            f"total cost: ${summary.cost_quality.cost_usd}"
        )
    if summary.false_negative_reason_counts:
        print(f"FN reasons: {summary.false_negative_reason_counts}")
    print(f"Macro precision/recall/F1: {summary.macro_precision}/{summary.macro_recall}/{summary.macro_f1_score}")
    print("Overview project × criterion metric:")
    print(
        f"Gold/predicted: {summary.overview_gold_total}/{summary.overview_predicted_total}; "
        f"TP/FP/FN: {summary.overview_true_positive}/{summary.overview_false_positive}/{summary.overview_false_negative}"
    )
    print(
        "Precision/recall/F1: "
        f"{summary.overview_precision}/{summary.overview_recall}/{summary.overview_f1_score}"
    )
    print(
        "Macro precision/recall/F1: "
        f"{summary.overview_macro_precision}/{summary.overview_macro_recall}/{summary.overview_macro_f1_score}"
    )
    print(f"Отчёты: {output_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
