"""The skills of the project divided between the tasks of a concept (rules 1.1). The designer says which skills a task
practises and how much, on a scale of five; code counts the shares and checks the judgement. Boundary: a name that is
not the project's, a skill nobody practises, a task that practises nothing are said, each with its weight in the
verdict. Retention: a run started with earlier rules, and a project without skills, get exactly what they got."""

import asyncio
import json

import pytest
from project_generator_lab.concept import skills
from project_generator_lab.concept.ingestion import parse_curriculum
from project_generator_lab.concept.models import (
    Brief,
    Concept,
    Curriculum,
    Run,
    SkillBlock,
    SkillPart,
    SkillTask,
    StartRequest,
    TaskPlan,
)
from project_generator_lab.concept.planning import (
    PROMPT_VERSION,
    PROMPTS,
    check_task_skills,
    context_payload,
    fingerprint,
    prepare,
    readable_export,
)
from project_generator_lab.tests.concept.test_passport_skills import Rows, plan_of, workbook
from project_generator_lab.tests.concept.test_validation import WorkflowClient
from project_generator_lab.tests.concept.test_workbench import concept_fixture
from project_generator_lab.tests.concept.workflow_fixtures import input_card
from pydantic import ValidationError

TASK = concept_fixture().tasks[0]
HEAD = "Название проекта на платформе и в Gitlab;Название проекта"


def task(weights: dict[str, int], optional: bool = False, hours: float = 1) -> TaskPlan:
    return TASK.model_copy(update={"skills": weights, "optional": optional, "hours": hours, "workload": None})


def concept(*tasks: TaskPlan) -> Concept:
    return concept_fixture().model_copy(update={"tasks": list(tasks)})


def passport(*block: list[object], plan: tuple[str, ...] = ("P1;Первый", "P2;Второй")) -> Curriculum:
    lines: Rows = [["Проект", "Задание", "Git", "Python", "SQL", "check"], *block]
    return parse_curriculum("p.xlsx", workbook(("УП", plan_of(HEAD, *plan)), ("Навыки", lines)))


def request(curriculum: Curriculum | None = None, named: str | None = None, bonus: bool = False) -> StartRequest:
    row = curriculum.rows[0] if curriculum else None
    brief = Brief(title="Первый  проект", description="Описание проекта", audience="Новички", outcomes="Выделять факты\nОбосновывать выводы", hours=4,
                  checking="Пир проверяет", student_result="Отчёт", output_package="README и чек-лист",
                  skills=(row.skills if row else "") if named is None else named, bonus=bonus)
    return StartRequest(request_id="00000000-0000-4000-8000-000000000001", brief=brief, curriculum=curriculum, selected_row_id=row.id if row else "")


STATED = passport(["P1", "all", 0.2, 0.8, None, 1], [None, "Bonus all", None, 0.5, 0.5, 1], ["P2", "all", 1])


# ---------- the weight of a skill in a task ----------


def test_a_weight_is_a_whole_number_from_one_to_five():
    assert TaskPlan.model_validate({**TASK.model_dump(), "skills": {"Git": 1, "Python": 5, "SQL": 3.0}}).skills == {"Git": 1, "Python": 5, "SQL": 3}
    assert TASK.skills == {} and "skills" not in TASK.model_dump(exclude_defaults=True)
    # A skill the task does not practise has no weight: «0» and null for the rest of the project's skills are not an error.
    assert TaskPlan.model_validate({**TASK.model_dump(), "skills": {"Git": 2, "Python": 0, "SQL": None}}).skills == {"Git": 2}
    # «No skills» is written in more than one way, and each is read as it is meant.
    nothing: object
    for nothing in (None, [], {}):
        assert TaskPlan.model_validate({**TASK.model_dump(), "skills": nothing}).skills == {}
    # What is not a weight is not turned into one: «true» is not one, and of two weights for one name neither is taken.
    for wrong in ({"Git": 6}, {"Git": -1}, {"Git": 2.5}, {"Git": "много"}, {" ": 3}, {f"Навык {i}": 1 for i in range(31)}, ["Git"], {"Git": True},
                  {"Git": 2, "Git ": 3}, "Git"):
        with pytest.raises(ValidationError):
            TaskPlan.model_validate({**TASK.model_dump(), "skills": wrong})


# ---------- the skills of the project ----------


@pytest.mark.parametrize(("text", "names"), [
    ("Git\nPython", ["Git", "Python"]), ("Git, Python; SQL", ["Git", "Python", "SQL"]), (" Problem   solving \n\n", ["Problem solving"]),
    ("Git\ngit\nGIT ", ["Git"]), ("", []), (" ;, \n", []), ("UI & Design tools\nHTML/CSS", ["UI & Design tools", "HTML/CSS"])])
def test_the_form_names_the_skills_of_the_project(text, names):
    assert skills.names_of(text) == names


