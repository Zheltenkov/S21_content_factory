"""Calendar schedule 3.2: periods, organization forms, hours and assessments."""

from __future__ import annotations

from content_factory.rpd.schedule import (
    build_schedule_table,
    refresh_schedule_projection,
    validate_schedule_table,
)
from content_factory.rpd.up_models import from_registry_plan


def _schedule_plan():  # type: ignore[no-untyped-def]
    return from_registry_plan(
        {
            "title": "Тестовая программа",
            "rows": [
                {
                    "block_title": "Основы инструментов",
                    "project_name": "Практическое задание",
                    "delivery_format": "индивидуальный",
                    "p2p_checks": 1,
                    "effort_hours": 8,
                },
                {
                    "block_title": "Профессиональные задачи",
                    "project_name": "Практическая работа",
                    "delivery_format": "практика",
                    "p2p_checks": 1,
                    "effort_hours": 18,
                },
                {
                    "block_title": "Проектная работа",
                    "project_name": "Проект",
                    "delivery_format": "групповой",
                    "effort_hours": 6,
                },
                {
                    "block_title": "Итоговая интеграция",
                    "project_name": "Итоговый проект и демо-день",
                    "delivery_format": "индивидуальный",
                    "effort_hours": 4,
                },
            ],
        }
    )


def test_schedule_matches_template_periods_and_assessment_semantics() -> None:
    up = _schedule_plan()

    result = build_schedule_table(
        up,
        duration="4 недели",
        final_attestation="Защита практической работы",
        profile_id="dpo_pk_v1",
    )

    assert result.rows == [
        [
            "Основы инструментов",
            "1-я неделя",
            "8",
            "Индивидуальная проектная работа с консультациями преподавателя",
            "Практическое задание",
        ],
        [
            "Профессиональные задачи",
            "2-3-я недели",
            "18",
            "Практические занятия, самостоятельная проектная работа и консультации",
            "Практическое задание",
        ],
        [
            "Проектная работа",
            "4-я неделя",
            "6",
            "Групповая проектная работа с консультациями и разбором результатов",
            "Проект",
        ],
        [
            "Итоговая аттестация",
            "4-я неделя",
            "4",
            "Самостоятельная подготовка, консультации и защита итоговой работы",
            "Защита практической работы",
        ],
        ["ИТОГО", "", "36", "", ""],
    ]
    assert result.trace.status == "passport_duration"
    assert not result.trace.review_required
    assert validate_schedule_table(
        result.header,
        result.rows,
        trace=result.trace,
    ).model_dump() == {
        "schema_violations": [],
        "component_violations": [],
        "period_violations": [],
        "hour_violations": [],
        "methodology_violations": [],
    }


def test_schedule_fallback_is_explicit_when_duration_and_final_marker_are_missing() -> None:
    up = from_registry_plan(
        {
            "title": "Тест",
            "rows": [
                {"block_title": "Тема первая", "project_name": "Проект 1", "effort_hours": 8},
                {"block_title": "Тема вторая", "project_name": "Проект 2", "effort_hours": 8},
            ],
        }
    )

    result = build_schedule_table(
        up,
        duration="[Требует уточнения методистом]",
        final_attestation="",
        profile_id="dpo_pk_v1",
    )

    assert result.trace.status == "sequential_default"
    assert result.trace.final_inferred
    assert result.trace.review_required
    assert result.rows[-2][0] == "Итоговая аттестация"
    assert result.rows[-2][4] == "Защита итоговой работы"


def test_schedule_validator_rejects_empty_period_raw_format_p2p_and_hour_drift() -> None:
    result = build_schedule_table(
        _schedule_plan(),
        duration="4 недели",
        final_attestation="Защита практической работы",
        profile_id="dpo_pk_v1",
    )
    result.rows[0][1] = ""
    result.rows[0][2] = "9"
    result.rows[0][3] = "индивидуальный"
    result.rows[0][4] = "p2p-проверка"

    validation = validate_schedule_table(
        result.header,
        result.rows,
        trace=result.trace,
    )

    assert "period:row:1" in validation.period_violations
    assert "organization_form:row:1" in validation.methodology_violations
    assert "control_form:row:1" in validation.methodology_violations
    assert "total_hours" in validation.hour_violations


def test_passport_refresh_changes_only_derived_periods_and_final_control() -> None:
    result = build_schedule_table(
        _schedule_plan(),
        duration="4 недели",
        final_attestation="Защита практической работы",
        profile_id="dpo_pk_v1",
    )
    result.rows[0][3] = "Авторская форма организации занятий методиста"

    refreshed = refresh_schedule_projection(
        result.rows,
        duration="6 недель",
        final_attestation="Защита итогового проекта",
        trace=result.trace,
    )

    assert refreshed.rows[0][1] == "1-2-я недели"
    assert refreshed.rows[1][1] == "3-5-я недели"
    assert refreshed.rows[0][3] == "Авторская форма организации занятий методиста"
    assert refreshed.rows[-2][1] == "6-я неделя"
    assert refreshed.rows[-2][4] == "Защита итогового проекта"
    assert refreshed.trace.duration_weeks == 6
