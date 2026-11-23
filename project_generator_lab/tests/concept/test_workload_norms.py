"""The norms of config/workload.json against the programme they were checked on, and the arithmetic of the two norms
added on 05.10.2026: a cap on the number of main tasks and the typical number of tasks.

On 02.10.2026 the working group took the passport of the programme «Батон» as the standard input. Run through the
norms of that day, two of its six accepted projects got a blocker: six tasks in six hours is one hour a task, and the
lower norm was an hour and a half. The offered number of tasks was 2–4 where the programme has 5–6.
"""

import pytest
from project_generator_lab.concept.models import Brief, TaskPlan, TaskWorkload
from project_generator_lab.concept.workload import Norms, TaskHours, WorkloadQuery, budget, check, load_norms
from project_generator_lab.tests.concept.test_workbench import concept_fixture

# Code of the project, its hours in the plan, its main tasks on the skills tab of the passport.
BATON = [("D1P1_Terminal", 6, 6), ("D2P2_Errorflow", 8, 5), ("D3T1_Teamcode", 15, 5),
         ("D6P3_Dataquery", 8, 6), ("D7P4_Userflow", 8, 5), ("D8T2_MCPcore", 15, 6)]


# What the module offers when the methodologist names no number (29.10.2026: the hours divided by 3,5 h, the middle of the
# task norm). The programme made its short projects of more and shorter tasks: 6 h — six tasks, 8 h — five or six.
OFFERED = {6: 2, 8: 2, 15: 4}


@pytest.mark.parametrize(("code", "hours", "tasks"), BATON)
def test_every_accepted_project_of_the_programme_fits_the_norms_of_the_file(code, hours, tasks):
    plan = budget(WorkloadQuery(hours=hours, tasks_count=tasks))
    assert plan.notes == [], code
    # Named by the methodologist, the programme's number is within the range and stops nothing…
    assert plan.tasks_min <= tasks <= plan.tasks_max
    # …unnamed, the module offers fewer: the number grows with the hours, as decided on 29.10.2026.
    assert budget(WorkloadQuery(hours=hours)).tasks_recommended == OFFERED[hours]


def norms(low: float = 1.0, high: float = 6.0, cap: int | None = 8, typical: int | None = 5) -> Norms:
    return Norms(task_hours=TaskHours(min=low, max=high), overhead_min_share={}, first_task_setup_min_hours={},
                 main_tasks_max=cap, tasks_typical=typical)


@pytest.mark.parametrize(("hours", "low", "high", "offered"), [
    (0.5, 1, 1, 1), (1, 1, 1, 1), (2.25, 1, 2, 2), (3, 1, 3, 3), (4, 1, 4, 4), (5, 1, 5, 5), (6, 1, 6, 5), (8, 2, 8, 5),
    (9, 2, 8, 5),      # nine hours would hold nine tasks of an hour: the cap holds it at eight
    (15, 3, 8, 5), (36, 6, 8, 6), (42, 7, 8, 7), (48, 8, 8, 8),
    (49, 8, 8, 8),     # nine tasks of six hours are needed and eight are allowed: eight, and a note
])
def test_the_range_of_tasks_by_hours_cap_and_typical_number(hours, low, high, offered):
    plan = budget(WorkloadQuery(hours=hours), norms())
    assert (plan.tasks_min, plan.tasks_max, plan.tasks_recommended) == (low, high, offered)


def test_what_the_budget_says_at_the_edges():
    assert budget(WorkloadQuery(hours=0.5), norms()).notes == [
        "0,5 ч меньше нормы одного задания (1 ч): возможно только одно короткое задание."]
    assert budget(WorkloadQuery(hours=48), norms()).notes == []
    assert budget(WorkloadQuery(hours=49), norms()).notes == [
        "49 ч не помещаются в 8 заданий по 6 ч: задания выйдут длиннее нормы. Проверьте трудоёмкость или разделите проект."]
    assert budget(WorkloadQuery(hours=6, tasks_count=6), norms()).notes == []
    assert budget(WorkloadQuery(hours=6, tasks_count=7), norms()).notes == [
        "7 заданий не помещаются в 6 ч при норме 1–6 ч на задание: подходит 1–6."]
    assert budget(WorkloadQuery(hours=15, tasks_count=8), norms()).notes == []
    # Nine tasks would fit fifteen hours by the hour norm: what stops them is the norm on their number, and it is named.
    assert budget(WorkloadQuery(hours=15, tasks_count=9), norms()).notes == [
        "9 заданий больше, чем бывает в проекте (до 8 основных): подходит 3–8."]
    assert budget(WorkloadQuery(hours=15, tasks_count=9), norms(cap=None)).notes == []
    assert budget(WorkloadQuery(hours=15, tasks_count=16), norms(cap=None)).notes == [
        "16 заданий не помещаются в 15 ч при норме 1–6 ч на задание: подходит 3–15."]
    # Too few is about hours, with or without a cap; a range of one number is written as one number.
    assert budget(WorkloadQuery(hours=15, tasks_count=2), norms()).notes == [
        "2 заданий не помещаются в 15 ч при норме 1–6 ч на задание: подходит 3–8."]
    assert budget(WorkloadQuery(hours=48, tasks_count=9), norms()).notes == ["9 заданий больше, чем бывает в проекте (до 8 основных): подходит 8."]
    assert budget(WorkloadQuery(hours=48, tasks_count=7), norms()).notes == [
        "7 заданий не помещаются в 48 ч при норме 1–6 ч на задание: подходит 8."]
    assert (budget(WorkloadQuery(hours=15), norms()).main_tasks_max, budget(WorkloadQuery(hours=15), norms(cap=None)).main_tasks_max) == (8, None)


