"""Regression evaluation for README regeneration.

The runtime regeneration pipeline already applies patches deterministically.
This module adds an offline eval contract that checks the blast radius: selected
sections may change, while neighbouring sections, document structure and rubric
quality must remain stable unless a case explicitly allows otherwise.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..models.criteria_models import CriteriaItem, CriteriaReport
from ..models.readme_document import ReadmeDocument, ReadmeSection
from ..regeneration_pipeline import build_regeneration_pipeline_input


class RegenerationEvalThresholds(BaseModel):
    """Pass/fail thresholds for one regeneration regression case."""

    model_config = ConfigDict(extra="forbid")

    max_unscoped_changed_sections: int = Field(default=0, ge=0)
    max_validation_errors: int = Field(default=0, ge=0)
    max_failed_patches: int = Field(default=0, ge=0)
    max_heading_count_delta: int = Field(default=0, ge=0)
    max_rubric_total_drop: int = Field(default=0, ge=0)
    max_new_failed_criteria: int = Field(default=0, ge=0)
    max_passed_to_failed_criteria: int = Field(default=0, ge=0)
    require_selected_section_change: bool = True
    require_outline_stable: bool = True
    require_rubric_comparison: bool = False


class RegenerationEvalCase(BaseModel):
    """One golden regeneration scenario."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    original_markdown: str = Field(min_length=1)
    comments: str = ""
    selected_section_titles: list[str] = Field(default_factory=list)
    protected_section_titles: list[str] = Field(default_factory=list)
    original_rubric_report: CriteriaReport | None = None
    thresholds: RegenerationEvalThresholds = Field(default_factory=RegenerationEvalThresholds)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_case(self) -> "RegenerationEvalCase":
        self.id = self.id.strip()
        self.title = self.title.strip()
        self.selected_section_titles = _unique_non_empty(self.selected_section_titles)
        self.protected_section_titles = _unique_non_empty(self.protected_section_titles)
        self.tags = _unique_non_empty(self.tags)
        return self


class RegenerationEvalOutput(BaseModel):
    """Regenerated README and optional runtime reports for one case."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    case_id: str = Field(min_length=1)
    regenerated_markdown: str = Field(min_length=1)
    validation_report: dict[str, Any] | None = None
    regenerated_rubric_report: CriteriaReport | None = None
    model_name: str | None = Field(default=None, alias="model")
    provider: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegenerationEvalDataset(BaseModel):
    """Versioned set of regeneration regression cases."""

    model_config = ConfigDict(extra="forbid")

    name: str = "regeneration-regressions"
    version: str = "v1"
    cases: list[RegenerationEvalCase] = Field(default_factory=list)
    defaults: RegenerationEvalThresholds = Field(default_factory=RegenerationEvalThresholds)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def ensure_unique_case_ids(self) -> "RegenerationEvalDataset":
        ids = [case.id for case in self.cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"Duplicate regeneration eval case ids: {', '.join(duplicates)}")
        return self


class RegenerationEvalMetrics(BaseModel):
    """Computed blast-radius and quality metrics for one regeneration case."""

    model_config = ConfigDict(extra="forbid")

    changed_section_titles: list[str] = Field(default_factory=list)
    selected_changed_section_titles: list[str] = Field(default_factory=list)
    unscoped_changed_section_titles: list[str] = Field(default_factory=list)
    protected_changed_section_titles: list[str] = Field(default_factory=list)
    selected_section_titles: list[str] = Field(default_factory=list)
    original_outline: list[dict[str, Any]] = Field(default_factory=list)
    regenerated_outline: list[dict[str, Any]] = Field(default_factory=list)
    outline_changed: bool = False
    heading_count_delta: int = 0
    validation_error_count: int = 0
    failed_patch_count: int = 0
    rubric_total_delta: int | None = None
    new_failed_criteria: list[str] = Field(default_factory=list)
    passed_to_failed_criteria: list[str] = Field(default_factory=list)


class RegenerationEvalCaseResult(BaseModel):
    """Evaluation result for one regeneration case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    title: str
    passed: bool
    metrics: RegenerationEvalMetrics = Field(default_factory=RegenerationEvalMetrics)
    reasons: list[str] = Field(default_factory=list)
    model_name: str | None = None
    provider: str | None = None
    run_id: str | None = None
    error: str | None = None


