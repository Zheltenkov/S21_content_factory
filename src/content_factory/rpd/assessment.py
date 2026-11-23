"""Deterministic assessment tables for the internal DPO-PK document profile.

Section 4.1 is a module-level projection of interim controls. Section 4.2 is the
passport of the final attestation. The model may help upstream components formulate
program content, but these structural tables remain source-bound and deterministic.
"""

from __future__ import annotations

import re

from .assessment_contracts import (
    AssessmentProjectionTrace,
    AssessmentTablesResult,
    CurrentControlValidation,
    FinalAttestationValidation,
)
from .schedule_contracts import ScheduleProjectionTrace
from .up_models import UpModule, UpPlan

SCHOOL_PK_PROFILE_ID = "dpo_pk_v1"

CURRENT_CONTROL_HEADER = [
    "Компонент",
    "Форма контроля",
    "Процедура",
    "Критерии",
    "Результат",
]
FINAL_ATTESTATION_HEADER = ["Параметр", "Минимальный пример заполнения"]
FINAL_ATTESTATION_PARAMETERS = [
    "Форма",
    "Проверяемый результат",
    "Условия допуска",
    "Продолжительность",
    "Критерии",
    "Порог успешного прохождения",
    "Фиксация результата",
]

_CONTROL_FORM_RE = re.compile(r"практи|проект|задан|работ|тест|зач[её]т", re.IGNORECASE)
_FINAL_FORM_RE = re.compile(r"защит|демонстрац|экзам|зач[её]т|работ|проект", re.IGNORECASE)
_EVIDENCE_SYSTEM_RE = re.compile(r"LMS|ЭИОС|платформ", re.IGNORECASE)


def build_assessment_tables(
    up: UpPlan,
    *,
    final_attestation: str,
    profile_id: str,
    schedule_trace: ScheduleProjectionTrace | None = None,
) -> AssessmentTablesResult:
    """Build 4.1 and 4.2 from the same public module titles and assessment source."""
    if profile_id != SCHOOL_PK_PROFILE_ID:
        raise ValueError(f"Assessment table contract is not registered for profile: {profile_id}")
    if not up.modules:
        return AssessmentTablesResult(
            current_header=list(CURRENT_CONTROL_HEADER),
            final_header=list(FINAL_ATTESTATION_HEADER),
        )

    final_index = _final_module_index(up, schedule_trace)
    schedule_by_index = {
        projection.module_index: projection
        for projection in schedule_trace.rows
    } if schedule_trace is not None else {}

    current_rows: list[list[str]] = []
    current_indices: list[int] = []
    current_components: list[str] = []
    current_forms: list[str] = []
    for module_index, module in enumerate(up.modules, start=1):
        # A compact one-module program can contain both current assignments and its final
        # attestation in the same component. With two or more modules, the final component is
        # excluded from 4.1 and described exclusively by 4.2.
        if len(up.modules) > 1 and module_index == final_index:
            continue
        projection = schedule_by_index.get(module_index)
        component = _compact(projection.component if projection is not None else module.title)
        control_form = _compact(
            projection.control_form if projection is not None else _current_control_form(module)
        )
        current_rows.append(
            [
                component,
                control_form,
                _current_procedure(module),
                _current_criteria(module, component),
                "Зачтено / не зачтено",
            ]
        )
        current_indices.append(module_index)
        current_components.append(component)
        current_forms.append(control_form)

    final_module = up.modules[final_index - 1]
    final_projection = schedule_by_index.get(final_index)
    final_component = _compact(
        final_projection.component if final_projection is not None else final_module.title
    )
    final_form = _final_control_form(
        final_attestation,
        final_projection.control_form if final_projection is not None else "",
    )
    final_rows = [
        ["Форма", final_form],
        ["Проверяемый результат", _checked_result(up, final_module)],
        ["Условия допуска", "Освоение всех модулей программы и выполнение текущих заданий"],
        ["Продолжительность", _final_duration(final_module)],
        [
            "Критерии",
            "Соответствие результата заданию, обоснованность решений, работоспособность и проверяемость результата, качество представления и ответов на вопросы",
        ],
        ["Порог успешного прохождения", "Не менее 70 % от максимального количества баллов"],
        ["Фиксация результата", "Протокол и ведомость итоговой аттестации"],
    ]
    return AssessmentTablesResult(
        current_header=list(CURRENT_CONTROL_HEADER),
        current_rows=current_rows,
        final_header=list(FINAL_ATTESTATION_HEADER),
        final_rows=final_rows,
        trace=AssessmentProjectionTrace(
            current_module_indices=current_indices,
            current_components=current_components,
            current_control_forms=current_forms,
            final_module_index=final_index,
            final_component=final_component,
            final_control_form=final_form,
            final_inferred=schedule_trace.final_inferred if schedule_trace is not None else True,
        ),
    )


