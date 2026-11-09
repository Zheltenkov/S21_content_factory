"""The skills of a project divided between its tasks: which task practises what is a judgement (a weight per task and
skill), the shares are arithmetic. Boundary: the shares add up to exactly one whatever the weights; what does not fit
the plan's skills is named, never made to fit. Retention: a block written out as the lines of a tab is read back as
the same block — every block of the three skills tabs of the passport «Батон»."""

import csv
import itertools
from pathlib import Path

import pytest
from project_generator_lab.concept import skills
from project_generator_lab.concept.models import SkillBlock, SkillPart, SkillTask

FIXTURES = Path(__file__).parent / "fixtures"
MAIN = PARTS_MAIN = skills.PARTS["main"]


def records(name: str) -> list[list[str]]:
    with (FIXTURES / name).open(encoding="utf-8-sig", newline="") as source:
        return list(csv.reader(source))


def hundredths(shares: dict) -> list[int]:
    return [round(value * 100) for value in shares.values()]


@pytest.mark.parametrize(("parts", "shares"), [
    ({"a": 1}, {"a": 1.0}),
    ({"a": 1, "b": 1}, {"a": 0.5, "b": 0.5}),
    # A hundredth that rounding leaves over goes to whoever lost most; of two that lost the same, to the earlier.
    ({"a": 1, "b": 1, "c": 1}, {"a": 0.34, "b": 0.33, "c": 0.33}),
    ({"a": 1, "b": 2, "c": 3}, {"a": 0.17, "b": 0.33, "c": 0.5}),
    ({"a": 3, "b": 2, "c": 1}, {"a": 0.5, "b": 0.33, "c": 0.17}),
    ({"a": 1, "b": 1, "c": 1, "d": 1, "e": 1, "f": 1}, {"a": 0.17, "b": 0.17, "c": 0.17, "d": 0.17, "e": 0.16, "f": 0.16}),
    ({"a": 2, "b": 1, "c": 1, "d": 3}, {"a": 0.29, "b": 0.14, "c": 0.14, "d": 0.43}),
    # Not weights only: any parts of a whole, whatever they add up to.
    ({"a": 0.29, "b": 0.71}, {"a": 0.29, "b": 0.71}),
    ({"a": 0.2, "b": 0.2}, {"a": 0.5, "b": 0.5}),
    ({"a": 58, "b": 29, "c": 29}, {"a": 0.5, "b": 0.25, "c": 0.25}),
    # What is not above zero has no share, and nothing at all is nothing.
    ({"a": 1, "b": 0, "c": -2}, {"a": 1.0}),
    ({"a": 0}, {}), ({}, {}),
    # A part below a hundredth of the whole can be left with nothing: there are no shares smaller than a hundredth.
    ({"a": 1000, "b": 1}, {"a": 1.0}),
])
def test_parts_become_shares_that_add_up_to_exactly_one(parts, shares):
    assert skills.exact(parts) == shares


def test_whatever_the_weights_the_shares_are_hundredths_and_their_sum_is_one():
    for count in range(1, 9):  # a project has up to eight main tasks
        for weights in itertools.product((1, 2, 3), repeat=count if count < 7 else 6):
            given = dict(enumerate([*weights, *[2] * (count - len(weights))]))
            shares = skills.exact(given)
            assert sum(hundredths(shares)) == 100 and list(shares) == list(given), given
            assert all(abs(value * 100 - round(value * 100)) < 1e-9 for value in shares.values()), given
            # A heavier task never gets less than a lighter one, and equal ones differ by a hundredth at most.
            for (a, b) in itertools.combinations(given, 2):
                if given[a] > given[b]:
                    assert shares[a] >= shares[b], given
                if given[a] == given[b]:
                    assert abs(shares[a] - shares[b]) < 0.0101, given


def test_a_part_is_counted_from_what_its_tasks_practise():
    part = skills.spread({}, [("01", {"Git": 3, "Python": 1}), ("02", {"Python": 2}), ("03", {"Git": 1, "SQL": 1, "Python": 0})])
    # A skill's share in the project is its part of all the weight; a skill is divided between the tasks by their weights.
    # (Git 4, Python 3 and SQL 1 of 8: 0,5, 0,375 and 0,125 — the hundredth left over goes to the earlier of the two halves.)
    assert part == SkillPart(shares={"Git": 0.5, "Python": 0.38, "SQL": 0.12}, tasks=[
        SkillTask(label="01", shares={"Git": 0.75, "Python": 0.33}),
        SkillTask(label="02", shares={"Python": 0.67}),
        SkillTask(label="03", shares={"Git": 0.25, "SQL": 1.0})])
    assert skills.part_issues(part, MAIN) == []
    assert skills.spread({}, []) == SkillPart() and skills.spread({}, [("01", {})]) == SkillPart(tasks=[SkillTask(label="01")])
    # Two tasks under one label are two tasks.
    same = skills.spread({}, [("Блок", {"Git": 1}), ("Блок", {"Git": 3})])
    assert [task.shares for task in same.tasks] == [{"Git": 0.25}, {"Git": 0.75}]


def test_the_shares_the_plan_states_are_taken_as_they_are():
    stated = {"Git": 0.2, "Python": 0.3, "Network & system administration": 0.3, "Problem solving": 0.2}  # D1P1_Terminal, the line `all`
    tasks = [("01", {"Git": 1, "Network & system administration": 1}), ("02", {"Python": 3, "Problem solving": 2}),
             ("03", {"Git": 3, "Python": 1, "Network & system administration": 2, "Problem solving": 1})]
    part = skills.spread(stated, tasks)
    assert part.shares == stated and part.shares is not stated
    assert [task.shares for task in part.tasks] == [
        {"Git": 0.25, "Network & system administration": 0.33}, {"Python": 0.75, "Problem solving": 0.67},
        {"Git": 0.75, "Python": 0.25, "Network & system administration": 0.67, "Problem solving": 0.33}]
    assert skills.part_issues(part, MAIN) == []


