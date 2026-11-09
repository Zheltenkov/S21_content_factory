"""The standard programme passport (РГ 02.10.2026): the tab «УП» of the workbook «Паспорт программы_Батон», as a CSV
export and as the workbook itself.

Before 05.10.2026 the plan was read with losses that nobody saw: the two exams became individual projects, the
project's theses, cases and form of checking were not recognised (the brief opened with an empty description), and the
project had no code to find its block on the skills tab by. The fixture is the real tab; a test that fails here means
the standard input is read differently, not that a made-up sample changed.
"""

import csv
import io
import json
import re
import zipfile
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from project_generator_lab import app as app_module
from project_generator_lab.concept import ingestion
from project_generator_lab.concept import runs as runs_module
from project_generator_lab.concept.ingestion import (
    InputError,
    astronomical,
    cell_text,
    parse_curriculum,
    row_kind,
)
from project_generator_lab.concept.input_semantics import input_semantics, learner_facts, program_map, program_position
from project_generator_lab.concept.models import (
    Brief,
    Curriculum,
    LearnerPath,
    Material,
    NextNeed,
    Passage,
    StartRequest,
)
from project_generator_lab.concept.planning import MAP_FIELDS, build_evidence, compact, map_row
from project_generator_lab.concept.validation import check_input_card
from project_generator_lab.core import isolated
from project_generator_lab.tests.concept.test_validation import reviewed_run
from project_generator_lab.tests.concept.workflow_fixtures import input_card
from pydantic import ValidationError

FIXTURES = Path(__file__).parent / "fixtures"
PLAN = FIXTURES / "baton_plan.csv"
CODES = ["D1P1_Terminal", "D2P2_Errorflow", "D3T1_Teamcode", "D5E1_Start", "D6P3_Dataquery", "D7P4_Userflow",
         "D8T2_MCPcore", "D10P5_LLMbridge", "D11P6_Scorecard", "D12E2_Final", "D13P7_Start_Incident", "D14P8_Pitchdeck"]


def plan() -> Curriculum:
    return parse_curriculum("Паспорт программы_Батон - УП.csv", PLAN.read_bytes())


def request(curriculum: Curriculum, code: str) -> StartRequest:
    brief = Brief(title="t", description="Описание проекта", audience="", outcomes="Делает")
    row = next(r for r in curriculum.rows if r.code == code)
    return StartRequest(request_id="00000000-0000-4000-8000-000000000001", brief=brief, curriculum=curriculum,
                        selected_row_id=row.id)


def csv_plan(*lines: str) -> bytes:
    return "\n".join(lines).encode()


# ---------- the tab as the passport gives it ----------


def test_the_passport_plan_is_read_whole():
    curriculum = plan()
    # The «ВСЕГО» line and the empty second days of the two-day projects are not projects.
    assert [r.code for r in curriculum.rows] == CODES
    assert [r.row_number for r in curriculum.rows] == [3, 4, 5, 7, 8, 9, 10, 12, 13, 14, 15, 16]
    assert [r.kind for r in curriculum.rows] == ["project"] * 3 + ["exam"] + ["project"] * 5 + ["exam"] + ["project"] * 2
    first, team = curriculum.rows[0], curriculum.rows[2]
    assert (first.title, first.block, first.hours, first.format, first.group_size, first.checking) == (
        "Набор базовых Python-скриптов", "Разработка: основы Python", "6", "individual", "", "P2P")
    # «2-4» people: the form takes the upper number, the range stays in the source line.
    assert (team.format, team.group_size, team.hours, team.checking) == ("group", "4", "15", "P2P + линтер")
    assert team.raw["Кол-во в группе"] == "2-4"
    # What the project consists of: the theses are its description, the cases its approved content.
    assert first.description.startswith("Участник осваивает работу в терминале, базовый Git-workflow")
    assert first.cases.startswith("1. Повреждённая память напарника:") and "\n4. Контрольный прогон:" in first.cases
    assert all(r.description and r.outcomes and r.hours for r in curriculum.rows)
    assert all(r.cases for r in curriculum.rows if r.kind == "project")


def test_the_report_of_columns_says_only_what_matters():
    curriculum = plan()
    assert curriculum.columns == {
        "block": ["Тематический блок"], "order": ["№"], "code": ["Название проекта на платформе и в Gitlab"],
        "title": ["Название проекта"], "description": ["Тезисное тематическое наполнение проекта"],
        "cases": ["Кейсы, которые будут решаться в проекте"], "outcomes": ["Образовательные результаты"],
        "software": ["Необходимое ПО/веб"], "storytelling": ["Сторителлинг"], "format": ["Формат"],
        "group_size": ["Кол-во в группе"], "hours": ["Трудоемкость, астр.часы"], "checking": ["Формат проверки"],
        "materials": ["Дополнительные материалы"], "repository": ["GitLab"]}
    # Neither «краткое описание» nor «уровень аудитории» is missing from a standard plan: the first is the theses,
    # the second is never in a plan.
    assert curriculum.missing_fields == []
    # The academic hours stand next to the astronomical ones and are not used.
    assert curriculum.unmapped_columns == ["День", "Трудоемкость, ак.часы", "Трудоемкость, дни", "Период выполнения (час)"]
    # The address of the repository is read as the plan gives it: with «.git», with the page of a file, or none yet.
    by_code = {r.code: r.repository for r in curriculum.rows}
    assert by_code["D1P1_Terminal"] == "https://git.21-school.ru/masters/D1P1_Terminal.ID_1578290.git"
    assert by_code["D6P3_Dataquery"].endswith("/-/blob/master/README.md?ref_type=heads") and by_code["D10P5_LLMbridge"] == ""
    assert curriculum.warnings == []


