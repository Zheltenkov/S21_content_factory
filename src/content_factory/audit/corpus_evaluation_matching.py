"""Deterministic matching helpers for audit corpus evaluation."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from content_factory.audit.corpus_evaluation_models import (
    CorpusEvaluationMatch,
    GoldCorpusCase,
    PredictedCorpusItem,
    _MatchCandidate,
)
from content_factory.audit.domain import CRITERION_LABELS, Criterion


def match_gold_cases(
    gold_cases: list[GoldCorpusCase],
    predicted_items: list[PredictedCorpusItem],
) -> tuple[list[CorpusEvaluationMatch], set[str]]:
    """Match gold defects to predictions one-to-one."""

    predicted_by_id = {item.finding_id: item for item in predicted_items}
    counted_candidates: list[_MatchCandidate] = []
    best_any_by_gold: dict[str, _MatchCandidate] = {}
    for gold in gold_cases:
        for predicted in predicted_items:
            if gold.project_id != predicted.project_id or gold.criterion != predicted.criterion:
                continue
            candidate = _score_gold_prediction_match(gold, predicted)
            current_best = best_any_by_gold.get(gold.case_id)
            if current_best is None or candidate.score > current_best.score:
                best_any_by_gold[gold.case_id] = candidate
            if _is_counted_match(candidate):
                counted_candidates.append(candidate)

    assigned_gold: set[str] = set()
    assigned_predictions: set[str] = set()
    assigned_by_gold: dict[str, _MatchCandidate] = {}
    for candidate in sorted(counted_candidates, key=lambda item: item.score, reverse=True):
        if candidate.gold_case_id in assigned_gold or candidate.prediction_id in assigned_predictions:
            continue
        assigned_gold.add(candidate.gold_case_id)
        assigned_predictions.add(candidate.prediction_id)
        assigned_by_gold[candidate.gold_case_id] = candidate

    rows: list[CorpusEvaluationMatch] = []
    for gold in gold_cases:
        matched: _MatchCandidate | None = assigned_by_gold.get(gold.case_id)
        counted = matched is not None
        if matched is None:
            matched = _best_unassigned_candidate(gold, predicted_items, assigned_predictions)
        prediction = predicted_by_id.get(matched.prediction_id) if matched is not None else None
        rows.append(_match_row(gold, prediction, matched, counted))
    return rows, assigned_predictions


def line_relation(
    gold_start: int | None,
    gold_end: int | None,
    pred_start: int | None,
    pred_end: int | None,
) -> str:
    """Return the relation between gold and predicted line ranges."""

    if gold_start is None or pred_start is None:
        return "none"
    gold_end = gold_end or gold_start
    pred_end = pred_end or pred_start
    if gold_start <= pred_end and pred_start <= gold_end:
        return "overlap"
    distance = min(abs(gold_start - pred_end), abs(pred_start - gold_end))
    return "near" if distance <= 2 else "far"


def normalize_match_text(value: str) -> str:
    """Normalize text before comparing a gold defect with a finding."""

    text = str(value or "").lower()
    text = re.sub(r"https?:[/\\]+", " ", text)
    text = re.sub(r"[`*_\"'«»()\[\]{}:;,.!?/\\|]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def same_missing_artifact_signal(gold_text: str, found_text: str) -> bool:
    """Match wording about a missing command marker inside an artifact."""

    gold = normalize_match_text(gold_text)
    found = normalize_match_text(found_text)
    if not gold or not found:
        return False
    shared_commands = _artifact_command_markers(gold) & _artifact_command_markers(found)
    if not shared_commands:
        return False
    return _mentions_artifact(gold) and _mentions_artifact(found) and _mentions_absence(gold) and _mentions_absence(found)


def format_range(start: int | None, end: int | None) -> str:
    """Format a source line range for human-readable reports."""

    if start is None:
        return ""
    if end is None or end == start:
        return str(start)
    return f"{start}-{end}"


def format_prediction_range(item: PredictedCorpusItem) -> str:
    """Format a finding file path and source line range."""

    line_range = format_range(item.line_start, item.line_end)
    if item.file_path and line_range:
        return f"{item.file_path}:{line_range}"
    return item.file_path or line_range


def criterion_label(criterion_value: str) -> str:
    """Return a human-readable criterion label."""

    try:
        return CRITERION_LABELS[Criterion(criterion_value)]
    except ValueError:
        return criterion_value


def _best_unassigned_candidate(
    gold: GoldCorpusCase,
    predicted_items: list[PredictedCorpusItem],
    assigned_predictions: set[str],
) -> _MatchCandidate | None:
    """Select the best unused prediction for explaining a miss."""

    best: _MatchCandidate | None = None
    for predicted in predicted_items:
        if predicted.finding_id in assigned_predictions:
            continue
        if gold.project_id != predicted.project_id or gold.criterion != predicted.criterion:
            continue
        candidate = _score_gold_prediction_match(gold, predicted)
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def _score_gold_prediction_match(gold: GoldCorpusCase, predicted: PredictedCorpusItem) -> _MatchCandidate:
    """Score one gold defect against one predicted finding."""

    relation = line_relation(gold.line_start, gold.line_end, predicted.line_start, predicted.line_end)
    text_score = _text_match_score(gold.gold_text, predicted.found_text)
    if same_missing_artifact_signal(gold.gold_text, predicted.found_text):
        return _candidate(
            gold,
            predicted,
            "artifact_missing_signal",
            max(0.78, text_score),
            "Совпали проект, критерий, ожидаемый маркер, тип артефакта и факт отсутствия маркера.",
        )
    if relation == "overlap" and text_score >= 0.25:
        return _candidate(
            gold,
            predicted,
            "line_and_text",
            max(0.9, text_score),
            "Совпали проект, критерий, диапазон строк и ключевой текст ошибки.",
        )
    if relation == "overlap":
        return _candidate(
            gold,
            predicted,
            "line_overlap",
            0.82,
            "Совпали проект, критерий и строка/диапазон; текстовое совпадение слабее, но строка указывает на тот же дефект.",
        )
    if relation == "near" and text_score >= 0.2:
        return _candidate(
            gold,
            predicted,
            "near_line_and_text",
            max(0.75, text_score),
            "Строки отличаются не более чем на две, критерий совпал, текст ошибки похож.",
        )
    if text_score >= 0.55:
        return _candidate(
            gold,
            predicted,
            "text_similarity",
            text_score,
            "Совпали проект, критерий и текст ошибки; строка отсутствует или отличается.",
        )
    return _candidate(
        gold,
        predicted,
        "criterion_only",
        max(0.15, text_score),
        "Совпал только проект и критерий; для основной метрики это не засчитывается.",
    )


def _candidate(
    gold: GoldCorpusCase,
    predicted: PredictedCorpusItem,
    match_type: str,
    score: float,
    reason: str,
) -> _MatchCandidate:
    """Build an intermediate matching candidate."""

    return _MatchCandidate(
        gold_case_id=gold.case_id,
        prediction_id=predicted.finding_id,
        match_type=match_type,
        score=round(min(max(score, 0.0), 1.0), 4),
        reason=reason,
    )


def _is_counted_match(candidate: _MatchCandidate) -> bool:
    """Return whether a candidate counts in the main detailed metric."""

    return candidate.match_type in {
        "line_and_text",
        "line_overlap",
        "near_line_and_text",
        "text_similarity",
        "artifact_missing_signal",
    }


def _match_row(
    gold: GoldCorpusCase,
    prediction: PredictedCorpusItem | None,
    candidate: _MatchCandidate | None,
    counted: bool,
) -> CorpusEvaluationMatch:
    """Build one row of the detailed matching report."""

    if prediction is None or candidate is None:
        return CorpusEvaluationMatch(
            project=gold.matched_project,
            project_id=gold.project_id,
            criterion=gold.criterion,
            label=criterion_label(gold.criterion),
            gold_row_number=gold.row_number,
            gold_line_range=format_range(gold.line_start, gold.line_end),
            gold_text=gold.gold_text,
            found_line_range="",
            found_text="",
            match_type="missed",
            match_score=0.0,
            counted=False,
            reason="По этому проекту и критерию не найдено подходящей ошибки с совпадающей строкой или текстом.",
        )
    return CorpusEvaluationMatch(
        project=gold.matched_project,
        project_id=gold.project_id,
        criterion=gold.criterion,
        label=criterion_label(gold.criterion),
        gold_row_number=gold.row_number,
        gold_line_range=format_range(gold.line_start, gold.line_end),
        gold_text=gold.gold_text,
        found_finding_id=prediction.finding_id,
        found_checker=prediction.checker_name,
        found_line_range=format_prediction_range(prediction),
        found_text=prediction.found_text,
        match_type=candidate.match_type,
        match_score=candidate.score,
        counted=counted,
        reason=candidate.reason if counted else f"{candidate.reason} Найденная ошибка показана для разбора, но не засчитана.",
    )


def _text_match_score(gold_text: str, found_text: str) -> float:
    """Compute textual similarity between a gold defect and a finding."""

    gold = normalize_match_text(gold_text)
    found = normalize_match_text(found_text)
    if not gold or not found:
        return 0.0
    if gold in found or found in gold:
        return 0.95
    gold_tokens = set(gold.split())
    found_tokens = set(found.split())
    overlap = len(gold_tokens & found_tokens)
    token_score = overlap / max(min(len(gold_tokens), len(found_tokens)), 1)
    sequence_score = SequenceMatcher(a=gold, b=found).ratio()
    return round(max(token_score, sequence_score), 4)


def _artifact_command_markers(text: str) -> set[str]:
    """Find command markers that make artifact-related defects comparable."""

    commands = {"whoami", "id", "uname", "ls", "hostname", "ifconfig", "ipconfig", "tcpdump", "tshark"}
    return {command for command in commands if re.search(rf"(?<![\w-]){re.escape(command)}(?![\w-])", text)}


def _mentions_artifact(text: str) -> bool:
    """Return whether text mentions an artifact, dump, capture or trace."""

    return bool(re.search(r"\b(pcap|pcapng|dump|capture|trace|log)\b|дамп|захват|артефакт", text))


def _mentions_absence(text: str) -> bool:
    """Return whether text says the expected marker is absent."""

    return bool(re.search(r"\bнет\b|не найден|не содержит|отсутств|missing|not found|without", text))