def test_what_does_not_fit_the_plan_is_named_and_not_made_to_fit():
    stated = {"Git": 0.5, "Python": 0.3, "SQL": 0.2}
    part = skills.spread(stated, [("01", {"Git": 2}), ("02", {"Git": 1, "Docker": 2}), ("03", {})])
    # The plan's skill no task practises keeps its share, the task's skill the plan does not have keeps its weight,
    # the task that practises nothing stays in the list: each is said in the words of the tab's own checks.
    assert part.shares == stated and [task.shares for task in part.tasks] == [{"Git": 0.67}, {"Git": 0.33, "Docker": 1.0}, {}]
    assert skills.part_issues(part, MAIN) == [
        f"{MAIN}: «Python» — доля 0,3, по заданиям сумма 0 вместо 1", f"{MAIN}: «SQL» — доля 0,2, по заданиям сумма 0 вместо 1",
        f"{MAIN}: «Docker» — доли в проекте нет, а задания дают 1", f"{MAIN}: без навыков проекта остаются задания 03"]


def test_a_block_is_written_as_the_lines_of_the_tab():
    block = SkillBlock(code="P1", main=skills.spread({}, [("01", {"Git": 3, "Python": 1}), ("02", {"Python": 2})]),
                       bonus=skills.spread({"SQL": 1.0}, [("Bonus 01", {"SQL": 1, "Git": 1})]))
    assert skills.table(block) == [
        ["Проект", "Задание", "Git", "Python", "SQL", "check"],
        ["P1", "all", "0,5", "0,5", "", "1"],
        ["", "01", "1", "0,33", "", ""],
        ["", "02", "", "0,67", "", ""],
        ["", "check", "1", "1", "", ""],
        ["", "Bonus all", "", "", "1", "1"],
        ["", "Bonus 01", "1", "", "1", ""],
        ["", "check", "1", "", "1", ""]]
    # No bonus part — no bonus lines; a project not divided into tasks yet has only its line `all`.
    assert skills.table(SkillBlock(code="P2", main=SkillPart(shares={"Git": 0.6, "SQL": 0.4}))) == [
        ["Проект", "Задание", "Git", "SQL", "check"], ["P2", "all", "0,6", "0,4", "1"]]
    assert skills.table(SkillBlock(code="P3")) == [["Проект", "Задание", "check"], ["P3", "all", "0"]]
    # A sum that is not one is written as it is: the tab's own check shows it.
    assert skills.table(SkillBlock(code="P4", main=SkillPart(shares={"Git": 0.6, "SQL": 0.3})))[1] == ["P4", "all", "0,6", "0,3", "0,9"]
    assert skills.table(SkillBlock(code="P5", bonus=SkillPart(tasks=[SkillTask(label="Bonus 01", shares={"Git": 0.5})]))) == [
        ["Проект", "Задание", "Git", "check"], ["P5", "all", "", "0"], ["", "Bonus all", "", "0"], ["", "Bonus 01", "0,5", ""], ["", "check", "0,5", ""]]


@pytest.mark.parametrize("fixture", ["baton_skills.csv", "baton_skills_old.csv", "baton_skills_exams.csv"])
def test_every_block_of_the_passport_written_out_is_read_back_the_same(fixture):
    # What a block says of its own cells (the exams' tab has a cell that is not a share) is said at reading and is not
    # part of what is written out.
    blocks = [block.model_copy(update={"issues": []}) for block in skills.parse_blocks(records(fixture))]
    assert len(blocks) >= 2 and sum(len(block.main.tasks) + len(block.bonus.tasks) for block in blocks) >= 10
    for block in blocks:
        assert skills.parse_blocks(skills.table(block)) == [block], block.code
    # … and all of them one under another, as on the tab: under one header.
    lines = [["Проект", "Задание", *records(fixture)[0][2:]]]
    names = lines[0][2:-1]
    for block in blocks:
        own = skills.table(block)
        at = {name: own[0].index(name) for name in own[0][2:-1]}
        lines.extend([line[0], line[1], *[line[at[name]] if name in at else "" for name in names], line[-1]] for line in own[1:])
    assert skills.parse_blocks(lines) == blocks


def test_a_spread_written_out_and_read_back_passes_the_checks_of_a_tab():
    stated = {"Git": 0.2, "Python": 0.3, "Problem solving": 0.5}
    main = skills.spread(stated, [(f"{i:02}", weights) for i, weights in enumerate(
        [{"Git": 1, "Problem solving": 1}, {"Python": 2, "Problem solving": 2}, {"Git": 3, "Python": 1}, {"Python": 3, "Problem solving": 3}], 1)])
    bonus = skills.spread({}, [("Bonus 01", {"Python": 2}), ("Bonus 02", {"Python": 1, "Git": 1})])
    block = SkillBlock(code="D1P1_Terminal", main=main, bonus=bonus)
    read, = skills.parse_blocks(skills.table(block))
    assert read == block and skills.block_issues(read, skills.strangers(skills.skill_names(read), ["Git", "Python", "Problem solving"])) == []
    assert [sum(hundredths({task.label: task.shares.get(name, 0) for task in read.main.tasks})) for name in stated] == [100, 100, 100]
