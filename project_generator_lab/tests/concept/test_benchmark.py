"""The benchmark runs the real pipeline; here a scripted client stands in for the paid model."""

import asyncio
import json

from project_generator_lab.concept import benchmark
from project_generator_lab.tests.concept.test_validation import WorkflowClient, request_fixture


def test_benchmark_runs_the_whole_pipeline_and_prices_tokens(tmp_path, monkeypatch):
    request = benchmark.example_request(1, "zero")
    assert (
        request.brief.title == "Отчёт о клиентской проблеме"
        and request.brief.hours == 14.0
        and request.brief.start_level == "zero"
    )
    # The scripted client answers for its own fixture request, so the pipeline runs on that one.
    request = request_fixture()
    monkeypatch.setattr(benchmark, "ModelClient", lambda model: WorkflowClient(request))
    row = asyncio.run(benchmark.one("openai/gpt-5.4", request, tmp_path))
    assert row["status"] == "review" and row["calls"] == 3
    saved = json.loads((tmp_path / "openai_gpt-5.4.json").read_text(encoding="utf-8"))
    assert saved["input_approved_at"] and saved["concept"]
    assert benchmark.cost("openai/gpt-5.4", 1_000_000, 100_000) == 4.0
    assert benchmark.cost("unknown/model", 1, 1) is None
    assert "| openai/gpt-5.4 | review |" in benchmark.table([row])


def test_the_benchmark_takes_a_plan_of_ones_own_and_fills_the_brief_as_the_form_does():
    from pathlib import Path

    import pytest
    from pydantic import ValidationError

    plan = Path(__file__).parent / "fixtures" / "baton_plan.csv"
    team = benchmark.example_request(3, "", plan).brief
    assert (team.title, team.hours, team.format, team.group_size, team.checking) == (
        "Командная сборка по договорённостям", 15.0, "group", 4, "P2P + линтер")
    theses, cases = team.description.split("\n\nКейсы проекта:\n")
    assert theses.startswith("Участник вместе с командой разрабатывает адаптивный тест по Python")
    assert cases.startswith("1. Командная разработка адаптивного теста")
    # The fourth line of the plan is an exam: no project is prepared for it.
    with pytest.raises(ValidationError, match="экзамен, а не проект"):
        benchmark.example_request(4, "", plan)
    # The example plan has no cases: its description stays as it was.
    assert "Кейсы проекта" not in benchmark.example_request(1, "zero").brief.description
