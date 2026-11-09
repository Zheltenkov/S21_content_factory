"""The model comparison is only as good as its counting: every number in the summary is pinned."""

from project_generator_lab.concept import benchmark
from project_generator_lab.concept.models import Event, Finding, Question, Review
from project_generator_lab.tests.concept.test_mutation_guards import task
from project_generator_lab.tests.concept.test_validation import reviewed_run


def finding(severity, origin, message):
    return Finding(severity=severity, message=message, suggestion="—", origin=origin)


def crafted_run():
    run = reviewed_run()
    run.request = run.request.model_copy(update={"brief": run.request.brief.model_copy(update={"outcomes": "ОР один\n\nОР два\nОР три", "hours": 10})})
    first, second, bonus = task(1, 4.25, None), task(2, 5.5, None), task(3, 3, None, optional=True)
    first.outcome_numbers, second.outcome_numbers, bonus.outcome_numbers = [1, 2], [2, 7], [3]
    run.concept.tasks = [first, second, bonus]
    run.input_card.questions = [Question(question="А?", why="—", blocking=True), Question(question="Б?", why="—", blocking=False),
                                Question(question="В?", why="—", blocking=True)]
    run.review = Review(summary="—", findings=[
        finding("blocker", "system", "Код: блокер"), finding("warning", "system", "Код: замечание"),
        finding("warning", "system", "Код: ещё"), finding("blocker", "model", "ИИ: блокер"),
        finding("warning", "model", "ИИ: 1"), finding("warning", "model", "ИИ: 2"), finding("warning", "model", "ИИ: 3")])
    run.events = [Event(stage="context", status="running", message="без вызова"),
                  Event(stage="context", status="done", message="карта", input_tokens=100_000, output_tokens=2_000, schema_repairs=1),
                  Event(stage="design", status="done", message="замысел", input_tokens=50_000, output_tokens=8_000),
                  Event(stage="review", status="done", message="проверка", input_tokens=30_000, output_tokens=0, schema_repairs=2)]
    run.status, run.error = "review", None
    return run


def test_summary_counts_exactly_what_the_table_shows():
    run = crafted_run()
    row = benchmark.summarize(run, "openai/gpt-6-sol", 166.6)
    assert row == {
        "model": "openai/gpt-6-sol", "status": "review", "error": None, "seconds": 167, "calls": 3, "schema_repairs": 3,
        "input_tokens": 180_000, "output_tokens": 10_000, "cost_usd": 0.46,
        "card_claims": len(run.input_card.claims), "card_questions": 3, "card_blocking_questions": 2,
        "tasks_main": 2, "tasks_bonus": 1, "hours_main": 9.75, "hours_brief": 10,
        "outcomes_total": 3, "outcomes_covered": 2,
        "blockers_code": 1, "warnings_code": 2, "blockers_model": 1, "warnings_model": 3,
        "code_findings": ["Код: блокер", "Код: замечание", "Код: ещё"],
        "task_titles": ["Задание 1 — 4.25 ч", "Задание 2 — 5.5 ч", "Задание 3 — 3.0 ч"], "skills_table": [],
    }


def test_failed_run_has_no_design_numbers():
    run = crafted_run()
    run.concept, run.review, run.input_card, run.status, run.error = None, None, None, "failed", "Шлюз модели ответил ошибкой 402."
    row = benchmark.summarize(run, "unknown/model", 5.4)
    assert (row["status"], row["error"], row["seconds"], row["cost_usd"]) == ("failed", "Шлюз модели ответил ошибкой 402.", 5, None)
    assert (row["card_claims"], row["card_questions"], row["card_blocking_questions"], row["tasks_bonus"]) == (None, None, None, None)
    assert (row["tasks_main"], row["hours_main"], row["outcomes_covered"], row["task_titles"]) == (0, 0, 0, [])


def test_cost_uses_input_and_output_prices_separately():
    assert benchmark.cost("openai/gpt-6-sol", 1_000_000, 0) == 2.0
    assert benchmark.cost("openai/gpt-6-sol", 0, 1_000_000) == 10.0
    assert benchmark.cost("openai/gpt-6-luna", 123_456, 7_890) == 0.0163


def test_table_has_every_column_in_order():
    row = benchmark.summarize(crafted_run(), "openai/gpt-6-sol", 1)
    header, rule, line = benchmark.table([row]).splitlines()
    assert header == ("| Модель | Итог | Время, с | Вход, ток. | Выход, ток. | Цена, $ | Починок JSON | Вопросов к входу | Заданий | "
                      "Часы заданий | ОР покрыто | Блокеры кода | Замечания кода | Блокеры ИИ | Замечания ИИ |")
    assert rule == "|" + "---|" * 15
    assert line == "| openai/gpt-6-sol | review | 1 | 180000 | 10000 | 0.46 | 3 | 3 | 2 | 9.75 | 2 | 1 | 2 | 1 | 3 |"


def test_example_request_fills_the_brief_like_the_form():
    request = benchmark.example_request(1, "zero")
    assert request.curriculum is not None
    brief, row = request.brief, request.curriculum.rows[0]
    assert request.selected_row_id == row.id and brief.start_level == "zero"
    assert (brief.title, brief.description, brief.outcomes, brief.skills) == (row.title, row.description, row.outcomes, row.skills)
    assert brief.tools == "интервью, карта эмпатии, опрос, персоны, сегментация" and brief.software == ""
    assert (brief.storytelling, brief.storytelling_type, brief.format, brief.hours) == (row.storytelling, "sjm", "individual", 14.0)
    assert brief.references == row.materials
    assert benchmark.example_request(2, "").brief.title == request.curriculum.rows[1].title
