"""Working-program evidence: every platform assignment and control needs its own screenshot."""

from __future__ import annotations

from content_factory.rpd.module_evidence import (
    build_assessment_evidence_table,
    build_module_program_table,
    synchronize_module_evidence,
    validate_module_evidence,
    validate_module_program_blocks,
)
from content_factory.rpd.up_models import from_registry_plan


def _plan():  # type: ignore[no-untyped-def]
    return from_registry_plan(
        {
            "title": "Тест",
            "meta": {"total_hours": 18},
            "rows": [
                {
                    "block_title": "Продуктовый модуль",
                    "project_name": "Проверка гипотезы",
                    "project_summary": "Собрать доказательства проблемы",
                    "delivery_format": "индивидуальный",
                    "required_tools": "LMS",
                    "p2p_checks": 1,
                    "effort_hours": 8,
                },
                {
                    "project_name": "Проектирование решения",
                    "delivery_format": "групповой",
                    "effort_hours": 10,
                },
            ],
        }
    )


def test_internal_profile_requires_assignment_and_control_screenshots_for_every_item() -> None:
    result = build_module_program_table(_plan(), profile_id="dpo_pk_v1")

    assert len(result.evidence) == 4
    assert [item.kind for item in result.evidence] == ["assignment", "control", "assignment", "control"]
    assert len(result.blocks) == 1
    block = result.blocks[0]
    assert [row[0] for row in block.passport_rows] == [
        "Наименование",
        "Цель",
        "Связанные компетенции",
        "Результаты",
        "Трудоемкость",
        "Форма контроля",
    ]
    assert len(block.topics_rows) == 3  # two source rows plus the mandatory module total
    assert block.topics_rows[0][2:] == [
        "Индивидуальная работа",
        "8",
        "Практикум в LMS/ЭИОС",
        "Практическое задание с взаимной проверкой",
    ]
    assert block.topics_rows[-1] == ["", "ИТОГО", "", "18", "", ""]
    assert len(block.evidence_rows) == 4
    evidence_text = "\n".join(cell for row in block.evidence_rows for cell in row)
    assert evidence_text.count("вставьте скрин задания") == 2
    assert evidence_text.count("вставьте скрин контроля") == 2
    assert all(item.reason == "builtin_school_platform_policy" for item in result.evidence)
    assert validate_module_program_blocks(result.blocks, expected_project_counts=[2]) == []


def test_external_profile_requires_evidence_only_for_explicit_online_delivery() -> None:
    up = _plan()
    up.modules[0].rows[0].fmt = "очно, в аудитории"
    up.modules[0].rows[0].software = ""
    up.modules[0].rows[0].description = ""
    up.modules[0].rows[1].fmt = "дистанционно в LMS"

    result = build_module_program_table(up, profile_id="external_dpo")

    assert len(result.evidence) == 2
    assert {item.item_title for item in result.evidence} == {"Проектирование решения"}
    assert all(item.reason == "source_online_marker" for item in result.evidence)


def test_each_declared_control_instance_gets_a_separate_screenshot_requirement() -> None:
    up = _plan()
    up.modules[0].rows[0].p2p = "3"

    result = build_module_program_table(up, profile_id="dpo_pk_v1")
    first_item = [item for item in result.evidence if item.item_index == 1]

    assert [item.kind for item in first_item] == ["assignment", "control", "control", "control"]
    assert [item.control_index for item in first_item] == [0, 1, 2, 3]
    assert [item.subject for item in first_item[1:]] == [
        "Практическое задание с взаимной проверкой №1",
        "Практическое задание с взаимной проверкой №2",
        "Практическое задание с взаимной проверкой №3",
    ]


def test_assessment_examples_reuse_only_control_evidence_in_template_table() -> None:
    module_result = build_module_program_table(_plan(), profile_id="dpo_pk_v1")

    header, rows = build_assessment_evidence_table(
        module_result.evidence,
        template_header=["Шаблонный заголовок 4.4"],
    )

    assert header == ["Шаблонный заголовок 4.4"]
    assert len(rows) == 2
    assert all("контроль" in row[0] for row in rows)
    assert all("RPD_EVIDENCE" in row[0] for row in rows)
    assert not any("скрин задания" in row[0] for row in rows)


def test_uploaded_evidence_updates_its_marker_and_passes_contract_validation() -> None:
    result = build_module_program_table(_plan(), profile_id="dpo_pk_v1")
    for requirement in result.evidence:
        requirement.status = "uploaded"
        requirement.relative_path = f"evidence/{requirement.evidence_id}.png"
        requirement.original_filename = "screen.png"
        requirement.content_type = "image/png"
        requirement.sha256 = "a" * 64
        assert synchronize_module_evidence(result.blocks, requirement)

    validation = validate_module_evidence(
        result.evidence,
        result.evidence_rows(),
        expected_minimum_count=4,
    )

    assert validation.contract_violations == []
    assert validation.missing_evidence == []
    assert validation.invalid_uploads == []
    text = "\n".join(cell for row in result.evidence_rows() for cell in row)
    assert "[Методист:" not in text
    assert text.count("Скриншот задания") == 2
    assert text.count("Скриншот контроля") == 2


def test_missing_marker_and_pending_upload_are_blocking_evidence() -> None:
    result = build_module_program_table(_plan(), profile_id="dpo_pk_v1")
    result.blocks[0].evidence_rows.pop(0)

    validation = validate_module_evidence(
        result.evidence,
        result.evidence_rows(),
        expected_minimum_count=4,
    )

    assert validation.contract_violations == ["marker_missing:m001-i001-assignment"]
    assert len(validation.missing_evidence) == 4
