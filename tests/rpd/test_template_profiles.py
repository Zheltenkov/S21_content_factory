"""Template-profile contract, recognition and current ПК golden semantics."""

from __future__ import annotations

from pathlib import Path

import docx
import pytest

from content_factory.rpd.build import build_rpd_sections
from content_factory.rpd.dpo_spec import Cat
from content_factory.rpd.template import load_rpd_template
from content_factory.rpd.template_profiles import (
    BUILTIN_PK_PROFILE_ID,
    UnsupportedTemplateProfileError,
    detect_template_profile,
    get_template_profile,
    registered_template_profiles,
)
from content_factory.rpd.up_models import from_registry_plan


def _plan() -> dict:
    return {
        "title": "Профильный тест",
        "meta": {"total_hours": 16},
        "rows": [
            {
                "block_title": "Модуль",
                "project_name": "Проект",
                "effort_hours": 16,
                "skills": ["Навык"],
            }
        ],
    }


def test_builtin_pk_profile_satisfies_its_capability_contract() -> None:
    profile = get_template_profile(BUILTIN_PK_PROFILE_ID)

    report = profile.inspect(profile.template_path())

    assert report.supported
    assert report.program_type == "повышение квалификации"
    assert report.section_count > 10
    assert len(report.template_sha256) == 64
    assert not report.missing_bindings
    assert not report.missing_table_labels
    assert not report.missing_section_keys
    assert not report.missing_table_schemas
    assert not report.duplicate_section_numbers
    assert report.section_orientations == ["portrait", "landscape", "portrait", "portrait"]
    detected, detected_report = detect_template_profile(profile.template_path())
    assert detected.profile_id == BUILTIN_PK_PROFILE_ID
    assert detected_report.template_sha256 == report.template_sha256


def test_build_persists_profile_and_template_fingerprints() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))

    assert build.template_profile_id == BUILTIN_PK_PROFILE_ID
    assert build.template_report is not None and build.template_report.supported
    assert build.generation_trace.template_sha256 == build.template_report.template_sha256
    assert len(build.generation_trace.source_sha256) == 64
    assert build.source_spec.module_count == 1
    assert build.source_spec.calculated_total_hours == "16"
    assert len(build.source_spec.fingerprint) == 64


def test_builtin_pk_exposes_mandatory_boilerplate_sections_for_review() -> None:
    profile = get_template_profile(BUILTIN_PK_PROFILE_ID)

    assert profile.should_review_boilerplate("1.2", "Нормативно-правовые основания")
    assert profile.should_review_boilerplate("4.3", "Паспорт оценочных материалов")
    assert profile.should_review_boilerplate("5.4", "Реализация с применением ЭО и ДОТ")
    assert profile.should_review_boilerplate("5.5", "Условия для инвалидов")
    assert profile.should_review_boilerplate("6", "Оценка качества реализации программы")
    assert not profile.should_review_boilerplate("7", "Произвольный раздел")


def test_builtin_pk_owns_assessment_materials_contract() -> None:
    profile = get_template_profile(BUILTIN_PK_PROFILE_ID)

    assert profile.category_for("4.3", "Паспорт оценочных материалов") is Cat.BOILERPLATE
    assert profile.category_for("4.4", "Пример оценки") is Cat.ASSESSMENT_EVIDENCE


def test_build_contains_all_twenty_one_methodologist_sections() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    sections = {section.number: section for section in build.sections}

    assert len(build.sections) == 21
    assert sections["2.1"].title == "Цели реализации программы"
    assert sections["2.1"].category == "generate"
    assert sections["2.1"].kind == "paragraphs"
    assert sections["2.1"].paragraphs == ["[Будет сгенерировано]"]
    assert sections["4.3"].preserve_template
    assert sections["4.3"].editable  # форма ДПО: правки чтутся, без правок — дословная вёрстка
    assert sections["4.3"].paragraphs == [
        "! Все вопросы итоговой аттестации должны быть заранее опубликованы без ключей !",
        "Ключи должны быть в отдельном файле (приложении), не в доке. "
        "Обычно их никто не спрашивает, но должны быть",
    ]
    assert sections["4.4"].category == "assessment_evidence"
    assert sections["4.4"].kind == "table"
    assert not sections["4.4"].editable
    assert sections["4.4"].table_header == [
        "Здесь нужен скрин из задания (если программа реализуется в очно-заочной форме)."
    ]
    assert len(sections["4.4"].table_rows) == 1
    assert sections["5.4"].preserve_template
    assert sections["5.4"].editable
    assert "электронная образовательная среда" in " ".join(sections["5.4"].paragraphs)
    assert sections["5.5"].preserve_template
    assert sections["5.5"].editable
    assert "доступные форматы учебных материалов" in " ".join(sections["5.5"].paragraphs)
    assert sections["6"].preserve_template
    assert sections["6"].editable
    assert sections["6"].kind == "table"
    assert sections["6"].table_header == [
        "Направление оценки",
        "Показатель",
        "Метод",
        "Периодичность",
        "Ответственный",
    ]
    assert [row[0] for row in sections["6"].table_rows] == [
        "Достижение результатов",
        "Удовлетворенность",
        "Актуальность содержания",
    ]
    assert sections["7"].category == "program_developers"
    assert sections["7"].kind == "table"
    assert [row[0] for row in sections["7"].table_rows] == [
        "Руководитель программы",
        "Разработчик",
        "Эксперт / рецензент",
    ]


def test_builtin_pk_treats_empty_goals_leaf_as_content_slot_not_container() -> None:
    profile = get_template_profile(BUILTIN_PK_PROFILE_ID)

    template = load_rpd_template(profile.template_path())
    sections = {section.number: section for section in template.sections}

    assert sections["2.1"].title == "Цели реализации программы"
    assert not sections["2.1"].is_container
    assert sections["2"].is_container
    assert sections["3.3"].is_container


def test_profile_rejects_docx_without_required_bindings(tmp_path: Path) -> None:
    path = tmp_path / "unknown.docx"
    document = docx.Document()
    document.add_heading("Произвольный документ", level=1)
    document.add_paragraph("Нет контракта РПД.")
    document.save(path)

    report = get_template_profile().inspect(path)

    assert not report.supported
    assert "program_title" in report.missing_bindings
    assert "Категория слушателей" in report.missing_table_labels
    with pytest.raises(UnsupportedTemplateProfileError, match="Unsupported RPD template"):
        detect_template_profile(path)


def test_unknown_profile_fails_explicitly() -> None:
    assert registered_template_profiles() == (BUILTIN_PK_PROFILE_ID,)
    with pytest.raises(UnsupportedTemplateProfileError, match="university_v1"):
        get_template_profile("university_v1")