class RegenerationEvalRunSummary(BaseModel):
    """Aggregated report for a regeneration regression run."""

    model_config = ConfigDict(extra="forbid")

    dataset_name: str
    dataset_version: str
    total_cases: int
    passed_cases: int
    pass_rate: float = Field(ge=0.0, le=1.0)
    total_unscoped_changed_sections: int = 0
    total_new_failed_criteria: int = 0
    results: list[RegenerationEvalCaseResult] = Field(default_factory=list)


class RegenerationEvaluationHarness:
    """Evaluate regenerated README outputs against scoped edit expectations."""

    def evaluate_case(
        self,
        case: RegenerationEvalCase,
        output: RegenerationEvalOutput,
        *,
        thresholds: RegenerationEvalThresholds | None = None,
    ) -> RegenerationEvalCaseResult:
        resolved_thresholds = thresholds or case.thresholds
        try:
            metrics = build_regeneration_eval_metrics(case, output)
            reasons = regeneration_threshold_failures(metrics, resolved_thresholds)
            return RegenerationEvalCaseResult(
                case_id=case.id,
                title=case.title,
                passed=not reasons,
                metrics=metrics,
                reasons=reasons,
                model_name=output.model_name,
                provider=output.provider,
                run_id=output.run_id,
            )
        except Exception as exc:
            return RegenerationEvalCaseResult(
                case_id=case.id,
                title=case.title,
                passed=False,
                reasons=[f"regeneration evaluation failed: {exc}"],
                model_name=output.model_name,
                provider=output.provider,
                run_id=output.run_id,
                error=str(exc),
            )

    def evaluate_dataset(
        self,
        dataset: RegenerationEvalDataset,
        outputs_by_case_id: dict[str, RegenerationEvalOutput],
    ) -> RegenerationEvalRunSummary:
        results: list[RegenerationEvalCaseResult] = []
        for case in dataset.cases:
            output = outputs_by_case_id.get(case.id)
            if output is None:
                results.append(
                    RegenerationEvalCaseResult(
                        case_id=case.id,
                        title=case.title,
                        passed=False,
                        reasons=["missing regenerated output"],
                    )
                )
                continue
            thresholds = _merge_thresholds(dataset.defaults, case.thresholds)
            results.append(self.evaluate_case(case, output, thresholds=thresholds))
        return _build_summary(dataset, results)


def build_regeneration_eval_metrics(
    case: RegenerationEvalCase,
    output: RegenerationEvalOutput,
) -> RegenerationEvalMetrics:
    """Compute deterministic regeneration blast-radius and rubric metrics."""
    original_sections = _section_snapshots(case.original_markdown)
    regenerated_sections = _section_snapshots(output.regenerated_markdown)
    selected_titles = _selected_titles(case, output)
    changed_keys = _changed_section_keys(original_sections, regenerated_sections)

    changed = [_display_section_title(original_sections, regenerated_sections, key) for key in changed_keys]
    selected_changed = [
        title
        for key, title in zip(changed_keys, changed, strict=False)
        if _is_selected_section(key, original_sections, regenerated_sections, selected_titles)
    ]
    unscoped_changed = [
        title
        for key, title in zip(changed_keys, changed, strict=False)
        if not _is_selected_section(key, original_sections, regenerated_sections, selected_titles)
    ]
    protected_changed = [
        title
        for key, title in zip(changed_keys, changed, strict=False)
        if _matches_any_title(
            _section_title_path(original_sections, regenerated_sections, key),
            case.protected_section_titles,
        )
    ]

    original_outline = ReadmeDocument.from_markdown(case.original_markdown, fallback_title=case.title).outline()
    regenerated_outline = ReadmeDocument.from_markdown(output.regenerated_markdown, fallback_title=case.title).outline()
    validation_error_count, failed_patch_count = _validation_counts(output.validation_report)
    rubric_total_delta, new_failed, passed_to_failed = _rubric_deltas(
        case.original_rubric_report,
        output.regenerated_rubric_report,
    )

    return RegenerationEvalMetrics(
        changed_section_titles=changed,
        selected_changed_section_titles=selected_changed,
        unscoped_changed_section_titles=unscoped_changed,
        protected_changed_section_titles=protected_changed,
        selected_section_titles=selected_titles,
        original_outline=original_outline,
        regenerated_outline=regenerated_outline,
        outline_changed=original_outline != regenerated_outline,
        heading_count_delta=len(regenerated_outline) - len(original_outline),
        validation_error_count=validation_error_count,
        failed_patch_count=failed_patch_count,
        rubric_total_delta=rubric_total_delta,
        new_failed_criteria=new_failed,
        passed_to_failed_criteria=passed_to_failed,
    )