def test_the_plans_shares_are_the_projects_when_they_are_about_the_same_skills():
    project = skills.planned(request(STATED))
    assert project.names == ["Git", "Python", "SQL"] and project.code == "P1" and not project.differs
    assert project.stated == SkillBlock(code="P1", main=SkillPart(shares={"Git": 0.2, "Python": 0.8}), bonus=SkillPart(shares={"Python": 0.5, "SQL": 0.5}))
    # The form's spelling and order are the project's; the plan's block is found by the code, however it is spaced.
    respelled = skills.planned(request(STATED, "sql\n python ;GIT"))
    assert respelled.names == ["sql", "python", "GIT"] and respelled.stated is not None
    assert respelled.stated.main.shares == {"GIT": 0.2, "python": 0.8} and respelled.stated.bonus.shares == {"python": 0.5, "sql": 0.5}
    # Another set of skills in the form: the plan's shares are parts of another whole, and say nothing — and that is said.
    for other in ("Git\nPython", "Git\nPython\nSQL\nDocker", "Docker"):
        changed = skills.planned(request(STATED, other))
        assert changed.stated is None and changed.differs and changed.code == "P1", other
    # No block for the project, no plan, no skills: nothing is stated and nothing differs.
    no_block = skills.planned(request(passport(["P2", "all", 1]), "Git"))
    assert (no_block.names, no_block.code, no_block.stated, no_block.differs) == (["Git"], "P1", None, False)
    alone = skills.planned(request(None, "Git"))
    assert (alone.names, alone.code, alone.stated, alone.differs) == (["Git"], "Первый проект", None, False)
    empty = skills.planned(request(STATED, ""))
    assert (empty.names, empty.stated, empty.differs) == ([], None, False)
    assert len(skills.planned(request(None, "Git").model_copy(update={"brief": request().brief.model_copy(update={"title": "я" * 300, "skills": "Git"})})).code) == skills.MAX_CODE
    # A code longer than a line of the tab can hold is cut for the block, as a title is: it used to fail the run after
    # both paid calls (independent review 05.10.2026).
    long_code = passport(["P2", "all", 1], plan=("К" * 250 + ";Первый", "P2;Второй"))
    assert skills.planned(request(long_code, "Git")).code == "К" * skills.MAX_CODE


# ---------- divided between the tasks ----------


def test_the_tasks_of_a_concept_become_the_lines_of_a_block():
    project = skills.planned(request(None, "Git\nPython\nSQL"))
    divided = skills.divide(concept(task({"Git": 5, "python": 1}), task({"Python": 3}), task({"SQL": 2, "Docker": 4}, optional=True), task({"sql": 1, "GIT ": 1}),
                                    task({}, optional=True), task({"Kubernetes": 5})), project)
    # Main tasks are numbered apart from bonus ones, each in the order of the concept; names are the form's.
    assert divided.block == SkillBlock(code="Первый проект", main=SkillPart(shares={"Git": 0.55, "Python": 0.36, "SQL": 0.09}, tasks=[
        SkillTask(label="01", shares={"Git": 0.83, "Python": 0.25}), SkillTask(label="02", shares={"Python": 0.75}),
        SkillTask(label="03", shares={"SQL": 1.0, "Git": 0.17}), SkillTask(label="04")]),
        bonus=SkillPart(shares={"SQL": 1.0}, tasks=[SkillTask(label="Bonus 01", shares={"SQL": 1.0}), SkillTask(label="Bonus 02")]))
    # What is not a skill of the project is not counted and is not lost; a task left with nothing is named by its number in the concept.
    assert divided.strangers == {3: ["Docker"], 6: ["Kubernetes"]} and divided.idle == [5, 6] and divided.twice == {}
    assert skills.parse_blocks(skills.table(divided.block)) == [divided.block]
    # Without bonus tasks the plan's line for the bonus part stays as the plan has it — it is not ours to drop.
    stated = skills.planned(request(STATED))
    plain = skills.divide(concept(task({"Git": 1, "Python": 1}), task({"Python": 3})), stated)
    assert plain.block.bonus == SkillPart(shares={"Python": 0.5, "SQL": 0.5}) and plain.block.code == "P1"
    assert skills.parse_blocks(skills.table(plain.block)) == [plain.block]
    assert skills.divide(concept(task({"Git": 1})), skills.planned(request(None, "Git"))).block.bonus == SkillPart()
    assert plain.block.main == SkillPart(shares={"Git": 0.2, "Python": 0.8}, tasks=[SkillTask(label="01", shares={"Git": 1.0, "Python": 0.25}),
                                                                                    SkillTask(label="02", shares={"Python": 0.75})])
    with_bonus = skills.divide(concept(task({"Git": 1, "Python": 1}), task({"SQL": 2, "Python": 2}, optional=True)), stated)
    assert with_bonus.block.bonus == SkillPart(shares={"Python": 0.5, "SQL": 0.5}, tasks=[SkillTask(label="Bonus 01", shares={"SQL": 1.0, "Python": 1.0})])


