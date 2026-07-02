"""Командная строка для аудита учебного контента."""

from __future__ import annotations

import argparse
from pathlib import Path

from content_audit.domain import AuditSettings
from content_audit.env import get_env_value, load_env_file
from content_audit.evaluation import write_evaluation
from content_audit.exporters import write_report
from content_audit.orchestrator import AuditRunner

DEFAULT_OPENROUTER_MODEL = "openai/gpt-5.4-mini"
DEFAULT_OPENROUTER_FACT_MODEL = "perplexity/sonar"
DEFAULT_OPENROUTER_TECH_MODEL = "qwen/qwen3-coder"


def main(argv: list[str] | None = None) -> int:
    """Разбираем аргументы, запускаем аудит и записываем отчёты."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    env_file_values = load_env_file(Path(".env"))
    openrouter_api_key = get_env_value(("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY"), env_file_values)
    openrouter_model = (
        args.openrouter_model
        or get_env_value(("OPENROUTER_MODEL", "OPEN_ROUTER_MODEL"), env_file_values)
        or DEFAULT_OPENROUTER_MODEL
    )
    openrouter_fact_model = (
        args.openrouter_fact_model
        or get_env_value(("OPENROUTER_FACT_MODEL", "OPEN_ROUTER_FACT_MODEL"), env_file_values)
        or DEFAULT_OPENROUTER_FACT_MODEL
    )
    openrouter_tech_model = (
        args.openrouter_tech_model
        or get_env_value(("OPENROUTER_TECH_MODEL", "OPEN_ROUTER_TECH_MODEL"), env_file_values)
        or DEFAULT_OPENROUTER_TECH_MODEL
    )
    settings = AuditSettings(
        input_path=args.input,
        output_path=args.output,
        allow_network=not args.skip_network,
        use_model=args.use_model,
        include_unknown=not args.hide_unknown,
        expected_languages=args.expected_languages if args.expected_languages is not None else ("RUS", "ENG", "UZ", "TG"),
        max_file_bytes=args.max_file_bytes,
        link_timeout_seconds=args.link_timeout,
        min_image_width=args.min_image_width,
        min_image_height=args.min_image_height,
        openrouter_api_key=openrouter_api_key,
        openrouter_model=openrouter_model,
        openrouter_fact_model=openrouter_fact_model,
        openrouter_tech_model=openrouter_tech_model,
    )

    report = AuditRunner(settings).run()
    write_report(report, settings.output_path)
    if args.gold:
        write_evaluation(report, args.gold.expanduser().resolve(), settings.output_path / "evaluation.json")
    _print_summary(report.summary, settings.output_path)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """Описываем интерфейс запуска."""

    parser = argparse.ArgumentParser(description="Аудит учебного контента по локальной папке проекта.")
    parser.add_argument("--input", required=True, type=Path, help="Путь к папке проекта или каталогу проектов.")
    parser.add_argument("--output", required=True, type=Path, help="Папка для отчётов.")
    parser.add_argument("--skip-network", action="store_true", help="Не проверять внешние ссылки по сети.")
    parser.add_argument("--use-model", action="store_true", help="Включить модельные проверки через OpenRouter.")
    parser.add_argument(
        "--openrouter-model",
        default=None,
        help=f"Модель OpenRouter для модельных проверок. По умолчанию: {DEFAULT_OPENROUTER_MODEL}.",
    )
    parser.add_argument(
        "--openrouter-fact-model",
        default=None,
        help=f"Модель OpenRouter для фактологической проверки. По умолчанию: {DEFAULT_OPENROUTER_FACT_MODEL}.",
    )
    parser.add_argument(
        "--openrouter-tech-model",
        default=None,
        help=f"Модель OpenRouter для проверки актуальности технологий. По умолчанию: {DEFAULT_OPENROUTER_TECH_MODEL}.",
    )
    parser.add_argument("--hide-unknown", action="store_true", help="Не включать случаи с вердиктом 'нужна проверка'.")
    parser.add_argument(
        "--expected-languages",
        default=None,
        help="Ожидаемые языки через запятую, например RUS,ENG,UZ,TG. Пустая строка отключает политику.",
    )
    parser.add_argument("--max-file-bytes", type=int, default=2_000_000, help="Максимальный размер текстового файла.")
    parser.add_argument("--link-timeout", type=float, default=8.0, help="Таймаут проверки ссылки в секундах.")
    parser.add_argument("--min-image-width", type=int, default=640, help="Минимальная ширина изображения.")
    parser.add_argument("--min-image-height", type=int, default=360, help="Минимальная высота изображения.")
    parser.add_argument("--gold", type=Path, default=None, help="Эталонная JSON/CSV разметка для расчёта метрик качества.")
    return parser


def _print_summary(summary, output_path: Path) -> None:
    """Печатаем короткий итог в терминал."""

    print(f"Единиц контента: {summary.units_total}")
    print(f"Файлов проверено: {summary.files_total}")
    print(f"Найденных случаев: {summary.findings_total}")
    print(f"Отчёты: {output_path}")
    if summary.warnings:
        print("Предупреждения:")
        for warning in summary.warnings:
            print(f"- {warning}")
