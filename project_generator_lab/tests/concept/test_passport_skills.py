"""The skills of a programme passport (РГ 02.10.2026): the catalogue «Available skills» and the tab «Развесовка
skills» — what share of each skill a project gives and how the share is split between its tasks.

The platform refuses a project whose shares do not come to one or whose skill is not in its catalogue. Until
05.10.2026 the module read none of this: the plan of a standard passport has no skills column, so every project came
without skills, and the participant's path («этот навык уже был, этот новый») was counted on nothing. The fixtures are
the passport's own tabs, with the discrepancies they really have.
"""

import csv
import functools
import io
import re
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from project_generator_lab import app as app_module
from project_generator_lab.concept import ingestion, skills
from project_generator_lab.concept import runs as runs_module
from project_generator_lab.concept.ingestion import parse_curriculum
from project_generator_lab.concept.input_semantics import program_map
from project_generator_lab.concept.models import Brief, Curriculum, SkillBlock, SkillPart, SkillTask, StartRequest
from project_generator_lab.concept.validation import check_skill_names, input_review_findings
from project_generator_lab.core import isolated
from project_generator_lab.tests.concept.test_validation import new_run

FIXTURES = Path(__file__).parent / "fixtures"
Rows = Sequence[Sequence[object]]


def records(name: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO((FIXTURES / name).read_text(encoding="utf-8"))))


def cells(name: str) -> Rows:
    """A fixture tab as a spreadsheet keeps it: numbers as numbers, the flag of the catalogue as a boolean."""
    def cell(text: str) -> object:
        if text in ("TRUE", "FALSE"):
            return text == "TRUE"
        if re.fullmatch(r"\d+(\.\d+)?", text) and not re.fullmatch(r"0\d+", text):  # «01» is a task's label
            return float(text)
        return text or None
    return [[cell(text) for text in record] for record in records(name)]


PLAN, SKILLS, OLD, EXAMS, CATALOG = (cells(f"baton_{name}.csv") for name in ("plan", "skills", "skills_old", "skills_exams", "available_skills"))
TAB, OLD_TAB, EXAMS_TAB = "Развесовка skills - ВЕРСИЯ со С", "Развесовка skills (OLD)", "Развесовка_exam skills"
CATALOG_NAMES = [record[1] for record in records("baton_available_skills.csv")[1:]]


def workbook(*tabs: tuple[str, Rows] | tuple[str, Rows, str]) -> bytes:
    book = Workbook()
    book.remove(book.active)
    for title, rows, *state in tabs:
        sheet = book.create_sheet(title)
        sheet.sheet_state = state[0] if state else "visible"
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


@functools.cache
def passport_file(old: bool) -> bytes:
    """The passport as one file: the choice of a tab is made on the same workbook sent again, byte for byte."""
    tabs: list[Any] = [("УП", PLAN), ("Available skills", CATALOG), (TAB, SKILLS), (EXAMS_TAB, EXAMS)]
    if old:
        tabs.insert(2, (OLD_TAB, OLD))
    return workbook(*tabs)


def passport(skills_tab: str = "", old: bool = False) -> Curriculum:
    return parse_curriculum("Паспорт программы_Батон.xlsx", passport_file(old), skills_tab)


# ---------- the tab as it is ----------


def test_the_real_tab_is_read_as_blocks_of_projects():
    blocks = {block.code: block for block in skills.parse_blocks(records("baton_skills.csv"))}
    assert list(blocks) == ["D1P1_Terminal", "D2P2_Errorflow", "D3T1_Teamcode", "D6P3_Dataquery", "D7P4_Userflow", "D8-9P5_MCP"]
    first = blocks["D1P1_Terminal"]
    assert first.main.shares == {"Git": 0.2, "Python": 0.3, "Network & system administration": 0.3, "Problem solving": 0.2}
    assert [task.label for task in first.main.tasks] == ["01", "02", "03", "04", "05", "06"]
    # Zeros are not kept: the third task gives Git and nothing else.
    assert first.main.tasks[2].shares == {"Git": 0.65}
    assert first.bonus.shares == {"Git": 0.1, "Python": 0.5, "Network & system administration": 0.1, "Problem solving": 0.3}
    assert [(task.label, task.shares["Python"]) for task in first.bonus.tasks] == [("Bonus 01", 0.4), ("Bonus 02", 0.6)]
    # How many main and bonus tasks the programme's projects have, and how many skills.
    assert {code: (len(b.main.tasks), len(b.bonus.tasks), len(b.main.shares)) for code, b in blocks.items()} == {
        "D1P1_Terminal": (6, 2, 4), "D2P2_Errorflow": (5, 2, 4), "D3T1_Teamcode": (5, 2, 4),
        "D6P3_Dataquery": (6, 1, 5), "D7P4_Userflow": (5, 3, 6), "D8-9P5_MCP": (6, 3, 6)}
    assert not any(block.issues for block in blocks.values())  # every cell of the tab is a share


def test_code_finds_what_does_not_add_up_on_the_real_tab_and_nothing_else():
    found = {block.code: issues_of(block, CATALOG_NAMES) for block in skills.parse_blocks(records("baton_skills.csv"))}
    assert found == {
        "D1P1_Terminal": [], "D2P2_Errorflow": [], "D3T1_Teamcode": [],
        "D6P3_Dataquery": ["бонусная часть: «AI Engineering» — доля 0,2, по заданиям сумма 0 вместо 1"],
        "D7P4_Userflow": ["бонусная часть: «DB & Data» — доли в проекте нет, а задания дают 1",
                          "бонусная часть: «SQL» — доли в проекте нет, а задания дают 1"],
        "D8-9P5_MCP": ["бонусная часть: заданий 3, а долей навыков нет"]}


# ---------- reading a tab ----------


def issues_of(block: SkillBlock, catalog: list[str]) -> list[str]:
    return skills.block_issues(block, skills.strangers(skills.skill_names(block), catalog) if catalog else {})


def tab(*lines: str) -> list[list[str]]:
    return [line.split(";") for line in ("Проект;Задание;Git;Python;check", *lines)]


def test_the_lines_of_a_block_are_told_apart_by_their_label():
    block, = skills.parse_blocks(tab("P1;ALL;0,4;0,6;1", ";1;1;0,5", ";2;;0,5", ";Check;1;1", ";bonus ALL;1;;1", ";Бонус 01;1", ";check;1"))
    assert block.main == SkillPart(shares={"Git": 0.4, "Python": 0.6}, tasks=[
        SkillTask(label="1", shares={"Git": 1.0, "Python": 0.5}), SkillTask(label="2", shares={"Python": 0.5})])
    assert block.bonus == SkillPart(shares={"Git": 1.0}, tasks=[SkillTask(label="Бонус 01", shares={"Git": 1.0})])
    # A code may stand on the line of the first task; lines above the first code belong to no project.
    late, = skills.parse_blocks(tab(";01;1;1", "P2;01;1;", ";all;1;"))
    assert (late.code, late.main.shares, [t.label for t in late.main.tasks]) == ("P2", {"Git": 1.0}, ["01"])
    # «Bonus» is a word of its own: a task called «Bonuses» or «Блок 1» is a main task.
    named, = skills.parse_blocks(tab("P3;all;1;", ";Блок 1;1;", ";Bonuses;;", ";Bonus all;;1", ";Bonus-1;;1"))
    assert [t.label for t in named.main.tasks] == ["Блок 1", "Bonuses"] and [t.label for t in named.bonus.tasks] == ["Bonus-1"]
    # Not a skills tab at all, an empty one, a header alone.
    assert skills.parse_blocks([["Название проекта", "Часы"], ["А", "4"]]) == []
    assert skills.parse_blocks([]) == [] and skills.parse_blocks(tab()) == []
    # The header is looked for by its names, wherever they stand; a short line is a line with empty cells.
    moved, = skills.parse_blocks([["", "Git", "Задание", "check", "Проект"], ["0,5", "all", "", "", "P4"], ["1"]])
    assert (moved.code, moved.main.shares, moved.main.tasks) == ("P4", {}, [])


