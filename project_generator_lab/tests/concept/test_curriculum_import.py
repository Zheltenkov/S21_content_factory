"""Input fidelity regressions against the existing generator's passport fixture."""

import csv
import io
from pathlib import Path

import pytest
from project_generator_lab.concept.ingestion import InputError, parse_curriculum


def csv_bytes(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";").writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def test_existing_generator_passport_retains_all_three_outcome_columns():
    # Copied from PROGRAM_TEMPLATE_COMMA_CSV_CONTENT in the production API tests.
    fixture = Path(__file__).parent / "fixtures/program_passport.csv"
    result = parse_curriculum(fixture.name, fixture.read_bytes())
    first, second = result.rows
    assert first.title == "BSA00_Decomposition"
    assert first.description.startswith("В этом проекте участник изучает")
    assert first.software == "draw.io"
    assert first.materials == "materials/context.md"
    assert first.outcomes.splitlines() == [
        "Основные этапы разработки ИТ-систем.", "Разбираться в этапах жизненного цикла.",
        "Знает роли и виды декомпозиции.",
    ]
    raw_outcomes = [text for key, text in first.raw.items() if key.startswith("Образовательные результаты")]
    assert raw_outcomes == first.outcomes.splitlines()
    assert second.format == "group"
    assert second.group_size == "4"
    assert first.block != second.block
    assert result.warnings


def test_missing_numbers_block_rows_and_goals_do_not_drop_projects_or_leak():
    data = csv_bytes([
        ["Тематический блок", "Цели блока", "№", "Название проекта"],
        ["Название блока", "Цель", "№", "Название проекта"],
        ["Первый", "Разобраться", "", ""],
        ["", "", "", "Проект А"],
        ["", "Проверить", "2", "Проект Б"],
        ["Второй", "", "", "Проект В"],
    ])
    result = parse_curriculum("plan.csv", data)
    assert [r.title for r in result.rows] == ["Проект А", "Проект Б", "Проект В"]
    assert result.rows[0].block == result.rows[1].block == "Первый"
    assert result.rows[1].block_goals == "Разобраться\nПроверить"
    assert result.rows[2].block_goals == ""
    assert result.rows[0].row_number == 4


def test_aliases_story_type_group_inference_and_unknown_metadata():
    data = csv_bytes([
        ["Название всего блока (если делим на блоки)", "Название контентной единицы", "Обязательные инструменты",
         "Необходимое ПО и среда", "Сторителтнг", "Тип сторителлинга", "Кол-во в группе", "Формат",
         "Дополнительные материалы для генерации", "Трудоемкость, астр.часы", "Что нужно разработать эксперту"],
        ["Блок", "Проект", "Git", "IDE", "Кейс команды", "case", "3–4", "", "guide.md", "12,5", "Чек-лист"],
    ])
    row = parse_curriculum("plan.csv", data).rows[0]
    assert (row.tools, row.software, row.storytelling, row.storytelling_type) == ("Git", "IDE", "Кейс команды", "case")
    assert (row.format, row.group_size, row.hours, row.materials) == ("group", "4", "12,5", "guide.md")
    assert row.raw["Кол-во в группе"] == "3–4"
    assert row.raw["Что нужно разработать эксперту"] == "Чек-лист"


def test_malformed_record_is_reported_instead_of_silently_losing_cells():
    data = 'Тематический блок;Название проекта\nБлок;Проект;лишняя ячейка'.encode()
    with pytest.raises(InputError, match="Запись 2"):
        parse_curriculum("plan.csv", data)


def test_short_file_sniffer_fallback_preserves_quoted_commas():
    data = '\n"Название проекта","Трудоемкость, астр.часы"\nПроект,4'.encode()
    row = parse_curriculum("plan.csv", data).rows[0]
    assert row.title == "Проект"
    assert row.hours == "4"


def test_blank_block_header_is_not_confused_with_project_number():
    data = csv_bytes([
        ["  ", "Цели блока", "№ ", "Название проекта"],
        ["Введение в управление", "Различать проекты и процессы", "1", "Проект А"],
        ["", "", "2", "Проект Б"],
        ["Подготовка проекта", "Собирать требования", "5", "Проект В"],
        ["", "", "6", "Проект Г"],
    ])
    result = parse_curriculum("project_manager.csv", data)
    assert [r.block for r in result.rows] == [
        "Введение в управление", "Введение в управление", "Подготовка проекта", "Подготовка проекта",
    ]
    assert result.rows[1].raw["№"] == "2"
    assert result.warnings == []


# ---------- какого шаблона ждём: отказ должен этому учить ----------

def test_a_plan_without_the_title_column_is_told_which_names_are_accepted():
    """«Не найдены колонки, хотя они там есть» — методолог не узнавал, какого шаблона ждут.

    Отказ теперь называет и принятые имена, и шаблон, который можно скачать и заполнить.
    """
    plan = "Тема занятия;Часы" + chr(10) + "Введение;8" + chr(10)

    with pytest.raises(InputError) as info:
        parse_curriculum("план.csv", plan.encode("utf-8-sig"))

    said = str(info.value)
    assert "«Тема занятия»" in said, "перечисляем, что нашли"
    assert "Название проекта" in said and "Название контентной единицы" in said, "и что принимаем"
    assert "шаблон" in said.lower(), "и куда идти за образцом"


def test_the_template_is_built_from_the_table_the_parser_reads_by():
    """Шаблон собирается из COLUMNS, поэтому разойтись с парсером не может."""
    from project_generator_lab.concept.curriculum_columns import COLUMNS
    from project_generator_lab.concept.template import REQUIRED, headers

    assert headers()[: len(REQUIRED)] == [COLUMNS[field][0] for field in REQUIRED]
    assert len(headers()) == len(set(headers())), "повторов в заголовках быть не должно"


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_the_template_is_read_by_the_parser_without_complaints(kind):
    """Проверка настоящая: шаблон проходит тот же путь, что и файл методолога."""
    from project_generator_lab.concept.template import template_bytes

    plan = parse_curriculum(f"шаблон.{kind}", template_bytes(kind))

    assert plan.rows, "в шаблоне есть заполненная строка-пример"
    assert plan.missing_fields == [], plan.missing_fields
    assert plan.unmapped_columns == [], plan.unmapped_columns


@pytest.mark.parametrize(("kind", "media"), [
    ("csv", "text/csv"),
    ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
])
def test_the_template_can_be_downloaded(kind, media):
    """Отказ отправляет за шаблоном — значит шаблон должен отдаваться."""
    from fastapi.testclient import TestClient
    from project_generator_lab import app as app_module

    with TestClient(app_module.app) as client:
        response = client.get(f"/api/curriculum-template.{kind}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media)
    assert parse_curriculum(f"шаблон.{kind}", response.content).rows


def test_an_unknown_template_format_is_refused():
    from fastapi.testclient import TestClient
    from project_generator_lab import app as app_module

    with TestClient(app_module.app) as client:
        assert client.get("/api/curriculum-template.pdf").status_code == 404
