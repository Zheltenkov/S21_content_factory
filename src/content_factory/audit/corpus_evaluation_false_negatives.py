"""False-negative analysis for audit corpus evaluation."""

from __future__ import annotations

import re
from collections import defaultdict

from content_factory.audit.corpus_evaluation_matching import normalize_match_text
from content_factory.audit.corpus_evaluation_models import CorpusEvaluationMatch, FalseNegativeAnalysisItem
from content_factory.audit.domain import Criterion


def false_negative_analysis(matches: list[CorpusEvaluationMatch]) -> list[FalseNegativeAnalysisItem]:
    """Classify missed gold defects so false negatives become an engineering backlog."""

    result: list[FalseNegativeAnalysisItem] = []
    for match in matches:
        if match.counted:
            continue
        reason_code, reason_label, next_step = classify_false_negative(match)
        result.append(
            FalseNegativeAnalysisItem(
                project=match.project,
                project_id=match.project_id,
                criterion=match.criterion,
                label=match.label,
                gold_line_range=match.gold_line_range,
                gold_text=match.gold_text,
                nearest_finding_id=match.found_finding_id,
                nearest_checker=match.found_checker,
                nearest_match_type=match.match_type,
                nearest_score=match.match_score,
                reason_code=reason_code,
                reason_label=reason_label,
                next_step=next_step,
            )
        )
    return result


def false_negative_reason_counts(items: list[FalseNegativeAnalysisItem]) -> dict[str, int]:
    """Count missed-defect reason codes for compact reporting."""

    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[item.reason_code] += 1
    return dict(sorted(counts.items()))


def classify_false_negative(match: CorpusEvaluationMatch) -> tuple[str, str, str]:
    """Return an engineering reason and next step for one missed gold defect."""

    text = normalize_match_text(match.gold_text)
    if _is_meaningful_near_miss(match):
        return (
            "near_miss_matching",
            "Похожая находка была, но строгий матч её не засчитал",
            "Проверить пороги сопоставления или критерий/строку у найденной ошибки.",
        )
    if _looks_like_deterministic_missing(text, match.criterion):
        return (
            "deterministic_possible",
            "Можно ловить правилом",
            "Добавить или расширить детерминированный чекер с тестом на этот паттерн.",
        )
    if _needs_external_data(text, match.criterion):
        return (
            "needs_external_data",
            "Нужен внешний источник данных",
            "Подключить метаданные платформы, манифест, статистику прохождения или курируемую базу.",
        )
    if _looks_like_model_semantics(text, match.criterion):
        return (
            "model_possible",
            "Нужен смысловой анализ",
            "Добавить модельный/семантический слой с проверяемым JSON-контрактом и примерами.",
        )
    return (
        "out_of_scope_or_needs_review",
        "Требуется ручной разбор области применимости",
        "Решить, должна ли локальная проверка ловить этот класс ошибок.",
    )


def _is_meaningful_near_miss(match: CorpusEvaluationMatch) -> bool:
    """Distinguish useful near misses from unrelated same-criterion findings."""

    if not match.found_finding_id or match.match_type == "missed":
        return False
    if match.match_type in {"line_overlap", "near_line_and_text", "text_similarity", "artifact_missing_signal"}:
        return True
    return match.match_type == "criterion_only" and match.match_score >= 0.45


def _looks_like_deterministic_missing(text: str, criterion: str) -> bool:
    """Return whether the missed defect is a good deterministic-rule candidate."""

    deterministic_markers = (
        "https",
        "http",
        "ссылка",
        "url",
        "двоеточ",
        "нумерац",
        "пронумер",
        "кавыч",
        "опечат",
        "тафтолог",
        "тавтолог",
        "input",
        "output",
        "example",
        "result",
    )
    if any(marker in text for marker in deterministic_markers):
        return True
    return criterion == Criterion.READABILITY.value and bool(re.search(r"\b\d{1,5}\b", text))


def _needs_external_data(text: str, criterion: str) -> bool:
    """Return whether the missed defect requires external platform or rights data."""

    markers = (
        "платформ",
        "репозитор",
        "обновлен",
        "обновл",
        "6мес",
        "год",
        "экзамен",
        "финаль",
        "время прохождения",
        "попыт",
        "доступн на территории",
        "рф",
        "лиценз",
        "права",
    )
    return criterion in {Criterion.ACTUALITY.value, Criterion.RIGHTS.value, Criterion.EXAM.value} and any(
        marker in text for marker in markers
    )


def _looks_like_model_semantics(text: str, criterion: str) -> bool:
    """Return whether the missed defect needs semantic/model-based analysis."""

    semantic_markers = (
        "противореч",
        "не является",
        "некоррект",
        "неверн",
        "по факту",
        "чеклист",
        "чек лист",
        "checklist",
        "check list",
        "артефакт",
        "ожида",
        "требован",
        "бизнес",
        "рын",
    )
    return criterion in {
        Criterion.CORRECTNESS.value,
        Criterion.CHECKLIST_ALIGNMENT.value,
        Criterion.MARKET_FIT.value,
    } or any(marker in text for marker in semantic_markers)
