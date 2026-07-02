"""Deterministic quality metrics for offline README generation evaluation."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..models.criteria_models import CriteriaItem, CriteriaReport
from ..models.readme_document import ReadmeDocument, ReadmeSection
from ..observability import fallback_trace_policy_issues
from .models import EvalMetricBreakdown, EvalThresholds, GeneratedProjectOutput, GoldenProjectCase


STRUCTURE_CRITERIA_PREFIXES = ("1.", "2.1", "2.2", "2.3")
PRACTICE_CRITERIA_PREFIXES = ("2.5",)
DIDACTICS_CRITERIA_PREFIXES = ("2.3", "2.4", "2.5", "3.", "4.")

DEFAULT_TOOL_VOCABULARY = {
    "Bash",
    "Docker",
    "Excel",
    "FastAPI",
    "Figma",
    "Git",
    "GitHub",
    "GitLab",
    "Google Analytics",
    "Jupyter",
    "Kubernetes",
    "Miro",
    "MongoDB",
    "Node.js",
    "NumPy",
    "Pandas",
    "PostgreSQL",
    "Postman",
    "Power BI",
    "Python",
    "React",
    "SQL",
    "Tableau",
    "TypeScript",
    "Яндекс Метрика",
}


def build_eval_metrics(
    *,
    case: GoldenProjectCase,
    output: GeneratedProjectOutput,
    document: ReadmeDocument,
    report: CriteriaReport,
) -> EvalMetricBreakdown:
    """Compute the core offline metrics requested by the evaluation harness."""
    expectations = case.expectations
    rubric_total = int(report.total or 0)
    rubric_max = int(report.max_score or len(report.items) or 0)
    score_ratio = _safe_ratio(rubric_total, rubric_max)

    actual_chapters = _chapter_numbers(document)
    missing_chapters = [chapter for chapter in expectations.required_chapters if chapter not in actual_chapters]
    chapter_coverage = _safe_ratio(
        len(expectations.required_chapters) - len(missing_chapters),
        len(expectations.required_chapters),
        default=1.0,
    )
    structure_rate = min(
        criteria_pass_rate(report.items, prefixes=STRUCTURE_CRITERIA_PREFIXES),
        chapter_coverage,
    )

    task_sections = _practice_task_sections(document)
    missing_task_titles = _missing_task_titles(task_sections, expectations.required_task_titles)
    task_count_rate = _safe_ratio(
        len(task_sections),
        expectations.required_task_count,
        default=1.0,
        clamp=True,
    )
    task_title_rate = _safe_ratio(
        len(expectations.required_task_titles) - len(missing_task_titles),
        len(expectations.required_task_titles),
        default=1.0,
    )
    practice_atomicity = min(
        criteria_pass_rate(report.items, prefixes=PRACTICE_CRITERIA_PREFIXES),
        task_count_rate,
        task_title_rate,
    )

    failed_required = _failed_required_criteria(report.items, expectations.required_criteria_ids)
    required_criteria_rate = _safe_ratio(
        len(expectations.required_criteria_ids) - len(failed_required),
        len(expectations.required_criteria_ids),
        default=criteria_pass_rate(report.items, prefixes=DIDACTICS_CRITERIA_PREFIXES),
    )
    tool_mentions = extract_tool_mentions(
        output.markdown,
        vocabulary=DEFAULT_TOOL_VOCABULARY
        | set(expectations.required_tools)
        | set(expectations.allowed_tools)
        | set(expectations.forbidden_tools),
    )
    missing_tools = _missing_tools(tool_mentions, expectations.required_tools)
    required_tool_rate = _safe_ratio(
        len(expectations.required_tools) - len(missing_tools),
        len(expectations.required_tools),
        default=1.0,
    )
    hallucinated = _hallucinated_tools(tool_mentions, case)
    didactics_compliance = min(required_criteria_rate, required_tool_rate)

    retry_count = _retry_count(output)
    fallback_count = _fallback_count(output)
    fallback_policy_violations = _fallback_policy_violations(output)
    cost_usd = _cost_usd(output)
    latency_ms = _latency_ms(output)

    return EvalMetricBreakdown(
        score_ratio=score_ratio,
        rubric_total=rubric_total,
        rubric_max_score=rubric_max,
        structure_pass_rate=structure_rate,
        practice_atomicity=practice_atomicity,
        didactics_compliance=didactics_compliance,
        hallucinated_tools=hallucinated,
        missing_required_tools=missing_tools,
        missing_required_chapters=missing_chapters,
        missing_required_task_titles=missing_task_titles,
        failed_required_criteria=failed_required,
        task_count=len(task_sections),
        retry_count=retry_count,
        fallback_count=fallback_count,
        fallback_policy_violations=fallback_policy_violations,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )


def threshold_failures(metrics: EvalMetricBreakdown, thresholds: EvalThresholds) -> list[str]:
    """Return human-readable threshold failures for one evaluated case."""
    failures: list[str] = []
    if thresholds.min_total_score is not None and metrics.rubric_total < thresholds.min_total_score:
        failures.append(f"rubric_total {metrics.rubric_total} < {thresholds.min_total_score}")
    if metrics.score_ratio < thresholds.min_score_ratio:
        failures.append(f"score_ratio {metrics.score_ratio:.2f} < {thresholds.min_score_ratio:.2f}")
    if metrics.structure_pass_rate < thresholds.min_structure_pass_rate:
        failures.append(
            f"structure_pass_rate {metrics.structure_pass_rate:.2f} < {thresholds.min_structure_pass_rate:.2f}"
        )
    if metrics.practice_atomicity < thresholds.min_practice_atomicity:
        failures.append(
            f"practice_atomicity {metrics.practice_atomicity:.2f} < {thresholds.min_practice_atomicity:.2f}"
        )
    if metrics.didactics_compliance < thresholds.min_didactics_compliance:
        failures.append(
            f"didactics_compliance {metrics.didactics_compliance:.2f} < {thresholds.min_didactics_compliance:.2f}"
        )
    if thresholds.max_hallucinated_tools is not None and len(metrics.hallucinated_tools) > thresholds.max_hallucinated_tools:
        failures.append(
            f"hallucinated_tools {len(metrics.hallucinated_tools)} > {thresholds.max_hallucinated_tools}: "
            f"{', '.join(metrics.hallucinated_tools)}"
        )
    if thresholds.max_retry_count is not None and metrics.retry_count > thresholds.max_retry_count:
        failures.append(f"retry_count {metrics.retry_count} > {thresholds.max_retry_count}")
    if thresholds.max_fallback_count is not None and metrics.fallback_count > thresholds.max_fallback_count:
        failures.append(f"fallback_count {metrics.fallback_count} > {thresholds.max_fallback_count}")
    if (
        thresholds.max_fallback_policy_violations is not None
        and len(metrics.fallback_policy_violations) > thresholds.max_fallback_policy_violations
    ):
        failures.append(
            f"fallback_policy_violations {len(metrics.fallback_policy_violations)} > "
            f"{thresholds.max_fallback_policy_violations}: "
            f"{'; '.join(metrics.fallback_policy_violations[:5])}"
        )
    if thresholds.max_cost_usd is not None and metrics.cost_usd > thresholds.max_cost_usd:
        failures.append(f"cost_usd {metrics.cost_usd:.6f} > {thresholds.max_cost_usd:.6f}")
    if thresholds.max_latency_ms is not None and metrics.latency_ms > thresholds.max_latency_ms:
        failures.append(f"latency_ms {metrics.latency_ms:.0f} > {thresholds.max_latency_ms:.0f}")
    failures.extend(f"missing required chapter {chapter}" for chapter in metrics.missing_required_chapters)
    failures.extend(f"missing required task title: {title}" for title in metrics.missing_required_task_titles)
    failures.extend(f"missing required tool: {tool}" for tool in metrics.missing_required_tools)
    failures.extend(f"failed required criterion: {criterion_id}" for criterion_id in metrics.failed_required_criteria)
    return failures


def criteria_pass_rate(
    items: Iterable[CriteriaItem],
    *,
    ids: Iterable[str] | None = None,
    prefixes: Iterable[str] | None = None,
) -> float:
    """Compute pass rate over explicit criterion IDs or ID prefixes."""
    selected_ids = {item_id.strip() for item_id in ids or [] if item_id.strip()}
    selected_prefixes = tuple(prefix.strip() for prefix in prefixes or [] if prefix.strip())
    selected = [
        item
        for item in items
        if (selected_ids and item.id in selected_ids)
        or (selected_prefixes and any(item.id.startswith(prefix) for prefix in selected_prefixes))
    ]
    if not selected:
        return 1.0
    return _safe_ratio(sum(1 for item in selected if item.score == 1), len(selected))


def extract_tool_mentions(markdown: str, *, vocabulary: Iterable[str]) -> list[str]:
    """Extract known tool mentions from Markdown with deterministic word-boundary matching."""
    text = markdown or ""
    mentions: list[str] = []
    for tool in sorted({str(item).strip() for item in vocabulary if str(item).strip()}, key=str.casefold):
        if len(tool) < 2:
            continue
        escaped = re.escape(tool)
        if re.search(rf"(?<![\wА-Яа-яЁё]){escaped}(?![\wА-Яа-яЁё])", text, flags=re.I):
            mentions.append(tool)
    return mentions


def _chapter_numbers(document: ReadmeDocument) -> set[int]:
    numbers: set[int] = set()
    for section in document.sections:
        number = section.metadata.get("chapter_number")
        if isinstance(number, int):
            numbers.add(number)
    return numbers


def _practice_task_sections(document: ReadmeDocument) -> list[ReadmeSection]:
    sections: list[ReadmeSection] = []
    for top_level in document.sections:
        for section in top_level.flatten():
            if section.metadata.get("section_kind") == "practice_task":
                sections.append(section)
    return sections


def _missing_task_titles(sections: list[ReadmeSection], expected_titles: list[str]) -> list[str]:
    titles = " \n ".join(section.title.casefold() for section in sections)
    return [title for title in expected_titles if title.casefold() not in titles]


def _failed_required_criteria(items: Iterable[CriteriaItem], required_ids: list[str]) -> list[str]:
    by_id = {item.id: item for item in items}
    return [
        criterion_id
        for criterion_id in required_ids
        if criterion_id not in by_id or by_id[criterion_id].score != 1
    ]


def _missing_tools(mentions: list[str], required_tools: list[str]) -> list[str]:
    normalized_mentions = {_normalize_tool(tool) for tool in mentions}
    return [tool for tool in required_tools if _normalize_tool(tool) not in normalized_mentions]


def _hallucinated_tools(mentions: list[str], case: GoldenProjectCase) -> list[str]:
    expectations = case.expectations
    allowed = {_normalize_tool(tool) for tool in expectations.required_tools + expectations.allowed_tools}
    forbidden = {_normalize_tool(tool) for tool in expectations.forbidden_tools}
    hallucinated: list[str] = []
    for tool in mentions:
        normalized = _normalize_tool(tool)
        if normalized in forbidden or (allowed and normalized not in allowed):
            hallucinated.append(tool)
    return sorted(set(hallucinated), key=str.casefold)


def _retry_count(output: GeneratedProjectOutput) -> int:
    repair_attempts = 0
    for event in [*output.node_traces, *output.llm_traces]:
        repair_attempts += _int_from_event(event, "repair_attempts")
        repair_attempts += _int_from_event(event, "retry_count")
    return output.retry_count + repair_attempts


def _fallback_count(output: GeneratedProjectOutput) -> int:
    if output.fallback_count is not None:
        return int(output.fallback_count)
    return len(output.fallback_traces)


def _fallback_policy_violations(output: GeneratedProjectOutput) -> list[str]:
    violations: list[str] = []
    for index, event in enumerate(output.fallback_traces):
        for issue in fallback_trace_policy_issues(event):
            node = event.get("node") or "unknown"
            fallback_type = event.get("fallback_type") or "unknown"
            violations.append(f"fallback[{index}] {node}/{fallback_type}: {issue}")
    return violations


def _cost_usd(output: GeneratedProjectOutput) -> float:
    if output.cost_usd is not None:
        return float(output.cost_usd)
    return sum(_float_from_event(event, "cost_usd") for event in [*output.node_traces, *output.llm_traces])


def _latency_ms(output: GeneratedProjectOutput) -> float:
    if output.latency_ms is not None:
        return float(output.latency_ms)
    return sum(_float_from_event(event, "latency_ms") for event in [*output.node_traces, *output.llm_traces])


def _int_from_event(event: dict[str, Any], key: str) -> int:
    value = event.get(key)
    if value is None and isinstance(event.get("metadata"), dict):
        value = event["metadata"].get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_from_event(event: dict[str, Any], key: str) -> float:
    value = event.get(key)
    if value is None and isinstance(event.get("metadata"), dict):
        value = event["metadata"].get(key)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_ratio(numerator: int | float, denominator: int | float | None, *, default: float = 0.0, clamp: bool = False) -> float:
    if denominator is None or denominator <= 0:
        return default
    value = float(numerator) / float(denominator)
    if clamp:
        value = min(value, 1.0)
    return max(0.0, min(1.0, value))


def _normalize_tool(tool: str) -> str:
    return re.sub(r"\s+", " ", (tool or "").strip()).casefold()
