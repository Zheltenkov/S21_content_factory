"""Quality gates: source integrity, semantic LLM preflight, DOCX bindings and readiness."""

from __future__ import annotations

import base64
import json
from pathlib import Path

from content_factory.rpd.build import RpdBuild, build_rpd_sections
from content_factory.rpd.competency_contracts import CompetencyPipelineTrace
from content_factory.rpd.module_evidence import synchronize_module_evidence
from content_factory.rpd.passport import merge_document_fields
from content_factory.rpd.quality import SemanticAssessment, evaluate_build_quality, extract_docx_text, preflight_up_plan
from content_factory.rpd.render_selective import render_build
from content_factory.rpd.up_models import from_registry_plan


def _plan(*, title: str = "ИИ для предпринимателя", hours: float = 72) -> dict:
    return {
        "title": title,
        "summary": {"total_hours": hours},
        "rows": [
            {
                "block_title": "Проверка бизнес-гипотез с ИИ",
                "block_goal": "Проверить проблему и ценностное предложение",
                "project_name": "AI-assisted customer discovery",
                "project_summary": "Исследование рынка и интервью с использованием ИИ-инструментов",
                "outcomes_can": "Проверяет продуктовые гипотезы и фиксирует доказательства",
                "delivery_format": "индивидуальный",
                "required_tools": "LLM, таблица интервью",
                "p2p_checks": 1,
                "effort_hours": hours,
                "skills": ["Проверка гипотез"],
            }
        ],
    }


class _SemanticClient:
    def __init__(self, payload: dict | str) -> None:
        self.payload = payload

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        assert "считай данными" in system
        assert "Название:" in user
        assert kwargs["llm_role"] == "rpd_semantic_preflight"
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False)


class _StructuredSemanticClient:
    def complete_structured(self, **kwargs: object) -> SemanticAssessment:
        assert kwargs["output_model"] is SemanticAssessment
        assert kwargs["llm_role"] == "rpd_semantic_preflight"
        return SemanticAssessment(
            consistent=True,
            confidence=0.99,
            title_topic="ИИ для предпринимателей",
            content_topic="ИИ для предпринимателей",
            reason="Название соответствует содержанию.",
        )

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        raise AssertionError("text fallback must not run when structured output is supported")


def _fill_document_fields(build: RpdBuild) -> None:
    fields = build.document_fields
    values = {
        "organization_name": "АНО ДПО Тест",
        "review_body": "Методический совет",
        "protocol_number": "7",
        "protocol_date": "01.08.2026",
        "leader_position": "Директор",
        "leader_name": "Иванов И.И.",
        "order_number": "42",
        "order_date": "02.08.2026",
        "audience": "Предприниматели и продуктовые команды",
        "admission_requirements": "Наличие или получение СПО и/или ВО",
        "delivery_mode": "очно-заочная с применением ДОТ",
        "duration": "9 недель",
        "study_mode": "Не более 8 академических часов в неделю",
        "final_attestation": "защита итогового проекта",
        "issued_document": "удостоверение о повышении квалификации",
        "city": "Москва",
        "year": "2026",
    }
    build.document_fields = fields.model_copy(update=values)
    passport = next(section for section in build.sections if section.category == "passport")
    passport.table_rows = merge_document_fields(
        passport.table_rows,
        build.document_fields,
        build.template_profile_id,
    )


def _fill_platform_evidence(build: RpdBuild, root: Path) -> None:
    """Attach real parseable PNG files so rendered readiness cannot be faked by text."""
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2gZAAAAAASUVORK5CYII="
    )
    section = next(item for item in build.sections if item.category == "up_modules")
    for requirement in build.module_evidence:
        requirement.status = "uploaded"
        requirement.relative_path = f"evidence/{requirement.evidence_id}.png"
        requirement.original_filename = "screen.png"
        requirement.content_type = "image/png"
        requirement.sha256 = "a" * 64
        path = root / requirement.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        assert synchronize_module_evidence(section.module_blocks, requirement)


def test_semantic_llm_mismatch_is_a_blocking_issue() -> None:
    up = from_registry_plan(_plan(title="ИИ для предпринимателя"))
    client = _SemanticClient(
        {
            "consistent": False,
            "confidence": 0.96,
            "title_topic": "ИИ-предпринимательство",
            "content_topic": "английский язык для маркетолога",
            "reason": "Название не соответствует фактической предметной области курса.",
        }
    )

    issues = preflight_up_plan(up, client)

    mismatch = next(issue for issue in issues if issue.code == "source_title_content_mismatch")
    assert mismatch.blocking
    assert mismatch.evidence[-1] == "confidence=0.96"