def test_a_skill_is_divided_by_how_much_of_a_task_it_is_and_how_big_the_task_is():
    project = skills.planned(request(None, "Git\nPython"))
    # An hour that is all about Git and eight hours that are half about it: by the weights alone the hour took 63% of
    # the skill (independent review 05.10.2026). The weight says what the task is about; its hours say how much of it there is.
    divided = skills.divide(concept(task({"Git": 5}, hours=1), task({"Git": 3, "Python": 3}, hours=8)), project)
    assert [t.shares for t in divided.block.main.tasks] == [{"Git": 0.17}, {"Git": 0.83, "Python": 1.0}]
    assert divided.block.main.shares == {"Git": 0.55, "Python": 0.45}  # 5 + 24 and 24 of 53
    # Equal hours change nothing, whatever they are.
    for hours in (1, 2.5):
        same = skills.divide(concept(task({"Git": 5}, hours=hours), task({"Git": 3, "Python": 3}, hours=hours)), project)
        assert [t.shares for t in same.block.main.tasks] == [{"Git": 0.63}, {"Git": 0.37, "Python": 1.0}]
    # One skill under two spellings in one task: the first weight is the one counted, and the second is said.
    twice = skills.divide(concept(task({"Git": 2, "git": 5, "Python": 1, "GIT": 1})), project)
    assert twice.twice == {1: ["git", "GIT"]} and twice.strangers == {} and twice.block.main.shares == {"Git": 0.67, "Python": 0.33}


# ---------- the judgement is checked ----------


def verdict(found: Concept, asked: StartRequest) -> list[tuple[str, str]]:
    project = skills.planned(asked)
    return [(f.severity, f.message) for f in check_task_skills(skills.divide(found, project), project)]


def test_a_concept_that_divides_the_skills_well_has_nothing_said_of_it():
    assert verdict(concept(task({"Git": 5}), task({"Python": 3, "Git": 1})), request(None, "Git\nPython")) == []
    assert verdict(concept(task({"Git": 3, "Python": 3}), task({"Python": 5}), task({"SQL": 3, "Python": 3}, optional=True)), request(STATED, bonus=True)) == []
    # A plan that states the main part only: the bonus tasks practise the project's skills, and their shares are counted.
    assert verdict(concept(task({"Git": 3, "Python": 3}), task({"Python": 5}), task({"Git": 1}, optional=True)),
                   request(passport(["P1", "all", 0.2, 0.8, None]), bonus=True)) == []


def test_what_is_wrong_with_the_division_is_said_with_its_weight():
    asked = request(None, "Git\nPython\nSQL")
    assert verdict(concept(task({"Git": 5, "Docker": 2, "CI": 1}), task({}), task({"SQL": 1}, optional=True), task({"Bash": 3}, optional=True)), asked) == [
        ("blocker", "Задание 1 называет навыки не из навыков проекта: «Docker», «CI»"),
        ("blocker", "Задание 4 называет навыки не из навыков проекта: «Bash»"),
        # A skill only a bonus task practises is not earned by everyone: without the plan's word it must be in the main part.
        ("blocker", "Навыки проекта не отрабатывает ни одно основное задание: «Python», «SQL»"),
        ("warning", "Задания, которые не отрабатывают ни одного навыка проекта: 2, 4. За них участник не получит баллов навыков")]
    # Nothing weighed at all — an answer in the old format, a model that skipped the field: every skill is missing.
    assert verdict(concept(task({}), task({})), asked) == [
        ("blocker", "Навыки проекта не отрабатывает ни одно основное задание: «Git», «Python», «SQL»"),
        ("warning", "Задания, которые не отрабатывают ни одного навыка проекта: 1, 2. За них участник не получит баллов навыков")]