def refresh_final_attestation_form(rows: list[list[str]], value: str) -> list[list[str]]:
    """Synchronize the 4.2 form with canonical passport metadata without touching other cells."""
    refreshed = [list(row) for row in rows]
    resolved = _final_control_form(value, "")
    for row in refreshed:
        if len(row) >= 2 and _compact(row[0]).casefold() == "форма":
            row[1] = resolved
            break
    return refreshed


def validate_current_control_table(
    header: list[str],
    rows: list[list[str]],
    trace: AssessmentProjectionTrace | None,
) -> CurrentControlValidation:
    """Validate schema, source-module coverage and usable assessment wording for 4.1."""
    schema: list[str] = []
    components: list[str] = []
    methodology: list[str] = []
    values: list[str] = []
    if header != CURRENT_CONTROL_HEADER:
        schema.append("header")
    expected = trace.current_components if trace is not None else []
    if expected and len(rows) != len(expected):
        schema.append("row_count")

    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if len(row) != len(CURRENT_CONTROL_HEADER):
            schema.append(f"columns:row:{index}")
            continue
        component, form, procedure, criteria, result = (_compact(cell) for cell in row)
        normalized_component = component.casefold()
        if normalized_component in seen:
            components.append(f"duplicate:row:{index}")
        seen.add(normalized_component)
        if index <= len(expected) and component != expected[index - 1]:
            components.append(f"component:row:{index}")
        for column, value in enumerate(row, start=1):
            if not _compact(value):
                values.append(f"empty:row:{index}:column:{column}")
        if "p2p" in form.casefold() or not _CONTROL_FORM_RE.search(form):
            methodology.append(f"control_form:row:{index}")
        if not _EVIDENCE_SYSTEM_RE.search(procedure) or "загруз" not in procedure.casefold():
            methodology.append(f"procedure:row:{index}")
        if len(criteria.split()) < 6:
            methodology.append(f"criteria:row:{index}")
        if not re.search(r"зачт|балл|оцен", result, re.IGNORECASE):
            methodology.append(f"result:row:{index}")
    return CurrentControlValidation(
        schema_violations=schema,
        component_violations=components,
        methodology_violations=methodology,
        value_violations=values,
    )


def validate_final_attestation_table(
    header: list[str],
    rows: list[list[str]],
) -> FinalAttestationValidation:
    """Validate the exact seven-row internal passport while allowing methodologist edits."""
    schema: list[str] = []
    labels: list[str] = []
    methodology: list[str] = []
    values: list[str] = []
    if header != FINAL_ATTESTATION_HEADER:
        schema.append("header")
    by_label: dict[str, list[tuple[int, str]]] = {}
    for index, row in enumerate(rows, start=1):
        if len(row) != len(FINAL_ATTESTATION_HEADER):
            schema.append(f"columns:row:{index}")
            continue
        label, value = (_compact(cell) for cell in row)
        by_label.setdefault(label, []).append((index, value))
        if not value:
            values.append(f"empty:{label or index}")
    for label in FINAL_ATTESTATION_PARAMETERS:
        entries = by_label.get(label, [])
        if not entries:
            labels.append(f"missing:{label}")
        elif len(entries) > 1:
            labels.append(f"duplicate:{label}")

    def value_for(label: str) -> str:
        entries = by_label.get(label, [])
        return entries[0][1] if entries else ""

    form = value_for("Форма")
    if "p2p" in form.casefold() or (form and not _FINAL_FORM_RE.search(form)):
        methodology.append("form")
    checked_result = value_for("Проверяемый результат")
    if checked_result and len(checked_result.split()) < 8:
        methodology.append("checked_result")
    admission = value_for("Условия допуска")
    if admission and not re.search(r"модул|задан|текущ", admission, re.IGNORECASE):
        methodology.append("admission")
    duration = value_for("Продолжительность")
    if duration and not re.search(r"час|минут", duration, re.IGNORECASE):
        methodology.append("duration")
    criteria = value_for("Критерии")
    if criteria and len(criteria.split()) < 8:
        methodology.append("criteria")
    threshold = value_for("Порог успешного прохождения")
    if threshold and not (re.search(r"\d", threshold) and ("%" in threshold or "процент" in threshold.casefold())):
        methodology.append("threshold")
    fixation = value_for("Фиксация результата")
    if fixation and not re.search(r"протокол|ведомост", fixation, re.IGNORECASE):
        methodology.append("fixation")
    return FinalAttestationValidation(
        schema_violations=schema,
        label_violations=labels,
        methodology_violations=methodology,
        value_violations=values,
    )


