"""Консилиум: классификация шаблона + ТЗ по УП + сборка прозы + валидатор, кэш, вплетение в build."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.rpd import build as build_mod
from content_factory.rpd import consilium as consilium_mod
from content_factory.rpd.consilium import run_consilium, section_key
from content_factory.rpd.up_models import from_registry_plan

_PLAN = {
    "title": "Тестовая программа",
    "meta": {"total_hours": 72},
    "rows": [{"block_title": "Модуль 1", "outcomes_can": "умеет применять", "effort_hours": 72, "skills": ["Навык"]}],
}


class _StageClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        del system, user, kwargs
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(consilium_mod, "_CACHE_DIR", tmp_path / "consilium_cache")


def test_run_consilium_four_agents_and_flags() -> None:
    sections = [{"number": "3.1", "title": "Учебный план"}, {"number": "1.3", "title": "Актуальность программы"}]
    up = from_registry_plan(_PLAN)
    classify = json.dumps({"3.1 Учебный план": "up_plan", "1.3 Актуальность программы": "generate"}, ensure_ascii=False)
    interpret = json.dumps({"1.3 Актуальность программы": "написать про актуальность"}, ensure_ascii=False)
    compose = json.dumps({"1.3 Актуальность программы": "Актуальность высока.\nВторой абзац."}, ensure_ascii=False)
    validate = json.dumps({"1.3 Актуальность программы": "Слишком общо."}, ensure_ascii=False)
    client = _StageClient([classify, interpret, compose, validate])

    roles, texts, flags = run_consilium(sections, up, client)

    assert roles[section_key("3.1", "Учебный план")] == "up_plan"
    assert texts[section_key("1.3", "Актуальность программы")] == ["Актуальность высока.", "Второй абзац."]
    assert flags[section_key("1.3", "Актуальность программы")] == "Слишком общо."
    assert client.calls == 4  # классификатор + интерпретатор + сборщик + критик


def test_run_consilium_is_cached() -> None:
    sections = [{"number": "1.3", "title": "Актуальность программы"}]
    up = from_registry_plan(_PLAN)
    responses = [
        json.dumps({"1.3 Актуальность программы": "generate"}, ensure_ascii=False),
        json.dumps({"1.3 Актуальность программы": "ТЗ"}, ensure_ascii=False),
        json.dumps({"1.3 Актуальность программы": "Текст."}, ensure_ascii=False),
        "{}",
    ]
    first = _StageClient(responses)
    run_consilium(sections, up, first)
    second = _StageClient(responses)
    roles, texts, _flags = run_consilium(sections, up, second)  # тот же вход → из кэша
    assert second.calls == 0  # LLM не звали
    assert texts[section_key("1.3", "Актуальность программы")] == ["Текст."]


def test_run_consilium_without_client_is_empty() -> None:
    roles, texts, flags = run_consilium([{"number": "1.1", "title": "Паспорт"}], from_registry_plan(_PLAN), None)
    assert roles == {} and texts == {} and flags == {}


def test_build_uses_consilium_texts_and_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_consilium(specs: list[dict[str, str]], up: object, client: object) -> tuple[dict, dict, dict]:
        del up, client
        roles: dict[str, str] = {}
        texts: dict[str, list[str]] = {}
        flags: dict[str, str] = {}
        for spec in specs:
            key = section_key(spec["number"], spec["title"])
            if "Актуальность" in spec["title"]:
                roles[key] = "generate"
                texts[key] = ["Текст из консилиума."]
                flags[key] = "Проверьте факты."
        return roles, texts, flags

    monkeypatch.setattr(build_mod, "run_consilium", fake_consilium)
    build = build_mod.build_rpd_sections(from_registry_plan(_PLAN), llm_client=object())
    relevance = build.section("1.3", "Актуальность программы")
    assert relevance is not None
    assert relevance.paragraphs == ["Текст из консилиума."]
    assert relevance.flag == "Проверьте факты."


def test_build_falls_back_to_category_for_when_consilium_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build_mod, "run_consilium", lambda specs, up, client: ({}, {}, {}))
    build = build_mod.build_rpd_sections(from_registry_plan(_PLAN), llm_client=None)
    plan = build.section("3.1", "Учебный план")
    assert plan is not None and plan.kind == "table"
