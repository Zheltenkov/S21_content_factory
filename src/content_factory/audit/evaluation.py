"""Каркас расчёта метрик качества по размеченной выборке."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from content_factory.audit.domain import AuditReport, Criterion, Severity


class EvaluationItem(BaseModel):
    """Одна эталонная или предсказанная находка для сравнения."""

    unit_id: str
    criterion: str
    severity: str | None = None
    file_path: str | None = None
    line_start: int | None = None

    def key(self, strict_severity: bool = False) -> tuple[object, ...]:
        """Ключ сопоставления с опциональным учётом критичности."""

        base: tuple[object, ...] = (self.unit_id, self.criterion, self.file_path or "", self.line_start or 0)
        if strict_severity:
            return (*base, self.severity or "")
        return base

    def scope_key(self) -> tuple[str, str]:
        """Базовая область сравнения: одна единица и один критерий."""

        return (self.unit_id, self.criterion)


class EvaluationSummary(BaseModel):
    """Метрики приёмки для текущего отчёта."""

    gold_total: int
    predicted_total: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    critical_recall: float
    false_positive_rate: float
    notes: list[str] = Field(default_factory=list)


def evaluate_report(report: AuditReport, gold_path: Path) -> EvaluationSummary:
    """Сравниваем отчёт с эталонной JSON/CSV разметкой."""

    gold_items = _load_gold_items(gold_path)
    predicted_items = _items_from_report(report)
    matches = _match_items(gold_items, predicted_items)
    matched_gold = {gold_index for gold_index, _ in matches}
    matched_predicted = {predicted_index for _, predicted_index in matches}
    true_positive = len(matches)
    false_positive = len(predicted_items) - len(matched_predicted)
    false_negative = len(gold_items) - len(matched_gold)
    critical_gold = {index for index, item in enumerate(gold_items) if item.severity == Severity.CRITICAL.value}
    critical_found = len(critical_gold & matched_gold)

    return EvaluationSummary(
        gold_total=len(gold_items),
        predicted_total=len(predicted_items),
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=_safe_ratio(true_positive, true_positive + false_positive),
        recall=_safe_ratio(true_positive, true_positive + false_negative),
        critical_recall=_safe_ratio(critical_found, len(critical_gold)),
        false_positive_rate=_safe_ratio(false_positive, len(predicted_items)),
        notes=[
            "Метрики считаются через мягкое сопоставление unit_id + criterion.",
            "Файл учитывается только если он заполнен в эталоне; пустой файл в gold не штрафует находку.",
            "Строка засчитывается при точном совпадении или отклонении до двух строк; пустая строка в gold игнорируется.",
        ],
    )


def _match_items(gold_items: list[EvaluationItem], predicted_items: list[EvaluationItem]) -> list[tuple[int, int]]:
    """Сопоставляет эталонные и найденные ошибки один-к-одному."""

    candidates: list[tuple[float, int, int]] = []
    for gold_index, gold in enumerate(gold_items):
        for predicted_index, predicted in enumerate(predicted_items):
            score = _match_score(gold, predicted)
            if score > 0:
                candidates.append((score, gold_index, predicted_index))

    matched_gold: set[int] = set()
    matched_predicted: set[int] = set()
    matches: list[tuple[int, int]] = []
    for score, gold_index, predicted_index in sorted(candidates, reverse=True):
        if gold_index in matched_gold or predicted_index in matched_predicted:
            continue
        matched_gold.add(gold_index)
        matched_predicted.add(predicted_index)
        matches.append((gold_index, predicted_index))
    return matches


def _match_score(gold: EvaluationItem, predicted: EvaluationItem) -> float:
    """Возвращает score совпадения или 0, если пару нельзя засчитать."""

    if gold.scope_key() != predicted.scope_key():
        return 0.0
    file_score = _file_match_score(gold.file_path, predicted.file_path)
    if file_score == 0.0:
        return 0.0
    line_score = _line_match_score(gold.line_start, predicted.line_start)
    if line_score == 0.0:
        return 0.0
    return round(0.6 + file_score * 0.2 + line_score * 0.2, 4)


def _file_match_score(gold_file: str | None, predicted_file: str | None) -> float:
    """Сравнивает файлы с учётом неполной ручной разметки."""

    if not gold_file:
        return 0.8
    if not predicted_file:
        return 0.0
    gold_normalized = _normalise_file_for_match(gold_file)
    predicted_normalized = _normalise_file_for_match(predicted_file)
    if gold_normalized == predicted_normalized:
        return 1.0
    return 0.0


def _line_match_score(gold_line: int | None, predicted_line: int | None) -> float:
    """Сравнивает строки с небольшим допуском для ручной разметки."""

    if gold_line is None:
        return 0.8
    if predicted_line is None:
        return 0.0
    distance = abs(gold_line - predicted_line)
    if distance == 0:
        return 1.0
    if distance <= 2:
        return 0.75
    return 0.0


def _normalise_file_for_match(value: str) -> str:
    """Нормализует имя файла и схлопывает языковые варианты README."""

    file_name = Path(value.replace("\\", "/")).name.lower()
    if file_name.startswith("readme"):
        return "readme"
    return file_name


def write_evaluation(report: AuditReport, gold_path: Path, output_path: Path) -> EvaluationSummary:
    """Считаем и записываем метрики качества."""

    summary = evaluate_report(report, gold_path)
    output_path.write_text(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _load_gold_items(path: Path) -> list[EvaluationItem]:
    """Загружаем эталонную выборку из JSON или CSV."""

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload.get("items", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [_item_from_row(row) for row in rows if isinstance(row, dict)]


def _items_from_report(report: AuditReport) -> list[EvaluationItem]:
    """Преобразуем находки отчёта к ключам оценки."""

    items: list[EvaluationItem] = []
    for finding in report.findings:
        file_path = finding.location.file_path if finding.location else None
        line_start = finding.location.line_start if finding.location else None
        items.append(
            EvaluationItem(
                unit_id=finding.unit_id,
                criterion=finding.criterion.value,
                severity=finding.severity.value,
                file_path=file_path,
                line_start=line_start,
            )
        )
    return items


def _item_from_row(row: dict[str, Any]) -> EvaluationItem:
    """Поддерживаем русские и технические имена полей в эталонной выборке."""

    return EvaluationItem(
        unit_id=str(_first_value(row, "unit_id", "ID единицы") or ""),
        criterion=_normalise_criterion(_first_value(row, "criterion", "Критерий")),
        severity=_normalise_severity(_first_value(row, "severity", "Критичность")),
        file_path=str(_first_value(row, "file_path", "Файл") or "") or None,
        line_start=_parse_optional_int(_first_value(row, "line_start", "Строка")),
    )


def _first_value(row: dict[str, Any], *keys: str) -> Any:
    """Берём первое имеющееся значение из строки."""

    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _normalise_criterion(value: Any) -> str:
    """Нормализуем критерий для сравнения."""

    text = str(value or "").strip().lower()
    labels = {
        "актуальность": Criterion.ACTUALITY.value,
        "ссылки": Criterion.LINKS.value,
        "версии и технологии": Criterion.TECHNOLOGY_FRESHNESS.value,
        "факты, определения, примеры": Criterion.FACTS.value,
        "точность и корректность": Criterion.CORRECTNESS.value,
        "грамотность и читаемость текста": Criterion.READABILITY.value,
        "качество изображений": Criterion.IMAGE_QUALITY.value,
    }
    return labels.get(text, text)


def _normalise_severity(value: Any) -> str | None:
    """Нормализуем критичность для будущих строгих сравнений."""

    text = str(value or "").strip().lower()
    labels = {
        "критическая": Severity.CRITICAL.value,
        "высокая": Severity.MAJOR.value,
        "средняя": Severity.MINOR.value,
        "справочно": Severity.INFO.value,
    }
    return labels.get(text, text or None)


def _parse_optional_int(value: Any) -> int | None:
    """Безопасно разбираем номер строки."""

    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _safe_ratio(numerator: int, denominator: int) -> float:
    """Деление для метрик без исключения на пустом наборе."""

    return round(numerator / denominator, 4) if denominator else 0.0
