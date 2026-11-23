"""Deterministic public calendar schedule for the internal DPO-PK template."""

from __future__ import annotations

import re
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from .schedule_contracts import (
    ScheduleProjectionTrace,
    ScheduleRowProjection,
    ScheduleTableResult,
    ScheduleTableValidation,
)
from .up_models import UpModule, UpPlan

SCHEDULE_HEADER = ["Компонент", "Период", "Часы", "Форма организации", "Контроль"]

_DURATION_WEEKS_RE = re.compile(r"(?P<weeks>\d+)\s*(?:календарн\w*\s+)?недел", re.IGNORECASE)
_PERIOD_RE = re.compile(r"^(?P<start>\d+)(?:-(?P<end>\d+))?-я недел(?:я|и)$", re.IGNORECASE)
_FINAL_RE = re.compile(r"итог|аттест|демо[- ]?день|выпускн", re.IGNORECASE)
_LECTURE_RE = re.compile(r"лекц|теори|вебинар", re.IGNORECASE)
_PRACTICAL_RE = re.compile(r"практи|проект|воркшоп|лаборатор|семинар|тренинг", re.IGNORECASE)
_INDEPENDENT_RE = re.compile(r"самостоят|асинхрон|домашн", re.IGNORECASE)
_INDIVIDUAL_RE = re.compile(r"индивидуал", re.IGNORECASE)
_GROUP_RE = re.compile(r"групп", re.IGNORECASE)
_INTERIM_CONTROL_RE = re.compile(r"практическ|проект|тест|зач[её]т|экзам", re.IGNORECASE)
_FINAL_CONTROL_RE = re.compile(r"защит|оцен|проект|работ|экзам|зач[её]т", re.IGNORECASE)


def build_schedule_table(
    up: UpPlan,
    *,
    duration: str,
    final_attestation: str,
    profile_id: str,
) -> ScheduleTableResult:
    """Project public modules onto periods without changing their order or total hours."""
    if profile_id != "dpo_pk_v1":
        raise ValueError(f"Schedule contract is not registered for profile: {profile_id}")
    if not up.modules:
        return ScheduleTableResult(header=list(SCHEDULE_HEADER))

    final_index, final_inferred = _final_module(up)
    learning = [
        (index, module)
        for index, module in enumerate(up.modules, start=1)
        if index != final_index
    ]
    parsed_duration = _duration_weeks(duration)
    total_weeks = parsed_duration or max(len(learning), 1)
    spans = _allocate_spans([_decimal(module.hours) for _, module in learning], total_weeks)
    span_by_index = {module_index: span for (module_index, _module), span in zip(learning, spans, strict=True)}

    rows: list[list[str]] = []
    projections: list[ScheduleRowProjection] = []
    for index, module in enumerate(up.modules, start=1):
        is_final = index == final_index
        start_week, end_week = (total_weeks, total_weeks) if is_final else span_by_index[index]
        component = "Итоговая аттестация" if is_final else module.title
        organization = _organization_form(module, is_final=is_final)
        control = _control_form(
            module,
            is_final=is_final,
            final_attestation=final_attestation,
        )
        hours = _decimal_str(_decimal(module.hours))
        rows.append(
            [
                component,
                _period_label(start_week, end_week),
                hours,
                organization,
                control,
            ]
        )
        projections.append(
            ScheduleRowProjection(
                module_index=index,
                source_title=module.title,
                component=component,
                hours=hours,
                start_week=start_week,
                end_week=end_week,
                organization_form=organization,
                control_form=control,
                is_final=is_final,
                final_status=(
                    "profile_default"
                    if is_final and final_inferred
                    else "source_explicit"
                    if is_final
                    else "not_final"
                ),
            )
        )

    total = sum((_decimal(module.hours) for module in up.modules), Decimal("0"))
    rows.append(["ИТОГО", "", _decimal_str(total), "", ""])
    trace = ScheduleProjectionTrace(
        status="passport_duration" if parsed_duration else "sequential_default",
        duration_source=duration.strip(),
        duration_weeks=total_weeks,
        final_module_index=final_index,
        final_inferred=final_inferred,
        review_required=not parsed_duration or final_inferred,
        rows=projections,
    )
    return ScheduleTableResult(header=list(SCHEDULE_HEADER), rows=rows, trace=trace)


