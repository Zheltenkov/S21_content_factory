"""Deterministic workload budget and checks. Norms live in config, not in prompts or model output."""

import math
import os
from pathlib import Path

from pydantic import BaseModel, Field

from .models import Brief, Concept, Finding, StartLevel, WorkloadBudget

NORMS_PATH = Path(os.getenv("PROJECT_LAB_WORKLOAD_NORMS", str(Path(__file__).parent / "config" / "workload.json")))
LEVEL_LABELS = {"zero": "нулевой", "basic": "базовый", "intermediate": "средний", "advanced": "продвинутый", "": "не указан"}


class TaskHours(BaseModel):
    min: float = Field(gt=0)
    max: float = Field(gt=0)


class Norms(BaseModel):
    status: str = ""
    task_hours: TaskHours
    overhead_min_share: dict[str, float]
    first_task_setup_min_hours: dict[str, float]
    # Main tasks must add up to the project hours within this many hours.
    hours_tolerance: float = Field(default=0.25, ge=0, le=5)
    # A project has no more main tasks than this, however many hours it takes (programme practice: 1–8).
    main_tasks_max: int | None = Field(default=None, ge=1, le=20)
    # How many main tasks a project usually has: the number offered when the methodologist names none. Without it
    # the number comes from the hours and the middle of the task norm — as config/workload.json has it since 29.10.2026:
    # with five for every project, one of 6 h and one of 24 h were offered the same five tasks.
    tasks_typical: int | None = Field(default=None, ge=1, le=20)


def load_norms() -> Norms:
    # Read on every call so methodologists can adjust norms without restarting the server.
    return Norms.model_validate_json(NORMS_PATH.read_text(encoding="utf-8"))


def _hours(value: float) -> str:
    return f"{value:g}".replace(".", ",")


said = _hours  # hours as the methodologist reads them: «7,5», not «7.5»


class WorkloadQuery(BaseModel):
    """The only brief fields the calculator reads; usable while the form is still incomplete."""

    hours: float | None = Field(default=None, gt=0, le=10000)
    tasks_count: int | None = Field(default=None, ge=1, le=20)
    start_level: StartLevel = ""
    tools: str = ""
    software: str = ""


def budget(brief: Brief | WorkloadQuery, norms: Norms | None = None) -> WorkloadBudget:
    norms = norms or load_norms()
    low, high = norms.task_hours.min, norms.task_hours.max
    level = brief.start_level or "unknown"
    result = WorkloadBudget(
        total_hours=brief.hours, start_level=brief.start_level, requested_tasks=brief.tasks_count,
        min_task_hours=low, max_task_hours=high, main_tasks_max=norms.main_tasks_max,
        overhead_min_share=norms.overhead_min_share.get(level, norms.overhead_min_share.get("unknown", 0)),
        first_task_setup_min=norms.first_task_setup_min_hours.get(level, norms.first_task_setup_min_hours.get("unknown", 0)),
        needs_setup=bool(brief.tools.strip() or brief.software.strip()), norms_status=norms.status,
    )
    if not brief.hours:
        result.notes.append("Трудоёмкость не задана: число и объём заданий проверить нельзя.")
        return result
    hours = brief.hours
    tasks_min = max(1, math.ceil(hours / high - 1e-9))
    tasks_max = math.floor(hours / low + 1e-9)
    cap = norms.main_tasks_max
    if tasks_max < tasks_min:
        # The whole budget is below one task's lower norm: one short task is the only honest option.
        tasks_min = tasks_max = 1
        result.notes.append(f"{_hours(hours)} ч меньше нормы одного задания ({_hours(low)} ч): возможно только одно короткое задание.")
    elif cap and cap < tasks_min:
        # More hours than the allowed number of tasks can hold at the upper norm: the tasks will be longer than the norm.
        tasks_min = tasks_max = cap
        result.notes.append(f"{_hours(hours)} ч не помещаются в {cap} заданий по {_hours(high)} ч: "
                            "задания выйдут длиннее нормы. Проверьте трудоёмкость или разделите проект.")
    elif cap:
        tasks_max = min(tasks_max, cap)
    result.tasks_min, result.tasks_max = tasks_min, tasks_max
    # Half up, as a person rounds: 8,75 h at 3,5 h a task are three tasks, not the two of round()'s half to even.
    typical = norms.tasks_typical or math.floor(hours / ((low + high) / 2) + 0.5)
    result.tasks_recommended = min(tasks_max, max(tasks_min, typical))
    requested = brief.tasks_count
    if requested and cap and requested > cap:
        # Named as what it is: the hours could hold more tasks, the norm on their number does not allow it.
        result.notes.append(f"{requested} заданий больше, чем бывает в проекте (до {cap} основных): подходит {_range(tasks_min, tasks_max)}.")
    elif requested and not tasks_min <= requested <= tasks_max:
        result.notes.append(f"{requested} заданий не помещаются в {_hours(hours)} ч при норме {_hours(low)}–{_hours(high)} ч "
                            f"на задание: подходит {_range(tasks_min, tasks_max)}.")
    return result


def overflow(plan: WorkloadBudget | None) -> float | None:
    """The most hours one project holds by the norms — the allowed number of main tasks, each at its upper norm —
    when this project's hours are more than that; None when they fit or there is nothing to compare. Such a project is
    not a case for a warning (29.10.2026: a project of 60 h was made as eight tasks like any other): the methodologist
    decides first whether to take fewer hours, split the project or keep the hours with longer tasks (concept/route.py)."""
    if not plan or not plan.total_hours or not plan.main_tasks_max:
        return None
    room = plan.main_tasks_max * plan.max_task_hours
    return room if plan.total_hours > room + 1e-9 else None