@pytest.mark.parametrize(("cell", "share"), [
    ("", 0.0), (" ", 0.0), ("0", 0.0), ("0,25", 0.25), ("0.25", 0.25), (" 1 ", 1.0), ("0.15000000000000002", 0.15),
    # To the ten-thousandth: an eighth stays an eighth. What a formula leaves around one and zero is one and zero.
    ("0.125", 0.125), ("0.0625", 0.0625), ("0.12345", 0.1235), ("1.0000000000000002", 1.0), ("-2.7755575615628914e-17", 0.0),
    ("0.00004", 0.0), ("1.00004", 1.0), ("+0,5", 0.5), (".5", 0.5), ("1.", 1.0), ("1e-1", 0.1), ("1E0", 1.0), ("-0.0", 0.0),
    # float() reads more than a table writes: none of these is a share.
    ("0_1", None), ("١", None), ("１", None), ("0x1", None), ("1e", None), (".", None), ("0.5.1", None), ("0 5", None), ("infinity", None),
    ("1,01", None), ("1.0001", None), ("-0,1", None), ("-0.0001", None), ("20", None), ("abc", None), ("25%", None),
    ("nan", None), ("inf", None), ("1e999", None)])
def test_a_cell_is_a_share_from_zero_to_one(cell, share):
    assert skills.number(cell) == share


def test_a_cell_that_is_not_a_share_is_reported_and_not_guessed():
    block, = skills.parse_blocks(tab("P1;all;0,4;60%;1", ";01;1;много"))
    assert block.main.shares == {"Git": 0.4} and block.main.tasks[0].shares == {"Git": 1.0}
    assert block.issues == ["строка «all», «Python»: «60%» — не доля от 0 до 1", "строка «01», «Python»: «много» — не доля от 0 до 1"]
    assert issues_of(block, []) == [*block.issues, "основная часть: сумма долей навыков 0,4 вместо 1"]


def test_what_a_tab_says_twice_is_taken_once_and_said():
    # A second line `all` — a code that slipped a line down, a block pasted twice — used to replace the first without a word.
    block, = skills.parse_blocks(tab("P1;all;1;;1", ";01;1;", ";all;;1;1", ";Bonus all;1;", ";bonus ALL;;1"))
    assert (block.main.shares, block.bonus.shares) == ({"Git": 1.0}, {"Git": 1.0})
    assert block.issues == ["строка «all» встречается в блоке дважды: взята первая", "строка «bonus ALL» встречается в блоке дважды: взята первая"]
    # The same skill in two columns: the first is the skill's column, the second is reported where it holds a share.
    twice = skills.parse_blocks([["Проект", "Задание", "Git", "Python", "git "], ["P1", "all", "0,4", "0,6", "0,5"], ["", "01", "1", "1", ""],
                                 ["", "02", "", "", "0,5"], ["P2", "all", "1", "", ""]])
    assert twice[0].main.shares == {"Git": 0.4, "Python": 0.6}
    assert twice[0].issues == ["колонка «git» на вкладке повторяется: значения второй не учтены"]
    assert twice[1].issues == []


def test_the_header_names_the_skills_between_the_two_columns_and_check():
    block, = skills.parse_blocks([["Проект", "Задание", "Network &\nsystem  administration", "Git", "check", "Комментарий", "XP"],
                                  ["P1", "all", "0,5", "0,5", "1", "0,3", "80"]])
    # A header broken over two lines is one name; what stands to the right of «check» is not a skill.
    assert block.main.shares == {"Network & system administration": 0.5, "Git": 0.5} and block.issues == []
    assert skills.unknown(list(block.main.shares), CATALOG_NAMES) == []


@pytest.mark.parametrize(("label", "line"), [
    ("all", (False, "all")), (" ALL ", (False, "all")), ("check", (False, "check")), ("Check", (False, "check")), ("01", (False, "task")),
    ("Блок 1", (False, "task")), ("Bonuses", (False, "task")), ("Bonusall", (False, "task")), ("allow", (False, "task")),
    ("Bonus all", (True, "all")), ("BONUS  ALL", (True, "all")), ("Бонус all", (True, "all")), ("Bonus: all", (True, "all")), ("bonus-all", (True, "all")),
    ("Bonus check", (True, "check")), ("Bonus 01", (True, "task")), ("Bonus01", (True, "task")), ("Bonus_02", (True, "task")),
    ("bonus", (True, "task")), ("Бонус 2", (True, "task"))])
def test_what_a_line_is_by_its_label(label, line):
    assert skills.line_of(label) == line


def test_a_note_left_on_the_tab_is_not_a_line_of_the_table_and_a_block_has_a_limit():
    note = "я" * (skills.MAX_CODE + 1)
    long_label = "я" * (skills.MAX_LABEL + 1)
    blocks = skills.parse_blocks(tab("P1;all;1;", f"{note};all;;1", f";{long_label};1;", ";01;1;", f"{'P' * skills.MAX_CODE};{'я' * skills.MAX_LABEL};1;"))
    assert [(b.code, b.main.shares, [task.label for task in b.main.tasks]) for b in blocks] == [
        ("P1", {"Git": 1.0}, ["01"]), ("P" * skills.MAX_CODE, {}, ["я" * skills.MAX_LABEL])]
    # More tasks than a project can have: the first fifty are read, the rest are said to be left out, once.
    many, = skills.parse_blocks(tab("P1;all;1;", *[f";{i:02};0,02;" for i in range(1, skills.MAX_TASKS + 3)], *[f";Bonus {i};;" for i in range(1, 60)]))
    assert (len(many.main.tasks), len(many.bonus.tasks)) == (skills.MAX_TASKS, skills.MAX_TASKS)
    assert many.issues == [f"основная часть: заданий больше {skills.MAX_TASKS} — остальные не прочитаны",
                           f"бонусная часть: заданий больше {skills.MAX_TASKS} — остальные не прочитаны"]
    exact, = skills.parse_blocks(tab("P1;all;1;", *[f";{i:02};0,02;" for i in range(1, skills.MAX_TASKS + 1)]))
    assert (len(exact.main.tasks), exact.issues) == (skills.MAX_TASKS, [])
    SkillBlock.model_validate(many.model_dump())  # what is kept is what the server will take back with a start request


def test_a_tab_written_in_percents_names_a_few_cells_and_counts_the_rest():
    lines = tab("P1;all;40;60", ";01;100;", ";02;;100", "P2;all;0,5;50%", "P3;all;1;", *[f";{i:02};2;3" for i in range(1, 13)], ";all;7;7")
    first, second, third = skills.parse_blocks(lines)
    # Every cell of a tab written the other way is «not a share»: a block names the first three and says how many more.
    assert first.issues == ["строка «all», «Git»: «40» — не доля от 0 до 1", "строка «all», «Python»: «60» — не доля от 0 до 1",
                            "строка «01», «Git»: «100» — не доля от 0 до 1", "и ещё в 1 ячейке не доля от 0 до 1"]
    assert second.issues == ["строка «all», «Python»: «50%» — не доля от 0 до 1"]
    # The cells come first, counted over the whole block; a line said twice is said once however many times it stands.
    assert third.issues[3:] == ["и ещё в 23 ячейках не доля от 0 до 1", "строка «all» встречается в блоке дважды: взята первая"]
    assert len(skills.parse_blocks(tab("P1;all;1;", *[";all;2;2"] * 7))[0].issues) == 3 + 1 + 1
    eleven, = skills.parse_blocks(tab("P1;all;1;", *[";01;2;2"] * 7))
    assert eleven.issues[-1] == "и ещё в 11 ячейках не доля от 0 до 1"
    assert skills.parse_blocks(tab("P1;all;1;", *[";01;2;2"] * 57))[0].issues[-2] == "и ещё в 111 ячейках не доля от 0 до 1"
    assert skills.parse_blocks(tab("P1;all;1;", *[";01;2;2"] * 12))[0].issues[-1] == "и ещё в 21 ячейке не доля от 0 до 1"
    three, = skills.parse_blocks(tab("P1;all;5;5", ";01;5;"))
    assert len(three.issues) == skills.MAX_NAMED and not three.issues[-1].startswith("и ещё")


