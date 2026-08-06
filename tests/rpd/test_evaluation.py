"""Pilot metrics: editing, acceptance without edits and LLM aggregation."""

from __future__ import annotations

from content_factory.rpd.build import build_rpd_sections
from content_factory.rpd.evaluation import evaluate_rpd_build
from content_factory.rpd.llm_runtime import RpdLlmEvent
from content_factory.rpd.up_models import from_registry_plan


def _build():  # type: ignore[no-untyped-def]
    return build_rpd_sections(
        from_registry_plan(
            {
                "title": "Evaluation test",
                "meta": {"total_hours": 8},
                "rows": [{"block_title": "Модуль", "project_name": "Проект", "effort_hours": 8}],
            }
        )
    )


def test_acceptance_without_edits_changes_after_methodologist_edit() -> None:
    build = _build()
    generated = next(section for section in build.sections if section.category == "generate")
    generated.paragraphs = ["Сгенерировано моделью."]
    generated.initial_content_hash = generated.content_hash()
    generated.done = True

    before = evaluate_rpd_build(build)
    generated.paragraphs = ["Исправлено методистом."]
    build.revision += 1
    after = evaluate_rpd_build(build)

    assert before.accepted_without_edits_rate == 1.0
    assert before.accepted_generated_without_edits == 1
    assert after.accepted_without_edits_rate == 0.0
    assert after.edited_sections == 1
    assert after.revision == 1


def test_llm_metrics_are_aggregated_by_role() -> None:
    build = _build()
    common = {"started_at": "2026-08-06T00:00:00+00:00", "provider": "p", "model": "m"}
    build.generation_trace.llm_events = [
        RpdLlmEvent(
            sequence=1,
            role="rpd_prose",
            prompt_version="p1",
            elapsed_ms=120,
            success=True,
            total_tokens=100,
            cost_usd=0.01,
            **common,
        ),
        RpdLlmEvent(
            sequence=2,
            role="rpd_validate",
            prompt_version="v1",
            elapsed_ms=80,
            success=False,
            error_type="TimeoutError",
            **common,
        ),
    ]

    artifact = evaluate_rpd_build(build)

    assert artifact.llm_calls == 2
    assert artifact.llm_failed_calls == 1
    assert artifact.llm_total_latency_ms == 200
    assert artifact.llm_total_tokens == 100
    assert artifact.llm_total_cost_usd == 0.01
    assert artifact.llm_by_role["rpd_validate"].failed_calls == 1
