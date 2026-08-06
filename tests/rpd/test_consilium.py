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
        self.roles: list[str] = []
        self.cache_hits: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        del system, user
        self.roles.append(str(kwargs["llm_role"]))
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response

    def record_cache_hit(self, *, role: str, prompt_version: str) -> None:
        self.cache_hits.append((role, prompt_version))


class _RecordingClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.users: list[str] = []

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        del system, kwargs
        self.users.append(user)
        return self.responses[len(self.users) - 1]


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
    client = _StageClient([classify, interpret, compose, validate, "{}"])

    roles, texts, flags = run_consilium(sections, up, client)

    assert roles[section_key("3.1", "Учебный план")] == "up_plan"
    assert texts[section_key("1.3", "Актуальность программы")] == ["Актуальность высока.", "Второй абзац."]
    assert flags[section_key("1.3", "Актуальность программы")] == "Слишком общо."
    assert client.calls == 5  # классификатор + интерпретатор + сборщик + критик + best-effort repair
    assert client.roles == ["rpd_classify", "rpd_interpret", "rpd_prose", "rpd_validate", "rpd_repair"]


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
    assert [role for role, _version in second.cache_hits] == [
        "rpd_classify",
        "rpd_interpret",
        "rpd_prose",
        "rpd_validate",
    ]
    assert all(version.startswith("rpd-consilium-v4:") for _role, version in second.cache_hits)


def test_run_consilium_without_client_is_empty() -> None:
    roles, texts, flags = run_consilium([{"number": "1.1", "title": "Паспорт"}], from_registry_plan(_PLAN), None)
    assert roles == {} and texts == {} and flags == {}


def test_validate_prose_sends_every_section_in_full(monkeypatch: pytest.MonkeyPatch) -> None:
    first = "Начало первого раздела. " + ("Подробное содержание. " * 30) + "Уникальный конец первого."
    second = "Начало второго раздела. " + ("Дополнительное содержание. " * 30) + "Уникальный конец второго."
    texts = {"1 Первый раздел": [first], "2 Второй раздел": [second]}
    client = _RecordingClient(["{}", "{}"])
    monkeypatch.setattr(consilium_mod, "_VALIDATION_BATCH_CHAR_LIMIT", 500)

    flags = consilium_mod._validate_prose(texts, from_registry_plan(_PLAN), client)

    assert flags == {}
    assert len(client.users) == 2
    prompts = "\n".join(client.users)
    assert first in prompts
    assert second in prompts
    assert prompts.count("Уникальный конец первого.") == 1
    assert prompts.count("Уникальный конец второго.") == 1


def test_validate_prose_ignores_unknown_section_keys() -> None:
    key = "1.3 Актуальность программы"
    response = json.dumps({key: "Проверьте факт.", "99 Несуществующий раздел": "Ошибка."}, ensure_ascii=False)
    client = _RecordingClient([response])

    flags = consilium_mod._validate_prose({key: ["Полный текст."]}, from_registry_plan(_PLAN), client)

    assert flags == {key: "Проверьте факт."}


def test_classification_forces_parent_sections_to_container() -> None:
    sections = [
        {"number": "1", "title": "Общая характеристика программы"},
        {"number": "1.1", "title": "Паспорт программы"},
        {"number": "1.3", "title": "Актуальность программы"},
    ]
    response = json.dumps(
        {
            "1 Общая характеристика программы": "generate",
            "1.1 Паспорт программы": "generate",
            "1.3 Актуальность программы": "generate",
        },
        ensure_ascii=False,
    )

    roles = consilium_mod._classify_sections(sections, _RecordingClient([response]))

    assert roles["1 Общая характеристика программы"] == "container"
    assert roles["1.1 Паспорт программы"] == "generate"