def test_what_the_weights_say_of_the_project_and_no_sum_shows():
    # The first run on a model, 05.10.2026, D1P1_Terminal: the plan gives «Problem solving» a fifth of the project, and
    # the concept — some of every task, 36% of the work. The block is right (the plan's shares go into it), and nothing
    # said that the concept is about something else than the plan.
    four = Curriculum.model_validate({**STATED.model_dump(), "skill_blocks": [
        {"code": "P1", "main": {"shares": {"Git": 0.2, "Python": 0.3, "Network": 0.3, "Problem solving": 0.2}}}]})
    asked = request(four, "Git\nPython\nNetwork\nProblem solving")
    designed = concept(task({"Network": 5, "Problem solving": 4}, hours=1.6), task({"Git": 5, "Problem solving": 1}, hours=1.1),
                       task({"Python": 5, "Problem solving": 2}, hours=1.1), task({"Python": 5, "Problem solving": 3}, hours=1.1),
                       task({"Git": 1, "Python": 3, "Network": 2, "Problem solving": 4}, hours=1.1))
    project = skills.planned(asked)
    divided = skills.divide(designed, project)
    assert divided.weighed == {"Network": 0.21, "Problem solving": 0.36, "Git": 0.14, "Python": 0.29}
    assert divided.peak == {"Network": 5, "Problem solving": 4, "Git": 5, "Python": 5}
    # Nine points apart is not said, sixteen is.
    assert verdict(designed, asked) == [
        ("warning", "По весам и часам заданий навыки занимают другую часть работы проекта, чем в УП: «Problem solving» — 36% при 20% в УП")]
    # The same run, D6P3_Dataquery: a skill with a weight of one in one task — that task gets the whole of the skill,
    # and the check «a skill no task practises» is satisfied. Two is still a touch; three is practice.
    touched = concept(task({"Git": 5, "Python": 2}), task({"Git": 3, "Python": 5}, optional=True))  # what a bonus task does is not what everyone does
    assert verdict(touched, request(None, "Git\nPython")) == [
        ("warning", "Навыков проекта основные задания только касаются (вес ниже 3 в каждом задании): «Python»")]
    assert verdict(concept(task({"Git": 5, "Python": 3})), request(None, "Git\nPython")) == []


def two(git: float, python: float) -> Curriculum:
    return Curriculum.model_validate({**STATED.model_dump(), "skill_blocks": [{"code": "P1", "main": {"shares": {"Git": git, "Python": python}}}]})


DRIFTED = "По весам и часам заданий навыки занимают другую часть работы проекта, чем в УП: "
TOUCHED = "Навыков проекта основные задания только касаются (вес ниже 3 в каждом задании): "


def test_with_the_plans_shares_the_plan_is_the_yardstick():
    # A skill the plan gives a twentieth of the project is rightly only touched. Said of every skill, «only touched»
    # and «another part than in the plan» could not both be met: a weight of three pushes a small skill past the plan
    # (independent check, 05.10.2026 — no clean weights at all for one task and a share under 0.3).
    light = request(two(0.95, 0.05), "Git\nPython")
    assert verdict(concept(task({"Git": 5, "Python": 1}), task({"Git": 5}, hours=3)), light) == []  # 1 of 21
    assert verdict(concept(task({"Git": 5, "Python": 1}, hours=4)), light) == [("warning", DRIFTED + "«Git» — 83% при 95% в УП; «Python» — 17% при 5% в УП")]
    # A skill the plan makes a notable part of the project — three tenths and more — has to be practised somewhere.
    # Weights that are low all round keep the plan's proportions, and the comparison of parts has nothing to say
    # (second independent check: «Git 1, Python 1» against a plan of halves passed in silence).
    assert verdict(concept(task({"Git": 1, "Python": 1}, hours=4)), request(two(0.5, 0.5), "Git\nPython")) == [
        ("warning", TOUCHED + "«Git» — доля в УП 0,5, «Python» — доля в УП 0,5")]
    third = request(two(0.3, 0.7), "Git\nPython")
    assert verdict(concept(task({"Git": 1, "Python": 2})), third) == [("warning", TOUCHED + "«Git» — доля в УП 0,3, «Python» — доля в УП 0,7")]
    assert verdict(concept(task({"Git": 2, "Python": 5})), third) == [("warning", TOUCHED + "«Git» — доля в УП 0,3")]
    assert verdict(concept(task({"Git": 2, "Python": 5})), request(two(0.29, 0.71), "Git\nPython")) == []  # under three tenths
    assert verdict(concept(task({"Git": 3, "Python": 5})), third) == []
    # Three tenths is three tenths when it is computed, too: 0.7 − 0.4 is a hair under 0.3 in a float and is shown as «0,3».
    assert verdict(concept(task({"Git": 2, "Python": 5})), request(two(0.7 - 0.4, 0.7), "Git\nPython"))[0][1].startswith(TOUCHED + "«Git» — доля в УП 0,3")
    # Ten points is ten points whichever way the fractions fall: 0.3 − 0.2 and 0.9 − 0.8 are not 0.1 in a float, and
    # a comparison of fractions said one skill of the pair and kept silent on the other.
    fifth = request(two(0.2, 0.8), "Git\nPython")
    assert verdict(concept(task({"Git": 3}, hours=1), task({"Python": 3}, hours=7 / 3)), fifth) == [
        ("warning", DRIFTED + "«Git» — 30% при 20% в УП; «Python» — 70% при 80% в УП")]
    assert verdict(concept(task({"Git": 3}, hours=1), task({"Python": 3}, hours=9)), fifth) == [
        ("warning", DRIFTED + "«Git» — 10% при 20% в УП; «Python» — 90% при 80% в УП")]
    assert verdict(concept(task({"Git": 3}, hours=29), task({"Python": 3}, hours=71)), fifth) == []  # nine points
    # A skill whose part of the work is under half a percent has no part at all, and is still compared.
    assert verdict(concept(task({"Git": 3}), task({"Python": 5}, hours=200)), fifth) == [
        ("warning", DRIFTED + "«Git» — 0% при 20% в УП; «Python» — 100% при 80% в УП")]
    assert verdict(concept(task({"Git": 3}), task({"Python": 5}, hours=200)), request(two(0.05, 0.95), "Git\nPython")) == []  # nothing is five points from 5%
    assert verdict(concept(task({"Git": 3}, hours=29), task({"Python": 3}, hours=71)), request(two(0.1234, 0.8766), "Git\nPython")) == [
        ("warning", DRIFTED + "«Git» — 29% при 12,34% в УП; «Python» — 71% при 87,66% в УП")]
    # Half a percent in the plan is not rounded away before the comparison: 52% of the work is nine and a half points
    # from 42,5%, and so is 48% from 57,5%. Rounded first, one of the pair was said and its mirror was not (second check).
    halves = request(two(0.425, 0.575), "Git\nPython")
    assert verdict(concept(task({"Git": 3}, hours=52), task({"Python": 3}, hours=48)), halves) == []
    assert verdict(concept(task({"Git": 3}, hours=53), task({"Python": 3}, hours=47)), halves) == [
        ("warning", DRIFTED + "«Git» — 53% при 42,5% в УП; «Python» — 47% при 57,5% в УП")]


