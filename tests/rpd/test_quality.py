"""Quality gates: source integrity, semantic LLM preflight, DOCX bindings and readiness."""

from __future__ import annotations

import json

from content_factory.rpd.build import build_rpd_sections
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


def _fill_document_fields(build: object) -> None:
    fields = build.document_fields  # type: ignore[attr-defined]
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
        "delivery_mode": "очно-заочная с применением ДОТ",
        "final_attestation": "защита итогового проекта",
        "issued_document": "удостоверение о повышении квалификации",
        "city": "Москва",
        "year": "2026",
    }
    build.document_fields = fields.model_copy(update=values)  # type: ignore[attr-defined]


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


def test_final_readiness_requires_completed_content_and_real_competency_codes() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    _fill_document_fields(build)
    for section in build.sections:
        section.done = True
        section.flag = ""
        if section.kind == "paragraphs":
            section.paragraphs = ["Раздел проверен и заполнен методистом по данным учебного плана."]
        elif section.category == "up_comp":
            for index, row in enumerate(section.table_rows, start=1):
                row[0] = f"ПК-{index}"

    rendered = render_build(build)
    report = evaluate_build_quality(build, rendered)

    assert report.ready, [issue.model_dump() for issue in report.issues]


def test_unreviewed_build_is_not_ready_even_when_docx_is_renderable() -> None:
    build = build_rpd_sections(from_registry_plan(_plan()))
    _fill_document_fields(build)

    report = evaluate_build_quality(build, render_build(build))

    assert not report.ready
    codes = {issue.code for issue in report.issues if issue.blocking}
    assert "methodologist_review_incomplete" in codes
    assert "competency_codes_missing" in codes
