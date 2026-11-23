"""Internal DPO professional-activity contract and DOCX rendering fidelity."""

from __future__ import annotations

from io import BytesIO

import docx

from content_factory.rpd.build import build_rpd_sections
from content_factory.rpd.dpo_spec import Cat, category_for
from content_factory.rpd.professional_activity import (
    PROFESSIONAL_ACTIVITY_REVIEW_MARKER,
    SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT,
    SchoolPkProfessionalActivitySuggestions,
    build_professional_activity_table,
)
from content_factory.rpd.quality import evaluate_build_quality
from content_factory.rpd.render_selective import render_build
from content_factory.rpd.template import default_pk_skeleton_path
from content_factory.rpd.template_profiles import get_template_profile
from content_factory.rpd.up_models import from_registry_plan

_PLAN = {
    "title": "Технологическое предпринимательство",
    "meta": {"total_hours": 72},
    "rows": [
        {
            "block_title": "Исследование продукта",
            "project_name": "Проверка проблемных и продуктовых гипотез",
            "project_summary": "Интервью, анализ рынка и проверка ценностного предложения",
            "outcomes_can": "проверяет гипотезы и обосновывает границы MVP",
            "outcomes_skills": "оформляет доказательства и принимает продуктовые решения",
            "effort_hours": 72,
            "skills": ["Проверка гипотез", "Проектирование MVP"],
        }
    ],
}


class _ProfessionalActivityClient:
    def __init__(self) -> None:
        self.user = ""
        self.kwargs: dict[str, object] = {}

    def complete_structured(self, **kwargs: object) -> SchoolPkProfessionalActivitySuggestions:
        self.user = str(kwargs["user"])
        self.kwargs = kwargs
        return SchoolPkProfessionalActivitySuggestions(
            activity_area="Продуктовая и организационно-управленческая деятельность в технологическом предпринимательстве",
            professional_tasks=[
                "Исследовать потребности целевой аудитории и проверять продуктовые гипотезы",
                "Определять состав MVP и обосновывать продуктовые решения",
            ],
            required_skills=[
                "Проводить проблемные интервью",
                "Формулировать и проверять гипотезы",
                "Документировать доказательства и принятые решения",
            ],
        )


def _table_with_header(document: docx.document.Document, header: list[str]) -> docx.table.Table:
    return next(
        table
        for table in document.tables
        if table.rows and [cell.text.strip() for cell in table.rows[0].cells] == header
    )


def test_only_internal_school_profile_requires_professional_activity_table() -> None:
    title = "Характеристика профессиональной деятельности"

    # The generic classifier remains prose-compatible for external profiles.
    assert category_for("1.5", title) is Cat.GENERATE
    assert get_template_profile("dpo_pk_v1").category_for("1.5", title) is Cat.PROF_ACTIVITY

    build = build_rpd_sections(from_registry_plan(_PLAN))
    section = build.section("1.5", title)

    assert section is not None
    assert section.category == "professional_activity"
    assert section.kind == "table"
    assert section.table_header == SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.header
    assert [row[0] for row in section.table_rows] == SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.labels
    assert all(row[1] == PROFESSIONAL_ACTIVITY_REVIEW_MARKER for row in section.table_rows)


def test_llm_fills_only_typed_semantic_cells_and_receives_up_evidence() -> None:
    client = _ProfessionalActivityClient()

    header, rows = build_professional_activity_table(from_registry_plan(_PLAN), client, "dpo_pk_v1")

    assert header == ["Позиция", "Минимальный пример заполнения", "Основание"]
    assert client.kwargs["output_model"] is SchoolPkProfessionalActivitySuggestions
    assert client.kwargs["llm_role"] == "rpd_prose"
    assert "Пример структуры для другой программы" in client.user
    assert "Исследование продукта" in client.user
    assert "проверяет гипотезы" in client.user
    assert [row[0] for row in rows] == [
        "Область деятельности",
        "Профессиональная задача",
        "Необходимые умения",
    ]
    assert rows[0][1].startswith("Продуктовая и организационно-управленческая")
    assert "Определять состав MVP" in rows[1][1]
    assert "Формулировать и проверять гипотезы" in rows[2][1]
    # Evidence labels are profile-owned and cannot be invented or rewritten by the model.
    assert [row[2] for row in rows] == [spec.basis for spec in SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.rows]


def test_render_reuses_internal_template_table_for_section_1_5() -> None:
    up = from_registry_plan(_PLAN)
    client = _ProfessionalActivityClient()
    build = build_rpd_sections(up)
    section = build.section("1.5", "Характеристика профессиональной деятельности")
    assert section is not None
    section.table_header, section.table_rows = build_professional_activity_table(up, client, build.template_profile_id)

    rendered = docx.Document(BytesIO(render_build(build)))
    template = docx.Document(str(default_pk_skeleton_path()))
    rendered_table = _table_with_header(rendered, SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.header)
    template_table = _table_with_header(template, SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.header)
    rendered_rows = [[cell.text.strip() for cell in row.cells] for row in rendered_table.rows]

    assert len(rendered_rows) == 4
    assert rendered_rows[0] == SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.header
    assert [row[0] for row in rendered_rows[1:]] == SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.labels
    assert "Проводить проблемные интервью" in rendered_rows[3][1]
    assert rendered_table.style.name == template_table.style.name


def test_quality_gate_reports_missing_values_and_damaged_schema_separately() -> None:
    build = build_rpd_sections(from_registry_plan(_PLAN))

    report = evaluate_build_quality(build)
    missing = next(issue for issue in report.issues if issue.code == "professional_activity_values_missing")
    assert missing.blocking
    assert missing.evidence == SCHOOL_PK_PROFESSIONAL_ACTIVITY_CONTRACT.labels

    section = next(item for item in build.sections if item.category == "professional_activity")
    section.table_header = ["Позиция", "Значение"]
    section.table_rows = section.table_rows[:-1]
    damaged = evaluate_build_quality(build)
    schema = next(issue for issue in damaged.issues if issue.code == "professional_activity_schema_mismatch")

    assert schema.blocking
    assert "header" in schema.evidence
    assert "missing:Необходимые умения" in schema.evidence
    assert "row_order" in schema.evidence