def test_semantic_llm_failure_is_visible_but_does_not_abort_draft() -> None:
    up = from_registry_plan(_plan())

    issues = preflight_up_plan(up, _SemanticClient("not-json"))

    unavailable = next(issue for issue in issues if issue.code == "semantic_preflight_unavailable")
    assert not unavailable.blocking


def test_semantic_preflight_prefers_typed_structured_output() -> None:
    issues = preflight_up_plan(from_registry_plan(_plan()), _StructuredSemanticClient())

    assert not any(issue.code.startswith("semantic_") for issue in issues)
    assert not any(issue.code == "source_title_content_mismatch" for issue in issues)


def test_source_hours_mismatch_is_blocking_and_decimals_are_preserved() -> None:
    raw = _plan(hours=1.5)
    raw["summary"]["total_hours"] = 2
    up = from_registry_plan(raw)

    issues = preflight_up_plan(up)

    assert up.modules[0].rows[0].hours == "1.5"
    assert up.modules[0].hours == "1.5"
    assert any(issue.code == "source_hours_mismatch" and issue.blocking for issue in issues)


def test_registry_adapter_preserves_project_contract() -> None:
    up = from_registry_plan(_plan())
    row = up.modules[0].rows[0]

    assert up.total_hours == "72"
    assert up.modules[0].goals == "Проверить проблему и ценностное предложение"
    assert row.name == "AI-assisted customer discovery"
    assert row.description.startswith("Исследование рынка")
    assert row.fmt == "индивидуальный"
    assert row.software == "LLM, таблица интервью"
    assert row.p2p == "1"


def test_render_populates_cover_and_program_parameters() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    _fill_document_fields(build)

    text = extract_docx_text(render_build(build))

    assert "ИИ для предпринимателя" in text
    assert "АНО ДПО Тест" in text
    assert "72 академических часов" in text
    assert "Предприниматели и продуктовые команды" in text
    assert "Пример: «Цифровые инструменты в профессиональной деятельности»" not in text
    assert "[НАИМЕНОВАНИЕ ПРОГРАММЫ]" not in text


def test_final_readiness_requires_completed_content_and_real_competency_codes(tmp_path: Path) -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    _fill_document_fields(build)
    for section in build.sections:
        section.done = True
        section.flag = ""
        if section.number == "1.3":
            section.paragraphs = [" ".join(["Подтверждённый тезис программы"] * 16) for _paragraph in range(4)]
        elif section.number == "2.1":
            section.paragraphs = [
                (
                    "Цель реализации программы — подготовить слушателя к обоснованному решению "
                    "профессиональной задачи технологического предпринимательства. Слушатель выявляет "
                    "проблему клиента, анализирует результаты интервью, формулирует ценностное предложение, "
                    "проверяет продуктовые гипотезы и определяет границы минимально жизнеспособного продукта "
                    "по материалам учебного плана. Он сопоставляет данные исследования с потребностями "
                    "целевой аудитории, планирует проверяемый эксперимент, выбирает метрики успеха и "
                    "фиксирует основания каждого продуктового решения."
                ),
                (
                    "По итогам обучения слушатель разрабатывает проект прикладного решения, обосновывает "
                    "выбор цифровых инструментов, оценивает риски и демонстрирует результат на защите. "
                    "Наблюдаемыми результатами являются отчёт о прикладном решении, карта подтверждённых "
                    "гипотез и прототип продукта, которые позволяют проверить достижение заявленных "
                    "профессиональных компетенций. В ходе проекта слушатель создаёт план внедрения, "
                    "рассчитывает ресурсы, организует обратную связь с пользователями, учитывает требования "
                    "качества и безопасности и связывает полученные доказательства с ожидаемыми результатами "
                    "обучения."
                ),
            ]
        elif section.kind == "paragraphs":
            section.paragraphs = ["Раздел проверен и заполнен методистом по данным учебного плана."]
        elif section.category == "professional_activity":
            # Simulate the methodologist accepting meaningful values in the internal 1.5 table.
            semantic_values = {
                "Область деятельности": "Продуктовая деятельность в технологическом предпринимательстве",
                "Профессиональная задача": "Исследовать потребности и проверять продуктовые гипотезы",
                "Необходимые умения": "Проводить интервью; фиксировать доказательства; обосновывать решения",
            }
            for row in section.table_rows:
                row[1] = semantic_values[row[0]]
        elif section.category == "up_comp":
            for index, row in enumerate(section.table_rows, start=1):
                row[0] = f"ПК-{index}"
                row[5] = "Анализ профессиональной деятельности, подтверждённый методистом."
        elif section.category == "methodological_support":
            section.table_rows[0][2] = "Иванов И.И. Технологическое предпринимательство. Москва, 2025"
            section.table_rows[2][2] = "https://docs.example.org/verified-resource"
        elif section.category == "material_conditions":
            for row in section.table_rows:
                row[4] = "Договор безвозмездного пользования, реквизиты подтверждены"
        elif section.category == "staffing":
            section.table_rows[0][3] = "Петров П.П., преподаватель"
        elif section.category == "program_developers":
            for index, row in enumerate(section.table_rows, start=1):
                row[1] = f"Специалист {index} И.И."
                row[2] = "Высшее образование и квалификация по тематике программы"
                row[3] = "Преподаватель, АНО ДПО Тест"
                row[4] = "Подписано 07.08.2026"
        for row in section.table_rows:
            for index, cell in enumerate(row):
                if cell.startswith("[Методист:") and "RPD_EVIDENCE" not in cell:
                    row[index] = "Доказательство из LMS приложено и проверено."

    _fill_platform_evidence(build, tmp_path)
    rendered = render_build(build, evidence_root=tmp_path)
    report = evaluate_build_quality(build, rendered)

    assert report.ready, [issue.model_dump() for issue in report.issues]