def test_interpreter_returns_structured_content_ownership_briefs() -> None:
    prose = [
        {"key": "1.3 Актуальность программы", "role": "generate"},
        {"key": "1.4 Цель и задачи программы", "role": "generate"},
    ]
    response = json.dumps(
        {
            prose[0]["key"]: {
                "purpose": "Обосновать потребность",
                "must_cover": ["изменение рынка"],
                "exclusive_focus": "почему программа нужна сейчас",
                "must_not_repeat": ["перечень целей обучения"],
            },
            prose[1]["key"]: {
                "purpose": "Зафиксировать цель",
                "must_cover": ["целевой результат"],
                "exclusive_focus": "что должна обеспечить программа",
                "must_not_repeat": ["обоснование актуальности"],
            },
        },
        ensure_ascii=False,
    )
    client = _RecordingClient([response])

    briefs = consilium_mod._interpret_up(prose, from_registry_plan(_PLAN), client)

    assert briefs[prose[0]["key"]].exclusive_focus == "почему программа нужна сейчас"
    assert briefs[prose[1]["key"]].must_not_repeat == ["обоснование актуальности"]
    assert all(item["key"] in client.users[0] for item in prose)


def test_composer_receives_the_whole_structured_content_plan() -> None:
    prose = [
        {"key": "1.3 Актуальность программы", "role": "generate"},
        {"key": "1.4 Цель и задачи программы", "role": "generate"},
    ]
    briefs = {
        item["key"]: consilium_mod.SectionBrief(
            purpose=f"Назначение {index}",
            exclusive_focus=f"Уникальный фокус {index}",
            must_not_repeat=[f"Тезис соседнего раздела {index}"],
        )
        for index, item in enumerate(prose, start=1)
    }
    response = json.dumps({item["key"]: f"Уникальный текст {index}." for index, item in enumerate(prose, start=1)}, ensure_ascii=False)
    client = _RecordingClient([response])

    texts = consilium_mod._compose_prose(prose, briefs, from_registry_plan(_PLAN), client)

    assert set(texts) == {item["key"] for item in prose}
    assert all(item["key"] in client.users[0] for item in prose)
    assert "must_not_repeat" in client.users[0]


def test_validate_prose_discards_false_truncation_for_completed_text() -> None:
    key = "1 Общая характеристика программы"
    client = _RecordingClient([json.dumps({key: "Текст обрывается и не завершается."}, ensure_ascii=False)])

    flags = consilium_mod._validate_prose({key: ["Полный текст заканчивается завершённым предложением."]}, from_registry_plan(_PLAN), client)

    assert flags == {}


def test_validate_prose_keeps_truncation_for_incomplete_text() -> None:
    key = "1.3 Актуальность программы"
    warning = "Текст обрывается на незавершённой формулировке."
    client = _RecordingClient([json.dumps({key: warning}, ensure_ascii=False)])

    flags = consilium_mod._validate_prose({key: ["Программа необходима для развития и"]}, from_registry_plan(_PLAN), client)

    assert flags == {key: warning}


def test_validate_prose_detects_exact_cross_section_repetition() -> None:
    repeated = (
        "Программа формирует способность исследовать продукт, проверять гипотезы и принимать решения "
        "на основании измеримых результатов экспериментов."
    )
    texts = {"1.3 Актуальность": [repeated], "1.4 Цель и задачи": [repeated]}

    flags = consilium_mod._validate_prose(texts, from_registry_plan(_PLAN), _RecordingClient(["{}"] ))

    assert "1.4 Цель и задачи" in flags
    assert "Повторяет длинное предложение" in flags["1.4 Цель и задачи"]


def test_validate_prose_allows_shared_domain_terms_with_distinct_meaning() -> None:
    texts = {
        "1.3 Актуальность": ["Искусственный интеллект востребован предпринимателями для ускорения анализа рынка."],
        "1.4 Цель и задачи": ["Цель программы — научить предпринимателей проверять продуктовые гипотезы с применением искусственного интеллекта."],
    }

    flags = consilium_mod._validate_prose(texts, from_registry_plan(_PLAN), _RecordingClient(["{}"] ))

    assert flags == {}