def regeneration_threshold_failures(
    metrics: RegenerationEvalMetrics,
    thresholds: RegenerationEvalThresholds,
) -> list[str]:
    """Return threshold failures for one regeneration eval result."""
    failures: list[str] = []
    if (
        thresholds.require_selected_section_change
        and metrics.selected_section_titles
        and not metrics.selected_changed_section_titles
    ):
        failures.append("selected sections did not change")
    if len(metrics.unscoped_changed_section_titles) > thresholds.max_unscoped_changed_sections:
        failures.append(
            "unscoped_changed_sections "
            f"{len(metrics.unscoped_changed_section_titles)} > {thresholds.max_unscoped_changed_sections}: "
            f"{', '.join(metrics.unscoped_changed_section_titles)}"
        )
    if metrics.protected_changed_section_titles:
        failures.append(f"protected sections changed: {', '.join(metrics.protected_changed_section_titles)}")
    if thresholds.require_outline_stable and metrics.outline_changed:
        failures.append("heading outline changed")
    if abs(metrics.heading_count_delta) > thresholds.max_heading_count_delta:
        failures.append(
            f"heading_count_delta {metrics.heading_count_delta} exceeds ±{thresholds.max_heading_count_delta}"
        )
    if metrics.validation_error_count > thresholds.max_validation_errors:
        failures.append(
            f"validation_error_count {metrics.validation_error_count} > {thresholds.max_validation_errors}"
        )
    if metrics.failed_patch_count > thresholds.max_failed_patches:
        failures.append(f"failed_patch_count {metrics.failed_patch_count} > {thresholds.max_failed_patches}")
    if thresholds.require_rubric_comparison and metrics.rubric_total_delta is None:
        failures.append("rubric comparison is required but missing")
    if metrics.rubric_total_delta is not None and metrics.rubric_total_delta < -thresholds.max_rubric_total_drop:
        failures.append(
            f"rubric_total_delta {metrics.rubric_total_delta} < -{thresholds.max_rubric_total_drop}"
        )
    if len(metrics.new_failed_criteria) > thresholds.max_new_failed_criteria:
        failures.append(
            f"new_failed_criteria {len(metrics.new_failed_criteria)} > {thresholds.max_new_failed_criteria}: "
            f"{', '.join(metrics.new_failed_criteria)}"
        )
    if len(metrics.passed_to_failed_criteria) > thresholds.max_passed_to_failed_criteria:
        failures.append(
            "passed_to_failed_criteria "
            f"{len(metrics.passed_to_failed_criteria)} > {thresholds.max_passed_to_failed_criteria}: "
            f"{', '.join(metrics.passed_to_failed_criteria)}"
        )
    return failures


def load_regeneration_eval_dataset(path: str | Path) -> RegenerationEvalDataset:
    """Load regeneration eval cases and resolve optional markdown paths."""
    dataset_path = Path(path)
    payload = _read_structured_file(dataset_path)
    if isinstance(payload, list):
        payload = {"cases": payload}
    if not isinstance(payload, dict):
        raise ValueError(f"Regeneration eval dataset must be a mapping or list: {dataset_path}")

    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError(f"Regeneration eval dataset cases must be a list: {dataset_path}")
    payload = dict(payload)
    payload["cases"] = [
        _resolve_markdown_path(case, dataset_path.parent, "original_markdown", "original_markdown_path")
        for case in cases
    ]
    return RegenerationEvalDataset.model_validate(payload)


