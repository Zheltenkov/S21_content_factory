"""Passport contract: structured LLM suggestions, editable table and DOCX fidelity."""

from __future__ import annotations

from io import BytesIO

import docx

from content_factory.rpd.build import build_rpd_sections
from content_factory.rpd.dpo_spec import Cat, category_for
from content_factory.rpd.passport import (
    PASSPORT_REVIEW_MARKER,
    SCHOOL_PK_PASSPORT_CONTRACT,
    SchoolPkPassportSuggestions,
    build_passport_table,
    merge_document_fields,
)
from content_factory.rpd.quality import evaluate_build_quality
from content_factory.rpd.render_selective import render_build
from content_factory.rpd.template import default_pk_skeleton_path
from content_factory.rpd.up_models import from_registry_plan

_PLAN = {
    "title": "Технологическое предпринимательство",
    "meta": {"total_hours": 72},
    "rows": [
        {
            "block_title": "Исследование продукта",
            "project_name": "Проверка гипотез",
            "outcomes_can": "проверяет продуктовые гипотезы и формулирует ценностное предложение",
            "effort_hours": 72,
            "skills": ["Проверка гипотез"],
        }
    ],
}


class _PassportClient:
    def __init__(self) -> None:
        self.user = ""
        self.kwargs: dict[str, object] = {}

    def complete_structured(self, **kwargs: object) -> SchoolPkPassportSuggestions:
        self.user = str(kwargs["user"])
        self.kwargs = kwargs
        return SchoolPkPassportSuggestions(
            audience="Начинающие технологические предприниматели и участники продуктовых команд",
            admission_requirements="Наличие или получение среднего профессионального и/или высшего образования",
            delivery_mode="Очно-заочная, с применением электронного обучения и ДОТ",
            duration="9 недель",
            study_mode="Не более 8 академических часов в неделю",
            final_attestation="Защита итогового продуктового проекта",
        )


def _values(rows: list[list[str]]) -> dict[str, str]:
    return {row[0]: row[1] for row in rows}


def _passport_table(document: docx.document.Document) -> docx.table.Table:
    return next(
        table
        for table in document.tables
        if table.rows
        and [row.cells[0].text.strip() for row in table.rows[:2]] == ["Параметр", "Наименование"]
    )


def test_standard_profile_classifies_program_passport_as_structured_table() -> None:
    assert category_for("1.1", "Паспорт программы") is Cat.PASSPORT

    build = build_rpd_sections(from_registry_plan(_PLAN))
    passport = build.section("1.1", "Паспорт программы")

    assert passport is not None
    assert passport.category == "passport"
    assert passport.kind == "table"
    assert passport.table_header == ["Параметр", "Значение"]
    assert [row[0] for row in passport.table_rows] == SCHOOL_PK_PASSPORT_CONTRACT.labels
    assert _values(passport.table_rows)["Наименование"] == "Технологическое предпринимательство"
    assert _values(passport.table_rows)["Объем"] == "72 академических часов"
    assert _values(passport.table_rows)["Категория слушателей"] == PASSPORT_REVIEW_MARKER


def test_passport_llm_receives_example_and_can_only_fill_typed_semantic_fields() -> None:
    client = _PassportClient()

    header, rows = build_passport_table(from_registry_plan(_PLAN), client, "dpo_pk_v1")
    values = _values(rows)

    assert header == ["Параметр", "Значение"]
    assert client.kwargs["output_model"] is SchoolPkPassportSuggestions
    assert client.kwargs["llm_role"] == "rpd_prose"
    assert "Пример формы ответа для другой программы" in client.user
    assert "Исследование продукта" in client.user
    assert values["Категория слушателей"].startswith("Начинающие технологические предприниматели")
    assert values["Наименование"] == "Технологическое предпринимательство"
    assert values["Вид программы"].endswith("повышения квалификации")
    assert values["Документ о квалификации"].startswith("Удостоверение")


def test_render_keeps_the_template_passport_table_and_populates_all_rows() -> None:
    build = build_rpd_sections(from_registry_plan(_PLAN))
    build.document_fields = build.document_fields.model_copy(
        update={
            "audience": "Технологические предприниматели и продуктовые команды",
            "admission_requirements": "Наличие или получение СПО и/или ВО",
            "delivery_mode": "очно-заочная с применением ДОТ",
            "duration": "9 недель",
            "study_mode": "не более 8 академических часов в неделю",
            "final_attestation": "защита итогового проекта",
            "issued_document": "удостоверение о повышении квалификации установленного образца",
        }
    )
    passport = next(section for section in build.sections if section.category == "passport")
    passport.table_rows = merge_document_fields(
        passport.table_rows,
        build.document_fields,
        build.template_profile_id,
    )

    rendered = docx.Document(BytesIO(render_build(build)))
    template = docx.Document(str(default_pk_skeleton_path()))
    rendered_passport = _passport_table(rendered)
    template_passport = _passport_table(template)
    rows = [[cell.text.strip() for cell in row.cells] for row in rendered_passport.rows]

    assert len(rows) == 11
    assert rows[0] == ["Параметр", "Значение"]
    assert [row[0] for row in rows[1:]] == SCHOOL_PK_PASSPORT_CONTRACT.labels
    assert _values(rows[1:])["Наименование"] == "Технологическое предпринимательство"
    assert _values(rows[1:])["Требования к поступающим"] == "Наличие или получение СПО и/или ВО"
    assert _values(rows[1:])["Продолжительность"] == "9 недель"
    assert rendered_passport.style.name == template_passport.style.name


def test_quality_gate_rejects_damaged_passport_schema() -> None:
    build = build_rpd_sections(from_registry_plan(_PLAN))
    passport = next(section for section in build.sections if section.category == "passport")
    passport.table_rows = [row for row in passport.table_rows if row[0] != "Режим занятий"]

    report = evaluate_build_quality(build, render_build(build))

    issue = next(item for item in report.issues if item.code == "passport_schema_mismatch")
    assert issue.blocking
    assert "missing:Режим занятий" in issue.evidence