def test_a_dash_in_a_cell_means_nothing_and_the_source_line_keeps_it():
    exam = plan().rows[3]
    assert (exam.title, exam.storytelling, exam.materials, exam.cases, exam.group_size) == ("Экзамен 1. Якорь", "", "", "", "")
    assert exam.raw["Сторителлинг"] == "-" and exam.raw["Кол-во в группе"] == "-"
    data = csv_plan("Название проекта;Сторителлинг;Форма контроля;Инструменты;Навыки",
                    "А;—;P2P - линтер;-;--", "Б;– ;-;Git - ветки;Python", "В;−;‒;нет;-1")
    a, b, c = parse_curriculum("plan.csv", data).rows
    assert (a.storytelling, a.checking, a.tools, a.skills) == ("", "P2P - линтер", "", "")
    assert (b.storytelling, b.checking, b.tools, b.skills) == ("", "", "Git - ветки", "Python")
    # The minus sign and the figure dash are dashes too; a word and a number with a dash are what they are.
    assert (c.storytelling, c.checking, c.tools, c.skills) == ("", "", "нет", "-1")


def test_a_dash_stays_as_written_in_what_says_what_the_line_is():
    # Block, goals, number and title were always taken as written: a dash there does not make the line disappear,
    # join the block above or lose its number.
    data = csv_plan("Тематический блок;Цели блока;№;Название проекта;Сторителлинг", "А;Цель;1;П1;-", "-;—;-;П2;-", ";;3;—;-")
    plan_ = parse_curriculum("plan.csv", data)
    assert [(r.block, r.block_goals, r.title, r.storytelling) for r in plan_.rows] == [
        ("А", "Цель", "П1", ""), ("-", "—", "П2", ""), ("-", "—", "—", "")]
    assert not any("Без номера" in w for w in plan_.warnings)


# ---------- exams ----------



def test_what_a_line_gives_the_form_does_not_depend_on_how_the_plan_writes_it():
    # None of this was held by a test: a format with a capital letter, one number for the group, a number in the
    # group column of an individual project, a plan without blocks and without numbers (mutation run 05.10.2026).
    read = parse_curriculum("plan.csv", csv_plan("Название проекта;Формат;Кол-во в группе", "Первый;Групповой;3", "Второй;ГРУППОВОЙ;2-4",
                                                 "Третий;Индивидуальный;1", "Четвёртый;;2"))
    assert [(r.format, r.group_size, r.block) for r in read.rows] == [
        ("group", "3", "Без блока"), ("group", "4", "Без блока"), ("individual", "", "Без блока"), ("group", "2", "Без блока")]
    assert read.warnings == ["Без номера: 4 проектов. Сохранён порядок записей в CSV."] and read.skills_tab == ""
    # Two hundred projects are a plan; one more is not.
    many = ["Название проекта", *[f"Проект {i}" for i in range(1, 201)]]
    assert len(parse_curriculum("plan.csv", csv_plan(*many)).rows) == 200
    with pytest.raises(InputError, match="до 200 проектов"):
        parse_curriculum("plan.csv", csv_plan(*many, "Проект 201"))


@pytest.mark.parametrize(("project_format", "title", "kind"), [
    ("экзамен", "Якорь", "exam"), ("Экзамен", "Якорь", "exam"), (" экзамен ", "Якорь", "exam"), ("Exam", "Final", "exam"),
    # The format is the whole cell: a word about an exam inside another format is not it.
    ("индивидуальный (экзамен в конце)", "Проект", "project"), ("экзаменационный проект", "Проект", "project"),
    ("example", "Проект", "project"), ("экзамены", "Проект", "project"),
    # No format in the plan: the word «экзамен» in the title, wherever it stands.
    ("", "Экзамен 1. Якорь", "exam"), ("", "Финальный экзамен (тестирование)", "exam"), ("", "ЭКЗАМЕН №2", "exam"),
    ("", "Экзамен_1", "exam"), ("", "Экзамен1", "exam"), ("", "итоговый экзамен", "exam"),
    ("", "Экзаменационный тренажёр", "project"), ("", "Подготовка к экзамену", "project"), ("", "Переэкзамен", "project"),
    ("", "Exam-like project", "project"), ("", "Проект", "project"), ("", "", "project"),
    # A stated format is the plan's own word: the title does not override it.
    ("индивидуальный", "Экзамен по SQL как проект", "project"), ("групповой", "Финальный экзамен", "project"),
])
def test_a_row_is_an_exam_by_its_format_or_by_its_title_when_the_plan_states_no_format(project_format, title, kind):
    assert row_kind(project_format, title) == kind


