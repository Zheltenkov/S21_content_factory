"""Public projection of the curriculum: editorial titles and four workload kinds."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .curriculum_contracts import (
    CurriculumProjectionTrace,
    CurriculumTableResult,
    CurriculumTableValidation,
    CurriculumTitleResponse,
    CurriculumWorkloadProjection,
    WorkloadKind,
)
from .curriculum_prompts import curriculum_titles_prompt
from .up_models import UpModule, UpPlan, UpRow

CURRICULUM_TITLES_PROMPT_VERSION = "rpd-curriculum-titles-v4"
CURRICULUM_HEADER = [
    "№",
    "Наименование модуля",
    "Всего",
    "Лекции",
    "Практические занятия",
    "Самостоятельная работа",
    "Контроль",
]

_ARROW_RE = re.compile(r"\s*(?:→|⇒|➜|->)\s*")
_GENERIC_TITLE_RE = re.compile(
    r"^(?:модуль|module|тематический блок)(?:\s*(?:№|#)?\s*\d+)?\s*$",
    re.IGNORECASE,
)
_PART_SUFFIX_RE = re.compile(r"(?:[·.]\s*)?часть\s+\d+\s*$", re.IGNORECASE)
_LECTURE_RE = re.compile(r"лекц|теори|вебинар", re.IGNORECASE)
_PRACTICAL_RE = re.compile(
    r"практи|проект|воркшоп|лаборатор|семинар|тренинг",
    re.IGNORECASE,
)
_ORGANIZATION_RE = re.compile(r"индивидуал|групп", re.IGNORECASE)
_INDEPENDENT_RE = re.compile(r"самостоят|асинхрон|домашн", re.IGNORECASE)
_CONTROL_RE = re.compile(r"контрол|аттест|экзам|зач[её]т|тестирован|защит", re.IGNORECASE)


def build_curriculum_table(
    up: UpPlan,
    client: Any,
    *,
    profile_id: str,
) -> CurriculumTableResult:
    """Build section 3.1 without leaking technical titles or control-form text into hour cells."""
    if profile_id != "dpo_pk_v1":
        raise ValueError(f"Curriculum table contract is not registered for profile: {profile_id}")
    if not up.modules:
        return CurriculumTableResult(header=list(CURRICULUM_HEADER))

    source_titles = [module.title for module in up.modules]
    fallback_titles = _deterministic_titles(up)
    display_titles, status, title_issues = _editorial_titles(up, fallback_titles, client)
    rows, projections, inferred_rows, unknown_formats = _workload_rows(up, display_titles)
    trace = CurriculumProjectionTrace(
        status=status,
        source_titles=source_titles,
        display_titles=display_titles,
        title_issues=title_issues,
        workload_projection=projections,
        inferred_workload_rows=inferred_rows,
        unknown_workload_formats=unknown_formats,
    )
    return CurriculumTableResult(header=list(CURRICULUM_HEADER), rows=rows, trace=trace)


def apply_curriculum_titles(up: UpPlan, display_titles: list[str]) -> UpPlan:
    """Create a presentation-only copy while preserving the source plan and its fingerprint."""
    if len(display_titles) != len(up.modules):
        return up.model_copy(deep=True)
    result = up.model_copy(deep=True)
    for module, title in zip(result.modules, display_titles, strict=True):
        module.title = title
        for row in module.rows:
            row.block_title = title
    return result


def validate_curriculum_table(
    header: list[str],
    rows: list[list[str]],
) -> CurriculumTableValidation:
    """Validate the exact public schema, titles and numeric four-way workload equation."""
    schema: list[str] = []
    titles: list[str] = []
    workloads: list[str] = []
    if header != CURRICULUM_HEADER:
        schema.append("header")
    if not rows or len(rows[-1]) < 2 or rows[-1][1].strip().casefold() != "итого":
        schema.append("total_row_missing")
        data_rows = rows
        total_row: list[str] | None = None
    else:
        data_rows = rows[:-1]
        total_row = rows[-1]

    expected_sums = [Decimal("0") for _ in range(5)]
    title_positions: dict[str, list[int]] = {}
    for index, row in enumerate(data_rows, start=1):
        if len(row) != len(CURRICULUM_HEADER):
            schema.append(f"columns:row:{index}")
            continue
        if row[0].strip() != str(index):
            schema.append(f"number:row:{index}")
        titles.extend(
            f"row:{index}:{issue}"
            for issue in _public_title_issues(row[1], strict_length=True)
        )
        normalized_title = _compact(row[1]).casefold()
        title_positions.setdefault(normalized_title, []).append(index)
        total = _decimal(row[2])
        allocations = [_decimal_or_zero(cell) for cell in row[3:7]]
        if total is None:
            workloads.append(f"total_not_numeric:row:{index}")
            continue
        if any(value is None for value in allocations):
            workloads.append(f"workload_not_numeric:row:{index}")
            continue
        resolved = [value for value in allocations if value is not None]
        if total != sum(resolved, Decimal("0")):
            workloads.append(f"workload_sum_mismatch:row:{index}")
        expected_sums[0] += total
        for column, value in enumerate(resolved, start=1):
            expected_sums[column] += value

    for positions in title_positions.values():
        if len(positions) > 1:
            titles.append(f"duplicate_rows:{','.join(str(item) for item in positions)}")

    if total_row is not None:
        if len(total_row) != len(CURRICULUM_HEADER):
            schema.append("columns:total")
        else:
            actual_sums = [_decimal(cell) for cell in total_row[2:7]]
            if any(value is None for value in actual_sums):
                workloads.append("total_row_not_numeric")
            elif [value for value in actual_sums if value is not None] != expected_sums:
                workloads.append("total_row_sum_mismatch")
    return CurriculumTableValidation(
        schema_violations=schema,
        title_violations=titles,
        workload_violations=workloads,
    )


def _editorial_titles(
    up: UpPlan,
    fallback_titles: list[str],
    client: Any,
) -> tuple[list[str], str, list[str]]:
    needs_llm = any(_title_needs_editorial(module.title) for module in up.modules)
    if not needs_llm or not callable(getattr(client, "complete_structured", None)):
        return fallback_titles, "deterministic", []
    system, user = curriculum_titles_prompt(up)
    try:
        response = client.complete_structured(
            output_model=CurriculumTitleResponse,
            system=system,
            user=user,
            llm_role="rpd_curriculum_titles",
            prompt_version=CURRICULUM_TITLES_PROMPT_VERSION,
        )
        parsed = CurriculumTitleResponse.model_validate(response)
    except Exception:  # noqa: BLE001 - a deterministic presentation fallback is mandatory
        return fallback_titles, "deterministic", ["llm_unavailable"]

    by_index = {item.module_index: _compact(item.title) for item in parsed.modules}
    expected = set(range(1, len(up.modules) + 1))
    if set(by_index) != expected or len(parsed.modules) != len(expected):
        return fallback_titles, "deterministic", ["llm_index_contract"]
    titles: list[str] = []
    issues: list[str] = []
    llm_rows: set[int] = set()
    for index in sorted(expected):
        candidate = by_index[index]
        candidate_issues = _public_title_issues(candidate, strict_length=True)
        if candidate_issues:
            titles.append(fallback_titles[index - 1])
            issues.extend(f"row:{index}:{issue}" for issue in candidate_issues)
        else:
            titles.append(candidate)
            llm_rows.add(index)

    positions_by_title: dict[str, list[int]] = {}
    for index, title in enumerate(titles, start=1):
        positions_by_title.setdefault(_compact(title).casefold(), []).append(index)
    for positions in positions_by_title.values():
        if len(positions) <= 1:
            continue
        issues.append(f"duplicate_rows:{','.join(str(item) for item in positions)}")
        for index in positions:
            titles[index - 1] = fallback_titles[index - 1]
            llm_rows.discard(index)
    return titles, "llm" if llm_rows else "deterministic", issues


def _deterministic_titles(up: UpPlan) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for index, module in enumerate(up.modules, start=1):
        title = _fallback_title(module, index)
        normalized = title.casefold()
        if normalized in seen:
            title = f"{title} (часть {index})"
            normalized = title.casefold()
        result.append(title)
        seen.add(normalized)
    return result


def _fallback_title(module: UpModule, index: int) -> str:
    source = _compact(module.title)
    if _GENERIC_TITLE_RE.fullmatch(source):
        project = next(
            (
                _compact(row.name)
                for row in module.rows
                if _compact(row.name).casefold() not in {"", "проект", "project"}
            ),
            "",
        )
        source = f"Практика: {project}" if project else f"Практический раздел {index}"
    # The left side of an arrow is already a curriculum topic; the right side is usually a
    # technical outcome chain. Keep an explicit part number so adjacent source blocks stay distinct.
    first_part = next((_compact(part) for part in _ARROW_RE.split(source) if _compact(part)), "")
    part_suffix = _PART_SUFFIX_RE.search(source)
    if part_suffix and not _PART_SUFFIX_RE.search(first_part):
        suffix = _compact(part_suffix.group()).lstrip("·. ")
        first_part = f"{first_part} · {suffix}"
    concise = " ".join(_sentence_case(first_part).split()[:12]).rstrip(" .;:-")
    return concise or f"Практический раздел {index}"


def _workload_rows(
    up: UpPlan,
    titles: list[str],
) -> tuple[list[list[str]], list[CurriculumWorkloadProjection], int, list[str]]:
    rows: list[list[str]] = []
    projections: list[CurriculumWorkloadProjection] = []
    grand: dict[WorkloadKind, Decimal] = {
        "lectures": Decimal("0"),
        "practical": Decimal("0"),
        "independent": Decimal("0"),
        "control": Decimal("0"),
    }
    inferred_rows = 0
    unknown_formats: list[str] = []
    for index, (module, title) in enumerate(zip(up.modules, titles, strict=True), start=1):
        totals: dict[WorkloadKind, Decimal] = {kind: Decimal("0") for kind in grand}
        exact_totals: dict[WorkloadKind, Decimal] = {kind: Decimal("0") for kind in grand}
        inferred_hours = Decimal("0")
        for source_row in module.rows:
            hours = _decimal(source_row.hours) or Decimal("0")
            kind, inferred, unknown = _workload_kind(source_row)
            totals[kind] += hours
            if inferred:
                inferred_rows += 1
                inferred_hours += hours
            else:
                exact_totals[kind] += hours
            if unknown and unknown not in unknown_formats:
                unknown_formats.append(unknown)
        total = sum(totals.values(), Decimal("0"))
        for kind, value in totals.items():
            grand[kind] += value
        rows.append(
            [
                str(index),
                title,
                _decimal_str(total),
                _cell_hours(totals["lectures"]),
                _cell_hours(totals["practical"]),
                _cell_hours(totals["independent"]),
                _cell_hours(totals["control"]),
            ]
        )
        has_exact = any(exact_totals.values())
        projections.append(
            CurriculumWorkloadProjection(
                module_index=index,
                total_hours=_decimal_str(total),
                allocated_hours={kind: _decimal_str(value) for kind, value in totals.items()},
                exact_allocated_hours={
                    kind: _decimal_str(value) for kind, value in exact_totals.items()
                },
                inferred_hours=_decimal_str(inferred_hours),
                provenance=(
                    "mixed"
                    if inferred_hours and has_exact
                    else "profile_default"
                    if inferred_hours
                    else "source_exact"
                ),
                review_required=bool(inferred_hours),
            )
        )
    grand_total = sum(grand.values(), Decimal("0"))
    rows.append(
        [
            "",
            "ИТОГО",
            _decimal_str(grand_total),
            _decimal_str(grand["lectures"]),
            _decimal_str(grand["practical"]),
            _decimal_str(grand["independent"]),
            _decimal_str(grand["control"]),
        ]
    )
    return rows, projections, inferred_rows, unknown_formats


def _workload_kind(row: UpRow) -> tuple[WorkloadKind, bool, str]:
    value = _compact(row.fmt)
    if _LECTURE_RE.search(value):
        return "lectures", False, ""
    if _INDEPENDENT_RE.search(value):
        return "independent", False, ""
    if _CONTROL_RE.search(value):
        return "control", False, ""
    if _PRACTICAL_RE.search(value):
        return "practical", False, ""
    if _ORGANIZATION_RE.search(value):
        # Individual/group format describes organization, not workload. UpRow is still a project,
        # so practical is the lossless fallback, but the inference must remain visible in the trace.
        return "practical", True, value
    # UpRow is a project by contract. Missing/unknown format therefore stays practical so hours are
    # never lost, while trace makes the inference observable for pilot analysis.
    return "practical", True, value or "<пусто>"


def _title_needs_editorial(value: str) -> bool:
    text = _compact(value)
    return bool(
        _GENERIC_TITLE_RE.fullmatch(text)
        or _ARROW_RE.search(text)
        or _starts_lowercase(text)
        or len(text.split()) > 14
    )


def _public_title_issues(value: str, *, strict_length: bool = False) -> list[str]:
    text = _compact(value)
    issues: list[str] = []
    if not text:
        issues.append("empty")
    if _GENERIC_TITLE_RE.fullmatch(text):
        issues.append("generic")
    if _ARROW_RE.search(text):
        issues.append("arrow")
    if _starts_lowercase(text):
        issues.append("lowercase_start")
    if "\n" in value or "\r" in value:
        issues.append("multiline")
    if strict_length and not 2 <= len(text.split()) <= 12:
        issues.append("word_count")
    return issues


def _starts_lowercase(value: str) -> bool:
    first = next((character for character in value if character.isalpha()), "")
    return bool(first and first.islower())


def _sentence_case(value: str) -> str:
    text = _compact(value)
    first_index = next((index for index, character in enumerate(text) if character.isalpha()), None)
    if first_index is None:
        return text
    return text[:first_index] + text[first_index].upper() + text[first_index + 1 :]


def _decimal(value: str) -> Decimal | None:
    text = _compact(value).replace(" ", "").replace(",", ".")
    if not text or text == "—":
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def _decimal_or_zero(value: str) -> Decimal | None:
    text = _compact(value)
    if text == "—":
        return Decimal("0")
    if not text:
        return None
    return _decimal(text)


def _decimal_str(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _cell_hours(value: Decimal) -> str:
    return _decimal_str(value) if value else "—"


def _compact(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


__all__ = [
    "CURRICULUM_HEADER",
    "CURRICULUM_TITLES_PROMPT_VERSION",
    "apply_curriculum_titles",
    "build_curriculum_table",
    "validate_curriculum_table",
]