def refresh_schedule_projection(
    rows: list[list[str]],
    *,
    duration: str,
    final_attestation: str,
    trace: ScheduleProjectionTrace,
) -> ScheduleTableResult:
    """Recalculate only derived periods/control after a passport edit, preserving methodologist cells."""
    result_rows = [list(row) for row in rows]
    data_rows = [row for row in result_rows if row and row[0].strip().casefold() != "итого"]
    if not data_rows:
        return ScheduleTableResult(header=list(SCHEDULE_HEADER), rows=result_rows, trace=trace)

    final_index = len(data_rows)
    parsed_duration = _duration_weeks(duration)
    total_weeks = parsed_duration or max(final_index - 1, 1)
    learning_hours = [_decimal(row[2]) for row in data_rows[:-1]]
    spans = _allocate_spans(learning_hours, total_weeks)
    for row, span in zip(data_rows[:-1], spans, strict=True):
        row[1] = _period_label(*span)
    data_rows[-1][1] = _period_label(total_weeks, total_weeks)
    data_rows[-1][4] = _final_control_value(final_attestation)

    updated_trace = trace.model_copy(deep=True)
    updated_trace.status = "passport_duration" if parsed_duration else "sequential_default"
    updated_trace.duration_source = duration.strip()
    updated_trace.duration_weeks = total_weeks
    updated_trace.review_required = not parsed_duration or updated_trace.final_inferred
    for projection, row in zip(updated_trace.rows, data_rows, strict=False):
        parsed_period = _parse_period(row[1])
        if parsed_period is not None:
            projection.start_week, projection.end_week = parsed_period
        projection.control_form = row[4]
    return ScheduleTableResult(
        header=list(SCHEDULE_HEADER),
        rows=result_rows,
        trace=updated_trace,
    )


def validate_schedule_table(
    header: list[str],
    rows: list[list[str]],
    *,
    plan_rows: list[list[str]] | None = None,
    trace: ScheduleProjectionTrace | None = None,
) -> ScheduleTableValidation:
    """Validate structure, calendar continuity, source hours and assessment terminology."""
    schema: list[str] = []
    components: list[str] = []
    periods: list[str] = []
    hours: list[str] = []
    methodology: list[str] = []
    if header != SCHEDULE_HEADER:
        schema.append("header")
    if not rows or len(rows[-1]) != len(SCHEDULE_HEADER) or rows[-1][0].strip().casefold() != "итого":
        schema.append("total_row_missing")
        data_rows = rows
        total_row: list[str] | None = None
    else:
        data_rows = rows[:-1]
        total_row = rows[-1]

    expected_plan_rows = [
        row
        for row in (plan_rows or [])
        if not (len(row) > 1 and row[1].strip().casefold() == "итого")
    ]
    expected_trace = {item.module_index: item for item in trace.rows} if trace else {}
    parsed_periods: list[tuple[int, int]] = []
    calculated_total = Decimal("0")
    for index, row in enumerate(data_rows, start=1):
        if len(row) != len(SCHEDULE_HEADER):
            schema.append(f"columns:row:{index}")
            continue
        projection = expected_trace.get(index)
        expected_component = projection.component if projection else (
            expected_plan_rows[index - 1][1] if index <= len(expected_plan_rows) else ""
        )
        if expected_component and row[0].strip() != expected_component:
            components.append(f"component:row:{index}")

        period = _parse_period(row[1])
        if period is None:
            periods.append(f"period:row:{index}")
        else:
            parsed_periods.append(period)
            if period[0] > period[1]:
                periods.append(f"period_order:row:{index}")

        value = _decimal_or_none(row[2])
        if value is None:
            hours.append(f"hours:row:{index}")
        else:
            calculated_total += value
            if index <= len(expected_plan_rows):
                expected_hours = _decimal_or_none(expected_plan_rows[index - 1][2])
                if expected_hours is not None and value != expected_hours:
                    hours.append(f"source_hours:row:{index}")

        organization = _compact(row[3])
        if len(organization.split()) < 4 or organization.casefold() in {"индивидуальный", "групповой"}:
            methodology.append(f"organization_form:row:{index}")
        control = _compact(row[4])
        if index == len(data_rows):
            if not _FINAL_CONTROL_RE.search(control) or "p2p" in control.casefold():
                methodology.append(f"final_control_form:row:{index}")
        elif not _INTERIM_CONTROL_RE.search(control) or "p2p" in control.casefold():
            methodology.append(f"control_form:row:{index}")

    if parsed_periods:
        if parsed_periods[0][0] != 1:
            periods.append("first_period")
        for previous, current in zip(parsed_periods, parsed_periods[1:], strict=False):
            if current[0] < previous[0] or current[0] > previous[1] + 1:
                periods.append("period_continuity")
                break
    if data_rows and data_rows[-1][0].strip().casefold() != "итоговая аттестация":
        methodology.append("final_attestation_row")

    if total_row is not None:
        actual_total = _decimal_or_none(total_row[2])
        if actual_total is None or actual_total != calculated_total:
            hours.append("total_hours")
        if any(cell.strip() for cell in (total_row[1], total_row[3], total_row[4])):
            schema.append("total_row_extra_values")
    if expected_plan_rows and len(data_rows) != len(expected_plan_rows):
        schema.append("row_count")
    return ScheduleTableValidation(
        schema_violations=schema,
        component_violations=components,
        period_violations=periods,
        hour_violations=hours,
        methodology_violations=methodology,
    )