def test_an_exam_guessed_from_the_title_is_reported_with_the_way_out():
    guessed = parse_curriculum("plan.csv", csv_plan("Название проекта;Формат", "Проект;", "Финальный экзамен (тестирование);", "Экзамен 2;-"))
    assert [r.kind for r in guessed.rows] == ["project", "exam", "exam"]
    assert guessed.warnings[-1] == ("Экзаменами сочтены по названию, формат в УП не указан: «Финальный экзамен (тестирование)», «Экзамен 2». "
                                    "Если это проект, укажите его формат в УП (индивидуальный или групповой).")
    # The way out works: with a format the same title is a project, and nothing is reported.
    stated = parse_curriculum("plan.csv", csv_plan("Название проекта;Формат", "Проект;", "Финальный экзамен (тестирование);индивидуальный"))
    assert [r.kind for r in stated.rows] == ["project", "project"] and not any("сочтены" in w for w in stated.warnings)
    # The passport states the format of its exams: nothing is guessed there.
    assert not any("сочтены" in w for w in plan().warnings)
    # The earlier export of a plan has no format column; its final exam used to be the last «project» of the programme.
    legacy = parse_curriculum("legacy_curriculum.csv", (FIXTURES / "legacy_curriculum.csv").read_bytes())
    assert [(r.title, r.kind) for r in legacy.rows if r.kind == "exam"] == [("Финальный экзамен (тестирование)", "exam")]
    assert "«Финальный экзамен (тестирование)»" in legacy.warnings[-1]


def test_the_three_columns_of_an_earlier_plan_are_read_as_one_list_of_outcomes():
    """РГ 02.10.2026, раздел 3: «знает / умеет / владеет» is a layer of the РПД, not of generation. The passport has no
    such columns; an earlier export has three, and they stay what they were — the outcomes of the project, in the
    order of the columns."""
    path = Path(__file__).parents[2] / "concept" / "examples" / "curriculum.csv"
    earlier = parse_curriculum(path.name, path.read_bytes())
    assert earlier.columns["outcomes"] == [
        "Обр. результаты — что узнает (ЗНАТЬ)", "Обр. результаты — что умеет (УМЕТЬ)", "Обр. результаты — какой навык (НАВЫКИ)"]
    assert earlier.rows[0].outcomes.splitlines() == [
        "Описывает контекст применения навыка «Выявление проблемы».", "Демонстрирует навык: выявление проблемы",
        "Применяет навык «Выявление проблемы» в практическом задании."]
    for row in earlier.rows:  # nothing of the three cells is lost and nothing is added, whichever of them are filled
        cells = [line.strip() for column in earlier.columns["outcomes"] for line in row.raw[column].splitlines() if line.strip()]
        assert row.outcomes.splitlines() == cells


def test_an_exam_cannot_be_chosen_for_preparation():
    curriculum = plan()
    with pytest.raises(ValidationError, match="«Экзамен 1. Якорь» в УП — экзамен, а не проект"):
        request(curriculum, "D5E1_Start")
    assert request(curriculum, "D6P3_Dataquery").selected_row_id == curriculum.rows[4].id
    with pytest.raises(ValidationError, match="Выберите проект из загруженного УП"):
        StartRequest(request_id="00000000-0000-4000-8000-000000000001", curriculum=curriculum, selected_row_id="нет такой",
                     brief=Brief(title="t", description="Описание проекта", audience="", outcomes="Делает"))


def test_a_plan_of_exams_only_is_refused_with_the_reason():
    with pytest.raises(InputError, match="В CSV только экзамены: проектов для подготовки нет"):
        parse_curriculum("plan.csv", csv_plan("Название проекта;Формат", "Экзамен 1;экзамен", "Итог;экзамен"))
    assert len(parse_curriculum("plan.csv", csv_plan("Название проекта;Формат", "Экзамен 1;экзамен", "Проект;")).rows) == 2


def test_the_place_in_the_programme_counts_projects_and_the_path_keeps_exams():
    curriculum = plan()
    after_exam = request(curriculum, "D6P3_Dataquery")
    # The fifth line of the plan is its fourth project; the project before it is the team one, not the exam.
    assert program_position(after_exam) == {
        "program": "", "position": 4, "total": 10, "stage": "начало программы", "block": "Работа с данными",
        "block_position": 2, "blocks_total": 7, "previous": "Командная сборка по договорённостям",
        "next": "Формулирование пользовательской проблемы"}
    m = program_map(after_exam)
    assert m is not None
    assert m["previous_project"] == {"title": "Командная сборка по договорённостям", "block": "Разработка: основы Python"}
    # Hours are the plan's: its own «ВСЕГО» is 96, and the exam before this project took two of the 31.
    assert (m["hours_before"], m["program_hours"], m["block_hours"]) == (31.0, 96.0, 8.0)
    first_block: Any = program_map(request(curriculum, "D2P2_Errorflow"))
    assert first_block is not None and first_block["block_hours"] == 31.0  # 6 + 8 + 15 and the exam's 2
    assert [p["title"] for p in first_block["block_projects"]] == [
        "Набор базовых Python-скриптов", "Программа работы с файлами и ошибками", "Командная сборка по договорённостям"]
    learner: Any = learner_facts(after_exam)
    assert learner is not None
    assert [(p["title"], p.get("kind")) for p in learner["before"]] == [
        ("Набор базовых Python-скриптов", None), ("Программа работы с файлами и ошибками", None),
        ("Командная сборка по договорённостям", None), ("Экзамен 1. Якорь", "exam")]
    assert [p["title"] for p in learner["later"] if p.get("kind") == "exam"] == ["Экзамен 2.финал"]
    assert "Записи с kind=exam — экзамены: контрольные точки программы, а не проекты." in learner["meaning"]
    # The models get the path by id and title; the mark of an exam goes with it.
    short = compact(m)
    assert short is not None
    assert short["learner"]["before"][-1] == {"id": curriculum.rows[3].id, "title": "Экзамен 1. Якорь", "kind": "exam"}  # type: ignore[index]
    assert set(short["learner"]["before"][0]) == {"id", "title"}  # type: ignore[index]
    # One number for the place, wherever the models read it; the order of lines keeps the exams.
    order: Any = input_semantics(after_exam)["program_order"]
    assert order["selected_position"] == 4
    assert order["before_ids"] == [r.id for r in curriculum.rows[:4]] and curriculum.rows[3].id in order["before_ids"]
    assert order["meaning"].endswith("before_ids и after_ids — все записи до и после, экзамены тоже.")
    # «В скольких проектах» counts projects: the exam ran on the platform and is not practice with it.
    platform = next(t for t in learner["tools_before"] if t["tool"] == "платформа")
    assert "Экзамен 1. Якорь" not in platform["projects"] and len(platform["projects"]) == 3
    assert not any("Экзамен" in line for line in learner["tools_ahead"])
    # The last project of the programme, although the plan ends with it after an exam.
    last = program_position(request(curriculum, "D14P8_Pitchdeck"))
    assert last is not None and (last["position"], last["total"], last["stage"]) == (10, 10, "последний проект программы")


