"""Synthetic cross-domain invariants, not a quality or generalization benchmark.

These probes deliberately avoid asserting project wording, counts, or pedagogical quality.
They verify that a non-digital domain can use the universal activity/skeleton layers without
receiving a digital-product artifact contract, and that ambiguity degrades visibly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from content_factory.catalog.pipeline import stage_dag_to_up
from content_factory.catalog.pipeline.curriculum.methodology_profile import (
    MethodologyProfile,
    PublicationThresholds,
)
from content_factory.catalog.pipeline.models import IndicatorSpec, SkillCandidate


@dataclass(frozen=True)
class DomainProbe:
    probe_id: str
    role: str
    group: str
    bloom: str
    expected_archetype: str
    skills: tuple[str, ...]


NEUTRAL_PROBE_PROFILE = MethodologyProfile(
    profile_id="cross_domain_invariant_probe",
    version="test-v1",
    program_family="cross_domain_probe",
    skill_density_range=(1, 4),
    single_skill_exempt_kinds=("lab",),
    capstone_policy="follow_design",
    publication_thresholds=PublicationThresholds(
        required_policy_coverage_pct=0.0,
        single_skill_max_pct=100.0,
    ),
    # An unknown set deliberately disables the digital-product profile layer. The
    # archetype skeleton must still produce a usable, domain-neutral contract.
    artifact_policy_set="cross-domain-probe/none",
)

PROBES = (
    DomainProbe(
        probe_id="agronomy-investigation",
        role="Агроном-исследователь",
        group="Полевые исследования",
        bloom="analyze",
        expected_archetype="investigate",
        skills=(
            "Анализ влажности почвы",
            "Исследование кислотности почвы",
            "Сравнение результатов полевого замера",
        ),
    ),
    DomainProbe(
        probe_id="language-performance",
        role="Специалист по деловой коммуникации",
        group="Деловая коммуникация",
        bloom="apply",
        expected_archetype="perform",
        skills=(
            "Проведение устной презентации",
            "Ведение переговоров с собеседником",
            "Письменная коммуникация с клиентом",
        ),
    ),
    DomainProbe(
        probe_id="museum-design",
        role="Проектировщик музейных экспозиций",
        group="Музейное проектирование",
        bloom="evaluate",
        expected_archetype="design",
        skills=(
            "Проектирование музейной экспозиции",
            "Моделирование маршрута посетителя",
            "Спецификация требований к экспозиции",
        ),
    ),
    DomainProbe(
        probe_id="manufacturing-operation",
        role="Оператор производственной линии",
        group="Эксплуатация оборудования",
        bloom="apply",
        expected_archetype="operate",
        skills=(
            "Эксплуатация производственной линии",
            "Мониторинг параметров оборудования",
            "Восстановление линии после остановки",
        ),
    ),
    DomainProbe(
        probe_id="public-administration-decision",
        role="Специалист транспортного планирования",
        group="Городская мобильность",
        bloom="evaluate",
        expected_archetype="decide",
        skills=(
            "Оценка вариантов городского маршрута",
            "Обоснование выбора схемы движения",
            "Принятие решения по распределению транспорта",
        ),
    ),
)

_DIGITAL_PRODUCT_CONTAMINATION = (
    "репозиторий",
    "ci pipeline",
    "health-check",
    "исполняемый workflow",
    "unit economics",
    "лендинг",
    "работающий mvp",
)


def _candidate(index: int, skill: str, probe: DomainProbe) -> SkillCandidate:
    return SkillCandidate(
        tmp_id=f"{probe.probe_id}-{index}",
        name=skill,
        group=probe.group,
        coverage_area=probe.group,
        indicators=[IndicatorSpec(text=f"Демонстрирует: {skill}", bloom=probe.bloom)],
        tools=[],
        resolution="new",
        confidence=0.98,
        council_agreement=1.0,
        entity_type="skill",
        atomicity="atomic",
        decision="accepted",
    )


def _run_probe(probe: DomainProbe) -> dict[str, Any]:
    candidates = [_candidate(index, skill, probe) for index, skill in enumerate(probe.skills, start=1)]
    dag = {
        "order": [{"id": candidate.tmp_id} for candidate in candidates],
        "final_edges": [],
    }
    return stage_dag_to_up.run(
        {
            "role": probe.role,
            "seniority": "начинающий",
            "domain": probe.group,
        },
        candidates,
        dag,
        profile=NEUTRAL_PROBE_PROFILE,
    )


def _contract_signature(plan: dict[str, Any]) -> list[tuple[object, ...]]:
    return [
        (
            tuple(row["node_ids"]),
            row["activity_archetype"],
            tuple(row["activity_archetype_modifiers"]),
            row["artifact"],
            row["validation_criteria"],
            row["artifact_contract"],
            tuple(row["artifact_contract_sources"]),
        )
        for row in plan["rows"]
    ]


@pytest.mark.parametrize("probe", PROBES, ids=lambda probe: probe.probe_id)
def test_explicit_cross_domain_activity_gets_neutral_reproducible_contract(
    probe: DomainProbe,
) -> None:
    first = _run_probe(probe)
    second = _run_probe(probe)

    assert first["status"] == "built"
    assert first["rows"]
    assert _contract_signature(first) == _contract_signature(second)

    expected_ids = {f"{probe.probe_id}-{index}" for index in range(1, len(probe.skills) + 1)}
    actual_ids = {node_id for row in first["rows"] for node_id in row["node_ids"]}
    assert actual_ids == expected_ids
    assert probe.expected_archetype in {row["activity_archetype"] for row in first["rows"]}

    for row in first["rows"]:
        assert "profile" not in row["artifact_contract_sources"]
        if row["activity_archetype"]:
            assert row["artifact_contract"]
            assert "archetype_skeleton" in row["artifact_contract_sources"]
            assert "→" in row["validation_criteria"]
        else:
            assert row["artifact_contract"] is None
            assert row["artifact_contract_sources"] == ["draft"]
            codes = {item["code"] for item in row["artifact_merge_diagnostics"]}
            assert "draft_artifact_contract_unresolved" in codes
        combined = f"{row['artifact']}\n{row['validation_criteria']}".casefold()
        assert not any(term in combined for term in _DIGITAL_PRODUCT_CONTAMINATION)

    metrics = first["report"]["quality_metrics"]
    resolved_count = sum(bool(row["artifact_contract"]) for row in first["rows"])
    assert resolved_count >= 1
    assert metrics["artifact_contract_coverage_pct"] == round(
        resolved_count / len(first["rows"]) * 100,
        1,
    )
    assert metrics["artifact_contract_unresolved_count"] == len(first["rows"]) - resolved_count


def test_ambiguous_cross_domain_activity_degrades_without_random_contract() -> None:
    probe = DomainProbe(
        probe_id="professional-ethics-unknown",
        role="Специалист помогающей практики",
        group="Профессиональная этика",
        bloom="understand",
        expected_archetype="",
        skills=(
            "Соблюдение профессиональной этики",
            "Осознание культурного контекста",
            "Ответственное отношение к практике",
        ),
    )

    plan = _run_probe(probe)

    assert plan["status"] == "built"
    assert plan["rows"]
    for row in plan["rows"]:
        assert row["activity_archetype"] == ""
        assert row["artifact_contract"] is None
        assert row["artifact_contract_sources"] == ["draft"]
        codes = {item["code"] for item in row["artifact_merge_diagnostics"]}
        assert "activity_skeleton_unavailable" in codes
        assert "draft_artifact_contract_unresolved" in codes
        combined = f"{row['artifact']}\n{row['validation_criteria']}".casefold()
        assert not any(term in combined for term in _DIGITAL_PRODUCT_CONTAMINATION)

    metrics = plan["report"]["quality_metrics"]
    assert metrics["artifact_contract_coverage_pct"] == 0.0
    assert metrics["artifact_contract_unresolved_count"] == len(plan["rows"])