def test_a_plan_with_a_tab_full_of_wrong_cells_can_still_be_started():
    # Ten projects of six tasks, 94 skills, every cell «25»: the notes used to be a megabyte, more than a start request
    # may weigh — the plan loaded and no project of it could be started.
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", *[f"P{i};Проект {i}" for i in range(1, 11)])
    block: list[list[object]] = [["Проект", "Задание", *CATALOG_NAMES, "check"]]
    for i in range(1, 11):
        block.append([f"P{i}", "all", *[25] * len(CATALOG_NAMES)])
        block.extend([None, f"{n:02}", *[25] * len(CATALOG_NAMES)] for n in range(1, 7))
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Available skills", CATALOG)))
    assert len(read.model_dump_json().encode()) < 100_000
    assert read.skill_notes[1] == ("Развесовка «P1»: строка «all», «" + CATALOG_NAMES[0] + "»: «25» — не доля от 0 до 1; строка «all», «" + CATALOG_NAMES[1]
                                   + "»: «25» — не доля от 0 до 1; строка «all», «" + CATALOG_NAMES[2] + "»: «25» — не доля от 0 до 1; "
                                   f"и ещё в {7 * len(CATALOG_NAMES) - 3} ячейках не доля от 0 до 1; основная часть: заданий 6, а долей навыков нет.")
    assert request(read, "P1").curriculum is not None


def test_what_a_skills_header_is():
    assert skills.is_skills_header(["Проект", "Задание", "Git"]) and skills.is_skills_header([" задание ", "x", "ПРОЕКТ"])
    assert not skills.is_skills_header(["Проект", "Git"]) and not skills.is_skills_header(["Задание"]) and not skills.is_skills_header([])
    assert not skills.is_skills_header(["Название проекта", "Задание"])


# ---------- the rules ----------


def part(shares: dict[str, float], *tasks: dict[str, float]) -> SkillPart:
    return SkillPart(shares=shares, tasks=[SkillTask(label=f"{i:02}", shares=task) for i, task in enumerate(tasks, 1)])


@pytest.mark.parametrize(("total", "said"), [(0.99, None), (1.01, None), (1.0, None), (0.9899, "0,9899"), (1.0101, "1,0101"), (0.5, "0,5")])
def test_the_shares_of_the_skills_come_to_one_within_a_hundredth(total, said):
    issues = skills.part_issues(part({"Git": total}), "основная часть")
    assert issues == ([] if said is None else [f"основная часть: сумма долей навыков {said} вместо 1"])


@pytest.mark.parametrize(("split", "said"), [((0.5, 0.49), None), ((0.5, 0.51), None), ((0.5, 0.4899), "0,9899"), ((0.6, 0.4101), "1,0101"), ((0.0, 0.0), "0")])
def test_the_tasks_split_each_skill_whole_within_a_hundredth(split, said):
    issues = skills.part_issues(part({"Git": 1.0}, *({"Git": share} if share else {} for share in split)), "основная часть")
    wanted = [] if said is None else [f"основная часть: «Git» — доля 1, по заданиям сумма {said} вместо 1"]
    assert [issue for issue in issues if "по заданиям" in issue] == wanted


def test_what_else_a_part_of_a_project_is_checked_for():
    title = "бонусная часть"
    # A project not broken into tasks yet has shares and nothing to split: no complaint.
    assert skills.part_issues(part({"Git": 0.4, "Python": 0.6}), title) == []
    assert skills.part_issues(SkillPart(), title) == []
    # Tasks without shares of the skills: the one thing to say.
    assert skills.part_issues(part({}, {"Git": 1.0}, {"Git": 0.5}), title) == ["бонусная часть: заданий 2, а долей навыков нет"]
    # A skill the tasks give and the project does not have; a task that gets none of the project's skills.
    assert skills.part_issues(part({"Git": 1.0}, {"Git": 1.0}, {"SQL": 0.25}, {}, {"SQL": 0.5}), title) == [
        "бонусная часть: «SQL» — доли в проекте нет, а задания дают 0,75",
        "бонусная часть: без навыков проекта остаются задания 02, 03, 04"]
    assert skills.part_issues(part({"Git": 0.5, "SQL": 0.5}, {"Git": 1.0}, {"SQL": 1.0}), title) == []


def test_a_block_is_checked_whole_main_part_bonus_part_and_names():
    block = SkillBlock(code="P", main=part({"git": 0.5, "Питон": 0.3}, {"git": 1.0, "Питон": 1.0}), bonus=part({"Network & system admin": 1.0}))
    assert issues_of(block, CATALOG_NAMES) == [
        "«Питон» нет в справочнике навыков",
        "«Network & system admin» нет в справочнике навыков (ближайшие: Network & system administration)",
        "основная часть: сумма долей навыков 0,8 вместо 1"]
    # Without a catalogue the names are not compared; an empty main part is said first.
    assert issues_of(block, []) == ["основная часть: сумма долей навыков 0,8 вместо 1"]
    assert issues_of(SkillBlock(code="P", bonus=part({"Git": 1.0})), CATALOG_NAMES) == ["в строке all нет ни одного навыка с долей"]
    # … and once: with tasks in it, the main part says the same thing itself.
    assert issues_of(SkillBlock(code="P", main=part({}, {"Git": 1.0})), CATALOG_NAMES) == ["основная часть: заданий 1, а долей навыков нет"]
    assert skills.skill_names(SkillBlock(code="P", main=part({"Git": 0.5, "SQL": 0.5}), bonus=part({"SQL": 0.6, "Python": 0.4}))) == ["Git", "SQL", "Python"]


def test_a_name_is_looked_up_without_regard_to_case_and_spacing_and_never_replaced():
    assert skills.unknown(["git", " DB  &  Data ", "ui & design tools"], CATALOG_NAMES) == []
    assert skills.unknown(["Pyton", "Гит", "Pyton"], CATALOG_NAMES) == [
        "«Pyton» нет в справочнике навыков (ближайшие: Python)", "«Гит» нет в справочнике навыков"]
    assert skills.nearest("Requirements Analisys", CATALOG_NAMES) == ["Requirements Analysis"]
    assert sorted(skills.nearest("Design tools", CATALOG_NAMES)) == ["UI & Design tools", "UX & Design tools"]
    assert skills.unknown(["Design tools"], CATALOG_NAMES) == ["«Design tools» нет в справочнике навыков (ближайшие: UX & Design tools, UI & Design tools)"]
    # Three at most, and only what is close: a name with nothing like it gets no hint.
    assert len(skills.nearest("Навык 1", [f"Навык {i}" for i in range(10, 20)])) == 3
    assert skills.nearest("Quantum", CATALOG_NAMES) == []
    assert skills.unknown([], CATALOG_NAMES) == [] and skills.unknown(["Git"], []) == ["«Git» нет в справочнике навыков"]


# ---------- the workbook ----------