def test_run_consilium_repairs_repeated_section_once() -> None:
    sections = [
        {"number": "1.3", "title": "Актуальность программы"},
        {"number": "1.4", "title": "Цель и задачи программы"},
    ]
    keys = [section_key(item["number"], item["title"]) for item in sections]
    repeated = (
        "Программа формирует способность исследовать продукт, проверять гипотезы и принимать решения "
        "на основании измеримых результатов экспериментов."
    )
    responses = [
        json.dumps(dict.fromkeys(keys, "generate"), ensure_ascii=False),
        json.dumps({key: {"purpose": key, "exclusive_focus": key} for key in keys}, ensure_ascii=False),
        json.dumps(dict.fromkeys(keys, repeated), ensure_ascii=False),
        "{}",
        json.dumps({keys[1]: "Цель программы — освоить проверку продуктовых гипотез."}, ensure_ascii=False),
        "{}",
    ]
    client = _StageClient(responses)

    _roles, texts, flags = run_consilium(sections, from_registry_plan(_PLAN), client)

    assert texts[keys[0]] == [repeated]
    assert texts[keys[1]] == ["Цель программы — освоить проверку продуктовых гипотез."]
    assert flags == {}
    assert client.roles == ["rpd_classify", "rpd_interpret", "rpd_prose", "rpd_validate", "rpd_repair", "rpd_validate"]


def test_run_consilium_repairs_non_repetition_validator_issue_once() -> None:
    sections = [{"number": "1.4", "title": "Цель и задачи программы"}]
    key = section_key("1.4", "Цель и задачи программы")
    responses = [
        json.dumps({key: "generate"}, ensure_ascii=False),
        json.dumps({key: {"purpose": "Зафиксировать цель", "exclusive_focus": "цель и задачи"}}, ensure_ascii=False),
        json.dumps({key: "Цель программы — освоить продуктовые навыки."}, ensure_ascii=False),
        json.dumps({key: "Цель не согласована с перечисленными задачами."}, ensure_ascii=False),
        json.dumps(
            {key: "Цель программы — освоить продуктовые навыки; задачи раскрывают этапы их применения."},
            ensure_ascii=False,
        ),
        "{}",
    ]
    client = _StageClient(responses)

    _roles, texts, flags = run_consilium(sections, from_registry_plan(_PLAN), client)

    assert texts[key] == ["Цель программы — освоить продуктовые навыки; задачи раскрывают этапы их применения."]
    assert flags == {}
    assert client.roles == ["rpd_classify", "rpd_interpret", "rpd_prose", "rpd_validate", "rpd_repair", "rpd_validate"]


def test_run_consilium_does_not_persist_redundant_flag_for_manual_section() -> None:
    sections = [{"number": "4.4", "title": "Пример оценки"}]
    key = section_key("4.4", "Пример оценки")
    issue = "Нужны фактические материалы с платформы."
    responses = [
        json.dumps({key: "platform"}, ensure_ascii=False),
        json.dumps({key: {"purpose": "Дать пример оценки", "exclusive_focus": "артефакт LMS"}}, ensure_ascii=False),
        json.dumps({key: "Черновое описание примера оценки."}, ensure_ascii=False),
        json.dumps({key: issue}, ensure_ascii=False),
        "{}",
    ]
    client = _StageClient(responses)

    _roles, texts, flags = run_consilium(sections, from_registry_plan(_PLAN), client)

    assert texts[key] == ["Черновое описание примера оценки."]
    assert flags == {}


def test_build_excludes_structural_container_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_containers: set[str] = set()

    def fake_consilium(specs: list[dict[str, str]], up: object, client: object) -> tuple[dict, dict, dict]:
        del up, client
        captured_containers.update(consilium_mod._container_section_keys(specs))
        return (dict.fromkeys(captured_containers, "container"), {}, {})

    monkeypatch.setattr(build_mod, "run_consilium", fake_consilium)

    build = build_mod.build_rpd_sections(from_registry_plan(_PLAN), llm_client=object())
    built_keys = {section_key(section.number, section.title) for section in build.sections}

    assert captured_containers
    assert captured_containers.isdisjoint(built_keys)


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