def test_a_tool_of_an_exam_is_not_a_tool_the_participant_will_practise():
    data = csv_plan("Название проекта;Формат;Инструменты", "А;;Git", "Б;;Git", "Экзамен;экзамен;Прокторинг", "В;;Docker", "Итог;экзамен;Тренажёр")
    learner: Any = learner_facts(request_by_title(parse_curriculum("plan.csv", data), "Б"))
    assert learner["tools_ahead"] == ["Docker — «В»"]
    after: Any = learner_facts(request_by_title(parse_curriculum("plan.csv", data), "В"))
    assert [(t["tool"], t["projects"]) for t in after["tools_before"]] == [("Git", ["А", "Б"])]
    assert after["tools_ahead"] == []


def test_a_plan_without_codes_and_exams_goes_to_the_models_exactly_as_before():
    legacy = parse_curriculum("plan.csv", csv_plan("Название проекта;Образовательные результаты;Часы", "А;Делать;4", "Б;Знать;6"))
    assert [map_row(r) for r in legacy.rows] == [r.model_dump(include=MAP_FIELDS) for r in legacy.rows]
    assert set(map_row(legacy.rows[0])) == {"id", "block", "block_goals", "title", "description", "outcomes", "skills", "tools", "software", "hours"}
    order = input_semantics(request_by_title(legacy, "Б"))["program_order"]
    assert order["selected_position"] == 2  # type: ignore[index]
    assert order["meaning"] == (  # type: ignore[index]
        "Порядок записей УП, а не доказательство освоения или обязательной зависимости. Не называй предыдущие записи последующими.")
    learner = learner_facts(request_by_title(legacy, "Б"))
    assert learner is not None
    assert set(learner["before"][0]) == {"id", "title", "block", "hours", "outcomes", "skills", "tools"}  # type: ignore[index]
    assert learner["meaning"] == ("Собрано кодом из УП: ОР, навыки и инструменты проектов до этого — то, что участник проходил по плану. "
                                  "Порядок плана — не доказательство, что это освоено.")
    # With them: the code on every row, the mark only on an exam.
    rows = plan().rows
    assert map_row(rows[0])["code"] == "D1P1_Terminal" and "kind" not in map_row(rows[0])
    assert (map_row(rows[3])["code"], map_row(rows[3])["kind"]) == ("D5E1_Start", "exam")
    assert "cases" not in map_row(rows[0])  # the cases of other projects are not this project's content


def request_by_title(curriculum: Curriculum, title: str) -> StartRequest:
    brief = Brief(title="t", description="Описание проекта", audience="", outcomes="Делает")
    row = next(r for r in curriculum.rows if r.title == title)
    return StartRequest(request_id="00000000-0000-4000-8000-000000000001", brief=brief, curriculum=curriculum,
                        selected_row_id=row.id)


# ---------- hours ----------


@pytest.mark.parametrize(("academic", "hours"), [
    ("10", "7.5"), ("8", "6"), ("3", "2.25"), (" 20 ", "15"), ("1e3", "750"), ("13333333", "9999999.75"),
    # To the hundredth, halves up: 2,5 ак. ч is 1,875 ч.
    ("2,5", "1.88"), ("3,5", "2.63"), ("1.5", "1.13"), ("0.01", "0.01"), ("0.006", "0"),
    ("0", ""), ("-2", ""), ("", ""), ("2-3", ""), ("много", ""), ("8 ч", ""), ("inf", ""), ("Infinity", ""), ("nan", "")])
def test_academic_hours_become_astronomical(academic, hours):
    assert astronomical(academic) == hours