def test_the_passport_gives_each_project_its_skills_and_says_what_does_not_add_up():
    book = passport()
    # The exams' tab has blocks of exams only: it is not a tab of this plan's projects, and nothing has to be chosen.
    assert (book.skill_tabs, book.skills_tab, len(book.skill_catalog)) == ([TAB], TAB, 94)
    assert book.skill_catalog[:3] == ["3D modeling", "AB-Testing", "Algorithms"]
    by_code = {row.code: row.skills.splitlines() for row in book.rows}
    assert by_code["D1P1_Terminal"] == ["Git", "Python", "Network & system administration", "Problem solving"]
    assert by_code["D6P3_Dataquery"] == ["Python", "Problem solving", "DB & Data", "SQL", "AI Engineering"]
    # Main part first, then what only the bonus part has; an exam and a project without a block have none.
    assert by_code["D7P4_Userflow"] == ["Problem solving", "SQL", "AI Engineering", "HTML/CSS", "UI & Design tools", "UX & Design tools"]
    assert by_code["D5E1_Start"] == [] and by_code["D8T2_MCPcore"] == [] and by_code["D10P5_LLMbridge"] == []
    assert [block.code for block in book.skill_blocks] == ["D1P1_Terminal", "D2P2_Errorflow", "D3T1_Teamcode", "D6P3_Dataquery", "D7P4_Userflow"]
    assert [bool(block.issues) for block in book.skill_blocks] == [False, False, False, True, True]
    # What is said about skills is kept apart from the warnings about the plan: those go to the models as the limits
    # of the plan as a source, and the sums of other projects' skills are not that.
    assert book.warnings == ["Учебный план прочитан с вкладки «УП»."]
    assert book.skill_notes == [
        f"Развесовка — вкладка «{TAB}»: навыки у 5 из 10 проектов; в справочнике навыков 94.",
        "Развесовка «D6P3_Dataquery»: бонусная часть: «AI Engineering» — доля 0,2, по заданиям сумма 0 вместо 1.",
        "Развесовка «D7P4_Userflow»: бонусная часть: «DB & Data» — доли в проекте нет, а задания дают 1; "
        "бонусная часть: «SQL» — доли в проекте нет, а задания дают 1.",
        "Без развесовки: D8T2_MCPcore, D10P5_LLMbridge, D11P6_Scorecard, D13P7_Start_Incident, D14P8_Pitchdeck.",
        "На вкладке развесовки есть коды, которых нет в УП: D8-9P5_MCP."]


def test_between_two_tabs_for_the_same_projects_nothing_is_chosen_for_the_methodologist():
    undecided = passport(old=True)
    assert (undecided.skill_tabs, undecided.skills_tab, undecided.skill_blocks) == ([OLD_TAB, TAB], "", [])
    assert not any(row.skills for row in undecided.rows)
    assert undecided.skill_notes == [f"В книге несколько вкладок развесовки: «{OLD_TAB}», «{TAB}». Выберите вкладку — навыки проектов возьмутся из неё."]
    assert len(undecided.skill_catalog) == 94
    # A tab that is not one of them is not a choice.
    assert passport("Нет такой вкладки", old=True).skills_tab == "" and passport(EXAMS_TAB, old=True).skills_tab == ""
    assert passport(f" {TAB} ", old=True).skills_tab == TAB  # the name as the page got it, spaces around it or not
    new, older = passport(TAB, old=True), passport(OLD_TAB, old=True)
    assert (new.skills_tab, older.skills_tab, new.skill_tabs, older.skill_tabs) == (TAB, OLD_TAB, [OLD_TAB, TAB], [OLD_TAB, TAB])
    assert [r.skills for r in new.rows] == [r.skills for r in passport().rows]
    # The older tab has other projects and other discrepancies: the choice changes what the projects get.
    assert [block.code for block in older.skill_blocks] == [
        "D1P1_Terminal", "D2P2_Errorflow", "D3T1_Teamcode", "D6P3_Dataquery", "D7P4_Userflow", "D10P5_LLMbridge", "D11P6_Scorecard", "D14P8_Pitchdeck"]
    assert "На вкладке развесовки есть коды, которых нет в УП: D8-9P5_MCP, D13P6_Cybersec." in older.skill_notes
    # Eight blocks are tied to projects, and one of them is empty: seven projects have skills, and seven is what is said.
    assert older.skill_notes[0] == f"Развесовка — вкладка «{OLD_TAB}»: навыки у 7 из 10 проектов; в справочнике навыков 94."
    assert "Развесовка «D7P4_Userflow»: основная часть: заданий 5, а долей навыков нет; бонусная часть: заданий 3, а долей навыков нет." in older.skill_notes
    # The rows keep their ids whatever is chosen: the project chosen on the page stays chosen.
    assert [r.id for r in new.rows] == [r.id for r in undecided.rows]


def plan_of(*lines: str) -> Rows:
    return [line.split(";") for line in lines]


def about_skills(curriculum: Curriculum) -> list[str]:
    return curriculum.skill_notes


def test_which_tabs_count_and_what_is_said_when_none_does():
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта;Формат", "P1;Первый;", "P2;Второй;", "E1;Итог;экзамен")
    block: Rows = [["Проект", "Задание", "Git", "check"], ["P1", "all", 1, 1], [None, "01", 1], ["E1", "all", 1]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block)))
    # Without a catalogue the names are not compared, and this is said; an exam on a projects' tab gets no skills.
    assert about_skills(read) == ["Развесовка — вкладка «Навыки»: навыки у 1 из 2 проектов; справочника навыков в книге нет — названия не сверены.",
                                  "Без развесовки: P2."]
    assert [r.skills for r in read.rows] == ["Git", "", ""] and read.skill_catalog == []
    # A hidden skills tab is a form or a sample when a visible one exists; alone, it is read.
    other: Rows = [["Проект", "Задание", "SQL"], ["P1", "all", 1]]
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Образец", other, "hidden"), ("Навыки", block))).skill_tabs == ["Навыки"]
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Образец", other, "hidden"))).rows[0].skills == "SQL"
    # A tab whose blocks are of other projects is not this plan's; a plan without codes has nothing to tie a tab to.
    foreign: Rows = [["Проект", "Задание", "Git"], ["X9", "all", 1]]
    not_ours = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Чужая", foreign)))
    assert about_skills(not_ours) == ["На вкладках развесовки нет ни одного проекта этого УП по коду: навыки не прочитаны."]
    assert (not_ours.skill_tabs, not_ours.skills_tab, not_ours.skill_blocks) == ([], "", [])
    no_codes = parse_curriculum("p.xlsx", workbook(("УП", plan_of("Название проекта", "Первый")), ("Навыки", block)))
    assert about_skills(no_codes) == ["У проектов УП нет кода (колонка «Название проекта на платформе и в Gitlab»): "
                                      "вкладки развесовки не с чем связать, навыки не прочитаны."]
    assert (no_codes.skill_tabs, no_codes.skills_tab, no_codes.skill_blocks) == ([], "", [])
    # No skills tab in a workbook is said once; a CSV has no tabs and nothing to say.
    bare = parse_curriculum("p.xlsx", workbook(("УП", plan)))
    assert about_skills(bare) == ["В книге нет вкладки развесовки (колонки «Проект» и «Задание»): навыки не прочитаны."]
    assert (bare.skill_tabs, bare.skills_tab, bare.skill_blocks) == ([], "", [])
    assert about_skills(parse_curriculum("plan.csv", (FIXTURES / "baton_plan.csv").read_bytes())) == []
    # Which tab belongs is decided first, and only then which of those is seen: a visible tab of other projects
    # (the exams', another programme's) does not hide the right one.
    hidden_own = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Чужая", foreign), ("Навыки", block, "hidden")))
    assert (hidden_own.skill_tabs, hidden_own.skills_tab, hidden_own.rows[0].skills) == (["Навыки"], "Навыки", "Git")
    both = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Старая", other, "hidden"), ("Навыки ", block)))
    # A title is taken without the spaces around it: it is what the page shows and sends back.
    assert (both.skill_tabs, both.skills_tab) == (["Навыки"], "Навыки")


