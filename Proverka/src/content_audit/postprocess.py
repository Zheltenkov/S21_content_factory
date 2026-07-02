"""Постобработка находок перед записью отчёта."""

from __future__ import annotations

import re
from collections import defaultdict

from content_audit.domain import Criterion, Evidence, Finding, Severity, Verdict


SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.MINOR: 1,
    Severity.MAJOR: 2,
    Severity.CRITICAL: 3,
}
MODEL_ONLY_CHECKERS = {"model_rubric_checker"}
MODEL_CHECKERS = {
    "curriculum_relevance_checker",
    "dependency_freshness_checker",
    "fact_checker_perplexity",
    "model_rubric_checker",
    "readability_checker",
    "rights_originality_checker",
    "spelling_wording_checker",
    "tech_freshness_checker",
}
LANGUAGE_SUFFIX_RE = re.compile(r"(?i)([_-](?:rus|ru|eng|en|uzb|uz))(?=\.[^.\\/]+$)")
GENERIC_MODEL_DETAILS = {
    "Проверка актуальности без отдельного пояснения.",
    "Фактологическая проверка без отдельного пояснения.",
    "Модельная проверка без отдельного источника.",
}
LOW_EVIDENCE_ACTUALITY_CONFIDENCE = 0.3


def postprocess_findings(findings: list[Finding]) -> tuple[list[Finding], list[str]]:
    """Очищает итоговые строки отчёта и возвращает предупреждения по удалённым шумам."""

    warnings: list[str] = []
    kept: list[Finding] = []
    tool_errors: list[Finding] = []
    empty_model_results = 0
    low_evidence_actuality = 0
    rights_duplicates = 0

    for finding in findings:
        if _is_external_tool_error(finding):
            tool_errors.append(finding)
            continue
        if _is_empty_model_result(finding):
            empty_model_results += 1
            continue
        if _is_low_evidence_actuality_unknown(finding):
            low_evidence_actuality += 1
            continue
        if finding.checker_name == "model_rubric_checker" and finding.criterion == Criterion.RIGHTS:
            rights_duplicates += 1
            continue
        kept.append(_normalize_review_and_severity(finding))

    collapsed, duplicate_count = _collapse_language_duplicates(kept)
    warnings.extend(_warning_summary(tool_errors, empty_model_results, low_evidence_actuality, rights_duplicates, duplicate_count))
    return collapsed, warnings


def _is_external_tool_error(finding: Finding) -> bool:
    """Отделяет сбои провайдера от методологических находок."""

    if finding.support_status == "ошибка проверки":
        return True
    details = " ".join(evidence.detail for evidence in finding.evidence)
    titles = {evidence.title for evidence in finding.evidence}
    if titles & {"Внешняя проверка", "Модельная проверка"}:
        return "OpenRouter" in details or "HTTP " in details or "ошибк" in details.lower()
    return False


def _is_empty_model_result(finding: Finding) -> bool:
    """Удаляет модельные unknown-строки без уверенности, источника и содержания."""

    if finding.checker_name not in MODEL_CHECKERS and not finding.prompt_version:
        return False
    if finding.verdict != Verdict.UNKNOWN or finding.confidence > 0.0:
        return False
    if finding.source or finding.latest_version or finding.recommended_version:
        return False
    details = [evidence.detail.strip() for evidence in finding.evidence if evidence.detail.strip()]
    if not details:
        return True
    return all(detail in GENERIC_MODEL_DETAILS for detail in details)


def _is_low_evidence_actuality_unknown(finding: Finding) -> bool:
    """Удаляет проверки актуальности без источников, версий и уверенности."""

    if finding.checker_name != "tech_freshness_checker" or finding.criterion != Criterion.TECHNOLOGY_FRESHNESS:
        return False
    if finding.verdict != Verdict.UNKNOWN:
        return False
    if finding.confidence >= LOW_EVIDENCE_ACTUALITY_CONFIDENCE:
        return False
    if finding.source or finding.latest_version or finding.recommended_version:
        return False
    support_status = (finding.support_status or "").strip().lower()
    return support_status in {"", "неизвестно", "unknown", "не проверялось"}


def _normalize_review_and_severity(finding: Finding) -> Finding:
    """Согласует критичность, вердикт и флаг ручного разбора."""

    severity = finding.severity
    if finding.verdict == Verdict.FAIL and severity == Severity.INFO:
        severity = Severity.MINOR
    elif finding.verdict == Verdict.PASS and severity != Severity.INFO:
        severity = Severity.INFO

    needs_review = (
        finding.verdict == Verdict.UNKNOWN
        or finding.confidence < 0.7
        or finding.checker_name in MODEL_ONLY_CHECKERS
    )

    extra = dict(finding.extra)
    if severity != finding.severity:
        extra.setdefault("original_severity", finding.severity.value)
        extra["postprocess_severity"] = "Вердикт и критичность приведены к согласованному виду."
    if needs_review != finding.needs_human_review:
        extra.setdefault("original_needs_human_review", finding.needs_human_review)
        extra["postprocess_review"] = "Флаг ручного разбора пересчитан по вердикту, уверенности и типу модуля."

    return finding.model_copy(update={"severity": severity, "needs_human_review": needs_review, "extra": extra})


