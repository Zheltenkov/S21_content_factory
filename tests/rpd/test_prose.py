"""LLM-генерация прозы (актуальность/цели/паспорт) и её вставка в рендер (B3)."""

from __future__ import annotations

from pathlib import Path

import docx

from content_factory.rpd.ingest import parse_dpo_program
from content_factory.rpd.prose import generate_prose
from content_factory.rpd.render_selective import render_rpd_pk
from content_factory.rpd.up_models import UpPlan, from_registry_plan

_PLAN = {
    "title": "Тестовая программа",
    "meta": {"total_hours": 72},
    "rows": [
        {
            "block_title": "Модуль 1",
            "block_goal": "Научиться решать прикладную задачу",
            "project_name": "Отчёт о прикладном решении",
            "outcomes_know": "знает основы",
            "effort_hours": 72,
            "skills": ["Навык"],
        }
    ],
}


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[tuple[str, str]] = []
        self.call_kwargs: list[dict[str, object]] = []

    def complete(self, system: str, user: str, **kwargs: object) -> str:
        self.calls.append((system, user))
        self.call_kwargs.append(kwargs)
        return self.text


def test_generate_prose_returns_paragraphs_for_prose_section() -> None:
    up = from_registry_plan(_PLAN)
    client = _FakeClient("Актуальность высока.\nВторой абзац актуальности.")
    paragraphs = generate_prose("Актуальность программы", up, client)
    assert paragraphs == ["Актуальность высока.", "Второй абзац актуальности."]
    assert client.calls and "Тестовая программа" in client.calls[0][1]  # УП-контекст в промпте
    assert client.call_kwargs[0]["llm_role"] == "rpd_prose"


def test_relevance_fallback_prompt_uses_long_form_contract() -> None:
    client = _FakeClient("Текст.")

    generate_prose("Актуальность программы", from_registry_plan(_PLAN), client)

    assert "220-280 слов" in client.calls[0][1]
    assert "4-5 содержательных абзацев" in client.calls[0][1]
    assert client.call_kwargs[0]["prompt_version"] == "rpd-prose-v4"


def test_program_implementation_goals_prompt_evaluates_up_instead_of_repeating_tasks() -> None:
    client = _FakeClient("Первый абзац.\nВторой абзац.")

    generate_prose("Цели реализации программы", from_registry_plan(_PLAN), client)

    prompt = client.calls[0][1]
    assert "120-170 слов" in prompt
    assert "2-3 содержательных абзаца" in prompt
    assert "не менее шести разных наблюдаемых действий" in prompt
    assert "не оформляй текст как список задач" in prompt
    assert "цель модуля: Научиться решать прикладную задачу" in prompt
    assert "Учебные продукты и задания:\n- Отчёт о прикладном решении" in prompt
    assert "Ключевые результаты: знает основы" in prompt


def test_render_places_program_implementation_goals_between_2_and_2_2(tmp_path: Path) -> None:
    class _GoalsClient:
        def complete(self, system: str, user: str, **kwargs: object) -> str:
            del system
            if kwargs.get("llm_role") != "rpd_prose":
                return "{}"
            if "Напиши раздел «Цели реализации программы»" in user:
                return "INTEGRATED_GOALS_A.\nINTEGRATED_GOALS_B."
            return "OTHER_SECTION."

    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_pk(from_registry_plan(_PLAN), llm_client=_GoalsClient()))
    rendered = parse_dpo_program(out)
    goals = next(section for section in rendered.sections if section.number == "2.1")

    assert goals.title == "Цели реализации программы"
    assert goals.paragraphs == ["INTEGRATED_GOALS_A.", "INTEGRATED_GOALS_B."]
    assert next(section for section in rendered.sections if section.number == "2.2").title.startswith(
        "Профессиональные компетенции"
    )


def test_generate_prose_skips_non_prose_and_missing_client() -> None:
    up = from_registry_plan(_PLAN)
    assert generate_prose("Учебный план", up, _FakeClient("x")) == []  # не проза
    assert generate_prose("Актуальность программы", up, None) == []  # нет клиента


def test_render_uses_generated_prose_instead_of_placeholder(tmp_path: Path) -> None:
    up = from_registry_plan(_PLAN)
    client = _FakeClient("Сгенерированная проза.")
    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_pk(up, llm_client=client))
    paragraphs = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert "Сгенерированная проза." in paragraphs
    assert "[Будет сгенерировано]" not in paragraphs  # все GENERATE-разделы заполнены


def test_render_without_client_falls_back_to_placeholder(tmp_path: Path) -> None:
    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_pk(from_registry_plan(_PLAN)))
    paragraphs = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert "[Будет сгенерировано]" in paragraphs  # без клиента — плейсхолдер


def test_render_with_failing_client_falls_back(tmp_path: Path) -> None:
    class _Boom:
        def complete(self, system: str, user: str, **kwargs: object) -> str:
            raise RuntimeError("llm down")

    out = tmp_path / "rpd.docx"
    out.write_bytes(render_rpd_pk(UpPlan(title="X", modules=[]), llm_client=_Boom()))
    # даже при сбое LLM документ собирается, проза → плейсхолдер
    paragraphs = "\n".join(p.text for p in docx.Document(str(out)).paragraphs)
    assert "[Будет сгенерировано]" in paragraphs