def test_a_plan_that_names_its_skills_keeps_them_and_a_code_met_twice_is_said():
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта;Список навыков", "P1;Первый;Linux, Shell/Bash", "P2;Второй;")
    block: Rows = [["Проект", "Задание", "Git"], ["P1", "all", 1], ["P2", "all", 1], ["P1", "all", 0.5]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block)))
    assert [r.skills for r in read.rows] == ["Linux, Shell/Bash", "Git"]
    assert "Развесовка «P1»: код встречается на вкладке дважды, взят первый блок." in read.skill_notes
    assert read.skill_blocks[0].main.shares == {"Git": 1.0}


def test_the_catalogue_is_the_names_the_platform_offers():
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый")
    block: Rows = [["Проект", "Задание", "Git", "Cobol", "Go"], ["P1", "all", 0.4, 0.3, 0.3]]
    catalog: Rows = [["№", "Skills", None], [1, "Git", True], [2, "Cobol", False], [3, " Python ", None], [4, "GIT", True], [5, None, True],
                     [6, "Go", "нет"], [7, "R", "ложь"], [8, "C", 0], [9, "Swift", "да"]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Available skills", catalog)))
    # A skill the platform marks as not offered is not in the catalogue; a name met twice is one skill.
    assert read.skill_catalog == ["Git", "Python", "Swift"]
    assert read.skill_blocks[0].issues == ["«Cobol» нет в справочнике навыков", "«Go» нет в справочнике навыков"]
    # The mark is the column without a header. A column with a header is something else — a description, a count of
    # projects — and its «нет» or 0 does not take a skill out of the catalogue.
    described: Rows = [["Skills", "Описание", None], ["Git", "нет", True], ["Go", 0, True], ["Cobol", "да", False]]
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Каталог", described))).skill_catalog == ["Git", "Go", "Cobol"]
    last: Rows = [["№", "Skills"], [1, "Git"], [2, "нет"]]
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Каталог", last))).skill_catalog == ["Git", "нет"]
    # A visible catalogue comes before a hidden one, wherever they stand in the workbook.
    two = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Образец", [["Skills"], ["Cobol"]], "hidden"), ("Каталог", [["Skills"], ["Git"]])))
    assert two.skill_catalog == ["Git"]
    # The catalogue is found by its header, whatever the tab is called; a hidden one is used when there is no other.
    renamed = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Справочник", [["Skills"], ["SQL"]], "hidden")))
    assert renamed.skill_catalog == ["SQL"]
    assert parse_curriculum("plan.csv", (FIXTURES / "baton_plan.csv").read_bytes()).skill_catalog == []


def test_the_tabs_are_read_as_spreadsheets_really_save_them():
    source, target = zipfile.ZipFile(io.BytesIO(passport_file(False))), io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            content = source.read(item)
            if item.filename.startswith("xl/worksheets/sheet"):
                xml = re.sub(r"<dimension[^>]*/>", "", content.decode("utf-8"))  # Google Sheets declares no size
                # … and stores the number 1 as 1.0 (a boolean, `t="b"`, stays 1)
                content = re.sub(r'(<c r="[A-Z]+\d+"(?: s="\d+")? t="n"><v>)(\d+)(</v>)', r"\g<1>\g<2>.0\g<3>", xml).encode("utf-8")
            out.writestr(item, content)
    sheets = [name for name in zipfile.ZipFile(io.BytesIO(target.getvalue())).namelist() if name.startswith("xl/worksheets/sheet")]
    stored = b"".join(zipfile.ZipFile(io.BytesIO(target.getvalue())).read(name) for name in sheets)
    assert b"<dimension" not in stored and b"<v>1.0</v>" in stored and b"<v>94.0</v>" in stored  # the file is what it is said to be
    saved, built = parse_curriculum("p.xlsx", target.getvalue()), passport()
    assert (saved.skill_catalog, saved.skill_blocks, saved.warnings) == (built.skill_catalog, built.skill_blocks, built.warnings)
    assert [r.skills for r in saved.rows] == [r.skills for r in built.rows]


def test_the_choice_of_a_tab_reaches_the_parser_through_the_upload(monkeypatch):
    client = TestClient(app_module.app)
    book = workbook(("УП", PLAN), ("Available skills", CATALOG), (OLD_TAB, OLD), (TAB, SKILLS))
    passed: list[tuple[Any, ...]] = []
    run_isolated = isolated.run

    def recorded(fn: Any, *args: Any, **limits: Any) -> Any:
        passed.append(args[2:])
        return run_isolated(fn, *args, **limits)

    monkeypatch.setattr(runs_module.isolated, "run", recorded)
    first = client.post("/api/curriculum", files={"file": ("Паспорт.xlsx", book)}).json()
    assert (first["skill_tabs"], first["skills_tab"]) == ([OLD_TAB, TAB], "") and not first["rows"][0]["skills"]
    chosen = client.post("/api/curriculum", files={"file": ("Паспорт.xlsx", book)}, data={"skills_tab": TAB}).json()
    assert chosen["skills_tab"] == TAB and chosen["rows"][0]["skills"].splitlines()[0] == "Git"
    assert passed == [("",), (TAB,)]
    assert client.post("/api/curriculum", files={"file": ("Паспорт.xlsx", book)}, data={"skills_tab": "я" * 201}).status_code == 422


# ---------- what the skills are for ----------


def request(curriculum: Curriculum, code: str, named: str = "") -> StartRequest:
    row = next(r for r in curriculum.rows if r.code == code)
    brief = Brief(title="t", description="Описание проекта", audience="", outcomes="Делает", skills=named or row.skills)
    return StartRequest(request_id="00000000-0000-4000-8000-000000000001", brief=brief, curriculum=curriculum, selected_row_id=row.id)


def test_the_participants_path_is_counted_on_the_skills_of_the_passport():
    m: Any = program_map(request(passport(), "D6P3_Dataquery"))
    # Before 05.10.2026 all three lists were empty for every project of a standard passport.
    assert m["skills_practised_before"] == ["Python", "Problem solving", "DB & Data"]
    assert m["skills_new"] == ["SQL", "AI Engineering"]
    assert [(s["skill"], s["projects"]) for s in m["skills_used_later"]] == [
        ("Problem solving", ["Формулирование пользовательской проблемы"]), ("SQL", ["Формулирование пользовательской проблемы"]),
        ("AI Engineering", ["Формулирование пользовательской проблемы"])]
    assert m["learner"]["before"][0]["skills"] == ["Git", "Python", "Network & system administration", "Problem solving"]


def test_a_skill_of_the_brief_that_the_platform_does_not_have_is_named_with_what_is_close():
    book = passport()
    assert check_skill_names(request(book, "D6P3_Dataquery")) == []
    finding, = check_skill_names(request(book, "D6P3_Dataquery", "Python\nData Vizualization; Питон, sql"))
    assert finding.message == ("Навыки задания, которых нет в справочнике платформы: «Data Vizualization» нет в справочнике навыков "
                               "(ближайшие: Data Visualization); «Питон» нет в справочнике навыков")
    assert (finding.severity, finding.origin, finding.rule_id) == ("blocker", "system", "constraints")
    assert "платформа не примет проект" in (finding.suggestion or "")
    # No catalogue (a CSV plan, no plan at all) or no skills in the brief: nothing to compare with.
    csv_plan = parse_curriculum("plan.csv", (FIXTURES / "baton_plan.csv").read_bytes())
    assert check_skill_names(request(csv_plan, "D6P3_Dataquery", "Питон")) == []
    brief = Brief(title="t", description="Описание проекта", audience="", outcomes="Делает", skills="Питон")
    assert check_skill_names(StartRequest(request_id="00000000-0000-4000-8000-000000000001", brief=brief)) == []
    empty = request(book, "D10P5_LLMbridge")
    assert empty.brief.skills == "" and check_skill_names(empty) == []
    # It is among the findings of the inputs the methodologist sees before the concept.
    run = new_run()
    run.request = request(book, "D6P3_Dataquery", "Питон")
    assert [f.message for f in input_review_findings(run) if "справочнике платформы" in f.message] == [
        "Навыки задания, которых нет в справочнике платформы: «Питон» нет в справочнике навыков"]


# ---------- what a workbook must not be able to do ----------


def test_nothing_on_a_tab_beside_the_plan_refuses_the_plan(monkeypatch):
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый")
    block: Rows = [["Проект", "Задание", "Git", "check"], ["P1", "all", 1, 1], [None, "01", 1], ["я" * 255, None, None, None], [None, "я" * 113, 1]]
    client = TestClient(app_module.app)
    # A long note in the table (the exams' tab of a real passport has «настройка на платформе …» under its blocks)
    # was a server error for the whole workbook (independent review 05.10.2026).
    noted = client.post("/api/curriculum", files={"file": ("p.xlsx", workbook(("УП", plan), ("Навыки", block)))})
    assert noted.status_code == 200 and noted.json()["rows"][0]["skills"] == "Git"
    assert [len(b["main"]["tasks"]) for b in noted.json()["skill_blocks"]] == [1]
    # A catalogue longer than the plan keeps: cut, and said.
    names: Rows = [["Skills"], *[[f"Навык {i}"] for i in range(1, 1002)]]
    cut = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Available skills", names)))
    assert len(cut.skill_catalog) == 1000 and cut.skill_notes[0] == "В справочнике навыков больше 1000 названий: взяты первые 1000."
    exact = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Каталог", names[:1001])))
    assert len(exact.skill_catalog) == 1000 and not any("В справочнике навыков больше" in note for note in exact.skill_notes)
    # A tab beside the plan that is too long is left out and named; the plan and the other tabs are read.
    endless: Rows = [["Проект", "Задание", "Git"], ["P1", "all", 1], *([[None, "check", 1]] * 4999)]  # 5001 lines
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Экзамены", endless), ("Навыки", block)))
    assert read.rows[0].skills == "Git" and read.skill_notes[0] == "Вкладка «Экзамены» не прочитана: в ней больше 5000 строк."
    assert read.skill_tabs == ["Навыки"]
    alone = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Экзамены", endless)))
    # Said once: a skills tab that was not read is not «no skills tab».
    assert alone.skill_notes == ["Вкладка «Экзамены» не прочитана: в ней больше 5000 строк."] and alone.rows[0].skills == ""
    # … and one line shorter it is a tab like any other.
    whole = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Экзамены", endless[:-1])))
    assert whole.rows[0].skills == "Git" and whole.skill_notes == ["Развесовка — вкладка «Экзамены»: навыки у 1 из 1 проектов; "
                                                                   "справочника навыков в книге нет — названия не сверены."]
    # The catalogue tab is held to the same length.
    long_catalog: Rows = [["Skills"], *[[f"Навык {i % 7}"] for i in range(5000)]]
    # … but a catalogue that was not read is not a skills tab that was not read: that there is none is still said.
    absent = "В книге нет вкладки развесовки (колонки «Проект» и «Задание»): навыки не прочитаны."
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Каталог", long_catalog))).skill_notes == [
        "Вкладка «Каталог» не прочитана: в ней больше 5000 строк.", absent]
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Available skills", names))).skill_notes == [
        "В справочнике навыков больше 1000 названий: взяты первые 1000.", absent]
    # The tabs of this plan are offered up to a limit, and the limit is said.
    eleven = parse_curriculum("p.xlsx", workbook(("УП", plan), *[(f"Навыки {i}", block) for i in range(1, 12)]))
    assert eleven.skill_tabs == [f"Навыки {i}" for i in range(1, 11)]
    assert eleven.skill_notes[0] == "Вкладок развесовки этого УП больше 10: на выбор даны первые 10."
    ten = parse_curriculum("p.xlsx", workbook(("УП", plan), *[(f"Навыки {i}", block) for i in range(1, 11)]))
    assert len(ten.skill_tabs) == 10 and ten.skill_notes[0].startswith("В книге несколько вкладок развесовки: «Навыки 1», ")
    # The limit is on what is offered, not on what is looked at: tabs of other plans and hidden ones do not use it up,
    # and the catalogue that stands after them is read (the second independent review 05.10.2026).
    other: Rows = [["Проект", "Задание", "Git"], ["X9", "all", 1]]
    far = parse_curriculum("p.xlsx", workbook(("УП", plan), *[(f"Чужая {i}", other) for i in range(1, 12)], *[(f"Скрытая {i}", block, "hidden") for i in range(1, 12)],
                                              ("Навыки", block), ("Available skills", [["Skills"], ["Git"]])))
    assert far.skill_tabs == ["Навыки"] and far.skills_tab == "Навыки" and far.skill_catalog == ["Git"]
    assert far.skill_notes == ["Развесовка — вкладка «Навыки»: навыки у 1 из 1 проектов; в справочнике навыков 1."]
    # Whatever is still more than a plan can hold is a refusal in words, never a server error.
    monkeypatch.setattr(ingestion, "MAX_CATALOG", 5000)
    with pytest.raises(ingestion.InputError, match="Учебный план не удалось разобрать: skill_catalog: List should have at most 1000 items"):
        parse_curriculum("p.xlsx", workbook(("УП", plan), ("Available skills", names)))


