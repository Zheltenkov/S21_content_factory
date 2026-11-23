"""Competency workflow: bounded agents, deterministic codes, evidence and source coverage."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from content_factory.rpd.competency_agents import (
    PROFESSIONAL_STANDARD_REVIEW_MARKER,
    build_competency_table,
    source_competency_items,
    validate_competency_table,
)
from content_factory.rpd.competency_contracts import (
    CompetencyCritique,
    CompetencyDesignResponse,
    CompetencyDraft,
    ProfessionalStandardMatch,
    ProfessionalStandardMatchResponse,
    ProfessionalStandardSelection,
)
from content_factory.rpd.professional_standards import (
    ProfessionalStandardCatalog,
    ProfessionalStandardFunction,
    ProfessionalStandardRecord,
)
from content_factory.rpd.up_models import from_registry_plan


def _up():  # type annotation is intentionally inferred from the public adapter
    return from_registry_plan(
        {
            "title": "Разработка цифровых продуктов",
            "rows": [
                {
                    "block_title": "Исследование продукта",
                    "outcomes_can": "Проверяет гипотезы на данных интервью",
                    "skills": ["Проверка продуктовых гипотез"],
                },
                {
                    "block_title": "Создание продукта",
                    "outcomes_can": "Проектирует минимально жизнеспособный продукт",
                    "skills": ["Проектирование цифрового продукта"],
                },
            ],
        }
    )


def _catalog() -> ProfessionalStandardCatalog:
    return ProfessionalStandardCatalog(
        source_sha256="catalog-sha",
        standards=[
            ProfessionalStandardRecord(
                registry_number="1000",
                code="06.035",
                title="Разработчик Web и мультимедийных приложений",
                activity_type="Разработка цифровых продуктов",
                functions=[
                    ProfessionalStandardFunction(
                        code="A/01.6",
                        title="Проектирование цифрового продукта",
                        required_skills=["Проверять продуктовые гипотезы"],
                    )
                ],
            )
        ],
    )


class _CompetencyClient:
    def __init__(self, *, professional_evidence: bool) -> None:
        self.professional_evidence = professional_evidence
        self.roles: list[str] = []

    def complete_structured(
        self,
        *,
        output_model: type[BaseModel],
        llm_role: str,
        **kwargs: Any,
    ) -> BaseModel:
        del kwargs
        self.roles.append(llm_role)
        if output_model is ProfessionalStandardMatchResponse:
            return ProfessionalStandardMatchResponse(
                matches=[
                    ProfessionalStandardMatch(
                        standard_code="06.035",
                        relevant_function_codes=["A/01.6"],
                        reason="Связано с проектированием цифрового продукта.",
                        confidence=0.91,
                    )
                ]
            )
        if output_model is CompetencyDesignResponse:
            evidence_refs = ["UP-SKILL-001", "UP-SKILL-002"]
            if self.professional_evidence:
                evidence_refs.append("PS:06.035:TF:A/01.6")
            return CompetencyDesignResponse(
                competencies=[
                    CompetencyDraft(
                        title="Способен исследовать и проектировать цифровой продукт",
                        baseline="Выполняет отдельные продуктовые действия по образцу",
                        qualitative_change="От отдельных действий к обоснованному продуктовому решению",
                        after_training="Проверяет гипотезы и проектирует минимально жизнеспособный продукт",
                        assessment="Придуманный моделью экзамен",
                        assessment_refs=["UP-ASSESS-001-001"],
                        covered_source_ids=["UP-SKILL-001", "UP-SKILL-002"],
                        evidence_refs=evidence_refs,
                    )
                ]
            )
        if output_model is CompetencyCritique:
            return CompetencyCritique(valid=True)
        raise AssertionError(f"Unexpected output model: {output_model}")


class _HallucinatingClient(_CompetencyClient):
    def complete_structured(
        self,
        *,
        output_model: type[BaseModel],
        llm_role: str,
        **kwargs: Any,
    ) -> BaseModel:
        if output_model is CompetencyDesignResponse:
            self.roles.append(llm_role)
            return CompetencyDesignResponse(
                competencies=[
                    CompetencyDraft(
                        title="Способен создавать продукт",
                        after_training="Создаёт продукт",
                        covered_source_ids=["INVENTED-SOURCE"],
                        evidence_refs=["PS:99.999:TF:X/99.9"],
                    )
                ]
            )
        return super().complete_structured(
            output_model=output_model,
            llm_role=llm_role,
            **kwargs,
        )


def test_no_llm_fallback_assigns_pk_codes_and_preserves_source_lineage() -> None:
    result = build_competency_table(
        _up(),
        None,
        profile_id="dpo_pk_v1",
        program_type="повышение квалификации",
        catalog=ProfessionalStandardCatalog(),
    )

    assert [row[0] for row in result.rows] == ["ПК-1", "ПК-2"]
    assert result.trace.status == "fallback"
    assert result.trace.source_coverage_rate == 1.0
    assert all(row[2] for row in result.rows)
    assert all(row[3] for row in result.rows)
    assert all(row[6].startswith("Защита проекта") for row in result.rows)
    assert all(row[5].startswith(PROFESSIONAL_STANDARD_REVIEW_MARKER) for row in result.rows)


def test_source_items_expose_closed_world_assessments_from_up_rows() -> None:
    items = source_competency_items(_up())

    assert items[0].assessments[0].assessment_id == "UP-ASSESS-001-001"
    assert items[0].assessments[0].title == "Исследование продукта"
    assert items[0].assessments[0].method == "Защита проекта по модулю «Исследование продукта»"


def test_fallback_qualitative_change_discards_generic_up_boilerplate() -> None:
    up = from_registry_plan(
        {
            "title": "Тест",
            "rows": [
                {
                    "block_title": "Модуль",
                    "outcomes_can": "Демонстрирует навык: выявление проблемы",
                    "skills": ["Выявление проблемы"],
                }
            ],
        }
    )

    result = build_competency_table(
        up,
        None,
        profile_id="dpo_pk_v1",
        program_type="повышение квалификации",
        catalog=ProfessionalStandardCatalog(),
    )

    assert "Демонстрирует навык" not in result.rows[0][3]
    assert "Выявление проблемы" in result.rows[0][3]


def test_general_program_uses_ok_and_does_not_require_profstandard() -> None:
    result = build_competency_table(
        _up(),
        None,
        profile_id="dpo_pk_v1",
        program_type="дополнительная общеразвивающая программа",
        catalog=ProfessionalStandardCatalog(),
    )

    assert [row[0] for row in result.rows] == ["ОК-1", "ОК-2"]
    assert all(row[5] == "Цели и планируемые результаты учебного плана" for row in result.rows)
    assert validate_competency_table(result.header, result.rows, result.trace).basis_review_required == []


def test_match_is_advisory_until_profstandard_is_explicitly_approved() -> None:
    client = _CompetencyClient(professional_evidence=False)
    result = build_competency_table(
        _up(),
        client,
        profile_id="dpo_pk_v1",
        program_type="повышение квалификации",
        catalog=_catalog(),
    )

    assert result.trace.matches[0].standard_code == "06.035"
    assert result.trace.approved_standard_codes == []
    assert result.rows[0][5].startswith(PROFESSIONAL_STANDARD_REVIEW_MARKER)
    assert result.trace.status == "validated"


def test_approved_profstandard_can_be_used_as_exact_evidence() -> None:
    client = _CompetencyClient(professional_evidence=True)
    result = build_competency_table(
        _up(),
        client,
        profile_id="dpo_pk_v1",
        program_type="повышение квалификации",
        catalog=_catalog(),
        selection=ProfessionalStandardSelection(
            approved_codes=["06.035"],
            approved_by="methodologist@example.test",
        ),
    )

    assert result.trace.status == "validated"
    assert result.trace.approved_standard_codes == ["06.035"]
    assert "ПС 06.035" in result.rows[0][5]
    assert "ТФ A/01.6" in result.rows[0][5]
    assert result.rows[0][2] == "Выполняет отдельные продуктовые действия по образцу"
    assert result.rows[0][3] == "От отдельных действий к обоснованному продуктовому решению"
    assert result.rows[0][6] == "Защита проекта по модулю «Исследование продукта»"
    assert validate_competency_table(result.header, result.rows, result.trace).basis_review_required == []
    assert client.roles == [
        "rpd_profstandard_match",
        "rpd_competency_design",
        "rpd_competency_validate",
    ]


def test_table_validator_allows_non_contiguous_codes_but_rejects_duplicates() -> None:
    result = build_competency_table(
        _up(),
        None,
        profile_id="dpo_pk_v1",
        program_type="повышение квалификации",
        catalog=ProfessionalStandardCatalog(),
    )
    for row in result.rows:
        row[5] = "Анализ профессиональной деятельности, подтверждённый методистом"
    result.rows[1][0] = "ПК-3"

    assert validate_competency_table(result.header, result.rows, result.trace).invalid_codes == []
    result.rows[1][0] = "ПК-1"
    validation = validate_competency_table(result.header, result.rows, result.trace)
    assert validation.duplicate_codes == ["ПК-1"]

    result.rows[0][2] = ""
    result.rows[0][3] = ""
    result.rows[0][6] = ""
    validation = validate_competency_table(result.header, result.rows, result.trace)
    assert validation.missing_semantic_fields == [
        "ПК-1:Исходный уровень",
        "ПК-1:Качественное изменение",
        "ПК-1:Оценка",
    ]


def test_hallucinated_evidence_is_repaired_once_then_falls_back_safely() -> None:
    client = _HallucinatingClient(professional_evidence=True)
    result = build_competency_table(
        _up(),
        client,
        profile_id="dpo_pk_v1",
        program_type="повышение квалификации",
        catalog=_catalog(),
        selection=ProfessionalStandardSelection(approved_codes=["06.035"]),
    )

    assert result.trace.status == "fallback"
    assert result.trace.fallback_reason == "validation_failed"
    assert result.trace.source_coverage_rate == 1.0
    assert "rpd_competency_design" in client.roles
    assert client.roles.count("rpd_competency_design") == 2
    assert all("99.999" not in row[5] for row in result.rows)