def test_academic_hours_are_read_only_when_the_plan_has_no_others():
    only_academic = parse_curriculum("plan.csv", csv_plan("Название проекта;Трудоемкость, ак.часы", "А;10", "Б;2,5", "В;—"))
    assert [r.hours for r in only_academic.rows] == ["7.5", "1.88", ""]
    converted = "Часы взяты из колонки академических часов и пересчитаны в астрономические (1 ак. ч = 45 минут)."
    assert converted in only_academic.warnings
    assert only_academic.columns["hours_academic"] == ["Трудоемкость, ак.часы"] and "hours" not in only_academic.columns
    assert not any("Трудоёмкость" in text for text in only_academic.missing_fields)
    both = parse_curriculum("plan.csv", csv_plan("Название проекта;Трудоемкость, астр.часы;Трудоемкость, ак.часы", "А;6;8", "Б;;10"))
    # A plan that counts in astronomical hours is not mixed with academic ones, even where a cell is empty.
    assert [r.hours for r in both.rows] == ["6", ""]
    # An astronomical column with not one cell filled gives no hours: the academic ones are the plan's hours.
    empty_column = parse_curriculum("plan.csv", csv_plan("Название проекта;Трудоемкость, астр.часы;Трудоемкость, ак.часы", "А;;8", "Б;-;10"))
    assert [r.hours for r in empty_column.rows] == ["6", "7.5"] and converted in empty_column.warnings
    assert not any("Трудоёмкость" in text for text in empty_column.missing_fields)
    assert converted not in both.warnings
    assert both.unmapped_columns == ["Трудоемкость, ак.часы"] and "hours_academic" not in both.columns
    concept_sheet = parse_curriculum("plan.csv", csv_plan("Название проекта;Нагрузка в ак. часах", "Цифровые угрозы;2,5"))
    assert concept_sheet.rows[0].hours == "1.88"
    neither = parse_curriculum("plan.csv", csv_plan("Название проекта;Формат", "А;"))
    assert "Трудоёмкость: колонка не найдена" in neither.missing_fields


def test_hours_that_are_not_a_finite_number_are_no_hours():
    # float() takes «inf» for a number; a sum with it cannot be written to JSON, and the chat card would not open.
    odd = parse_curriculum("plan.csv", csv_plan("Название проекта;Часы", "А;inf", "Б;nan", "В;-3", "Г;0", "Д;4,5", "Ж;0,5", "Е;1e2"))
    m = program_map(request_by_title(odd, "Е"))
    assert m is not None
    assert [p["hours"] for p in m["learner"]["before"]] == [None, None, None, None, 4.5, 0.5]  # type: ignore[index]
    assert (m["hours_before"], m["program_hours"]) == (5.0, 105.0)
    json.dumps(m, allow_nan=False)
    # Totals are to the hundredth of an hour.
    thirds = parse_curriculum("plan.csv", csv_plan("Название проекта;Часы", "А;1,111", "Б;2,222", "В;1"))
    sums = program_map(request_by_title(thirds, "В"))
    assert sums is not None and (sums["hours_before"], sums["program_hours"]) == (3.33, 4.33)


def test_the_audience_level_is_reported_only_when_the_plan_has_the_column_and_left_it_empty():
    absent = parse_curriculum("plan.csv", csv_plan("Название проекта;Часы", "А;4"))
    assert not any("аудитори" in text for text in absent.missing_fields)
    empty = parse_curriculum("plan.csv", csv_plan("Название проекта;Часы;Уровень аудитории", "А;4;", "Б;6;-"))
    assert "Уровень аудитории: колонка пустая во всех проектах" in empty.missing_fields
    filled = parse_curriculum("plan.csv", csv_plan("Название проекта;Часы;Уровень аудитории", "А;4;базовый"))
    assert not any("аудитори" in text for text in filled.missing_fields) and filled.rows[0].audience == "базовый"


def test_a_result_is_not_handed_on_to_an_exam():
    run = reviewed_run()
    run.request.curriculum = plan()
    run.request.selected_row_id = next(r.id for r in run.request.curriculum.rows if r.code == "D6P3_Dataquery")
    run.evidence = build_evidence(run.request)[0]
    run.input_card = input_card(run.request)
    run.input_card.learner_path = LearnerPath(before="По УП участник прошёл три проекта на Python и экзамен по ним.", builds_on=[], next_needs=[
        NextNeed(project="Экзамен 2.финал", needs="Сдать тест", teach_here="Повторить SQL"),
        NextNeed(project="Формулирование пользовательской проблемы", needs="Данные для экранов", teach_here="Аналитическая записка"),
        NextNeed(project="Экзамен 1. Якорь", needs="…", teach_here="…")])
    found = {f.message: f.suggestion for f in check_input_card(run.input_card, run.request, run.evidence) if "Понадобится дальше" in f.message}
    assert found == {
        "«Понадобится дальше» ссылается на экзамен, а не на проект": "«Экзамен 2.финал» — экзамен: ему результат проекта не передаётся.",
        "«Понадобится дальше» ссылается на проект, который идёт раньше": "«Экзамен 1. Якорь» стоит в УП до этого проекта."}


# ---------- the workbook ----------


HEADER = PLAN.read_text(encoding="utf-8").splitlines()[0]


Rows = Sequence[Sequence[object]]


@pytest.mark.parametrize("header", ["GitLab", "Gitlab ", "ссылка на gitlab", "GitLab (ссылка)", "Репозиторий", "Репозиторий проекта", "Репозиторий GitLab",
                                    "Репозиторий в GitLab", "GitLab-репозиторий", "Ссылка на репозиторий", "Ссылка на проект в GitLab"])
def test_the_column_of_the_repository_is_read_under_the_names_plans_give_it(header):
    plan = f"Название проекта;{header};Образовательные результаты\nПроект;https://git.example.org/masters/D1P1;Умеет\n"
    read = parse_curriculum("plan.csv", plan.encode("utf-8-sig"))
    assert read.rows[0].repository == "https://git.example.org/masters/D1P1" and read.unmapped_columns == []