def load_regeneration_eval_outputs(path: str | Path) -> dict[str, RegenerationEvalOutput]:
    """Load regenerated README outputs and resolve optional markdown paths."""
    output_path = Path(path)
    payload = _read_structured_file(output_path)
    raw_outputs = payload.get("outputs", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_outputs, list):
        raise ValueError(f"Regeneration eval outputs must be a list or contain outputs[]: {output_path}")

    outputs: dict[str, RegenerationEvalOutput] = {}
    for raw in raw_outputs:
        if not isinstance(raw, dict):
            raise ValueError(f"Regeneration eval output entry must be a mapping: {raw!r}")
        entry = _resolve_markdown_path(raw, output_path.parent, "regenerated_markdown", "regenerated_markdown_path")
        output = RegenerationEvalOutput.model_validate(entry)
        if output.case_id in outputs:
            raise ValueError(f"Duplicate regenerated output for case_id={output.case_id!r}")
        outputs[output.case_id] = output
    return outputs


class _SectionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    path: list[str]
    normalized_markdown_hash: str


def _section_snapshots(markdown: str) -> dict[str, _SectionSnapshot]:
    document = ReadmeDocument.from_markdown(markdown, fallback_title="README")
    snapshots: dict[str, _SectionSnapshot] = {}
    snapshots["__title__"] = _snapshot("__title__", "Название проекта", ["Название проекта"], document.title)
    snapshots["__annotation__"] = _snapshot("__annotation__", "Аннотация", ["Аннотация"], document.annotation)
    for index, section in enumerate(document.sections):
        _collect_section_snapshots(section, snapshots, path=[], index_path=[index])
    return snapshots


def _collect_section_snapshots(
    section: ReadmeSection,
    snapshots: dict[str, _SectionSnapshot],
    *,
    path: list[str],
    index_path: list[int],
) -> None:
    section_path = [*path, section.title]
    key = "/".join([str(section.level), *[str(item) for item in index_path], ReadmeDocument.slugify(section.title)])
    snapshots[key] = _snapshot(key, section.title, section_path, section.body_markdown())
    for child_index, child in enumerate(section.children):
        _collect_section_snapshots(
            child,
            snapshots,
            path=section_path,
            index_path=[*index_path, child_index],
        )


def _snapshot(key: str, title: str, path: list[str], markdown: str) -> _SectionSnapshot:
    normalized = _normalize_markdown_for_hash(markdown)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return _SectionSnapshot(
        key=key,
        title=title,
        path=path,
        normalized_markdown_hash=digest,
    )


def _changed_section_keys(
    original: dict[str, _SectionSnapshot],
    regenerated: dict[str, _SectionSnapshot],
) -> list[str]:
    keys = sorted(set(original) | set(regenerated))
    changed: list[str] = []
    for key in keys:
        before = original.get(key)
        after = regenerated.get(key)
        if before is None or after is None:
            changed.append(key)
        elif before.normalized_markdown_hash != after.normalized_markdown_hash:
            changed.append(key)
    return changed


def _display_section_title(
    original: dict[str, _SectionSnapshot],
    regenerated: dict[str, _SectionSnapshot],
    key: str,
) -> str:
    before = original.get(key)
    after = regenerated.get(key)
    if before and after:
        return after.title
    if before:
        return f"{before.title} (removed)"
    if after:
        return f"{after.title} (added)"
    return key


def _section_title_path(
    original: dict[str, _SectionSnapshot],
    regenerated: dict[str, _SectionSnapshot],
    key: str,
) -> list[str]:
    snapshot = regenerated.get(key) or original.get(key)
    return snapshot.path if snapshot else [key]


def _is_selected_section(
    key: str,
    original: dict[str, _SectionSnapshot],
    regenerated: dict[str, _SectionSnapshot],
    selected_titles: list[str],
) -> bool:
    path = _section_title_path(original, regenerated, key)
    return _matches_any_title(path, selected_titles)


def _matches_any_title(path: list[str], expected_titles: list[str]) -> bool:
    if not expected_titles:
        return False
    path_text = " / ".join(path)
    path_normalized = _normalize_title(path_text)
    for title in expected_titles:
        normalized = _normalize_title(title)
        if not normalized:
            continue
        if normalized in path_normalized or path_normalized in normalized:
            return True
    return False


