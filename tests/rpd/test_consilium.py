"""Консилиум из 3 агентов: классификация шаблона + ТЗ по УП + сборка прозы, и его вплетение в build."""

from __future__ import annotations

import json

from content_factory.rpd import build as build_mod
from content_factory.rpd.consilium import run_consilium, section_key
from content_factory.rpd.up_models import from_registry_plan

_PLAN = {
    "title": "Тестовая программа",
    "meta": {"total_hours": 72},
    "rows": [{"block_title": "Модуль 1", "outcomes_can": "умеет применять", "effort_hours": 72, "skills": ["Навык"]}],
}


class _StageClient:
    """Возвращает заготовленные ответы по стадиям (classify → interpret → compose)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        del system, user, kwargs
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def test_run_consilium_three_stages() -> None:
    sections = [{"number": "3.1", "title": "Учебный план"}, {"number": "1.3", "title": "Актуальность программы"}]
    up = from_registry_plan(_PLAN)
    classify = json.dumps({"3.1 Учебный план": "up_plan", "1.3 Актуальность программы": "generate"}, ensure_ascii=False)
    interpret = json.dumps({"1.3 Актуальность программы": "написать про актуальность"}, ensure_ascii=False)
    compose = json.dumps({"1.3 Актуальность программы": "Актуальность высока.\nВторой абзац."}, ensure_ascii=False)
    client = _StageClient([classify, interpret, compose])

    roles, texts = run_consilium(sections, up, client)

    assert roles[section_key("3.1", "Учебный план")] == "up_plan"
    assert roles[section_key("1.3", "Актуальность программы")] == "generate"
    assert texts[section_key("1.3", "Актуальность программы")] == ["Актуальность высока.", "Второй абзац."]
    assert client.calls == 3  # три стадии-агента


def test_run_consilium_without_client_is_empty() -> None:
    roles, texts = run_consilium([{"number": "1.1", "title": "Паспорт"}], from_registry_plan(_PLAN), None)
    assert roles == {} and texts == {}


def test_build_uses_consilium_roles_and_texts(monkeypatch) -> None:
    def fake_consilium(specs: list[dict[str, str]], up: object, client: object) -> tuple[dict, dict]:
        del up, client
        roles: dict[str, str] = {}
        texts: dict[str, list[str]] = {}
        for spec in specs:
            key = section_key(spec["number"], spec["title"])
            if "Актуальность" in spec["title"]:
                roles[key] = "generate"
                texts[key] = ["Текст из консилиума."]
        return roles, texts

    monkeypatch.setattr(build_mod, "run_consilium", fake_consilium)
    build = build_mod.build_rpd_sections(from_registry_plan(_PLAN), llm_client=object())
    relevance = build.section("1.3", "Актуальность программы")
    assert relevance is not None and relevance.paragraphs == ["Текст из консилиума."]


def test_build_falls_back_to_category_for_when_consilium_empty(monkeypatch) -> None:
    monkeypatch.setattr(build_mod, "run_consilium", lambda specs, up, client: ({}, {}))
    build = build_mod.build_rpd_sections(from_registry_plan(_PLAN), llm_client=None)
    # учебный план всё равно распознан детерминированно и наполнен из УП
    plan = build.section("3.1", "Учебный план")
    assert plan is not None and plan.kind == "table"