def _final_module_index(up: UpPlan, trace: ScheduleProjectionTrace | None) -> int:
    if trace is not None and 1 <= trace.final_module_index <= len(up.modules):
        return trace.final_module_index
    return len(up.modules)


def _current_control_form(module: UpModule) -> str:
    if any(_positive_int(row.p2p) for row in module.rows):
        return "Практическое задание с взаимной проверкой"
    return "Практическая работа"


def _current_procedure(module: UpModule) -> str:
    if any(_positive_int(row.p2p) for row in module.rows):
        return "Выполнение заданий в LMS/ЭИОС, загрузка результатов и взаимная проверка по установленным критериям"
    return "Выполнение практической работы в LMS/ЭИОС, загрузка результата и проверка преподавателем"


def _current_criteria(module: UpModule, component: str) -> str:
    outcomes: list[str] = []
    for row in module.rows:
        for source in (row.outcomes_skills, row.outcomes_can, row.outcomes_know):
            for sentence in _sentences(source):
                if sentence.casefold() not in {item.casefold() for item in outcomes}:
                    outcomes.append(sentence)
    if outcomes:
        result = (
            "Соответствие результата заданию и подтверждение результатов обучения: "
            + "; ".join(outcomes[:2])
        )
        return result[:500].rstrip(" ;,.") + "."
    return (
        f"Соответствие результата заданиям модуля «{component}», обоснованность решений, "
        "полнота выполнения и качество оформления"
    )


def _final_control_form(metadata_value: str, schedule_value: str) -> str:
    value = _compact(metadata_value) or _compact(schedule_value)
    value = re.sub(r"^итоговая аттестация\s*:\s*", "", value, flags=re.IGNORECASE)
    if not value or value.startswith("["):
        value = "Защита итоговой работы"
    return value[0].upper() + value[1:]


def _checked_result(up: UpPlan, final_module: UpModule) -> str:
    outcomes: list[str] = []
    for row in final_module.rows:
        for source in (row.outcomes_skills, row.outcomes_can, row.outcomes_know):
            for sentence in _sentences(source):
                if sentence.casefold() not in {item.casefold() for item in outcomes}:
                    outcomes.append(sentence)
    if outcomes:
        result = "Интегральный результат обучения: " + "; ".join(outcomes[:3])
        return result[:700].rstrip(" ;,.") + "."
    topic = _compact(final_module.rows[0].name) if final_module.rows else ""
    if topic:
        return (
            f"Способность выполнить и защитить итоговую работу «{topic}», применив результаты "
            "обучения и обосновав принятые решения"
        )
    return (
        f"Способность интегрировать результаты обучения программы «{_compact(up.title)}» "
        "в итоговой работе и обосновать принятые решения"
    )


def _final_duration(module: UpModule) -> str:
    hours = _compact(module.hours)
    if hours:
        return f"{hours} академических часов, включая подготовку, представление результата и ответы на вопросы"
    return "Продолжительность определяется учебным планом и включает представление результата и ответы на вопросы"


def _sentences(value: str) -> list[str]:
    return [
        _compact(item).rstrip(".;")
        for item in re.split(r"(?:\r?\n)+|(?<=[.!?])\s+", value)
        if _compact(item)
    ]


def _positive_int(value: str) -> bool:
    try:
        return int(str(value or "").strip() or "0") > 0
    except ValueError:
        return False


def _compact(value: str) -> str:
    return " ".join(str(value or "").split())


__all__ = [
    "CURRENT_CONTROL_HEADER",
    "FINAL_ATTESTATION_HEADER",
    "FINAL_ATTESTATION_PARAMETERS",
    "SCHOOL_PK_PROFILE_ID",
    "build_assessment_tables",
    "refresh_final_attestation_form",
    "validate_current_control_table",
    "validate_final_attestation_table",
]
