"""Anton's curriculum export (РГ 25.09, 25:19): other column names, outcomes in one cell.

The content is deliberately absurd (pudding chemistry, hamster ophthalmology): the file tests the
parser here, and the nonsense-input rule of the interpreter in a real run.
"""

from pathlib import Path

from project_generator_lab.concept.ingestion import outcome_lines, parse_curriculum
from project_generator_lab.concept.input_semantics import program_position
from project_generator_lab.concept.models import Brief, StartRequest

FIXTURE = Path(__file__).parent / "fixtures" / "absurd_program.csv"


def test_module_columns_are_recognised_and_gaps_reported():
    curriculum = parse_curriculum("absurd_program.csv", FIXTURE.read_bytes())
    assert len(curriculum.rows) == 9
    row = curriculum.rows[2]
    assert row.title == "Химические свойства пудинга: коллоидные системы и желирование"
    assert (row.hours, row.tools, row.checking) == ("12", "Кастрюля, термометр, венчик", "Лабораторный отчёт")
    assert row.skills == "Лабораторный анализ; органолептическая оценка"
    assert row.program == "Междисциплинарные цифровые компетенции специалиста будущего"
    assert row.outcomes.splitlines() == [
        "Определяет температуру желирования крахмального пудинга",
        "объясняет роль казеина в стабилизации структуры",
        "прогнозирует синерезис при хранении",
    ]
    # The audience level is never in a plan (it is in the brief and the programme concept): its absence is not reported.
    assert curriculum.missing_fields == ["Краткое описание: колонка не найдена"]
    assert curriculum.unmapped_columns == ["Тип"]
    assert curriculum.columns["title"] == ["Модуль"] and curriculum.columns["order"] == ["Код модуля"]


def test_program_name_reaches_the_models_through_the_position():
    curriculum = parse_curriculum("absurd_program.csv", FIXTURE.read_bytes())
    brief = Brief(title="t", description="Описание проекта", audience="", outcomes="Делает")
    request = StartRequest(request_id="00000000-0000-4000-8000-000000000001", brief=brief, curriculum=curriculum,
                           selected_row_id=curriculum.rows[0].id)
    position = program_position(request)
    assert position is not None and position["program"] == "Междисциплинарные цифровые компетенции специалиста будущего"
    assert (position["position"], position["total"], position["stage"]) == (1, 9, "первый проект программы")


def test_outcomes_split_only_when_a_cell_lists_them_with_semicolons():
    assert outcome_lines("А; Б ;В") == "А\nБ\nВ"
    assert outcome_lines("А;") == "А"
    assert outcome_lines("Одна строка") == "Одна строка"
    assert outcome_lines("Строка 1\nСтрока 2; с точкой с запятой") == "Строка 1\nСтрока 2; с точкой с запятой"
    assert outcome_lines("") == ""