def overflow_text(plan: WorkloadBudget, room: float) -> str:
    each = plan.total_hours / plan.main_tasks_max if plan.total_hours and plan.main_tasks_max else 0
    return (f"{_hours(plan.total_hours or 0)} ч не помещаются в один проект: по нормам в нём до {plan.main_tasks_max} основных заданий "
            f"по {_hours(plan.min_task_hours)}–{_hours(plan.max_task_hours)} ч, то есть до {_hours(room)} ч. Варианты: уменьшить "
            f"трудоёмкость (напишите, например: «трудоёмкость {_hours(room)} часов»), разделить проект на два — этот и следующий, "
            f"с остатком часов, — или оставить {_hours(plan.total_hours or 0)} ч: тогда каждое задание выйдет примерно по "
            f"{_hours(round(each, 1))} ч, длиннее нормы.")


def _range(low: int, high: int) -> str:
    return str(low) if low == high else f"{low}–{high}"


def _finding(message: str, suggestion: str, locations: list[str], severity: str = "warning") -> Finding:
    return Finding(severity="blocker" if severity == "blocker" else "warning", origin="system", rule_id="constraints",
                   message=message, suggestion=suggestion, concept_locations=locations,
                   consequence="Участник не уложится в заявленное время или часы нельзя проверить.")


def check(concept: Concept, brief: Brief, plan: WorkloadBudget) -> list[Finding]:
    """Structural mismatches are blockers; deviations from draft norms are warnings."""
    findings: list[Finding] = []
    main = [t for t in concept.tasks if not t.optional]
    # Bonus tasks are extra content for faster learners: only main tasks must fill the project hours.
    total = sum(t.hours for t in main)
    level = LEVEL_LABELS.get(brief.start_level, brief.start_level)
    if brief.hours and brief.hours - total > load_norms().hours_tolerance + 1e-9:
        findings.append(_finding(f"Основные задания занимают {_hours(round(total, 2))} ч из {_hours(brief.hours)}: "
                                 f"{_hours(round(brief.hours - total, 2))} ч не распределены.",
                                 "Распределить часы по заданиям или уменьшить трудоёмкость.", ["tasks"], "blocker"))
    if plan.tasks_min and plan.tasks_max and plan.main_tasks_max and len(main) > plan.main_tasks_max:
        findings.append(_finding(f"Основных заданий {len(main)}; в проекте их бывает до {plan.main_tasks_max}, "
                                 f"при {_hours(brief.hours or 0)} ч подходит {_range(plan.tasks_min, plan.tasks_max)}.",
                                 "Объединить задания или вынести часть в бонусные.", ["tasks"], "blocker"))
    elif plan.tasks_min and plan.tasks_max and not plan.tasks_min <= len(main) <= plan.tasks_max:
        findings.append(_finding(f"Основных заданий {len(main)}; при {_hours(brief.hours or 0)} ч и норме "
                                 f"{_hours(plan.min_task_hours)}–{_hours(plan.max_task_hours)} ч подходит "
                                 f"{_range(plan.tasks_min, plan.tasks_max)}.",
                                 "Объединить или разделить задания либо изменить трудоёмкость.", ["tasks"], "blocker"))
    for number, task in enumerate(concept.tasks, 1):
        where = [f"tasks.{number}"]
        if not task.optional and not plan.min_task_hours - 0.01 <= task.hours <= plan.max_task_hours + 0.01:
            findings.append(_finding(f"Задание {number}: {_hours(task.hours)} ч вне нормы "
                                     f"{_hours(plan.min_task_hours)}–{_hours(plan.max_task_hours)} ч.",
                                     "Проверить объём задания или нормы в config/workload.json.", where))
        if not task.workload:
            findings.append(_finding(f"Задание {number}: нет разбивки часов на чтение, настройку, выполнение, ошибки и сдачу.",
                                     "Подготовить замысел заново: разбивку проверяет код.", where, "blocker"))
            continue
        parts = task.workload
        spent = parts.reading + parts.setup + parts.doing + parts.debugging + parts.submission
        if abs(spent - task.hours) > 0.05:
            findings.append(_finding(f"Задание {number}: разбивка даёт {_hours(round(spent, 2))} ч, а указано {_hours(task.hours)} ч.",
                                     "Согласовать разбивку с часами задания.", where, "blocker"))
        overhead = (spent - parts.doing) / task.hours if task.hours else 0
        if overhead + 1e-6 < plan.overhead_min_share:
            findings.append(_finding(f"Задание {number}: на чтение, настройку, ошибки и сдачу {overhead:.0%} времени; "
                                     f"норма для уровня «{level}» — от {plan.overhead_min_share:.0%}.",
                                     "Заложить время на чтение условий, отладку и повторные попытки.", where))
    if main and main[0].workload and plan.needs_setup and main[0].workload.setup + 1e-6 < plan.first_task_setup_min:
        findings.append(_finding(f"Первое задание: на настройку {_hours(main[0].workload.setup)} ч при обязательных инструментах; "
                                 f"норма для уровня «{level}» — от {_hours(plan.first_task_setup_min)} ч.",
                                 "Заложить установку и первое знакомство с инструментами.", ["tasks.1"]))
    return findings