def test_two_lines_of_the_plan_with_one_code_share_one_block():
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый", "P1;Первый, вторая часть", "P2;Второй")
    block: Rows = [["Проект", "Задание", "Git"], ["P1", "all", 0.5], ["P2", "all", None], [None, "01", 1]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block)))
    assert [r.skills for r in read.rows] == ["Git", "Git", ""] and [b.code for b in read.skill_blocks] == ["P1", "P2"]
    # Two lines of three got skills from the tab: the third has a block with no skills in it.
    assert read.skill_notes == [
        "Развесовка — вкладка «Навыки»: навыки у 2 из 3 проектов; справочника навыков в книге нет — названия не сверены.",
        "Развесовка «P1»: основная часть: сумма долей навыков 0,5 вместо 1.",
        "Развесовка «P2»: основная часть: заданий 1, а долей навыков нет."]


def test_a_tab_of_many_programmes_is_not_listed_code_by_code():
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый")
    block: Rows = [["Проект", "Задание", "Git"], ["P1", "all", 1], *[[f"X{i}", "all", 1] for i in range(1, 13)], ["P1", "all", 1], ["P1", "all", 1], ["X1", "all", 1]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block)))
    # A repeated code is said once, though it comes back twice — and only when it is this plan's: the blocks of
    # other programmes are counted, not gone through.
    assert read.skill_notes[1:] == [
        "Развесовка «P1»: код встречается на вкладке дважды, взят первый блок.",
        "На вкладке развесовки есть коды, которых нет в УП: X1, X2, X3, X4, X5, X6, X7, X8, X9, X10 и ещё 2."]
    ten = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block[:12])))
    assert ten.skill_notes[1:] == ["На вкладке развесовки есть коды, которых нет в УП: X1, X2, X3, X4, X5, X6, X7, X8, X9, X10."]


