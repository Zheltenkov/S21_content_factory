"""Internal DPO-PK organizational conditions 5.1-5.3."""

from __future__ import annotations

from io import BytesIO

import docx

from content_factory.rpd.build import build_rpd_sections
from content_factory.rpd.dpo_spec import Cat, category_for
from content_factory.rpd.organizational_conditions import (
    MATERIAL_HEADER,
    MATERIAL_WORK_KINDS,
    METHODOLOGICAL_CATEGORIES,
    METHODOLOGICAL_HEADER,
    STAFFING_HEADER,
    build_organizational_conditions,
    validate_material_conditions_table,
    validate_methodological_support_table,
    validate_staffing_table,
)
from content_factory.rpd.quality import evaluate_build_quality
from content_factory.rpd.render_selective import render_build
from content_factory.rpd.template_profiles import get_template_profile
from content_factory.rpd.up_models import UpModule, UpPlan, UpRow


def _up() -> UpPlan:
    return UpPlan(
        title="Технологическое предпринимательство и цифровые продукты",
        total_hours="24",
        modules=[
            UpModule(
                title="Исследование продукта",
                rows=[
                    UpRow(
                        name="Проверка гипотез",
                        hours="12",
                        fmt="индивидуальный",
                        software="Git, GitHub, Docker, IDE, LLM, CRM, аналитика",
                    )
                ],
            ),
            UpModule(
                title="Итоговая аттестация",
                rows=[UpRow(name="Защита продукта", hours="12", fmt="контроль")],
            ),
        ],
    )


def _filled_rows(result):
    methodological = [list(row) for row in result.methodological_rows]
    methodological[0][2] = "Иванов И.И. Технологическое предпринимательство. Москва, 2025"
    methodological[2][2] = "https://docs.github.com; https://docs.docker.com"
    material = [list(row) for row in result.material_rows]
    for row in material:
        row[4] = "Договор безвозмездного пользования, реквизиты подтверждены"
    staffing = [list(row) for row in result.staffing_rows]
    staffing[0][3] = "Петров П.П., преподаватель"
    return methodological, material, staffing


def test_conditions_builder_preserves_three_exact_template_schemas() -> None:
    result = build_organizational_conditions(_up(), profile_id="dpo_pk_v1")

    assert result.methodological_header == METHODOLOGICAL_HEADER
    assert [row[0] for row in result.methodological_rows] == METHODOLOGICAL_CATEGORIES
    assert result.material_header == MATERIAL_HEADER
    assert [row[0] for row in result.material_rows] == MATERIAL_WORK_KINDS
    assert result.staffing_header == STAFFING_HEADER
    assert [row[0] for row in result.staffing_rows] == ["Преподаватель"]
    assert "273-ФЗ" in result.methodological_rows[1][2]
    assert "GitHub" in result.methodological_rows[2][1]
    assert "Docker" in result.material_rows[1][2]
    assert result.trace.detected_tools[:4] == ["GitHub", "Git", "Docker", "IDE"]


def test_unknown_facts_stay_human_owned_instead_of_being_invented() -> None:
    result = build_organizational_conditions(_up(), profile_id="dpo_pk_v1")

    support = validate_methodological_support_table(
        result.methodological_header,
        result.methodological_rows,
    )
    material = validate_material_conditions_table(result.material_header, result.material_rows)
    staffing = validate_staffing_table(result.staffing_header, result.staffing_rows)

    assert support.review_required == ["row:1:column:3", "row:3:column:3"]
    assert material.review_required == ["row:1:column:5", "row:2:column:5"]
    assert staffing.review_required == ["row:1:column:4"]
    assert not support.schema_violations
    assert not material.schema_violations
    assert not staffing.schema_violations


def test_completed_human_evidence_passes_all_conditions_validators() -> None:
    result = build_organizational_conditions(_up(), profile_id="dpo_pk_v1")
    methodological, material, staffing = _filled_rows(result)

    validations = [
        validate_methodological_support_table(result.methodological_header, methodological),
        validate_material_conditions_table(result.material_header, material),
        validate_staffing_table(result.staffing_header, staffing),
    ]
    assert all(not validation.schema_violations for validation in validations)
    assert all(not validation.label_violations for validation in validations)
    assert all(not validation.methodology_violations for validation in validations)
    assert all(not validation.value_violations for validation in validations)
    assert all(not validation.review_required for validation in validations)


def test_conditions_are_capabilities_of_internal_profile_only() -> None:
    profile = get_template_profile("dpo_pk_v1")

    assert profile.category_for("5.1", "Учебно-методическое и информационное обеспечение") is Cat.METHODOLOGICAL_SUPPORT
    assert profile.category_for("5.2", "Материально-технические условия") is Cat.MATERIAL_CONDITIONS
    assert profile.category_for("5.3", "Кадровое обеспечение") is Cat.STAFFING
    assert category_for("5.1", "Учебно-методическое и информационное обеспечение") is Cat.GAP


def test_build_quality_editor_and_docx_keep_51_53_as_tables() -> None:
    build = build_rpd_sections(_up())
    sections = {
        section.category: section
        for section in build.sections
        if section.category in {"methodological_support", "material_conditions", "staffing"}
    }
    assert set(sections) == {"methodological_support", "material_conditions", "staffing"}
    assert all(section.kind == "table" for section in sections.values())
    assert {issue.code for issue in build.quality.issues}.issuperset(
        {
            "methodological_support_evidence_required",
            "material_conditions_access_required",
            "staffing_identity_required",
        }
    )

    result = build_organizational_conditions(_up(), profile_id="dpo_pk_v1")
    methodological, material, staffing = _filled_rows(result)
    sections["methodological_support"].table_rows = methodological
    sections["material_conditions"].table_rows = material
    sections["staffing"].table_rows = staffing
    quality = evaluate_build_quality(build)
    assert not any(
        issue.code.startswith(("methodological_support_", "material_conditions_", "staffing_"))
        for issue in quality.issues
    )

    rendered = docx.Document(BytesIO(render_build(build)))
    headers = [tuple(cell.text.strip() for cell in table.rows[0].cells) for table in rendered.tables if table.rows]
    assert tuple(METHODOLOGICAL_HEADER) in headers
    assert tuple(MATERIAL_HEADER) in headers
    assert tuple(STAFFING_HEADER) in headers