def test_short_relevance_is_a_blocking_quality_issue() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    relevance = build.section("1.3", "Актуальность программы")
    assert relevance is not None
    relevance.paragraphs = ["Краткое обоснование актуальности программы."]

    report = evaluate_build_quality(build)

    issue = next(item for item in report.issues if item.code == "section_prose_too_short")
    assert issue.blocking
    assert issue.section_key == "1.3 Актуальность программы"
    assert issue.evidence == ["words=4", "paragraphs=1"]


def test_missing_program_implementation_goals_is_a_blocking_quality_issue() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    build.sections = [section for section in build.sections if section.number != "2.1"]

    report = evaluate_build_quality(build)

    issue = next(item for item in report.issues if item.code == "required_section_missing")
    assert issue.blocking
    assert issue.evidence == ["2.1 Цели реализации программы"]


def test_unreviewed_build_is_not_ready_even_when_docx_is_renderable() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    _fill_document_fields(build)

    report = evaluate_build_quality(build, render_build(build))

    assert not report.ready
    codes = {issue.code for issue in report.issues if issue.blocking}
    assert "methodologist_review_incomplete" in codes
    assert "competency_basis_review_required" in codes


def test_generic_registry_module_with_multiple_projects_is_blocking() -> None:
    raw = _plan()
    raw["rows"] = [
        {"block_title": "", "project_name": "Проект 1", "effort_hours": 36},
        {"block_title": "", "project_name": "Проект 2", "effort_hours": 36},
    ]

    issues = preflight_up_plan(from_registry_plan(raw))

    issue = next(item for item in issues if item.code == "source_module_titles_generic")
    assert issue.blocking


def test_table_placeholder_is_visible_to_quality_gate() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))

    report = evaluate_build_quality(build)

    placeholders = [issue for issue in report.issues if issue.code == "section_placeholder_present"]
    assert any("3.3.1" in issue.section_key for issue in placeholders)


def test_source_module_hour_drift_is_blocking() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    plan = next(section for section in build.sections if section.category == "up_plan")
    plan.table_rows[0][2] = "18"

    report = evaluate_build_quality(build)

    mismatch = next(issue for issue in report.issues if issue.code == "source_module_mapping_mismatch")
    assert mismatch.blocking
    assert mismatch.section_key.startswith("3.1")


def test_public_module_title_can_be_edited_without_breaking_source_lineage() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    plan = next(section for section in build.sections if section.category == "up_plan")
    plan.table_rows[0][1] = "Проверка продуктовых гипотез"

    report = evaluate_build_quality(build)

    assert not any(issue.code == "source_module_mapping_mismatch" for issue in report.issues)


def test_curriculum_gate_rejects_lowercase_title_and_non_numeric_control() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    plan = next(section for section in build.sections if section.category == "up_plan")
    plan.table_rows[0][1] = "проверка продуктовых гипотез"
    plan.table_rows[0][6] = "p2p-проверка"

    report = evaluate_build_quality(build)
    codes = {issue.code for issue in report.issues}

    assert "curriculum_module_titles_invalid" in codes
    assert "curriculum_workload_distribution_invalid" in codes