def _final_module(up: UpPlan) -> tuple[int, bool]:
    module = up.modules[-1]
    evidence = " ".join([module.title, module.goals, *(row.name for row in module.rows)])
    if _FINAL_RE.search(evidence):
        return len(up.modules), False
    # The internal DPO profile requires a final assessment. The last component is a visible,
    # reviewable default when the source has no explicit attestation marker.
    return len(up.modules), True


def _allocate_spans(hours: list[Decimal], weeks: int) -> list[tuple[int, int]]:
    if not hours:
        return []
    if weeks < len(hours):
        # Several compact modules may share a week; order stays stable and no gaps are introduced.
        return [
            (min(weeks, (index * weeks) // len(hours) + 1),) * 2
            for index in range(len(hours))
        ]

    durations = [1 for _ in hours]
    remaining = weeks - len(hours)
    total = sum(hours, Decimal("0"))
    weights = hours if total else [Decimal("1") for _ in hours]
    weight_total = sum(weights, Decimal("0"))
    exact = [Decimal(remaining) * weight / weight_total for weight in weights]
    extras = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in exact]
    for index in sorted(
        range(len(hours)),
        key=lambda item: (exact[item] - extras[item], weights[item]),
        reverse=True,
    )[: remaining - sum(extras)]:
        extras[index] += 1
    durations = [base + extra for base, extra in zip(durations, extras, strict=True)]
    spans: list[tuple[int, int]] = []
    start = 1
    for duration in durations:
        end = start + duration - 1
        spans.append((start, end))
        start = end + 1
    return spans


def _organization_form(module: UpModule, *, is_final: bool) -> str:
    if is_final:
        return "Самостоятельная подготовка, консультации и защита итоговой работы"
    formats = " ".join(row.fmt for row in module.rows if row.fmt.strip())
    has_lecture = bool(_LECTURE_RE.search(formats))
    has_practical = bool(_PRACTICAL_RE.search(formats))
    has_independent = bool(_INDEPENDENT_RE.search(formats))
    if _INDIVIDUAL_RE.search(formats):
        return "Индивидуальная проектная работа с консультациями преподавателя"
    if _GROUP_RE.search(formats):
        return "Групповая проектная работа с консультациями и разбором результатов"
    if has_lecture and has_practical:
        return "Лекции и практические занятия с применением электронного обучения"
    if has_lecture:
        return "Лекции с применением электронного обучения и дистанционных технологий"
    if has_independent and not has_practical:
        return "Самостоятельная работа с консультационной поддержкой преподавателя"
    return "Практические занятия, самостоятельная проектная работа и консультации"


def _control_form(module: UpModule, *, is_final: bool, final_attestation: str) -> str:
    if is_final:
        return _final_control_value(final_attestation)
    if any(_positive_int(row.p2p) for row in module.rows):
        return "Практическое задание"
    return "Проект"


def _final_control_value(value: str) -> str:
    result = _compact(value)
    result = re.sub(r"^итоговая аттестация\s*:\s*", "", result, flags=re.IGNORECASE)
    if not result or result.startswith("["):
        result = "Защита итоговой работы"
    return result[0].upper() + result[1:]


def _duration_weeks(value: str) -> int:
    match = _DURATION_WEEKS_RE.search(_compact(value))
    return int(match.group("weeks")) if match else 0


def _period_label(start: int, end: int) -> str:
    return f"{start}-я неделя" if start == end else f"{start}-{end}-я недели"


def _parse_period(value: str) -> tuple[int, int] | None:
    match = _PERIOD_RE.fullmatch(_compact(value))
    if match is None:
        return None
    start = int(match.group("start"))
    return start, int(match.group("end") or start)


def _positive_int(value: str) -> bool:
    try:
        return int(str(value or "").strip() or "0") > 0
    except ValueError:
        return False


def _decimal(value: str) -> Decimal:
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None else Decimal("0")


def _decimal_or_none(value: str) -> Decimal | None:
    text = _compact(value).replace(" ", "").replace(",", ".")
    if not text or text == "—":
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def _decimal_str(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _compact(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


__all__ = [
    "SCHEDULE_HEADER",
    "build_schedule_table",
    "refresh_schedule_projection",
    "validate_schedule_table",
]