def test_a_code_is_the_same_code_however_it_is_spaced_and_wherever_it_is_repeated():
    # The tab's side of a code was tidied and the plan's was not: a code with two spaces in it stopped matching itself.
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "D1  Term;Первый", "D2 Flow;Второй", "d3;Третий")
    block: Rows = [["Проект", "Задание", "Git", "Python"], ["D1 Term", "all", 1], [" D2\nFlow ", "all", None, 1], ["D3", "all", 1]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block)))
    assert [r.skills for r in read.rows] == ["Git", "Python", ""] and [b.code for b in read.skill_blocks] == ["D1 Term", "D2 Flow"]
    # Case is not spacing: «D3» is not «d3», and both sides are named as they are written.
    assert read.skill_notes[1:] == ["Без развесовки: d3.", "На вкладке развесовки есть коды, которых нет в УП: D3."]
    # A code written on every line of its block is one block, with its tasks and its bonus part — not a block for each
    # line, of which all but the first were dropped as repetitions.
    filled = skills.parse_blocks(tab("P1;all;0,4;0,6", "P1;01;1;0,5", "P1;02;;0,5", "P1;Bonus all;1;", "P1;Bonus 01;1;", "P2;all;1;", "P2;01;1;", "P1;all;;1"))
    assert [(b.code, len(b.main.tasks), len(b.bonus.tasks), b.issues) for b in filled] == [("P1", 2, 1, []), ("P2", 1, 0, []), ("P1", 0, 0, [])]
    assert issues_of(filled[0], []) == []
    # … and a block pasted right under itself says so in the block.
    pasted, = skills.parse_blocks(tab("P1;all;1;", ";01;1;", "P1;all;;1"))
    assert pasted.main.shares == {"Git": 1.0} and pasted.issues == ["строка «all» встречается в блоке дважды: взята первая"]


def test_two_tabs_are_two_tabs_whatever_their_titles():
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый")
    own: Rows = [["Проект", "Задание", "Git"], ["P1", "all", 1]]
    other: Rows = [["Проект", "Задание", "Python"], ["X9", "all", 1]]
    # Titles that differ by a space at the end: the second took the place of the first, and the plan's tab was «not found».
    for tabs in ((("Навыки", own), ("Навыки ", other)), (("Навыки ", other), ("Навыки", own))):
        read = parse_curriculum("p.xlsx", workbook(("УП", plan), *tabs))
        assert (read.skill_tabs, read.skills_tab, read.rows[0].skills) == (["Навыки"], "Навыки", "Git")
    # Both the plan's: both are offered; the choice by such a title takes the first.
    older: Rows = [["Проект", "Задание", "Python"], ["P1", "all", 1]]
    both = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", own), ("Навыки ", older)), " Навыки ")
    assert (both.skill_tabs, both.skills_tab, both.rows[0].skills) == (["Навыки", "Навыки"], "Навыки", "Git")


def test_the_catalogue_is_read_from_a_table_as_it_is():
    # Lines of a table are not all of one length (a CSV, a tab read without padding): a short line has no name or no mark.
    assert ingestion.catalog_names([["Skills"], ["Git"], ["Go", "FALSE"], []]) == ["Git"]  # nothing after «Skills»: the next cell is the mark
    assert ingestion.catalog_names([["Skills", "Описание"], ["Git", "FALSE"], ["Go"]]) == ["Git", "Go"]  # a column with a header decides nothing
    assert ingestion.catalog_names([["Описание", "Skills"], ["только описание"], ["x", "Git"], ["y", "Go", "нет"]]) == ["Git"]
    assert ingestion.catalog_names([["№", "Skills", ""], ["1", "Git"], ["2", "Go", "0"]]) == ["Git"]


def test_tabs_that_are_neither_do_not_end_the_reading_and_the_plan_is_not_its_own_catalogue():
    # A passport has two dozen tabs; the skills tab and the catalogue stand among periods, charts and settings.
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый")
    block: Rows = [["Проект", "Задание", "Git"], ["P1", "all", 1]]
    aside: Rows = [["Неделя", "Дата"], [1, "01.09"]]
    read = parse_curriculum("p.xlsx", workbook(("Периоды", aside), ("УП", plan), ("Гант", aside), ("Навыки", block), ("Настройки", aside), ("Available skills", CATALOG)))
    assert (read.skills_tab, read.rows[0].skills, len(read.skill_catalog)) == ("Навыки", "Git", len(CATALOG_NAMES))
    # A plan that keeps its skills in a column called «Skills» is not the catalogue of the workbook.
    with_column: Rows = [["Название проекта на платформе и в Gitlab", "Название проекта", "Skills"], ["P1", "Первый", "Git"]]
    own = parse_curriculum("p.xlsx", workbook(("УП", with_column), ("Навыки", block), ("Available skills", CATALOG)))
    assert len(own.skill_catalog) == len(CATALOG_NAMES) and len(own.skill_notes) == 1


def test_between_two_catalogues_none_is_taken():
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый")
    block: Rows = [["Проект", "Задание", "Git"], ["P1", "all", 1]]
    matrix: Rows = [["Skills", "Описание"], ["Soft", "x"]]
    # The first tab with a «Skills» column was the catalogue: with a matrix of soft skills before the real one, every
    # real skill was «not in the catalogue», and the project could not be started for it.
    two = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Матрица", matrix), ("Навыки", block), ("Available skills", CATALOG)))
    assert two.skill_catalog == [] and two.skill_blocks[0].issues == []
    assert two.skill_notes == ["В книге несколько вкладок с колонкой «Skills»: «Матрица», «Available skills». Справочник навыков не взят ни с одной — названия не сверены.",
                               "Развесовка — вкладка «Навыки»: навыки у 1 из 1 проектов; справочника навыков в книге нет — названия не сверены."]
    assert check_skill_names(request(two, "P1")) == []
    # A hidden one beside a visible one is a form or a sample, as with plans: the visible one is the catalogue.
    one = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Матрица", matrix, "hidden"), ("Навыки", block), ("Available skills", CATALOG)))
    assert len(one.skill_catalog) == len(CATALOG_NAMES) and len(one.skill_notes) == 1
    only_hidden = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Available skills", CATALOG, "hidden")))
    assert len(only_hidden.skill_catalog) == len(CATALOG_NAMES)


def test_a_name_has_a_length_and_a_plan_has_a_weight(monkeypatch):
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", *[f"P{i};Проект {i}" for i in range(1, 4)])
    longest, too_long = "я" * skills.MAX_NAME, "я" * (skills.MAX_NAME + 1)
    # A text in the header line or in the catalogue that is longer than any name is a note, not a skill.
    block: list[list[object]] = [["Проект", "Задание", "Git", too_long, longest], *[[f"P{i}", "all", 0.5, 0.25, 0.5] for i in range(1, 4)]]
    names: Rows = [["Skills"], ["Git"], [too_long], [longest]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Available skills", names)))
    assert read.skill_catalog == ["Git", longest] and read.skill_blocks[0].main.shares == {"Git": 0.5, longest: 0.5}
    assert read.skill_notes == ["Развесовка — вкладка «Навыки»: навыки у 3 из 3 проектов; в справочнике навыков 2."]
    # A plan goes back to the server whole with every start request. Blocks heavier than a plan may carry are not kept
    # — the names of the skills are; notes beyond what a card can show are counted. Otherwise the plan would load
    # and no project of it could be started (the second independent review 05.10.2026).
    weight = sum(len(b.model_dump_json()) for b in read.skill_blocks)
    monkeypatch.setattr(ingestion, "MAX_BLOCKS_SIZE", weight)
    assert len(parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block))).skill_blocks) == 3
    monkeypatch.setattr(ingestion, "MAX_BLOCKS_SIZE", weight - 1)
    heavy = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block)))
    assert heavy.skill_blocks == [] and [r.skills for r in heavy.rows] == [f"Git\n{longest}"] * 3
    assert heavy.skill_notes[-1] == "Доли развесовки с планом не сохранены: вкладка слишком большая. Названия навыков проектов из неё взяты."
    wrong: list[list[object]] = [["Проект", "Задание", "Git"], *[[f"P{i}", "all", 0.5] for i in range(1, 4)]]
    said = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", wrong))).skill_notes
    assert len(said) == 4
    monkeypatch.setattr(ingestion, "MAX_NOTES_SIZE", sum(map(len, said[:3])))
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", wrong))).skill_notes == [
        *said[:3], "Заметок о развесовке слишком много, чтобы показать все: не показано 1."]
    monkeypatch.setattr(ingestion, "MAX_NOTES_SIZE", sum(map(len, said)))
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", wrong))).skill_notes == said


