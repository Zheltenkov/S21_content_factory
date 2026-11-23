"""Public curriculum table: editorial names and an exact four-way workload contract."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from content_factory.rpd.curriculum import build_curriculum_table, validate_curriculum_table
from content_factory.rpd.curriculum_contracts import (
    CurriculumTitleDraft,
    CurriculumTitleResponse,
)
from content_factory.rpd.up_models import from_registry_plan


def _mixed_plan():  # type: ignore[no-untyped-def]
    return from_registry_plan(
        {
            "title": "Публичный учебный план",
            "rows": [
                {
                    "block_title": "основы продукта → итоговое решение",
                    "project_name": "Теоретическая рамка",
                    "delivery_format": "лекция",
                    "effort_hours": 2,
                },
                {
                    "block_title": "",
                    "project_name": "Практический проект",
                    "delivery_format": "практическое занятие",
                    "effort_hours": 4,
                },
                {
                    "block_title": "",
                    "project_name": "Самостоятельный анализ",
                    "delivery_format": "самостоятельная работа",
                    "effort_hours": 3,
                },
                {
                    "block_title": "",
                    "project_name": "Защита",
                    "delivery_format": "итоговая аттестация",
                    "effort_hours": 1,
                },
            ],
        }
    )


class _TitleClient:
    def __init__(self, *, invalid: bool = False, title: str = "") -> None:
        self.invalid = invalid
        self.title = title
        self.roles: list[str] = []

    def complete_structured(
        self,
        *,
        output_model: type[BaseModel],
        llm_role: str,
        **kwargs: Any,
    ) -> BaseModel:
        del kwargs
        assert output_model is CurriculumTitleResponse
        self.roles.append(llm_role)
        title = self.title or (
            "модуль" if self.invalid else "Исследование продукта и подготовка итогового решения"
        )
        return CurriculumTitleResponse(
            modules=[CurriculumTitleDraft(module_index=1, title=title)]
        )


def test_deterministic_projection_uses_four_numeric_workload_kinds_and_total() -> None:
    result = build_curriculum_table(
        _mixed_plan(),
        None,
        profile_id="dpo_pk_v1",
    )

    assert result.rows == [
        [
            "1",
            "Основы продукта",
            "10",
            "2",
            "4",
            "3",
            "1",
        ],
        ["", "ИТОГО", "10", "2", "4", "3", "1"],
    ]
    assert result.trace.status == "deterministic"
    assert result.trace.workload_kinds == (
        "lectures",
        "practical",
        "independent",
        "control",
    )
    assert validate_curriculum_table(result.header, result.rows).model_dump() == {
        "schema_violations": [],
        "title_violations": [],
        "workload_violations": [],
    }


def test_bare_module_placeholder_is_replaced_with_project_name() -> None:
    up = from_registry_plan(
        {
            "title": "Тест",
            "rows": [
                {
                    "block_title": "Модуль",
                    "project_name": "Карта клиентской проблемы",
                    "effort_hours": 8,
                }
            ],
        }
    )

    result = build_curriculum_table(up, None, profile_id="dpo_pk_v1")

    assert result.rows[0][1] == "Практика: Карта клиентской проблемы"
    assert result.rows[0][4] == "8"
    assert result.rows[0][6] == "—"
    assert result.trace.inferred_workload_rows == 1


def test_deterministic_title_preserves_part_number_without_duplicate_separator() -> None:
    up = from_registry_plan(
        {
            "title": "Тест",
            "rows": [
                {
                    "block_title": "инфраструктура продукта → автоматизация · часть 2",
                    "project_name": "Эксплуатация",
                    "delivery_format": "практика",
                    "effort_hours": 8,
                }
            ],
        }
    )

    result = build_curriculum_table(up, None, profile_id="dpo_pk_v1")

    assert result.rows[0][1] == "Инфраструктура продукта · часть 2"


def test_typed_title_editor_is_used_and_invalid_output_falls_back() -> None:
    valid_client = _TitleClient()
    valid = build_curriculum_table(
        _mixed_plan(),
        valid_client,
        profile_id="dpo_pk_v1",
    )
    invalid = build_curriculum_table(
        _mixed_plan(),
        _TitleClient(invalid=True),
        profile_id="dpo_pk_v1",
    )

    assert valid.trace.status == "llm"
    assert valid.rows[0][1] == "Исследование продукта и подготовка итогового решения"
    assert valid_client.roles == ["rpd_curriculum_titles"]
    assert invalid.trace.status == "deterministic"
    assert invalid.rows[0][1] == "Основы продукта"
    assert invalid.trace.title_issues


def test_two_word_public_title_is_valid_for_short_final_modules() -> None:
    client = _TitleClient(title="Итоговая интеграция")

    result = build_curriculum_table(_mixed_plan(), client, profile_id="dpo_pk_v1")

    assert result.trace.status == "llm"
    assert result.rows[0][1] == "Итоговая интеграция"


def test_duplicate_llm_titles_fall_back_per_row_without_discarding_valid_rows() -> None:
    up = from_registry_plan(
        {
            "title": "Тест",
            "rows": [
                {"block_title": "исследование клиента → отчёт", "project_name": "Интервью", "effort_hours": 2},
                {"block_title": "разработка продукта → прототип", "project_name": "MVP", "effort_hours": 2},
                {"block_title": "запуск продукта → продажи", "project_name": "Запуск", "effort_hours": 2},
            ],
        }
    )

    class _DuplicateClient:
        def complete_structured(self, **_kwargs: Any) -> CurriculumTitleResponse:
            return CurriculumTitleResponse(
                modules=[
                    CurriculumTitleDraft(module_index=1, title="Работа над продуктом"),
                    CurriculumTitleDraft(module_index=2, title="Работа над продуктом"),
                    CurriculumTitleDraft(module_index=3, title="Запуск цифрового продукта"),
                ]
            )

    result = build_curriculum_table(up, _DuplicateClient(), profile_id="dpo_pk_v1")

    assert result.trace.status == "llm"
    assert result.trace.title_issues == ["duplicate_rows:1,2"]
    assert result.trace.display_titles == [
        "Исследование клиента",
        "Разработка продукта",
        "Запуск цифрового продукта",
    ]


def test_validator_rejects_lowercase_generic_title_control_text_and_bad_sum() -> None:
    result = build_curriculum_table(_mixed_plan(), None, profile_id="dpo_pk_v1")
    result.rows[0][1] = "модуль"
    result.rows[0][6] = "p2p-проверка"

    validation = validate_curriculum_table(result.header, result.rows)

    assert "row:1:generic" in validation.title_violations
    assert "row:1:lowercase_start" in validation.title_violations
    assert "workload_not_numeric:row:1" in validation.workload_violations


def test_validator_rejects_service_duplicates_long_titles_and_invalid_numbers() -> None:
    result = build_curriculum_table(_mixed_plan(), None, profile_id="dpo_pk_v1")
    duplicate = result.rows[0].copy()
    duplicate[0] = "2"
    result.rows.insert(1, duplicate)
    result.rows[-1] = ["", "ИТОГО", "20", "4", "8", "6", "2"]
    result.rows[0][1] = "Модуль 2"
    result.rows[1][1] = "Очень длинное название модуля с техническими подробностями процесса работы и дополнительными служебными словами внутри"
    result.rows[0][3] = ""
    result.rows[1][3] = "Infinity"

    validation = validate_curriculum_table(result.header, result.rows)

    assert "row:1:generic" in validation.title_violations
    assert "row:2:word_count" in validation.title_violations
    assert "workload_not_numeric:row:1" in validation.workload_violations
    assert "workload_not_numeric:row:2" in validation.workload_violations

    duplicate_result = build_curriculum_table(_mixed_plan(), None, profile_id="dpo_pk_v1")
    duplicate_row = duplicate_result.rows[0].copy()
    duplicate_row[0] = "2"
    duplicate_result.rows.insert(1, duplicate_row)
    duplicate_result.rows[-1] = ["", "ИТОГО", "20", "4", "8", "6", "2"]
    duplicate_validation = validate_curriculum_table(
        duplicate_result.header,
        duplicate_result.rows,
    )
    assert "duplicate_rows:1,2" in duplicate_validation.title_violations

    negative_result = build_curriculum_table(_mixed_plan(), None, profile_id="dpo_pk_v1")
    negative_result.rows[0][3] = "-2"
    negative_result.rows[0][4] = "6"
    negative_validation = validate_curriculum_table(
        negative_result.header,
        negative_result.rows,
    )
    assert "workload_not_numeric:row:1" in negative_validation.workload_violations


def test_organization_form_uses_reviewable_profile_default_not_source_exactness() -> None:
    up = from_registry_plan(
        {
            "title": "Тест",
            "rows": [
                {
                    "block_title": "Практика продукта",
                    "project_name": "Прототип",
                    "delivery_format": "индивидуальный",
                    "effort_hours": 8,
                }
            ],
        }
    )

    result = build_curriculum_table(up, None, profile_id="dpo_pk_v1")

    projection = result.trace.workload_projection[0]
    assert result.rows[0][4] == "8"
    assert projection.provenance == "profile_default"
    assert projection.inferred_hours == "8"
    assert projection.exact_allocated_hours["practical"] == "0"
    assert projection.review_required
