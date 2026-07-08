"""Rule-based market-fit signal extraction for audit checks."""

from __future__ import annotations

import re
from typing import Any, cast

from content_factory.audit.domain import ContentUnit, Severity, TextLocation, Verdict


def _market_fit_signals(unit: ContentUnit, patterns: dict[str, tuple[str, ...]]) -> dict[str, dict[str, object]]:
    """Find data, business-context and success-metric signals."""

    signals: dict[str, dict[str, object]] = {
        name: {"present": False, "matches": [], "source": "rules"} for name in patterns
    }
    for file in unit.files:
        if file.kind not in {"readme", "material", "text"}:
            continue
        for line_number, line in enumerate(file.text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if _is_market_fit_noise_line(stripped):
                continue
            lowered = stripped.lower()
            for signal_name, signal_patterns in patterns.items():
                if signals[signal_name]["present"]:
                    continue
                if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in signal_patterns):
                    signals[signal_name]["present"] = True
                    signals[signal_name]["matches"] = [
                        {
                            "file_path": file.relative_path,
                            "line_start": line_number,
                            "text": stripped[:220],
                        }
                    ]
    if not signals["real_data"]["present"]:
        _mark_dataset_files(unit, signals)
    return signals


def _mark_dataset_files(unit: ContentUnit, signals: dict[str, dict[str, object]]) -> None:
    """Treat market-like dataset files as real-data evidence."""

    for file in unit.files:
        if _looks_like_market_dataset_file(file.relative_path):
            signals["real_data"]["present"] = True
            signals["real_data"]["matches"] = [
                {"file_path": file.relative_path, "line_start": None, "text": "Найден файл или папка данных."}
            ]
            return


def _looks_like_market_dataset_file(relative_path: str) -> bool:
    """Separate market datasets from fixtures, reports and expected outputs."""

    lower_path = relative_path.lower()
    data_suffixes = (".csv", ".xlsx", ".parquet", ".jsonl")
    if not lower_path.endswith(data_suffixes):
        return False
    if _is_market_fit_noise_path(lower_path):
        return False
    path_parts = lower_path.split("/")
    if any(part in {"data", "dataset", "datasets"} for part in path_parts):
        return True
    return bool(
        re.search(
            r"(customer|client|sales|transaction|order|churn|bank|retail|market|user|billing|claim|loan|price|product|"
            r"клиент|продаж|транзакц|заказ|отток|банк|рынок|пользовател|заявк|кредит|товар)",
            lower_path,
            flags=re.IGNORECASE,
        )
    )


def _is_market_fit_noise_path(lower_path: str) -> bool:
    """Filter technical data that does not prove market relevance."""

    return bool(
        re.search(
            r"(^|/)(test|tests|fixture|fixtures|mock|mocks|expected|actual|output|outputs|report|reports|coverage|"
            r"autotest|golden)(/|\.|_|-)",
            lower_path,
        )
    )


def _is_market_fit_noise_line(value: str) -> bool:
    """Filter fixture/test/output lines unless they also carry business meaning."""

    lowered = value.lower()
    technical_markers = (
        "autotest",
        "unit test",
        "integration test",
        "fixture",
        "mock",
        "expected output",
        "correct output",
        "test data",
        "synthetic data",
        "toy dataset",
        "тестов",
        "автотест",
        "фикстур",
        "мок",
        "ожидаем",
        "стандартн",
        "синтетическ",
        "игрушечн",
    )
    if not any(marker in lowered for marker in technical_markers):
        return False
    business_markers = (
        "business",
        "customer",
        "client",
        "market",
        "revenue",
        "retention",
        "churn",
        "бизнес",
        "клиент",
        "заказчик",
        "рынок",
        "выруч",
        "удержан",
        "отток",
    )
    return not any(marker in lowered for marker in business_markers)


def _merge_market_signals(
    signals: dict[str, dict[str, object]],
    model_item: dict[str, Any] | None,
) -> dict[str, dict[str, object]]:
    """Merge rule signals and model refinement without losing evidence."""

    merged = {
        key: {"present": bool(value["present"]), "matches": list(cast("list[Any]", value["matches"])), "source": value["source"]}
        for key, value in signals.items()
    }
    if model_item is None:
        return merged
    for key in ("real_data", "business_context", "success_metrics"):
        value = model_item.get(key)
        if isinstance(value, bool) and value:
            merged[key]["present"] = True
            merged[key]["source"] = "model" if not merged[key]["matches"] else "rules+model"
    return merged


def _market_fit_signal_count(signals: dict[str, dict[str, object]]) -> int:
    """Count application signals before model refinement."""

    return sum(1 for item in signals.values() if bool(item.get("present")))


def _market_fit_verdict(score: int) -> tuple[Verdict, Severity]:
    """Assign baseline verdict from the three sub-checks."""

    if score >= 3:
        return Verdict.PASS, Severity.INFO
    if score >= 1:
        return Verdict.WARNING, Severity.MINOR
    return Verdict.WARNING, Severity.MAJOR


def _market_fit_evidence(signals: dict[str, dict[str, object]], labels: dict[str, str]) -> str:
    """Build readable evidence for the three sub-checks."""

    parts: list[str] = []
    for key, label in labels.items():
        signal = signals[key]
        status = "есть" if signal["present"] else "нет"
        detail = ""
        matches = signal.get("matches")
        if isinstance(matches, list) and matches:
            first = matches[0]
            if isinstance(first, dict):
                location = first.get("file_path") or ""
                line = first.get("line_start")
                text = first.get("text") or ""
                detail = f" ({location}{':' + str(line) if line else ''}: {text})"
        parts.append(f"{label}: {status}{detail}")
    return "; ".join(parts)


def _market_fit_recommendation(signals: dict[str, dict[str, object]], model_item: dict[str, Any] | None) -> str:
    """Build recommendation from missing market-fit signals."""

    if model_item is not None:
        recommendation = _optional_text(model_item.get("recommendation"))
        if recommendation:
            return recommendation
    missing = [key for key, value in signals.items() if not value["present"]]
    if not missing:
        return "Действий не требуется: данные, бизнес-контекст и метрики/требования найдены."
    mapping = {
        "real_data": "добавить датасет или ссылку на реальные данные",
        "business_context": "описать бизнес-проблему, заказчика или целевую аудиторию",
        "success_metrics": "зафиксировать бизнес-метрики, ограничения или требования к результату",
    }
    return "Усилить прикладной контекст: " + "; ".join(mapping[key] for key in missing) + "."


def _first_market_location(signals: dict[str, dict[str, object]]) -> TextLocation | None:
    """Return the first location where a market-fit signal was found."""

    for signal in signals.values():
        matches = signal.get("matches")
        if not isinstance(matches, list) or not matches:
            continue
        first = matches[0]
        if not isinstance(first, dict) or not first.get("file_path"):
            continue
        line = first.get("line_start") if isinstance(first.get("line_start"), int) else None
        return TextLocation(file_path=str(first["file_path"]), line_start=line, line_end=line)
    return None


def _optional_text(value: object) -> str | None:
    """Normalize optional model text without depending on checks.py internals."""

    if value is None:
        return None
    text = str(value).strip()
    return text or None
