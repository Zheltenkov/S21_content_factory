"""Deterministic length and structure contracts for RPD prose."""

from __future__ import annotations

from content_factory.rpd.prose_contracts import (
    assess_program_implementation_goals,
    prose_contract_for,
    prose_contracts_for,
    validate_prose_contract,
)
from content_factory.rpd.up_models import from_registry_plan


def _long_relevance_paragraphs() -> list[str]:
    return [
        " ".join([f"Подтверждённый тезис программы {paragraph}-{item}." for item in range(12)])
        for paragraph in range(4)
    ]


def test_relevance_contract_is_semantic_and_template_number_independent() -> None:
    contract = prose_contract_for("7.4 Обоснование актуальности обучения")

    assert contract is not None
    assert contract.min_words == 180
    assert (contract.target_words_min, contract.target_words_max) == (220, 280)
    assert (contract.min_paragraphs, contract.max_paragraphs) == (4, 5)
    assert prose_contract_for("1.4 Цель и задачи программы") is None

    goals = prose_contract_for("9.7 Цели реализации программы")
    assert goals is not None
    assert (goals.target_words_min, goals.target_words_max) == (120, 170)
    assert (goals.min_paragraphs, goals.max_paragraphs) == (2, 3)
    assert goals.min_observable_actions == 6
    assert goals.min_source_anchors == 5


def test_short_relevance_is_rejected_but_long_four_paragraph_version_passes() -> None:
    short = [
        "Программа востребована в связи с изменением профессиональной деятельности.",
        "Содержание учебного плана позволяет освоить необходимые практические инструменты.",
    ]

    violation = validate_prose_contract("1.3 Актуальность программы", short)

    assert violation is not None
    assert violation.word_count < 180 and violation.paragraph_count == 2
    assert validate_prose_contract("1.3 Актуальность программы", _long_relevance_paragraphs()) is None


def test_relevance_prompt_payload_exposes_measurable_target_and_outline() -> None:
    payload = prose_contracts_for(
        {
            "1.3 Актуальность программы",
            "1.4 Цель и задачи программы",
            "2.1 Цели реализации программы",
        }
    )

    assert set(payload) == {"1.3 Актуальность программы", "2.1 Цели реализации программы"}
    assert payload["1.3 Актуальность программы"]["target_words"] == "220-280"
    assert len(payload["1.3 Актуальность программы"]["required_structure"]) == 5
    assert payload["2.1 Цели реализации программы"]["target_words"] == "120-170"
    assert payload["2.1 Цели реализации программы"]["minimum_observable_actions"] == 6
    assert payload["2.1 Цели реализации программы"]["minimum_source_anchors"] == 5
    assert len(payload["2.1 Цели реализации программы"]["required_structure"]) == 5


def test_short_program_implementation_goals_are_rejected() -> None:
    violation = validate_prose_contract(
        "2.1 Цели реализации программы",
        ["Слушатель освоит программу.", "Результаты применяются на практике."],
    )

    assert violation is not None
    assert violation.word_count < 70
    assert violation.paragraph_count == 2


def test_program_implementation_goals_signals_require_actions_and_up_grounding() -> None:
    up = from_registry_plan(
        {
            "title": "Технологическое предпринимательство",
            "meta": {"total_hours": 36},
            "rows": [
                {
                    "block_title": "Проблема клиента и ценностное предложение",
                    "block_goal": "Проверить проблему клиента и продуктовую гипотезу",
                    "project_name": "Отчёт о проблеме и клиенте",
                    "description": "Исследование рынка и формулирование ценностного предложения",
                    "outcomes_can": "выявляет проблему; проверяет гипотезу; обосновывает решение",
                    "effort_hours": 36,
                    "skills": ["Понимание клиента", "Проверка гипотез"],
                }
            ],
        }
    )
    paragraphs = [
        "Слушатель выявляет проблему клиента, анализирует рынок и формулирует ценностное предложение, "
        "связывая выводы с проверяемой продуктовой гипотезой.",
        "Он проверяет гипотезу, обосновывает решение, создаёт отчёт о проблеме и клиенте и демонстрирует "
        "полученный результат на защите.",
    ]

    signals = assess_program_implementation_goals(paragraphs, up)

    assert signals.sufficient
    assert len(signals.observable_actions) >= 6
    assert len(signals.source_anchors) >= 5


def test_generation_placeholder_does_not_duplicate_placeholder_quality_issue() -> None:
    assert validate_prose_contract("1.3 Актуальность программы", ["[Будет сгенерировано]"]) is None