def test_the_two_remarks_of_the_weights_can_be_met_together():
    """What the first version failed: for a small share there were no weights at all that passed both remarks. Now
    some weights pass for every plan of two skills in whole percents once the project has two tasks. With a single
    task they pass from 8% to 92%: under that the scale itself cannot say it — one to five is a sixth at the least."""
    from itertools import product

    def passes(plan: int, designs) -> bool:
        project = skills.planned(request(two(plan / 100, (100 - plan) / 100), "Git\nPython"))
        return any(not check_task_skills(skills.divide(designed, project), project) for designed in designs)

    def one():
        return (concept(task({"Git": git, "Python": python}, hours=4)) for git, python in product(range(1, 6), repeat=2))

    def pair():
        return (concept(task({"Git": a, "Python": b}), task({"Git": c, "Python": d}, hours=3))
                for a, b, c, d in product(range(6), repeat=4) if (a or b) and (c or d))

    assert [plan for plan in range(1, 100) if passes(plan, one())] == list(range(8, 93))
    assert all(passes(plan, pair()) for plan in range(1, 100))


def test_with_the_plans_shares_each_part_is_held_to_its_own_skills():
    asked = request(STATED, bonus=True)
    # The plan keeps «SQL» for the bonus part: a main task that practises it gives a skill the main part has no share of.
    assert verdict(concept(task({"Git": 3, "SQL": 2}), task({"Git": 1}, optional=True), task({}, optional=True)), asked) == [
        ("blocker", "Навыки проекта не отрабатывает ни одно основное задание: «Python»"),
        ("warning", "Основные задания отрабатывают навыки, у которых в этой части развесовки УП нет доли: «SQL»"),
        ("warning", "Навыки проекта не отрабатывает ни одно бонусное задание: «Python», «SQL»"),
        ("warning", "Бонусные задания отрабатывают навыки, у которых в этой части развесовки УП нет доли: «Git»"),
        ("warning", "По весам и часам заданий навыки занимают другую часть работы проекта, чем в УП: «Git» — 60% при 20% в УП"),
        ("warning", "Задания, которые не отрабатывают ни одного навыка проекта: 3. За них участник не получит баллов навыков")]
    # A concept of bonus tasks only: nobody earns the skills of the main part, and the bonus part is still looked at.
    assert verdict(concept(task({"Git": 1}, optional=True)), asked) == [
        ("blocker", "Навыки проекта не отрабатывает ни одно основное задание: «Git», «Python»"),
        ("warning", "Навыки проекта не отрабатывает ни одно бонусное задание: «Python», «SQL»"),
        ("warning", "Бонусные задания отрабатывают навыки, у которых в этой части развесовки УП нет доли: «Git»")]
    # A bonus-only skill is not asked of the main tasks. With no bonus tasks at all, the plan's bonus part has nobody
    # to earn it: «SQL» is a skill of the project that the concept leaves out, and that is said.
    assert verdict(concept(task({"Git": 3, "Python": 3}), task({"Python": 5})), asked) == [
        ("warning", "В УП у проекта есть бонусная часть развесовки («Python», «SQL»), а бонусных заданий в замысле нет")]
    assert verdict(concept(task({"Git": 3})), request(None, "Git")) == []
    assert verdict(concept(task({"Git": 3, "git": 4})), request(None, "Git")) == [
        ("blocker", "Задание 1 называет один навык дважды, в разном написании: «git» — учтён первый вес")]
    # A skill so light that its share in the project rounds to nothing is not left to vanish from the line `all`.
    light = concept(*[task({"Python": 5, "SQL": 5}) for _ in range(19)], task({"Git": 1, "Python": 5, "SQL": 5}))
    assert verdict(light, request(None, "Git\nPython\nSQL")) == [
        ("warning", "Доля навыка в проекте меньше сотой и округлилась до нуля (основная часть): «Git»"),
        ("warning", "Навыков проекта основные задания только касаются (вес ниже 3 в каждом задании): «Git»")]
    # The plan's own line does not add up: the block would be refused where it is typed in, and that is said here.
    short = passport(["P1", "all", 0.2, 0.6, None, 0.8], [None, "Bonus all", None, 0.5, 0.6])
    # (The work of the tasks is a whole and the plan's line is not: one of the skills is bound to stand apart from it.)
    assert verdict(concept(task({"Git": 3}), task({"Python": 4}, hours=2), task({"Python": 1, "SQL": 1}, optional=True)), request(short, "Git\nPython\nSQL", bonus=True)) == [
        ("warning", "Доли навыков проекта на вкладке развесовки (основная часть) в сумме дают 0,8, а не 1: платформа такой блок не примет"),
        ("warning", "Доли навыков проекта на вкладке развесовки (бонусная часть) в сумме дают 1,1, а не 1: платформа такой блок не примет"),
        ("warning", "По весам и часам заданий навыки занимают другую часть работы проекта, чем в УП: «Python» — 73% при 60% в УП")]
    assert verdict(concept(task({"Git": 3}), task({"Python": 5}, hours=2)), request(passport(["P1", "all", 0.2, 0.79, None], ["P2", "all", 1]))) == []
    # Another set of skills in the form: the shares are counted from the weights, and why is said.
    assert verdict(concept(task({"Git": 3, "Docker": 4})), request(STATED, "Git\nDocker")) == [
        ("warning", "Доли навыков в проекте посчитаны из весов заданий, а не взяты из УП: в поле «Навыки» другой набор навыков, чем в блоке проекта на вкладке развесовки")]