def _selected_titles(case: RegenerationEvalCase, output: RegenerationEvalOutput) -> list[str]:
    titles = list(case.selected_section_titles)
    report = output.validation_report if isinstance(output.validation_report, dict) else {}
    sections = report.get("selected_sections") if isinstance(report, dict) else None
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict) and section.get("title"):
                titles.append(str(section["title"]))
    if not titles and case.comments:
        try:
            pipeline_input = build_regeneration_pipeline_input(
                original_md=case.original_markdown,
                comments=case.comments,
                language="ru",
            )
            titles.extend(section.title for section in pipeline_input.selected_sections)
        except Exception:
            pass
    return _unique_non_empty(titles)


def _validation_counts(validation_report: dict[str, Any] | None) -> tuple[int, int]:
    if not isinstance(validation_report, dict):
        return 0, 0
    issues = validation_report.get("issues") if isinstance(validation_report.get("issues"), list) else []
    errors = sum(1 for issue in issues if isinstance(issue, dict) and issue.get("severity") == "error")
    failed_patches = int(validation_report.get("failed_patch_count") or 0)
    return errors, failed_patches


def _rubric_deltas(
    original: CriteriaReport | None,
    regenerated: CriteriaReport | None,
) -> tuple[int | None, list[str], list[str]]:
    if original is None or regenerated is None:
        return None, [], []
    original_failed = _failed_criteria(original.items)
    regenerated_failed = _failed_criteria(regenerated.items)
    original_passed = _passed_criteria(original.items)
    rubric_total_delta = int(regenerated.total or 0) - int(original.total or 0)
    new_failed = sorted(regenerated_failed - original_failed, key=_criterion_sort_key)
    passed_to_failed = sorted(original_passed & regenerated_failed, key=_criterion_sort_key)
    return rubric_total_delta, new_failed, passed_to_failed


def _failed_criteria(items: list[CriteriaItem]) -> set[str]:
    return {item.id for item in items if int(item.score or 0) < 1}


def _passed_criteria(items: list[CriteriaItem]) -> set[str]:
    return {item.id for item in items if int(item.score or 0) >= 1}


def _criterion_sort_key(item_id: str) -> tuple[int | str, ...]:
    parts: list[int | str] = []
    for part in re.split(r"([0-9]+)", item_id):
        if not part:
            continue
        parts.append(int(part) if part.isdigit() else part)
    return tuple(parts)


def _normalize_markdown_for_hash(markdown: str) -> str:
    return re.sub(r"\s+", " ", markdown or "").strip()


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[#*_`]", "", title or "")).strip().casefold()


def _merge_thresholds(
    defaults: RegenerationEvalThresholds,
    overrides: RegenerationEvalThresholds,
) -> RegenerationEvalThresholds:
    merged = defaults.model_dump()
    for field_name in overrides.model_fields_set:
        merged[field_name] = getattr(overrides, field_name)
    return RegenerationEvalThresholds.model_validate(merged)


def _build_summary(
    dataset: RegenerationEvalDataset,
    results: list[RegenerationEvalCaseResult],
) -> RegenerationEvalRunSummary:
    total_cases = len(results)
    passed_cases = sum(1 for result in results if result.passed)
    return RegenerationEvalRunSummary(
        dataset_name=dataset.name,
        dataset_version=dataset.version,
        total_cases=total_cases,
        passed_cases=passed_cases,
        pass_rate=(passed_cases / total_cases) if total_cases else 0.0,
        total_unscoped_changed_sections=sum(
            len(result.metrics.unscoped_changed_section_titles) for result in results
        ),
        total_new_failed_criteria=sum(len(result.metrics.new_failed_criteria) for result in results),
        results=results,
    )


def _resolve_markdown_path(
    raw: Any,
    base_dir: Path,
    markdown_field: str,
    path_field: str,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Regeneration eval entry must be a mapping: {raw!r}")
    entry = dict(raw)
    markdown = str(entry.get(markdown_field) or "")
    markdown_path = entry.pop(path_field, None)
    if not markdown and markdown_path:
        markdown = (base_dir / str(markdown_path)).resolve().read_text(encoding="utf-8")
    entry[markdown_field] = markdown
    return entry


def _read_structured_file(path: Path) -> Any:
    suffix = path.suffix.casefold()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        return json.loads(text)
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text) or {}
    raise ValueError(f"Unsupported regeneration eval file extension: {path.suffix}")


def _unique_non_empty(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result