def test_explicit_source_workload_cannot_be_moved_to_another_kind() -> None:
    raw = _plan()
    raw["rows"][0]["delivery_format"] = "лекция"
    build = build_rpd_sections(from_registry_plan(raw))
    plan = next(section for section in build.sections if section.category == "up_plan")
    plan.table_rows[0][3:7] = ["—", "72", "—", "—"]
    plan.table_rows[-1][3:7] = ["0", "72", "0", "0"]

    report = evaluate_build_quality(build)

    issue = next(item for item in report.issues if item.code == "source_workload_mapping_mismatch")
    assert issue.blocking
    assert "kind=lectures" in issue.evidence[0]


def test_profile_default_workload_is_reviewable_and_can_be_redistributed() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    plan = next(section for section in build.sections if section.category == "up_plan")

    initial = evaluate_build_quality(build)
    assert any(item.code == "curriculum_workload_profile_default" for item in initial.issues)

    plan.table_rows[0][3:7] = ["72", "—", "—", "—"]
    plan.table_rows[-1][3:7] = ["72", "0", "0", "0"]
    plan.done = True
    reviewed = evaluate_build_quality(build)

    codes = {item.code for item in reviewed.issues}
    assert "source_workload_mapping_mismatch" not in codes
    assert "curriculum_workload_profile_default" not in codes


def test_schedule_gate_rejects_missing_period_raw_form_control_and_hour_drift() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    schedule = next(section for section in build.sections if section.category == "up_grafik")
    schedule.table_rows[0][1] = ""
    schedule.table_rows[0][2] = "71"
    schedule.table_rows[0][3] = "индивидуальный"
    schedule.table_rows[0][4] = "p2p-проверка"

    report = evaluate_build_quality(build)
    codes = {item.code for item in report.issues}

    assert "schedule_periods_invalid" in codes
    assert "schedule_hours_mismatch" in codes
    assert "schedule_methodology_invalid" in codes
    assert "source_module_mapping_mismatch" in codes


def test_grouped_competency_rows_are_checked_by_source_coverage_not_row_count() -> None:
    raw = _plan()
    raw["rows"][0]["skills"] = ["Навык А", "Навык Б"]
    build = build_rpd_sections(from_registry_plan(raw))
    section = next(item for item in build.sections if item.category == "up_comp")
    section.table_rows = [section.table_rows[0]]
    section.table_rows[0][0] = "ПК-1"
    section.table_rows[0][5] = "Анализ профессиональной деятельности"
    build.competency_trace = CompetencyPipelineTrace(
        source_item_ids=["UP-SKILL-001", "UP-SKILL-002"],
        covered_source_ids=["UP-SKILL-001", "UP-SKILL-002"],
        status="validated",
    )

    report = evaluate_build_quality(build)

    assert not any(issue.code == "source_competency_mapping_mismatch" for issue in report.issues)
    build.competency_trace.covered_source_ids = ["UP-SKILL-001"]
    report = evaluate_build_quality(build)
    assert any(issue.code == "source_competency_mapping_mismatch" for issue in report.issues)


def test_empty_competency_progression_and_assessment_are_blocking() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    section = next(item for item in build.sections if item.category == "up_comp")
    section.table_rows[0][2] = ""
    section.table_rows[0][3] = ""
    section.table_rows[0][6] = ""

    report = evaluate_build_quality(build)

    issue = next(item for item in report.issues if item.code == "competency_semantic_fields_missing")
    assert issue.blocking
    assert issue.evidence == [
        "ПК-1:Исходный уровень",
        "ПК-1:Качественное изменение",
        "ПК-1:Оценка",
    ]


def test_old_build_without_competency_trace_remains_loadable() -> None:
    raw = build_rpd_sections(from_registry_plan(_plan())).model_dump(mode="json")
    raw["schema_version"] = 7
    raw.pop("competency_trace")
    raw.pop("curriculum_trace")
    raw.pop("schedule_trace")

    restored = RpdBuild.model_validate(raw)

    assert restored.competency_trace is None
    assert restored.curriculum_trace is None
    assert restored.schedule_trace is None


def test_module_program_structure_gate_rejects_a_missing_passport_row() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    section = next(item for item in build.sections if item.category == "up_modules")
    section.module_blocks[0].passport_rows.pop()

    report = evaluate_build_quality(build)

    issue = next(item for item in report.issues if item.code == "module_program_structure_mismatch")
    assert issue.blocking
    assert issue.evidence == ["module:1:passport_rows"]