# ---------- in a preparation ----------


class Designer(WorkflowClient):
    def __init__(self, asked: StartRequest, designed: Concept):
        super().__init__(asked)
        self.designed = designed

    async def generate(self, schema, system, payload):
        response, metrics = await super().generate(schema, system, payload)
        return (self.designed if schema is Concept else response), metrics


def said_of_skills(run: Run) -> list[tuple[str, str, str, str]]:
    assert run.review
    return [(f.severity, f.origin, f.rule_id, f.message) for f in run.review.findings if "навык" in f.message.lower() or "развесовк" in f.message]


def prepared(asked: StartRequest, designed: Concept, version: str | None = None) -> tuple[Run, Designer]:
    run = Run(id=str(asked.request_id), request=asked, fingerprint=fingerprint(asked))
    client = Designer(asked, designed)
    asyncio.run(prepare(run, lambda r: None, client))
    run.input_approved_at, run.status = "2026-10-05T10:00:00+00:00", "running"
    if version:
        run.prompt_version = version  # a run started with earlier rules and resumed now
    asyncio.run(prepare(run, lambda r: None, client))
    assert run.status == "review" and run.review, run.error
    return run, client


def test_a_preparation_tells_the_designer_the_skills_and_keeps_what_code_divided():
    designed = concept(task({"Git": 3, "Python": 3}, hours=2), task({"Python": 5, "Kotlin": 1}, hours=2))
    run, client = prepared(request(STATED), designed)
    assert run.prompt_version == PROMPT_VERSION == "1.2"
    # The designer and the reviewer get the names as code compares them and the plan's shares for the main part. The
    # bonus part is not given: the form allows no bonus tasks. The interpreter's rules do not speak of skills weights.
    assert [name for name, _, payload in client.calls if "project_skills" in payload["computed_state"]] == ["Concept", "GroundedReview"]
    for _, _, payload in client.calls[1:]:
        assert payload["computed_state"]["project_skills"] == {"names": ["Git", "Python", "SQL"], "main": {"Git": 0.2, "Python": 0.8}}
    assert client.calls[-1][2]["concept"]["tasks"][0]["skills"] == {"Git": 3, "Python": 3}
    assert run.skill_block == SkillBlock(code="P1", main=SkillPart(shares={"Git": 0.2, "Python": 0.8}, tasks=[
        SkillTask(label="01", shares={"Git": 1.0, "Python": 0.38}), SkillTask(label="02", shares={"Python": 0.62})]),
        bonus=SkillPart(shares={"Python": 0.5, "SQL": 0.5}))
    # In the demonstration mode a blocker is shown as a remark, like every other finding of code.
    assert said_of_skills(run) == [
        ("warning", "system", "concept_integrity", "Задание 2 называет навыки не из навыков проекта: «Kotlin»"),
        ("warning", "system", "concept_integrity", "В УП у проекта есть бонусная часть развесовки («Python», «SQL»), а бонусных заданий в замысле нет")]
    # The text the methodologist downloads has the block as the lines of the tab.
    export = readable_export(run)
    assert "## Развесовка навыков по заданиям" in export
    assert "| Проект | Задание | Git | Python | SQL | check |\n| --- | --- | --- | --- | --- | --- |\n| P1 | all | 0,2 | 0,8 |  | 1 |\n|  | 01 | 1 | 0,38 |  |  |" in export
    # With bonus tasks allowed the designer is told which skills the plan keeps for them.
    _, bonus_client = prepared(request(STATED, bonus=True), designed)
    assert bonus_client.calls[1][2]["computed_state"]["project_skills"]["bonus"] == {"Python": 0.5, "SQL": 0.5}
    # A plan that states no bonus part gives none.
    _, client_3 = prepared(request(passport(["P1", "all", 0.5, 0.5, None]), bonus=True), designed)
    assert client_3.calls[1][2]["computed_state"]["project_skills"] == {"names": ["Git", "Python"], "main": {"Git": 0.5, "Python": 0.5}}
    # The rules the designer got name the field code sends and the field code reads.
    assert "computed_state.project_skills" in run.prompt_snapshots["designer"] and "- skills — " in run.prompt_snapshots["designer"]
    assert "tasks.N.skills" in run.prompt_snapshots["reviewer"]
    assert (PROMPTS / "designer.md").read_text(encoding="utf-8") == run.prompt_snapshots["designer"]
    # Stored and read back as it is.
    assert Run.model_validate_json(run.model_dump_json()).skill_block == run.skill_block
    # Without the plan's shares the designer gets the names only.
    _, alone = prepared(request(None, "Git; Python"), designed)
    assert alone.calls[1][2]["computed_state"]["project_skills"] == {"names": ["Git", "Python"]}