@pytest.mark.parametrize("header", ["Git", "URL", "Ссылка", "Название проекта на платформе и в Gitlab"])
def test_a_column_that_only_sounds_like_the_repository_is_not_taken_for_it(header):
    plan = f"Название проекта;{header};Образовательные результаты\nПроект;https://git.example.org/masters/D1P1;Умеет\n"
    assert parse_curriculum("plan.csv", plan.encode("utf-8-sig")).rows[0].repository == ""


def workbook(*tabs: tuple[str, str, Rows]) -> bytes:
    """tabs: (title, state, rows)."""
    book = Workbook()
    book.remove(book.active)
    for title, state, rows in tabs:
        sheet = book.create_sheet(title)
        sheet.sheet_state = state
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def plan_rows() -> list[list[object]]:
    """The fixture as a spreadsheet keeps it: numbers as numbers, the range of a group as the date it was typed as."""
    records = list(csv.reader(io.StringIO(PLAN.read_text(encoding="utf-8"))))
    size, numbers = records[0].index("Кол-во в группе"), {records[0].index("Трудоемкость, астр.часы"), records[0].index("№ ")}
    table: list[list[object]] = [list(records[0])]
    for record in records[1:]:
        table.append([date(2026, 4, 2) if i == size and cell == "2-4" else float(cell) if i in numbers and cell else cell or None
                      for i, cell in enumerate(record)])
    return table


SKILLS_TAB: Rows = [["Проект", "Задание", "Git", "Python", "check"], ["D1P1_Terminal", "all", 0.4, 0.6, 1]]
FORM_TAB: Rows = [["Пример заполнения"], ["Тематический блок", "Название проекта"], ["Блок", "Пример проекта"]]


def test_the_workbook_gives_the_same_plan_as_its_csv_export():
    # As in the passport: hidden forms and samples (one of them with the plan's own header), the plan, the skills tabs.
    data = workbook(("Концепция программы", "hidden", FORM_TAB), ("Программа", "hidden", plan_rows()[:3]), ("УП", "visible", plan_rows()),
                    ("Available skills", "visible", [["№", "Skills"], [1, "Git"]]),
                    ("Развесовка skills (OLD)", "visible", SKILLS_TAB), ("Развесовка skills", "visible", SKILLS_TAB))
    from_book, from_csv = parse_curriculum("Паспорт программы_Батон.xlsx", data), plan()
    assert from_book.warnings[0] == "Учебный план прочитан с вкладки «УП»."
    assert (from_book.columns, from_book.missing_fields, from_book.unmapped_columns) == (
        from_csv.columns, from_csv.missing_fields, from_csv.unmapped_columns)
    # The skills come from the workbook's own tabs (test_passport_skills.py): a CSV of the plan tab has none.
    fields = set(type(from_csv.rows[0]).model_fields) - {"id", "raw", "skills"}
    assert [r.model_dump(include=fields) for r in from_book.rows] == [r.model_dump(include=fields) for r in from_csv.rows]
    # A row is named by its line on the tab, as in the CSV; the source line keeps what the form normalised.
    assert from_book.rows[2].raw["Кол-во в группе"] == "2-4" and from_book.rows[2].raw["Трудоемкость, астр.часы"] == "15"
    assert from_book.filename == "Паспорт программы_Батон.xlsx" and from_book.rows[0].id.endswith("-3")


def as_a_spreadsheet_saves(data: bytes, dimension: str | None = None) -> bytes:
    """The workbook as Google Sheets and Excel write it, where openpyxl writes it otherwise: whole numbers stored as
    floats (<v>6.0</v>) and no declared size of a tab — or, with `dimension`, a declared size that is wrong."""
    source, target = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            content = source.read(item)
            if item.filename.startswith("xl/worksheets/sheet"):
                xml = content.decode("utf-8")
                assert "<dimension" in xml
                xml = re.sub(r"<dimension[^>]*/>", f'<dimension ref="{dimension}"/>' if dimension else "", xml)
                xml = re.sub(r"<v>(\d+)</v>", r"<v>\1.0</v>", xml)
                content = xml.encode("utf-8")
            out.writestr(item, content)
    return target.getvalue()


def test_the_workbook_is_read_as_spreadsheets_really_save_it():
    book = workbook(("УП", "visible", plan_rows()), ("Развесовка skills", "visible", SKILLS_TAB))
    fields = set(type(plan().rows[0]).model_fields) - {"id", "raw", "skills"}
    expected = [r.model_dump(include=fields) for r in plan().rows]
    # No declared size and every number a float: the passport downloaded from Google Sheets.
    undeclared = parse_curriculum("p.xlsx", as_a_spreadsheet_saves(book))
    assert [r.model_dump(include=fields) for r in undeclared.rows] == expected
    assert undeclared.rows[2].raw["Трудоемкость, астр.часы"] == "15" and undeclared.rows[2].raw["№"] == "3"
    # A declared size smaller than the tab (a stale one) must not cut the plan off after its third line.
    stale = parse_curriculum("p.xlsx", as_a_spreadsheet_saves(book, "A1:B3"))
    assert [r.model_dump(include=fields) for r in stale.rows] == expected