def test_the_heaviest_tab_a_passport_can_have_still_starts_a_project():
    # Fifty projects of six tasks with every one of the 94 skills in every line, all valid shares: 867 thousand
    # characters of blocks, more than a start request may weigh.
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", *[f"P{i};Проект {i}" for i in range(1, 51)])
    share = round(1 / len(CATALOG_NAMES), 4)
    block: list[list[object]] = [["Проект", "Задание", *CATALOG_NAMES]]
    for i in range(1, 51):
        block.append([f"P{i}", "all", *[share] * len(CATALOG_NAMES)])
        block.extend([None, f"{n:02}", *[0.1667] * len(CATALOG_NAMES)] for n in range(1, 7))
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Available skills", CATALOG)))
    assert read.skill_blocks == [] and read.rows[0].skills.splitlines() == CATALOG_NAMES
    assert request(read, "P1").curriculum is not None


def test_a_name_is_looked_up_in_the_catalogue_once(monkeypatch):
    # A tab repeats its skills in every block; a lookup goes through the whole catalogue. Two hundred projects with
    # forty strangers each took the thirty seconds a file is given, and the plan was refused for its tabs.
    calls: list[str] = []
    close = skills.difflib.get_close_matches

    def counted(name: str, *args: Any, **kwargs: Any) -> list[str]:
        calls.append(name)
        return close(name, *args, **kwargs)

    monkeypatch.setattr(skills.difflib, "get_close_matches", counted)
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", *[f"P{i};Проект {i}" for i in range(1, 31)])
    block: Rows = [["Проект", "Задание", "Pyton", "Git", "Гит"], *[[f"P{i}", "all", 0.5, 0.25, 0.25] for i in range(1, 31)], ["X1", "all", None, None, 1]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Available skills", CATALOG)))
    assert sorted(calls) == ["pyton", "гит"]
    assert len({tuple(b.issues) for b in read.skill_blocks}) == 1 and len(read.skill_blocks) == 30
    assert read.skill_blocks[0].issues == ["«Pyton» нет в справочнике навыков (ближайшие: Python)", "«Гит» нет в справочнике навыков"]
    # Without a catalogue nothing is looked up at all.
    calls.clear()
    assert parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block))).skill_blocks[0].issues == [] and calls == []
    assert skills.block_issues(read.skill_blocks[0].model_copy(update={"issues": []}), {"Гит": "про Гит", "SQL": "про SQL"}) == ["про Гит"]


def test_skills_that_differ_by_a_sign_are_different_skills():
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый", "P2;Второй", "P3;Третий")
    block: Rows = [["Проект", "Задание", "C", "C++", "C#"], ["P1", "all", 1], ["P2", "all", None, 1], ["P3", "all", None, None, 1]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block), ("Available skills", CATALOG)))
    assert read.skill_blocks[1].issues == []  # all three are names of the platform's catalogue
    m: Any = program_map(request(read, "P2"))
    # «C», «C++» and «C#» used to be one skill here: the second project had nothing new, and «C++» was «used later» in the third.
    assert (m["skills_practised_before"], m["skills_new"], m["skills_used_later"]) == ([], ["C++"], [])
    last: Any = program_map(request(read, "P3"))
    assert (last["skills_practised_before"], last["skills_new"]) == ([], ["C#"])


def test_a_skill_written_another_way_is_the_same_skill():
    # What stays one skill when signs became part of a name: case, spacing, hyphens and commas, «ё», and the misprint
    # of the real plan («Requirements Analisys»). Not one of these was held by a test (mutation run 05.10.2026).
    head = ["Название проекта на платформе и в Gitlab", "Название проекта", "Навыки"]
    plan: Rows = [head, ["P1", "Первый", "Requirements Analisys, Ёмкость; Problem-solving"],
                  ["P2", "Второй", "requirements analysis\nемкость\nProblem  Solving\nC++"], ["P3", "Третий", "REQUIREMENTS ANALYSIS"]]
    m: Any = program_map(request(parse_curriculum("p.xlsx", workbook(("УП", plan))), "P2"))
    assert m["skills_practised_before"] == ["requirements analysis", "емкость", "Problem  Solving"] and m["skills_new"] == ["C++"]
    assert m["skills_used_later"] == [{"skill": "requirements analysis", "projects": ["Третий"], "count": 1}]


def test_a_project_the_tab_gives_a_bonus_part_is_marked_and_prepared_with_bonus_tasks_allowed(tmp_path):
    from project_generator_lab.concept import benchmark

    book = passport(TAB)
    # Five projects have a block on the current tab, and each block has a bonus part; a project without a block and an exam have none.
    assert {r.code: r.bonus for r in book.rows if r.bonus} == dict.fromkeys(["D1P1_Terminal", "D2P2_Errorflow", "D3T1_Teamcode", "D6P3_Dataquery", "D7P4_Userflow"], True)
    assert not any(r.bonus for r in passport(old=True).rows)  # between two tabs none is chosen yet — nothing is known
    plan: Rows = plan_of("Название проекта на платформе и в Gitlab;Название проекта", "P1;Первый", "P2;Второй", "P3;Третий")
    block: Rows = [["Проект", "Задание", "Git"], ["P1", "all", 1], [None, "01", 1], ["P2", "all", 1], [None, "Bonus all", 1], ["P3", "all", 1], [None, "Bonus 01", 1]]
    read = parse_curriculum("p.xlsx", workbook(("УП", plan), ("Навыки", block)))
    assert [r.bonus for r in read.rows] == [False, True, True]
    # The model run of 05.10.2026 prepared both projects without a bonus the plan has: the comparison takes it from the plan now.
    file = tmp_path / "Паспорт.xlsx"
    file.write_bytes(passport_file(False))
    assert benchmark.example_request(1, "", file).brief.bonus is True
    assert benchmark.example_request(1, "", FIXTURES / "baton_plan.csv").brief.bonus is False


def test_only_a_projects_worth_of_names_is_looked_up():
    book = passport()
    many = "\n".join(f"Нет такого {i}" for i in range(1, 80))
    finding, = check_skill_names(request(book, "D6P3_Dataquery", many))
    assert finding.message.count("нет в справочнике навыков") == 50 and "«Нет такого 50»" in finding.message and "«Нет такого 51»" not in finding.message


def test_the_benchmark_reads_the_skills_of_the_tab_it_is_told(tmp_path, capsys):
    from project_generator_lab.concept import benchmark

    file = tmp_path / "Паспорт.xlsx"
    file.write_bytes(passport_file(True))
    assert benchmark.example_request(1, "", file).brief.skills == ""
    assert "В книге несколько вкладок развесовки" in capsys.readouterr().out
    chosen = benchmark.example_request(1, "", file, TAB).brief
    assert chosen.skills.splitlines() == ["Git", "Python", "Network & system administration", "Problem solving"]