def test_the_request_to_the_models_has_the_parts_their_rules_name():
    # The rules name these parts by their keys («computed_state посчитан кодом…», «previous_concept — прошлый замысел»):
    # a key renamed in code would leave a rule pointing at nothing, and no test held the names (mutation run 05.10.2026).
    asked = request(None, "Git")
    run = Run(id="r", request=asked, fingerprint=fingerprint(asked), prompt_version=PROMPT_VERSION)
    first = context_payload(run)["computed_state"]
    assert isinstance(first, dict) and "project_skills" not in first  # the interpreter's request
    run.input_card = input_card(asked)
    payload = context_payload(run)
    assert set(payload) == {"brief", "selected_row_id", "input_semantics", "curriculum_map", "evidence", "coverage_warnings", "source_registry", "input_card",
                            "input_approved_at", "input_warnings", "input_review_policy", "revision_changes", "previous_concept", "previous_approved_at",
                            "allowed_evidence_ids", "fixed_rules", "computed_state", "resolved_questions", "source_policy"}
    assert list(payload)[-1] == "computed_state"  # what code counted stands last
    state = payload["computed_state"]
    assert isinstance(state, dict) and set(state) == {"program_position", "program_map", "workload_budget", "learner_term", "project_skills"}
    assert (payload["curriculum_map"], payload["selected_row_id"], payload["revision_changes"], payload["input_approved_at"]) == ([], "", [], None)
    designer = (PROMPTS / "designer.md").read_text(encoding="utf-8")
    for key in ("input_semantics", "curriculum_map", "source_registry", "input_card", "input_approved_at", "revision_changes", "previous_concept",
                "previous_approved_at", "allowed_evidence_ids", "fixed_rules", "computed_state", "selected_row_id"):
        assert key in designer, key


def test_a_project_without_skills_and_a_run_of_earlier_rules_get_what_they_got():
    designed = concept(task({"Git": 2}, hours=2), task({}, hours=2))
    # No skills in the form: nothing to divide, nothing to say, nothing more in the request to the models.
    run, client = prepared(request(STATED, ""), designed)
    assert run.skill_block is None and not said_of_skills(run)
    assert all("project_skills" not in payload["computed_state"] for _, _, payload in client.calls)
    # A run started with rules 1.0 and resumed by this release: its frozen rules do not ask for weights, so their
    # absence is not a finding, and the models get the request they got.
    older, client = prepared(request(STATED), designed, "1.0")
    assert older.prompt_version == "1.0" and older.skill_block is None
    assert not said_of_skills(older)
    assert all("project_skills" not in payload["computed_state"] for _, _, payload in client.calls)
    assert "project_skills" not in json.dumps(context_payload(older), ensure_ascii=False)
    # … and the concept as they got it: without a field their rules do not know. What the designer wrote into it
    # unasked (the answer's schema lists it for every run) is not kept, not sent on and not shown.
    assert older.concept and [t.skills for t in older.concept.tasks] == [{}, {}]
    assert all("skills" not in t for t in client.calls[-1][2]["concept"]["tasks"])
    older.baseline_concept = concept(task({"Git": 2}, hours=2), task({}, hours=2))
    previous = context_payload(older)["previous_concept"]
    assert isinstance(previous, dict) and all("skills" not in t for t in previous["tasks"])
    assert "Развесовка навыков" not in readable_export(older)
    # Under the rules that ask for weights the previous concept is given with them.
    current, _ = prepared(request(None, "Git"), concept(task({"Git": 2}, hours=4)))
    current.baseline_concept = concept(task({"Git": 2}, hours=4))
    given = context_payload(current)["previous_concept"]
    assert isinstance(given, dict) and given["tasks"][0]["skills"] == {"Git": 2}