def test_the_plan_tab_is_found_by_its_header_and_never_guessed():
    rows = plan_rows()[:4]
    # A hidden tab is a form or a sample: it is read only when no visible tab has projects.
    only_hidden = parse_curriculum("p.xlsx", workbook(("Лист1", "visible", [["Заметки"]]), ("План", "hidden", rows)))
    assert only_hidden.warnings == ["Учебный план прочитан с вкладки «План»."]
    for state in ("hidden", "veryHidden"):
        chosen = parse_curriculum("p.xlsx", workbook(("Образец", state, rows), ("УП", "visible", rows)))
        assert chosen.warnings == ["Учебный план прочитан с вкладки «УП»."]
    with pytest.raises(InputError, match="В книге несколько вкладок с проектами: «УП», «УП \\(копия\\)». Выгрузите нужную в CSV"):
        parse_curriculum("p.xlsx", workbook(("УП", "visible", rows), ("УП (копия)", "visible", rows), ("Архив", "hidden", rows)))
    with pytest.raises(InputError) as none:
        parse_curriculum("p.xlsx", workbook(("Развесовка", "visible", SKILLS_TAB), ("Пусто", "visible", [])))
    assert str(none.value) == ("В книге нет вкладки с колонкой «Название проекта». Вкладки: «Развесовка», «Пусто». "
                               "Проверьте заголовки вкладки с учебным планом или выгрузите её в CSV.")
    # The header is the first filled line of the tab; a title lower down is a cell of a table, not a plan.
    below: Rows = [["Пример"], *([[None]] * 3), ["Тематический блок", "Название проекта"], ["Б", "Проект"]]
    with pytest.raises(InputError, match="В книге нет вкладки с колонкой «Название проекта»"):
        parse_curriculum("p.xlsx", workbook(("Форма", "visible", below)))
    # Empty lines above the header are not a header, and neither is a line of spaces: as in a CSV.
    padded: Rows = [[None, None], [" ", None], ["Тематический блок", "Название проекта"], ["Б", "Проект"]]
    assert parse_curriculum("p.xlsx", workbook(("УП", "visible", padded))).rows[0].row_number == 4


def test_how_far_a_tab_is_read():
    head: Rows = [["Название проекта", "Часы"]]
    def book(rows: Rows) -> bytes:
        return workbook(("УП", "visible", rows))
    # The header is looked for in the first lines of a tab, and no further.
    at_the_edge = [*([[None, None]] * (ingestion.HEADER_SEARCH_ROWS - 1)), *head, ["Проект", 4]]
    assert parse_curriculum("p.xlsx", book(at_the_edge)).rows[0].row_number == ingestion.HEADER_SEARCH_ROWS + 1
    with pytest.raises(InputError, match="В книге нет вкладки с колонкой «Название проекта»"):
        parse_curriculum("p.xlsx", book([[None, None], *at_the_edge]))
    # A plan has gaps, never hundreds of empty lines: what a workbook keeps far below the plan is not read.
    gap = [[None, None]] * ingestion.EMPTY_RUN
    near = parse_curriculum("p.xlsx", book([*head, ["А", 4], *gap, ["Б", 6]]))
    assert [r.title for r in near.rows] == ["А", "Б"] and near.rows[1].row_number == ingestion.EMPTY_RUN + 3
    far = parse_curriculum("p.xlsx", book([*head, ["А", 4], *gap, [None, None], ["Далеко внизу", 6]]))
    assert [r.title for r in far.rows] == ["А"]
    # Cells to the right of the last header belong to no column: they neither break the line nor reach the source line.
    wide = parse_curriculum("p.xlsx", book([["Название проекта", "Часы", " ", None], ["А", 4, "заметка на полях", "ещё"]]))
    assert (wide.rows[0].hours, wide.rows[0].raw) == ("4", {"Название проекта": "А", "Часы": "4"})
    # A date is a range only under the header of the group size; under any other header, an empty one too, it is a date.
    dated = parse_curriculum("p.xlsx", book([["Название проекта", "", "Кол-во в группе", "Срок"],
                                             ["А", date(2026, 4, 2), date(2026, 4, 2), date(2026, 4, 2)]]))
    assert dated.rows[0].raw == {"Название проекта": "А", "": "02.04.2026", "Кол-во в группе": "2-4", "Срок": "02.04.2026"}
    # The lines actually read are counted, whatever size the workbook declares: the limit itself is still read.
    many = [*head, *([["Проект", 1]] * ingestion.MAX_PLAN_ROWS)]
    with pytest.raises(InputError) as too_long:
        parse_curriculum("p.xlsx", book(many))
    assert str(too_long.value) == (f"На вкладке «УП» больше {ingestion.MAX_PLAN_ROWS} строк. "
                                   "Скопируйте строки учебного плана на новую вкладку и загрузите книгу снова.")
    with pytest.raises(InputError, match="В прототипе поддерживается до 200 проектов"):  # read whole; refused for another reason
        parse_curriculum("p.xlsx", book(many[:-1]))