def test_each_of_the_two_norms_works_without_the_other():
    # No cap: the hours alone bound the number of tasks.
    assert (budget(WorkloadQuery(hours=15), norms(cap=None)).tasks_max, budget(WorkloadQuery(hours=60), norms(cap=None)).notes) == (15, [])
    # No typical number: the number comes from the hours and the middle of the task norm, as before 05.10.2026.
    by_hours = [budget(WorkloadQuery(hours=h), norms(typical=None)).tasks_recommended for h in (3, 7, 12, 21, 40)]
    assert by_hours == [1, 2, 3, 6, 8]
    # A typical number above the range or below it is brought into the range.
    assert budget(WorkloadQuery(hours=3), norms(typical=20)).tasks_recommended == 3
    assert budget(WorkloadQuery(hours=42), norms(typical=1)).tasks_recommended == 7
    with pytest.raises(ValueError):
        norms(cap=0)
    with pytest.raises(ValueError):
        norms(typical=0)


def task(hours: float) -> TaskPlan:
    return TaskPlan(title="Задание", action="Сделать", outcome_numbers=[1], deliverable="Файл", peer_check="Пир",
                    independent_choice="Выбор", support="Опора", evidence_ids=["form.outcomes"], hours=hours,
                    workload=TaskWorkload(reading=hours * 0.2, setup=0, doing=hours * 0.6, debugging=hours * 0.1, submission=hours * 0.1))


def test_a_concept_with_more_main_tasks_than_the_cap_is_stopped():
    brief = Brief(title="Проект", description="Описание проекта", audience="", outcomes="Делать", hours=15)
    concept = concept_fixture()
    concept.tasks = [task(15 / 9) for _ in range(9)]
    stopped = [(f.severity, f.message, f.suggestion, f.concept_locations)
               for f in check(concept, brief, budget(brief, norms())) if "Основных заданий" in f.message]
    assert stopped == [("blocker", "Основных заданий 9; в проекте их бывает до 8, при 15 ч подходит 3–8.",
                        "Объединить задания или вынести часть в бонусные.", ["tasks"])]
    concept.tasks = [task(15 / 8) for _ in range(8)]
    assert not [m for _, m in check(concept, brief, budget(brief, norms())) if "Основных заданий" in m]
    # Too few tasks is about hours; without a cap nine tasks in fifteen hours are fine.
    concept.tasks = [task(7.5) for _ in range(2)]
    assert [f.message for f in check(concept, brief, budget(brief, norms())) if "Основных заданий" in f.message] == [
        "Основных заданий 2; при 15 ч и норме 1–6 ч подходит 3–8."]
    concept.tasks = [task(15 / 9) for _ in range(9)]
    assert not [f for f in check(concept, brief, budget(brief, norms(cap=None))) if "Основных заданий" in f.message]
    # Six tasks of an hour each in six hours: the first project of the programme, a blocker before 05.10.2026.
    brief.hours = 6
    concept.tasks = [task(1) for _ in range(6)]
    assert [f for f in check(concept, brief, budget(brief, norms())) if f.severity == "blocker"] == []


def test_the_file_says_what_its_numbers_were_checked_against():
    file = load_norms()
    assert "Батон" in file.status and "не сверены с временем участников" in file.status and "29.10.2026" in file.status
    # No typical number: the number of tasks grows with the hours (6 h — 2, 12 h — 3, 24 h — 7, 60 h — the cap of 8).
    assert file.task_hours.min <= 1.0 and file.main_tasks_max == 8 and file.tasks_typical is None
    assert [budget(WorkloadQuery(hours=h)).tasks_recommended for h in (6, 8.75, 12, 24, 48, 60)] == [2, 3, 3, 7, 8, 8]
