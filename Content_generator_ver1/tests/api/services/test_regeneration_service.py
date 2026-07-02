import pytest

import asyncio

import api.services.regeneration_service as regeneration_service
from api.services.regeneration_service import (
    RegenerationCommand,
    RegenerationValidationError,
    RegenerationService,
    _extract_cached_rubric,
    _raise_if_rubric_regressed,
    _refresh_toc_for_structural_regeneration,
    _rubric_regression_details,
    _rubric_failed_count,
)


def _rubric(*scores: tuple[str, int]) -> dict:
    return {
        "items": [
            {"id": item_id, "score": score, "title": item_id, "comments": []}
            for item_id, score in scores
        ]
    }


def test_rubric_failed_count_counts_zero_scores() -> None:
    assert _rubric_failed_count(_rubric(("1.1", 1), ("2.1", 0), ("3.1", "0"))) == 2


def test_extract_cached_rubric_prefers_latest_regenerated_rubric() -> None:
    original = _rubric(("1.1", 1), ("2.1", 0))
    regenerated = _rubric(("1.1", 1), ("2.1", 1))

    assert _extract_cached_rubric({"rubric": original, "regenerated": {"rubric": regenerated}}) is regenerated


def test_raise_if_rubric_regressed_rejects_more_failed_criteria() -> None:
    baseline = _rubric(("1.1", 1), ("2.1", 0), ("3.1", 1))
    regenerated = _rubric(("1.1", 0), ("2.1", 0), ("3.1", 0))

    with pytest.raises(RegenerationValidationError) as exc_info:
        _raise_if_rubric_regressed(baseline, regenerated)

    assert exc_info.value.status_code == 422
    assert "было непройдено 1, стало 3" in exc_info.value.detail
    assert "1.1" in exc_info.value.detail


def test_raise_if_rubric_regressed_allows_same_failed_count() -> None:
    baseline = _rubric(("1.1", 1), ("2.1", 0))
    regenerated = _rubric(("1.1", 0), ("2.1", 1))

    _raise_if_rubric_regressed(baseline, regenerated)


def test_rubric_regression_details_returns_warning_payload() -> None:
    baseline = _rubric(("1.1", 1), ("2.1", 0))
    regenerated = {
        "items": [
            {"id": "1.1", "score": 0, "title": "Проверка ссылок", "evidence": "сломана ссылка"},
            {"id": "2.1", "score": 0, "title": "Старый непройденный критерий"},
        ]
    }

    details = _rubric_regression_details(baseline, regenerated)

    assert details is not None
    assert details["baseline_failed"] == 1
    assert details["regenerated_failed"] == 2
    assert details["new_failed"][0]["id"] == "1.1"
    assert details["new_failed"][0]["title"] == "Проверка ссылок"
    assert details["new_failed"][0]["evidence"] == "сломана ссылка"
    assert "Уточните запрос" in details["message"]


def test_rubric_regression_details_marks_structural_context() -> None:
    details = _rubric_regression_details(
        _rubric(("1.5", 1), ("2.4.1", 1)),
        _rubric(("1.5", 0), ("2.4.1", 0)),
        change_intent="structural_document_edit",
    )

    assert details is not None
    assert details["change_intent"] == "structural_document_edit"
    assert "Структурная перегенерация" in details["message"]
    assert "обязательные главы 1-3" in details["message"]


def test_apply_quality_checks_skips_global_llm_rewrites_for_scoped_regeneration(monkeypatch) -> None:
    class RaisingContentEditor:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("ContentEditor must not run for scoped regeneration")

    class RaisingStyleGuard:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("StyleGuard must not run for scoped regeneration")

    monkeypatch.setattr(regeneration_service, "ContentEditorAgent", RaisingContentEditor)
    monkeypatch.setattr(regeneration_service, "StyleGuardRepair", RaisingStyleGuard)

    service = RegenerationService()
    result = asyncio.run(
        service._apply_quality_checks(
            llm_client=object(),
            markdown="# README\n\nТекст.",
            seed=object(),
            language="ru",
            allow_llm_rewrites=False,
        )
    )

    assert result == "# README\n\nТекст."


def test_refresh_toc_for_structural_regeneration_adds_new_chapter_link() -> None:
    markdown = """# README

## Содержание
- [Глава 1. Введение и инструкция](#глава-1-введение-и-инструкция)
- [Глава 2. Теоретический блок](#глава-2-теоретический-блок)
- [Глава 3. Практический блок](#глава-3-практический-блок)

## Глава 1. Введение и инструкция
Введение.

## Глава 2. Теоретический блок
Теория.

## Глава 3. Практический блок
Практика.

## Глава 4. Финальное ревью
Проверка.
"""

    refreshed, changed = _refresh_toc_for_structural_regeneration(markdown, "ru")

    assert changed is True
    assert "- [Глава 4. Финальное ревью](#глава-4-финальное-ревью)" in refreshed
    assert "## Глава 2. Теоретический блок" in refreshed


def test_persist_regeneration_accepts_result_metadata() -> None:
    cached_result: dict = {"report_json": {}}
    db_calls: list[tuple] = []

    service = RegenerationService(
        cache_getter=lambda _request_id: cached_result,
        db_updater=lambda *args: db_calls.append(args),
    )

    asyncio.run(
        service._persist_regeneration(
            command=RegenerationCommand(
                request_id="regen-1",
                user_id="1",
                original_request_id="source-1",
                original_md="# Old",
                comments="Правка",
                language="ru",
            ),
            cached_result=cached_result,
            regenerated_md="# New",
            changes=["changed"],
            rubric_json=_rubric(("1.1", 1)),
            text_stats={"words": 2},
            learning_outcomes=["LO"],
            skills=["Skill"],
            seed_source="seed",
            learning_context_source="cache",
            accepted=True,
            warnings=[],
            rubric_regression=None,
            validation_report={"schema_version": "regeneration.pipeline.v1", "applied_patch_count": 1},
        )
    )

    assert cached_result["regenerated"]["accepted"] is True
    assert cached_result["regenerated"]["warnings"] == []
    assert cached_result["regenerated"]["validation_report"]["applied_patch_count"] == 1
    assert db_calls