def test_a_workbook_that_cannot_be_read_says_so():
    with pytest.raises(InputError, match="Повреждённый документ Office"):
        parse_curriculum("p.xlsx", b"not a zip at all")
    not_a_book = io.BytesIO()
    with zipfile.ZipFile(not_a_book, "w") as archive:
        archive.writestr("readme.txt", "это не книга")
    with pytest.raises(InputError) as unreadable:
        parse_curriculum("p.xlsx", not_a_book.getvalue())
    assert str(unreadable.value) == "Не удалось прочитать книгу XLSX. Сохраните её заново или выгрузите вкладку учебного плана в CSV."
    with pytest.raises(InputError, match="На вкладке «УП» нет проектов с заполненным названием"):
        parse_curriculum("p.xlsx", workbook(("УП", "visible", [["Название проекта", "Часы"], [None, 5]])))
    # A workbook under another name is not decoded as text.
    book = workbook(("УП", "visible", [["Название проекта"], ["А"]]))
    for name in ("plan.csv", "plan.xlsm", "plan"):
        with pytest.raises(InputError, match="Это не CSV. Загрузите учебный план как CSV или как книгу XLSX."):
            parse_curriculum(name, book)
    assert parse_curriculum("PLAN.XLSX", book).rows[0].title == "А"
    # A formula the workbook holds no computed value for gives no hours — never its own text as hours.
    formula = parse_curriculum("p.xlsx", workbook(("УП", "visible", [["Название проекта", "Часы"], ["А", "=2+2"], ["Б", 6]])))
    assert [r.hours for r in formula.rows] == ["", "6"]


@pytest.mark.parametrize(("value", "as_range", "text"), [
    (None, False, ""), (6.0, False, "6"), (7.5, False, "7.5"), (0.25, False, "0.25"), (15, False, "15"), ("P2P", False, "P2P"),
    (0.0, False, "0"), (-3.0, False, "-3"),
    # A workbook stores every number as a float: a long one must not come out rounded or with an exponent.
    (1578279.0, False, "1578279"), (20261005.0, False, "20261005"), (123456.75, False, "123456.75"), (1e15, False, "1000000000000000"),
    (date(2026, 4, 2), True, "2-4"), (date(2026, 2, 4), True, "2-4"), (date(2026, 3, 3), True, "3-3"),
    (datetime(2026, 4, 2, 0, 0), True, "2-4"), (date(2026, 4, 2), False, "02.04.2026")])
def test_a_workbook_cell_as_text(value, as_range, text):
    assert cell_text(value, as_range) == text


def test_the_upload_accepts_the_workbook_and_explains_a_refusal(monkeypatch):
    client = TestClient(app_module.app)
    book = workbook(("УП", "visible", plan_rows()), ("Развесовка skills", "visible", SKILLS_TAB))
    # A workbook is an Office file from outside: it is parsed in the child process under limits, a CSV is not.
    isolated_calls: list[tuple[str, str]] = []
    run_isolated = isolated.run

    def recorded(fn: Any, *args: Any, **limits: Any) -> Any:
        isolated_calls.append((fn.__name__, args[0]))
        return run_isolated(fn, *args, **limits)

    monkeypatch.setattr(runs_module.isolated, "run", recorded)
    done = client.post("/api/curriculum", files={"file": ("Паспорт программы_Батон.XLSX", book)})
    assert done.status_code == 200
    assert [row["code"] for row in done.json()["rows"]] == CODES and done.json()["rows"][3]["kind"] == "exam"
    as_csv = client.post("/api/curriculum", files={"file": ("plan.csv", PLAN.read_bytes())})
    assert as_csv.status_code == 200 and len(as_csv.json()["rows"]) == 12
    assert isolated_calls == [("parse_curriculum", "Паспорт программы_Батон.XLSX")]
    refused = client.post("/api/curriculum", files={"file": ("p.xlsx", workbook(("Развесовка", "visible", SKILLS_TAB)))})
    assert refused.status_code == 422 and "нет вкладки с колонкой «Название проекта»" in refused.json()["detail"]

    def too_heavy(fn: Any, *args: Any, **limits: Any) -> Any:
        raise isolated.ParseRefused(isolated.HEAVY)

    monkeypatch.setattr(runs_module.isolated, "run", too_heavy)
    heavy = client.post("/api/curriculum", files={"file": ("p.xlsx", book)})
    assert (heavy.status_code, heavy.json()["detail"]) == (422, isolated.HEAVY)


def test_a_stored_request_does_not_outgrow_the_limit_by_gaining_fields():
    # Every stored run is validated again when it is read. Three new fields on each of 200 lines added thousands of
    # characters: a run saved just under the limit would have stopped opening.
    rows = "\n".join(f"Проект {i};Делать" for i in range(200))
    curriculum = parse_curriculum("plan.csv", ("Название проекта;Образовательные результаты\n" + rows).encode())
    brief = Brief(title="t", description="Описание проекта", audience="", outcomes="Делает")
    full = 11000  # of the 12000 a passage may hold: the last one takes what is left

    def request_with(passages: int, last: int) -> StartRequest:
        texts = ["я" * full] * passages + ["я" * last]
        material = Material(id="m", name="m.txt", sha256="0" * 64, passages=[
            Passage(id=f"s-{i:03}", location="Строки 1–35", text=text) for i, text in enumerate(texts)])
        return StartRequest(request_id="00000000-0000-4000-8000-000000000001", brief=brief, curriculum=curriculum,
                            selected_row_id=curriculum.rows[0].id, materials=[material])

    def counted(request: StartRequest) -> int:
        return len(request.model_dump_json(exclude_defaults=True))

    rest = counted(request_with(0, 1)) - 1
    passage = counted(request_with(1, 1)) - rest - 1
    passages = (800000 - rest - 1) // passage
    last = 800000 - rest - passages * passage
    at_the_limit = request_with(passages, last)
    assert counted(at_the_limit) == 800000
    assert len(at_the_limit.model_dump_json()) > 800000 + 200 * 30  # what the fields at their defaults would have added
    with pytest.raises(ValidationError, match="Слишком большой комплект"):
        request_with(passages, last + 1)
