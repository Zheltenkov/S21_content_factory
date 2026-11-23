"""Section 7 program-developers contract and DOCX rendering."""

from __future__ import annotations

from io import BytesIO

import docx

from content_factory.rpd.build import build_rpd_sections
from content_factory.rpd.program_developers import (
    PROGRAM_DEVELOPER_ROLES,
    PROGRAM_DEVELOPERS_HEADER,
    build_program_developers_table,
    validate_program_developers_table,
)
from content_factory.rpd.quality import evaluate_build_quality
from content_factory.rpd.render_selective import render_build
from content_factory.rpd.up_models import UpPlan


def test_program_developers_preserves_exact_internal_template_contract() -> None:
    header, rows = build_program_developers_table("dpo_pk_v1")

    assert tuple(header) == PROGRAM_DEVELOPERS_HEADER
    assert [row[0] for row in rows] == list(PROGRAM_DEVELOPER_ROLES)
    assert rows[0][1:] == ["[Указать]"] * 4
    assert rows[2][1] == "[При наличии]"
    assert not validate_program_developers_table(header, rows)


def test_program_developers_rejects_schema_and_role_drift() -> None:
    header, rows = build_program_developers_table("dpo_pk_v1")
    header[0] = "Позиция"
    rows[0][0] = "Автор"

    violations = validate_program_developers_table(header, rows)

    assert len(violations) == 2


def test_build_and_docx_keep_program_developers_as_a_table() -> None:
    build = build_rpd_sections(UpPlan(title="Тестовая программа"))
    section = next(item for item in build.sections if item.number == "7")

    assert section.category == "program_developers"
    assert section.kind == "table"
    assert section.editable and not section.preserve_template
    rendered = docx.Document(BytesIO(render_build(build)))
    table = next(
        table
        for table in rendered.tables
        if table.rows
        and tuple(cell.text.strip() for cell in table.rows[0].cells) == PROGRAM_DEVELOPERS_HEADER
    )
    assert [row.cells[0].text.strip() for row in table.rows[1:]] == list(PROGRAM_DEVELOPER_ROLES)


def test_quality_gate_rejects_program_developer_role_drift() -> None:
    build = build_rpd_sections(UpPlan(title="Тестовая программа"))
    section = next(item for item in build.sections if item.category == "program_developers")
    section.table_rows.pop()

    report = evaluate_build_quality(build)

    issue = next(item for item in report.issues if item.code == "program_developers_schema_mismatch")
    assert issue.blocking
    assert issue.section_key.startswith("7 ")
