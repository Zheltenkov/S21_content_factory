"""Sections 4.1/4.2: source-bound controls and final-attestation passport."""

from __future__ import annotations

from io import BytesIO

import docx

from content_factory.rpd.assessment import (
    CURRENT_CONTROL_HEADER,
    FINAL_ATTESTATION_HEADER,
    FINAL_ATTESTATION_PARAMETERS,
    build_assessment_tables,
    refresh_final_attestation_form,
    validate_current_control_table,
    validate_final_attestation_table,
)
from content_factory.rpd.build import build_rpd_sections
from content_factory.rpd.dpo_spec import Cat, category_for
from content_factory.rpd.quality import evaluate_build_quality
from content_factory.rpd.render_selective import render_build
from content_factory.rpd.schedule import build_schedule_table
from content_factory.rpd.template_profiles import get_template_profile
from content_factory.rpd.up_models import UpModule, UpPlan, UpRow


def _up() -> UpPlan:
    return UpPlan(
        title="Цифровой продукт",
        total_hours="36",
        modules=[
            UpModule(
                title="Исследование потребностей клиента",
                rows=[
                    UpRow(
                        name="Интервью с клиентом",
                        hours="8",
                        fmt="практика",
                        p2p="1",
                        outcomes_skills="Формулирует подтверждённую проблему клиента.",
                    )
                ],
            ),
            UpModule(
                title="Проектирование цифрового решения",
                rows=[
                    UpRow(
                        name="Прототип решения",
                        hours="18",
                        fmt="практика",
                        outcomes_skills="Проверяет работоспособность прототипа.",
                    )
                ],
            ),
            UpModule(
                title="Итоговая аттестация",
                rows=[
                    UpRow(
                        name="Защита итогового продукта",
                        hours="10",
                        fmt="контроль",
                        outcomes_skills=(
                            "Обосновывает выбранное решение. "
                            "Демонстрирует работоспособность итогового продукта."
                        ),
                    )
                ],
            ),
        ],
    )


def _tables():
    up = _up()
    schedule = build_schedule_table(
        up,
        duration="3 недели",
        final_attestation="Защита итогового продукта",
        profile_id="dpo_pk_v1",
    )
    return build_assessment_tables(
        up,
        final_attestation="Защита итогового продукта",
        profile_id="dpo_pk_v1",
        schedule_trace=schedule.trace,
    )


def test_current_control_covers_each_learning_module_and_uses_human_terms() -> None:
    result = _tables()

    assert result.current_header == CURRENT_CONTROL_HEADER
    assert [row[0] for row in result.current_rows] == [
        "Исследование потребностей клиента",
        "Проектирование цифрового решения",
    ]
    assert result.trace.current_module_indices == [1, 2]
    assert result.trace.final_module_index == 3
    assert all("p2p" not in row[1].casefold() for row in result.current_rows)
    assert "взаимная проверка" in result.current_rows[0][2].casefold()
    assert "проверка преподавателем" in result.current_rows[1][2].casefold()
    assert "подтверждённую проблему клиента" in result.current_rows[0][3]
    assert all("LMS/ЭИОС" in row[2] and row[4] == "Зачтено / не зачтено" for row in result.current_rows)


def test_assessment_tables_are_an_internal_profile_capability() -> None:
    profile = get_template_profile("dpo_pk_v1")

    assert profile.category_for("4.1", "Текущий контроль") is Cat.CURRENT_CONTROL
    assert profile.category_for("4.2", "Итоговая аттестация") is Cat.FINAL_ATTESTATION
    assert category_for("4.1", "Текущий контроль") is Cat.GAP
    assert category_for("4.2", "Итоговая аттестация") is Cat.GAP


def test_final_attestation_is_a_complete_seven_row_passport() -> None:
    result = _tables()
    values = {row[0]: row[1] for row in result.final_rows}

    assert result.final_header == FINAL_ATTESTATION_HEADER
    assert [row[0] for row in result.final_rows] == FINAL_ATTESTATION_PARAMETERS
    assert values["Форма"] == "Защита итогового продукта"
    assert values["Продолжительность"].startswith("10 академических часов")
    assert "Демонстрирует работоспособность" in values["Проверяемый результат"]
    assert "70 %" in values["Порог успешного прохождения"]
    assert "Протокол" in values["Фиксация результата"]


def test_assessment_validators_block_lost_modules_and_incomplete_passport() -> None:
    result = _tables()
    current = validate_current_control_table(
        result.current_header,
        result.current_rows[:-1],
        result.trace,
    )
    final_rows = [list(row) for row in result.final_rows]
    final_rows[1][1] = ""
    final = validate_final_attestation_table(result.final_header, final_rows)

    assert "row_count" in current.schema_violations
    assert final.value_violations == ["empty:Проверяемый результат"]


def test_final_form_refresh_changes_only_the_form_row() -> None:
    result = _tables()
    refreshed = refresh_final_attestation_form(
        result.final_rows,
        "Защита практической работы",
    )

    assert refreshed[0] == ["Форма", "Защита практической работы"]
    assert refreshed[1:] == result.final_rows[1:]


def test_build_editor_quality_and_docx_keep_41_42_as_template_tables() -> None:
    build = build_rpd_sections(_up())
    current = next(section for section in build.sections if section.category == "current_control")
    final = next(section for section in build.sections if section.category == "final_attestation")

    assert current.kind == final.kind == "table"
    assert current.table_header == CURRENT_CONTROL_HEADER
    assert final.table_header == FINAL_ATTESTATION_HEADER
    assert not any(
        issue.code.startswith(("current_control_", "final_attestation_"))
        for issue in build.quality.issues
    )

    rendered = docx.Document(BytesIO(render_build(build)))
    headers = [tuple(cell.text.strip() for cell in table.rows[0].cells) for table in rendered.tables if table.rows]
    assert tuple(CURRENT_CONTROL_HEADER) in headers
    assert tuple(FINAL_ATTESTATION_HEADER) in headers

    current.table_rows.pop()
    final.table_rows[1][1] = ""
    quality = evaluate_build_quality(build)
    codes = {issue.code for issue in quality.issues}
    assert "current_control_schema_mismatch" in codes
    assert "final_attestation_values_missing" in codes