def test_the_benchmark_report_shows_the_weights_and_the_block():
    from project_generator_lab.concept import benchmark

    run, _ = prepared(request(STATED), concept(task({"Git": 2, "Python": 1}, hours=2), task({"Python": 4}, hours=2)))
    summary = benchmark.summarize(run, "test", 1.0)
    assert summary["task_titles"] == ["Выделить факты — 2 ч · Git: 2, Python: 1", "Выделить факты — 2 ч · Python: 4"]
    assert run.skill_block and summary["skills_table"] == skills.table(run.skill_block)
    assert benchmark.markdown(summary["skills_table"]).splitlines() == [
        "| Проект | Задание | Git | Python | SQL | check |", "| --- | --- | --- | --- | --- | --- |", "| P1 | all | 0,2 | 0,8 |  | 1 |",
        "|  | 01 | 1 | 0,2 |  |  |", "|  | 02 |  | 0,8 |  |  |", "|  | check | 1 | 1 |  |  |", "|  | Bonus all |  | 0,5 | 0,5 | 1 |"]
    plain, _ = prepared(request(STATED, ""), concept(task({}, hours=4)))
    report = benchmark.summarize(plain, "test", 1.0)
    assert report["skills_table"] == [] and report["task_titles"] == ["Выделить факты — 4 ч"]


# ---------- out of the module ----------


def test_the_block_is_downloaded_as_a_file_a_spreadsheet_opens(tmp_path, monkeypatch):
    import csv
    import io

    from fastapi.testclient import TestClient
    from project_generator_lab import app as app_module
    from project_generator_lab.concept import runs as runs_module

    monkeypatch.setattr(runs_module, "RUNS_DIR", tmp_path)
    run, _ = prepared(request(STATED, bonus=True), concept(task({"Git": 3, "Python": 3}, hours=2), task({"Python": 5}, hours=2), task({"SQL": 4}, optional=True)))
    runs_module.save_run(run)
    client = TestClient(app_module.app)
    answer = client.get(f"/api/runs/{run.id}/export/skills")
    assert answer.status_code == 200 and answer.headers["content-type"] == "text/csv; charset=utf-8"
    assert answer.headers["content-disposition"] == 'attachment; filename="skills.csv"'
    # A byte-order mark for Excel, «;» between the cells, a comma in the numbers, lines ended as a spreadsheet ends them.
    assert answer.content.startswith(b"\xef\xbb\xbf") and answer.content.count(b"\r\n") == answer.content.count(b"\n") == 8
    text = answer.content.decode("utf-8-sig")
    assert text.splitlines()[:3] == ["Проект;Задание;Git;Python;SQL;check", "P1;all;0,2;0,8;;1", ";01;1;0,38;;"]
    # … and read back as the tab it is, it is the block of the run.
    assert run.skill_block and skills.parse_blocks(list(csv.reader(io.StringIO(text), delimiter=";"))) == [run.skill_block]
    assert skills.sheet(run.skill_block) == text
    # A cell a spreadsheet would run as a formula is written as text: the name came from somebody's passport.
    risky = skills.SkillBlock(code="=P1", main=skills.SkillPart(shares={"=1+1": 0.5, "+cmd": 0.2, "-x": 0.1, "@sum": 0.1, "C++; .NET": 0.1}))
    assert skills.sheet(risky).splitlines() == ["Проект;Задание;'=1+1;'+cmd;'-x;'@sum;\"C++; .NET\";check", "'=P1;all;0,5;0,2;0,1;0,1;0,1;1"]
    # A run without a block has no such file, and says so.
    plain, _ = prepared(request(STATED, ""), concept(task({}, hours=4)))
    plain.id = "00000000-0000-4000-8000-000000000002"
    runs_module.save_run(plain)
    missing = client.get(f"/api/runs/{plain.id}/export/skills")
    assert missing.status_code == 404 and missing.json()["detail"] == "У этого запуска нет развесовки навыков по заданиям"
    assert client.get(f"/api/runs/{run.id}/export/md").status_code == 200 and client.get(f"/api/runs/{run.id}/export/xls").status_code == 404