def _collapse_language_duplicates(findings: list[Finding]) -> tuple[list[Finding], int]:
    """Схлопывает одинаковые случаи из языковых вариантов одного файла."""

    groups: dict[tuple[str, ...], list[Finding]] = defaultdict(list)
    passthrough: list[Finding] = []
    for finding in findings:
        key = _language_duplicate_key(finding)
        if key is None:
            passthrough.append(finding)
            continue
        groups[key].append(finding)

    collapsed = list(passthrough)
    duplicate_count = 0
    for group in groups.values():
        if len(group) == 1 or not _has_language_variants(group):
            collapsed.extend(group)
            continue
        primary = _select_primary(group)
        duplicate_count += len(group) - 1
        collapsed.append(_merge_language_group(primary, group))

    collapsed.sort(key=lambda item: item.finding_id)
    return collapsed, duplicate_count


def _language_duplicate_key(finding: Finding) -> tuple[str, ...] | None:
    """Строит ключ дубля на уровне базового файла, критерия и типа проверки."""

    if finding.location is None or not finding.location.file_path:
        return None
    base_file = _base_language_path(finding.location.file_path)
    return (
        finding.unit_id,
        finding.branch or "",
        finding.criterion.value,
        finding.checker_name,
        finding.verdict.value,
        finding.severity.value,
        base_file,
        _finding_signature(finding),
    )


def _base_language_path(path: str) -> str:
    """Удаляет языковой суффикс из имени файла, сохраняя каталог и расширение."""

    return LANGUAGE_SUFFIX_RE.sub("", path.replace("\\", "/"))


def _finding_signature(finding: Finding) -> str:
    """Выделяет содержательный признак проблемы для защиты от чрезмерного схлопывания."""

    for key in ("candidate", "claim", "kind", "ecosystem"):
        value = finding.extra.get(key)
        if value:
            return f"{key}:{_norm(str(value))}"
    evidence = " ".join(f"{item.title}:{item.detail}" for item in finding.evidence)
    recommendation = finding.recommendation or ""
    return _norm(f"{evidence} {recommendation}")[:240]


def _norm(value: str) -> str:
    """Нормализует текст для ключей дедупликации."""

    return re.sub(r"\s+", " ", value.strip().lower())


def _has_language_variants(group: list[Finding]) -> bool:
    """Проверяет, что в группе реально есть разные языковые файлы."""

    paths = {finding.location.file_path for finding in group if finding.location}
    base_paths = {_base_language_path(path) for path in paths}
    return len(paths) > 1 and len(base_paths) == 1


def _select_primary(group: list[Finding]) -> Finding:
    """Выбирает главную строку группы: более важную и уверенную."""

    return sorted(
        group,
        key=lambda item: (
            SEVERITY_RANK[item.severity],
            item.confidence,
            0 if item.verdict == Verdict.UNKNOWN else 1,
        ),
        reverse=True,
    )[0]


def _merge_language_group(primary: Finding, group: list[Finding]) -> Finding:
    """Сохраняет одну строку и добавляет список затронутых языковых файлов."""

    files = sorted({finding.location.file_path for finding in group if finding.location})
    extra = dict(primary.extra)
    extra["merged_language_duplicates"] = len(group) - 1
    extra["affected_files"] = files
    evidence = list(primary.evidence)
    evidence.append(
        Evidence(
            title="Языковые дубли",
            detail=f"Схлопнуто {len(group)} строк: {', '.join(files)}.",
        )
    )
    return primary.model_copy(update={"evidence": evidence, "extra": extra})


def _warning_summary(
    tool_errors: list[Finding],
    empty_model_results: int,
    low_evidence_actuality: int,
    rights_duplicates: int,
    duplicate_count: int,
) -> list[str]:
    """Формирует короткие предупреждения постобработки для сводки прогона."""

    warnings: list[str] = []
    if tool_errors:
        examples = "; ".join(_first_evidence_detail(finding) for finding in tool_errors[:3])
        warnings.append(
            f"Постобработка: {len(tool_errors)} служебных ошибок внешних проверок перенесены из таблицы в предупреждения. {examples}"
        )
    if empty_model_results:
        warnings.append(f"Постобработка: {empty_model_results} пустых модельных результатов без уверенности удалены из таблицы.")
    if low_evidence_actuality:
        warnings.append(
            f"Постобработка: {low_evidence_actuality} низкоуверенных проверок актуальности без источников и версий удалены из таблицы."
        )
    if rights_duplicates:
        warnings.append(f"Постобработка: {rights_duplicates} дублей по правам от общей модельной рубрики удалены.")
    if duplicate_count:
        warnings.append(f"Постобработка: {duplicate_count} языковых дублей схлопнуты в базовые строки.")
    return warnings


def _first_evidence_detail(finding: Finding) -> str:
    """Возвращает короткий текст первого основания для предупреждения."""

    detail = finding.evidence[0].detail if finding.evidence else finding.recommendation
    return detail[:240]
