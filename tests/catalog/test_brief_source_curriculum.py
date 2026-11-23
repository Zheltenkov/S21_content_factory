"""Regression tests for historical registers crossing intake and persisted rebuilds."""

from __future__ import annotations

import json
from typing import Any

import pytest

from content_factory.catalog.pipeline import brief_persistence, stage_brief_to_catalog
from content_factory.catalog.pipeline.brief_source_curriculum import split_curriculum_brief
from content_factory.catalog.viewer.intake_dag import load_brief_spec_for_plan

BRIEF = """# Программа «Реставрация»
Основной маршрут — 3 недели по 6 часов в неделю.
## Какие результаты должно покрыть обучение
- Подготовить раствор. Проверить консистенцию.
- Восстановить образец по чертежу.

## Реестр действовавших работ для сверки покрытия
### Основы реставрации
**Материалы_01** · Архив!B2:E2 · 12 ч в прежнем плане · индивидуальный проект.
Содержание прежней работы. Подбор и испытание образцов.
Старая карточка «Выбор материала» в [Архив], «Образцы»!A7:Y7. Знает: свойства. Умеет: испытывать образец.
Отзыв эксперта о прежней работе, «Проверка»!C4. «Добавить образцы».
### Мастерская
**Стенд** · Архив!B5:E5 · 4,5 ч в прежнем плане · групповой проект.
Содержание прежней работы. 8–9 месяцев при нагрузке 60 часов в неделю.
## Новые ограничения
Сдавать фотографии с одинаковым освещением.
"""


def test_register_preserves_evidence_and_target_context() -> None:
    parsed = split_curriculum_brief(BRIEF)
    first, second = parsed.projects
    assert first["title"] == "Материалы_01"
    assert first["source_ref"] == "Архив!B2:E2"
    assert first["source_hours"] == 12 and first["team_mode"] == "individual"
    assert first["description"] == "Подбор и испытание образцов."
    assert first["card_title"] == "Выбор материала" and first["card_ref"] == "[Архив], «Образцы»!A7:Y7"
    assert first["outcomes"] == ["Знает: свойства.", "Умеет: испытывать образец."]
    assert "«Проверка»!C4" in first["review_note"] and "Добавить образцы" in first["review_note"]
    assert second["source_hours"] == 4.5 and second["team_mode"] == "team"
    assert (first["block"], second["block"]) == ("Основы реставрации", "Мастерская")
    assert second["outcomes"] == [] and second["review_note"] == ""
    assert "Добавить образцы" not in parsed.requirements_text
    assert "Сдавать фотографии" in parsed.requirements_text
    renamed = split_curriculum_brief(BRIEF.replace("Материалы_01", "Переименовано"))
    assert renamed.projects[0]["source_id"] == first["source_id"] != second["source_id"]


def test_markdown_table_without_coordinates_has_stable_ids_on_reorder() -> None:
    head = "## Исходные проекты\n### Музыка\nНазвание | Описание | Часы | Форма | ОР\n--- | --- | --- | --- | ---\n"
    first = r"Ритм \| Темп | Игра в ансамбле | 8 | командная | Поддерживает темп" + "\n"
    second = "Разминка | Разбор фразировки | — | — | \n"
    entries = split_curriculum_brief(head + first + second).projects
    assert len(entries) == 2
    assert entries[0]["title"] == "Ритм | Темп" and entries[0]["source_ref"] == ""
    assert entries[1]["source_hours"] is None and entries[1]["team_mode"] == "unknown"
    assert list(reversed(entries)) == split_curriculum_brief(head + second + first).projects


@pytest.mark.parametrize("brief", [
    "# Проекты новой программы\n- **Проект** · ссылки · 2 ч · индивидуальный.\n",
    "## Обсуждение прежних результатов\n- Групповая работа\n- Дискуссия",
    "Обычный свободный бриф без разметки.",
])
def test_unrelated_lists_and_headings_keep_original_free_form_brief(brief: str) -> None:
    parsed = split_curriculum_brief(brief)
    assert parsed.requirements_text == brief and parsed.projects == []


@pytest.mark.parametrize(("brief", "error"), [
    (BRIEF.replace("Архив!B5:E5", "Архив!B2:E2"), "Повтор"),
    (BRIEF.replace("12 ч в прежнем плане", "много часов"), "трудоёмкость"),
    ("## Реестр прежних работ\n| неизвестная колонка |\n|---|\n| А |", "не распознано"),
    ("## Реестр прежних работ\n**Проект** · Архив!A1 · 10", "Неполная строка"),
])
def test_ambiguous_or_unreadable_inventory_fails_intake(brief: str, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        split_curriculum_brief(brief)


def test_normal_entry_point_offline_separates_skills_budget_and_register(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stage_brief_to_catalog.config, "USE_LIVE", False)
    spec = stage_brief_to_catalog.decompose(BRIEF)
    assert len(spec["source_curriculum"]) == 2
    assert spec["must_include_areas"] == ["Подготовить раствор", "Восстановить образец по чертежу"]
    assert "раствор" not in spec["role"]
    assert spec["target_total_hours"] == 18
    assert "Материалы_01" not in spec["requirements_text"]


def test_live_decomposition_and_skill_prompts_use_requirements(monkeypatch: pytest.MonkeyPatch) -> None:
    from content_factory.catalog.pipeline.curriculum import revision_proposals

    sent: list[str] = []
    def chat(_model: str, messages: list[dict[str, str]], **_kwargs: Any) -> dict[str, Any]:
        sent.append(messages[-1]["content"])
        return {"choices": [{"message": {"content": json.dumps({"source_curriculum": ["invented"]})}}]}

    seen_inventory: list[str] = []
    def revisions(spec: dict[str, Any]) -> dict[str, Any]:
        seen_inventory.extend(row["title"] for row in spec["source_curriculum"])
        return {"revision_decisions": []}

    monkeypatch.setattr(stage_brief_to_catalog.config, "USE_LIVE", True)
    monkeypatch.setattr(stage_brief_to_catalog.llm, "chat", chat)
    monkeypatch.setattr(revision_proposals, "propose_curriculum_revisions", revisions)
    spec = stage_brief_to_catalog.decompose(BRIEF)
    stage_brief_to_catalog.synthesize_draft_from_brief(BRIEF, spec)
    assert seen_inventory == ["Материалы_01", "Стенд"]
    assert len(sent) == 2 and all("Материалы_01" not in text and "Проверка»!C4" not in text for text in sent)
    assert json.loads(sent[1])["brief"] == sent[0] == spec["requirements_text"]


def test_restore_registry_and_revisions_without_job_and_reparse_old_raw(catalog_conn: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stage_brief_to_catalog.config, "USE_LIVE", False)
    spec = stage_brief_to_catalog.decompose(BRIEF)
    spec["revision_decisions"] = [{"source_ids": [spec["source_curriculum"][0]["source_id"]]}]
    brief_id = brief_persistence.save_brief(catalog_conn, BRIEF, spec)
    reloaded = load_brief_spec_for_plan(catalog_conn, brief_id)
    assert reloaded["source_curriculum"] == spec["source_curriculum"]
    assert reloaded["revision_decisions"] == spec["revision_decisions"]
    assert reloaded["raw_text"] == BRIEF and reloaded["requirements_text"] == spec["requirements_text"]
    catalog_conn.execute("UPDATE profile_brief SET metadata_json = NULL WHERE id = ?", (brief_id,))
    restored = load_brief_spec_for_plan(catalog_conn, brief_id)
    assert restored["source_curriculum"] == spec["source_curriculum"] and restored["target_total_hours"] == 18
