"""Report-only project-quality metrics (project-contract epic, slice 1)."""

from __future__ import annotations

from content_factory.catalog.pipeline.curriculum.project_quality import (
    is_classified,
    is_generic_artifact,
    is_generic_criterion,
    is_testable_criterion,
    report_only_quality_metrics,
    title_violations,
)


def test_testable_criterion_requires_structure_not_just_non_generic() -> None:
    # non-generic prose is NOT testable on its own
    assert not is_testable_criterion("Проект выполнен качественно и защищён.")
    # a check → expected-result form is testable
    assert is_testable_criterion("- сервис: отвечает на health-check → 200 OK (скриншот, ручная)")
    # the generic fallback is never testable
    assert not is_testable_criterion("артефакт создан и предъявлен; результат можно проверить по заявленным ЗУН")


def test_single_skill_metric_reports_raw_facts_by_type_not_policy() -> None:
    # Measurement is profile-independent: the RAW single-skill share plus by-type counts.
    # The lab EXEMPTION is policy, applied by the publication gate, not baked into the metric.
    rows = [
        {"project_name": "A", "node_ids": ["x"], "project_type": "lab"},
        {"project_name": "B", "node_ids": ["y"], "project_type": "project"},
        {"project_name": "C", "node_ids": ["z", "w"], "project_type": "project"},
    ]
    metrics = report_only_quality_metrics(rows)
    assert metrics["single_skill_project_pct"] == round(2 / 3 * 100, 1)  # raw: A and B
    assert metrics["single_skill_count_by_type"] == {"lab": 1, "project": 1}
    assert metrics["project_count_by_type"] == {"lab": 1, "project": 2}


def test_is_classified_by_confidence_and_confirmation() -> None:
    assert is_classified({"policy_area": "ai_automation", "policy_area_confidence": "high"})
    assert is_classified({"policy_area": "ai_automation", "policy_area_confidence": "medium"})
    assert not is_classified({"policy_area": "ai_automation", "policy_area_confidence": "low"})
    assert not is_classified({"policy_area": "", "policy_area_confidence": "none"})
    # methodologist confirmation classifies regardless of confidence
    assert is_classified({"policy_area": "operations", "policy_area_confidence": "low", "policy_area_source": "confirmed"})
    # legacy row without a confidence field but with an area stays classified
    assert is_classified({"policy_area": "operations"})


def test_low_confidence_classification_metric() -> None:
    rows = [
        {"project_name": "A", "artifact": "x", "validation_criteria": "y", "node_ids": ["a", "b"], "policy_area": "ai_automation", "policy_area_confidence": "high"},
        {"project_name": "B", "artifact": "x", "validation_criteria": "y", "node_ids": ["c", "d"], "policy_area": "operations", "policy_area_confidence": "low"},
        {"project_name": "C", "artifact": "x", "validation_criteria": "y", "node_ids": ["e", "f"], "policy_area": "", "policy_area_confidence": "none"},
    ]
    metrics = report_only_quality_metrics(rows)
    assert metrics["low_confidence_classification_count"] == 2  # low + none
    assert metrics["unclassified_policy_area_count"] == 2
    assert metrics["policy_area_coverage_pct"] == round(1 / 3 * 100, 1)


def test_activity_archetype_metrics_are_report_only_facts() -> None:
    rows = [
        {
            "project_name": "A",
            "node_ids": ["a", "b"],
            "activity_archetype": "construct",
            "activity_archetype_suggestion": "construct",
            "activity_archetype_modifiers": ["integrative"],
        },
        {
            "project_name": "B",
            "node_ids": ["c", "d"],
            "activity_archetype": "",
            "activity_archetype_suggestion": "investigate",
            "activity_archetype_modifiers": ["experiment"],
        },
        {
            "project_name": "C",
            "node_ids": ["e", "f"],
            "activity_archetype": "",
            "activity_archetype_suggestion": "",
            "activity_archetype_modifiers": [],
        },
    ]

    metrics = report_only_quality_metrics(rows)

    assert metrics["activity_archetype_coverage_pct"] == round(1 / 3 * 100, 1)
    assert metrics["activity_archetype_unclassified_count"] == 2
    assert metrics["activity_archetype_ambiguous_count"] == 1
    assert metrics["activity_archetype_count_by_type"] == {"construct": 1}
    assert metrics["activity_modifier_count_by_type"] == {"integrative": 1, "experiment": 1}


def test_title_violations_flags_long_and_wordy() -> None:
    assert title_violations("Прототип продукта с AI") == ()
    assert "too_long" in title_violations("П" * 80)
    assert "too_many_words" in title_violations("один два три четыре пять шесть семь восемь девять")


def test_is_generic_artifact_matches_planner_fallbacks() -> None:
    assert is_generic_artifact("Проверяемый артефакт (практика) по навыку «SQL»")
    assert is_generic_artifact("Интегративный артефакт (документ) по теме «CI»: a, b")
    assert not is_generic_artifact("Запускаемый прототип с репозиторием и инструкцией")


def test_is_generic_criterion_matches_assessment_fallback() -> None:
    generic = (
        "Критерии проверки: артефакт «X» создан и предъявлен; "
        "в решении явно применены навыки: A, B; результат можно проверить по заявленным ЗУН."
    )
    assert is_generic_criterion(generic)
    assert not is_generic_criterion("Workflow запускается на контрольном входе и сохраняет результат.")
    assert not is_generic_criterion(generic + "\n- запуск: проверен → получен результат")
    assert is_testable_criterion(generic + "\n- запуск: проверен → получен результат")


def test_report_only_metrics_empty() -> None:
    metrics = report_only_quality_metrics([])
    assert metrics["title_violation_count"] == 0
    assert metrics["single_skill_project_pct"] == 0.0
    assert metrics["artifact_contract_coverage_pct"] == 0.0


def test_report_only_metrics_counts() -> None:
    rows = [
        {
            "project_name": "Прототип продукта с AI",
            "artifact": "Запускаемый прототип",
            # structurally testable: a check → expected result with evidence
            "validation_criteria": "- workflow: запускается на контрольном входе → сохраняет результат (журнал, ручная проверка)",
            "node_ids": ["a", "b"],
            "artifact_contract": {"artifact_type": "working_implementation"},
            "artifact_contract_sources": ["profile", "archetype_skeleton"],
            "artifact_merge_diagnostics": [
                {"code": "refined", "severity": "info"},
            ],
        },
        {
            "project_name": "П" * 90,  # long title violation
            "artifact": "Проверяемый артефакт (практика) по навыку «SQL»",  # generic
            "validation_criteria": (
                "Критерии проверки: артефакт «X» создан и предъявлен; "
                "результат можно проверить по заявленным ЗУН."
            ),  # generic criterion
            "node_ids": ["c"],  # single skill
            "artifact_merge_diagnostics": [
                {"code": "unresolved", "severity": "warning"},
            ],
        },
    ]
    metrics = report_only_quality_metrics(rows)
    assert metrics["title_violation_count"] == 1
    assert metrics["generic_artifact_count"] == 1
    assert metrics["generic_criterion_count"] == 1
    assert metrics["single_skill_project_pct"] == 50.0
    assert metrics["testable_criteria_coverage_pct"] == 50.0
    assert metrics["artifact_contract_coverage_pct"] == 50.0
    assert metrics["artifact_contract_unresolved_count"] == 1
    assert metrics["artifact_merge_warning_count"] == 1
    assert metrics["artifact_contract_source_count_by_type"] == {
        "profile": 1,
        "archetype_skeleton": 1,
    }
