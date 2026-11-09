"""Rules 0.6: the chat leads to the next stage, the place in the curriculum is computed, freshness has real links."""

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from project_generator_lab.concept import freshness
from project_generator_lab.concept.ingestion import parse_curriculum
from project_generator_lab.concept.input_semantics import program_map
from project_generator_lab.concept.models import FreshIdea, FreshItem, Question, SearchAnswer
from project_generator_lab.concept.route import next_step
from project_generator_lab.concept.thread import Proposal, ProposedChange, Thread
from project_generator_lab.core.intents import is_agreement, is_proceed
from project_generator_lab.tests.concept.test_dialogue import DialogueClient, api, start_thread  # noqa: F401 - fixture
from project_generator_lab.tests.concept.test_validation import new_run
from project_generator_lab.tests.concept.test_workbench import concept_fixture

PACKAGE = Path(__file__).resolve().parents[2]
STATIC = PACKAGE / "static"


def patch_proposal(proceed=False):
    return Proposal(reply="Зафиксирую комплект.", proceed=proceed, changes=[
        ProposedChange(field="output_package", value="Кейс и шаблон сметы", reason="Материалов нет", origin="request")])


def wait_for(api, run_id, status, attempts=50):  # noqa: F811 - the api fixture
    for _ in range(attempts):
        run = api.get(f"/api/runs/{run_id}").json()
        if run["status"] == status:
            return run
        asyncio.run(asyncio.sleep(0.02))
    raise AssertionError(f"{run_id}: {run['status']} instead of {status}")


# ---------- what counts as «go on» ----------

@pytest.mark.parametrize("text", ["Генерируй задания то", "Продолжаем", "давай дальше", "Дальше!", "да, дальше", "Поехали",
                                  "переходим к замыслу", "go"])
def test_short_requests_to_move_on_need_no_model(text):
    assert is_proceed(text) and not is_agreement(text)


@pytest.mark.parametrize("text", ["Что дальше?", "Материалов нет давай дальше пойдем", "дальше сделай 4 задания", "пойдёт",
                                  "Задание 2 заменить", "", "дальше нельзя без Excel"])
def test_questions_and_messages_with_content_go_to_the_model(text):
    assert not is_proceed(text)


# ---------- the route is computed from state ----------

def thread_for(run, **draft):
    return Thread(id=run.id, owner_id="local", runs=[run.id], draft=run.request.brief.model_copy(update=draft))


def test_next_step_follows_the_state_of_the_run():
    run = new_run()
    assert next_step(thread_for(run), run)["title"] == "Идёт разбор входов" and next_step(thread_for(run), run)["action"] is None
    run.status = "input_review"
    step = next_step(thread_for(run), run)
    assert (step["action"], step["button"]) == ("confirm_inputs", "Подготовить замысел")
    assert "входы → замысел → проверка" in str(step["route"])
    step = next_step(thread_for(run, tools="Excel"), run)
    assert step["action"] == "new_version" and "Инструменты и ПО" in str(step["text"])
    run.status = "review"
    assert next_step(thread_for(run), run)["action"] == "approve"
    run.status = "approved"
    assert next_step(thread_for(run), run)["action"] is None and "контент" in str(next_step(thread_for(run), run)["text"])
    run.status, run.error = "failed", "Шлюз ответил 503"
    step = next_step(thread_for(run), run)
    assert step["action"] == "resume" and "503" in str(step["text"])


def test_open_questions_are_counted_but_do_not_block():
    run = new_run()
    run.status = "input_review"
    from project_generator_lab.tests.concept.workflow_fixtures import input_card

    run.input_card = input_card(run.request)
    run.input_card.questions = [Question(question="Кто готовит кейс?", why="Комплект", blocking=True)]
    step = next_step(thread_for(run), run)
    assert "Вопросов без ответа: 1" in str(step["text"])
    # Both paths are named: change the requirements, or go on to the concept.
    assert "изменить или дополнить требования" in str(step["text"]) and step["edit_hint"]


def test_doubtful_claims_come_first_contradictions_then_gaps_then_assumptions():
    from project_generator_lab.concept.models import InputCard, InputClaim
    from project_generator_lab.tests.concept.workflow_fixtures import input_card

    run = new_run()
    run.status = "input_review"
    card = input_card(run.request)
    claim = lambda area, text, kind: InputClaim(area=area, text=text, kind=kind, evidence_ids=[], outcome_numbers=[])  # noqa: E731
    run.input_card = InputCard(**{**card.model_dump(), "claims": [
        claim("outcomes", "ОР заданы", "given"), claim("constraints", "Среда кампуса задана", "given"),
        claim("audience", "Средний уровень — предложение", "proposal"),
        claim("materials", "Кейс не указан", "unknown"), claim("program", "Порядок расходится с брифом", "conflict")]})
    checks = next_step(thread_for(run), run)["checks"]
    assert checks == [{"kind": "conflict", "area": "Программа", "text": "Порядок расходится с брифом"},
                      {"kind": "unknown", "area": "Материалы", "text": "Кейс не указан"},
                      {"kind": "proposal", "area": "Аудитория", "text": "Средний уровень — предложение"}]


# ---------- the chat moves on ----------

def test_generate_tasks_starts_the_concept_without_asking_the_model(api, monkeypatch):  # noqa: F811
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Генерируй задания то"}).json()
    assert not [c for c in model.calls if c[0] == "Proposal"]
    assert result["messages"][-1]["role"] == "assistant" and "Готовлю замысел" in result["messages"][-1]["text"]
    assert result["draft_changes"] == []
    wait_for(api, thread["runs"][0], "review")


def test_move_on_with_new_facts_waits_for_the_patch_decision(api, monkeypatch):  # noqa: F811
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r, patch_proposal(proceed=True)))
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Материалов нет давай дальше пойдем"}).json()
    reply = result["messages"][-1]
    assert reply["status"] == "pending" and any("Применить правки и продолжить" in n for n in reply["notes"])
    assert result["next_step"]["action"] == "apply_and_continue"
    assert api.get(f"/api/runs/{thread['runs'][0]}").json()["status"] == "input_review"
    # The button applies the proposal and starts a version that goes on to the concept by itself.
    moved = api.post(f"/api/threads/{thread['id']}/advance", json={"action": "apply_and_continue"}).json()
    assert [m for m in moved["messages"] if m["status"]][-1]["status"] == "applied" and len(moved["runs"]) == 2
    assert [m["role"] for m in moved["messages"][-3:]] == ["methodologist", "system", "assistant"]
    child = wait_for(api, moved["runs"][1], "review")
    assert child["auto_continue"] and child["input_approved_at"] and child["concept"]
    assert any(e["stage"] == "inputs_approval" and "попросил продолжить" in e["message"] for e in child["events"])


def test_model_proceed_without_edits_moves_on_in_the_same_reply(api, monkeypatch):  # noqa: F811
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r, Proposal(reply="Материалы подготовит генератор.", proceed=True)))
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Материалов нет, идём дальше"}).json()
    assert "Материалы подготовит генератор." in result["messages"][-1]["text"] and "Готовлю замысел" in result["messages"][-1]["text"]
    wait_for(api, thread["runs"][0], "review")


def test_an_important_new_question_stops_the_automatic_continuation(api, monkeypatch):  # noqa: F811
    class FreshQuestions(DialogueClient):
        # Every interpretation gets its own question objects: demo policy clears blocking on the ones it shows.
        async def generate(self, schema, system, payload):
            self.questions = [Question(question="Кто готовит кейс?", why="Комплект", blocking=True)]
            return await super().generate(schema, system, payload)

    thread, _ = start_thread(api, monkeypatch, lambda r: FreshQuestions(r, patch_proposal()))
    thread = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Комплект: кейс и шаблон"}).json()
    moved = api.post(f"/api/threads/{thread['id']}/advance", json={"action": "apply_and_continue"}).json()
    child = wait_for(api, moved["runs"][1], "input_review")
    assert child["auto_continue"] and not child["input_approved_at"] and not child["concept"]


def test_approval_stays_a_button_and_stale_buttons_are_refused(api, monkeypatch):  # noqa: F811
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    run_id = thread["runs"][0]
    stale = api.post(f"/api/threads/{thread['id']}/advance", json={"action": "approve"})
    assert stale.status_code == 409 and "Состояние изменилось" in stale.json()["detail"]
    assert api.post(f"/api/threads/{thread['id']}/advance", json={"action": "confirm_inputs"}).status_code == 200
    wait_for(api, run_id, "review")
    said = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "дальше"}).json()
    assert "по кнопке" in said["messages"][-1]["text"] and api.get(f"/api/runs/{run_id}").json()["status"] == "review"
    approved = api.post(f"/api/threads/{thread['id']}/advance", json={"action": "approve"}).json()
    assert approved["next_step"]["title"] == "Замысел согласован"
    said, answer = approved["messages"][-2:]
    assert (said["role"], said["text"]) == ("methodologist", "Согласовать замысел")
    assert answer["role"] == "assistant" and "согласован" in answer["text"]


def test_agreement_without_a_proposal_points_to_the_next_step(api, monkeypatch):  # noqa: F811
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "ок"}).json()
    assert "«Подготовить замысел»" in result["messages"][-1]["text"]


# ---------- place in the curriculum ----------

PLAN = (
    "Тематический блок;Название проекта;Навыки;Часы\n"
    "Б1;Старт;Excel, Анализ;4\n"
    "Б2;План работ;Планирование;6\n"
    ';Смета;"Excel; Планирование, Бюджет";12\n'
    "Б3;Переговоры;бюджет;8\n"
)


def test_program_map_counts_block_neighbours_hours_and_skills():
    run = new_run()
    run.request.curriculum = parse_curriculum("plan.csv", PLAN.encode())
    run.request.selected_row_id = run.request.curriculum.rows[2].id
    m = program_map(run.request)
    assert m is not None
    assert (m["block"], m["position_in_block"], m["block_hours"], m["program_hours"], m["hours_before"]) == ("Б2", 2, 18.0, 30.0, 10.0)
    assert m["block_projects"] == [{"title": "План работ", "hours": 6.0, "current": False}, {"title": "Смета", "hours": 12.0, "current": True}]
    assert m["previous_project"] == {"title": "План работ", "block": "Б2"} and m["next_project"] == {"title": "Переговоры", "block": "Б3"}
    assert m["skills_practised_before"] == ["Excel", "Планирование"] and m["skills_new"] == ["Бюджет"]
    assert m["skills_used_later"] == [{"skill": "Бюджет", "projects": ["Переговоры"], "count": 1}]
    run.request.selected_row_id = run.request.curriculum.rows[0].id
    first = program_map(run.request)
    assert first and first["previous_project"] is None and first["hours_before"] is None
    run.request.selected_row_id = "missing"
    assert program_map(run.request) is None


def test_models_and_the_chat_receive_the_program_map(api, monkeypatch):  # noqa: F811
    from project_generator_lab.concept.planning import context_payload

    run = new_run()
    run.request.curriculum = parse_curriculum("plan.csv", PLAN.encode())
    run.request.selected_row_id = run.request.curriculum.rows[2].id
    state = json.loads(json.dumps(context_payload(run)["computed_state"], ensure_ascii=False))
    assert state["program_map"]["skills_new"] == ["Бюджет"]
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    assert "program_map" in thread and thread["next_step"]["action"] == "confirm_inputs"


# ---------- freshness through a search model ----------

def answer():
    return SearchAnswer(summary="Два пункта требуют внимания.", items=[
        FreshItem(topic="Reserved instances", where="задание 2", status="outdated", finding="Модель изменилась в 2026", suggestion="Savings plans", source_numbers=[2, 2]),
        FreshItem(topic="Excel-шаблон сметы", status="current", finding="Шаблоны доступны", source_numbers=[7]),
    ], ideas=[FreshIdea(idea="Тариф облака", why="Для задания 2", source_numbers=[1, 9])])


def test_only_links_returned_by_the_provider_reach_the_report():
    items, ideas, dropped = freshness.attach_sources(answer(), ["https://a.example/1", "https://b.example/2"])
    assert items[0].sources == ["https://b.example/2"] and items[0].status == "outdated"
    # A verdict whose only source was invented becomes «unclear».
    assert items[1].sources == [] and items[1].status == "unclear"
    assert ideas[0].sources == ["https://a.example/1"] and dropped == 2
    assert freshness.citations({"citations": ["https://x.example", "javascript:alert(1)"]}) == ["https://x.example"]
    assert freshness.citations({"search_results": [{"url": "https://y.example"}, {"title": "без ссылки"}]}) == ["https://y.example"]
    assert freshness.citations({}) == []


def test_search_sees_the_concept_but_not_the_chat_or_materials():
    run = new_run()
    run.concept = concept_fixture()
    payload = json.loads(json.dumps(freshness.search_payload(run, date(2026, 9, 29)), ensure_ascii=False))
    assert payload["today"] == "2026-09-29" and payload["tasks"][0]["title"] == "Выделить факты"
    assert set(payload) == {"today", "title", "description", "outcomes", "tools", "storytelling", "tasks", "material_gaps"}


def test_freshness_endpoint_saves_the_report_after_the_concept(api, monkeypatch):  # noqa: F811
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    run_id = thread["runs"][0]
    assert api.post(f"/api/runs/{run_id}/freshness").status_code == 409
    api.post(f"/api/threads/{thread['id']}/advance", json={"action": "confirm_inputs"})
    wait_for(api, run_id, "review")

    async def fake_search(system, payload):
        assert "актуал" in system and "Разбор интервью" in payload
        return answer(), ["https://a.example/1", "https://b.example/2"], {"model": "perplexity/sonar-pro", "elapsed_ms": 5, "input_tokens": 1, "output_tokens": 2}

    monkeypatch.setattr(freshness, "search", fake_search)
    run = api.post(f"/api/runs/{run_id}/freshness").json()
    assert run["freshness"]["model"] == "perplexity/sonar-pro" and run["freshness"]["dropped_references"] == 2
    assert api.get(f"/api/runs/{run_id}").json()["freshness"]["items"][0]["sources"] == ["https://b.example/2"]
    assert api.get("/api/config").json()["search_model"] == "perplexity/sonar-pro"


def test_search_failure_is_a_readable_error(api, monkeypatch):  # noqa: F811
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    run_id = thread["runs"][0]
    api.post(f"/api/threads/{thread['id']}/advance", json={"action": "confirm_inputs"})
    wait_for(api, run_id, "review")

    async def failing(system, payload):
        raise freshness.ModelUnavailable("Шлюз поисковой модели ответил ошибкой 404.")

    monkeypatch.setattr(freshness, "search", failing)
    response = api.post(f"/api/runs/{run_id}/freshness")
    assert response.status_code == 502 and "404" in response.json()["detail"]


def test_search_model_is_configurable(monkeypatch):
    monkeypatch.delenv("PROJECT_LAB_SEARCH_MODEL", raising=False)
    assert freshness.search_model() == "perplexity/sonar-pro"
    monkeypatch.setenv("PROJECT_LAB_SEARCH_MODEL", " perplexity/sonar ")
    assert freshness.search_model() == "perplexity/sonar"


# ---------- the interface ----------

def test_chat_has_no_field_filter_and_the_brief_is_a_drawer():
    html, js, css = ((STATIC / name).read_text(encoding="utf-8") for name in ("chat.html", "chat.js", "chat.css"))
    assert "targetField" not in html and "target_field" not in js
    assert "grid-template-columns: 264px minmax(0, 1fr);" in css and "420px;" not in css.split(".panel {")[0]
    assert "api/threads/${S.thread.id}/advance" in js and "nextStepMessage" in js and "programMapMessage" in js and "suggestions" in js
    assert "rel: 'noopener noreferrer'" in js


def test_outcomes_are_named_by_wording_and_reply_templates_append():
    js = (STATIC / "chat.js").read_text(encoding="utf-8")
    assert "`ОР ${task.outcome_numbers" not in js and "`ОР ${o.outcome_number}`" not in js
    assert "function expandOutcomes" in js and "function addToComposer" in js
    for name in ("interpreter", "designer", "reviewer", "dialogue"):
        prompt = (PACKAGE / "concept" / "prompts" / f"{name}.md").read_text(encoding="utf-8")
        assert "формулировкой" in prompt, name


def test_the_models_wish_to_move_on_is_not_obeyed_without_the_methodologists_words(api, monkeypatch):  # noqa: F811
    # A model can be steered by text from files or found pages; the methodologist's message cannot. Moving on applies
    # edits and starts a paid run, so it follows his words only (audit 30.09.2026, execution-layer guardrail).
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r, Proposal(reply="Понял про проверку.", proceed=True)))
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Проверку делаем парами, пир смотрит вместе с автором"}).json()
    last = result["messages"][-1]
    assert "Готовлю замысел" not in last["text"] and api.get(f"/api/runs/{thread['runs'][0]}").json()["input_approved_at"] is None
    assert last["notes"] == ["Перехожу дальше только по вашему слову: кнопка «Подготовить замысел» или напишите «дальше»."]
    # A question never moves on either, even with the flag.
    question = Proposal(kind="question", reply="Потому что так задан УП.", proceed=True)
    thread2, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r, question))
    result = api.post(f"/api/threads/{thread2['id']}/messages", json={"text": "Почему четыре задания? давай дальше"}).json()
    assert result["messages"][-1]["notes"] == [] and "Готовлю замысел" not in result["messages"][-1]["text"]


@pytest.mark.parametrize("text, expected", [
    ("Материалов нет, идём дальше", True), ("Задание 2 ок, генерируй дальше", True), ("всё верно и переходим к замыслу", True),
    ("дальше сделай задание короче", False), ("Почему дальше идёт теория?", False), ("не надо дальше", False),
    ("Проверку делаем парами", False),
    # Independent review 30.09.2026: idioms, questions and «first X» are not a request to move on.
    ("Цели распределены со второго проекта и далее", False), ("Добавь роли, сроки и так далее", False),
    ("дальше, но сначала проверь ссылки", False), ("Далее — посмотрим", False), ("А дальше?", False),
    ("как запустить?", False), ("Материалов больше не нужно, давай дальше", True), ("Материалов пока нет, давай дальше", True),
])
def test_moving_on_is_read_in_any_clause_of_the_methodologists_message(text, expected):
    from project_generator_lab.core.intents import mentions_proceed

    assert mentions_proceed(text) is expected


# ---------- ссылки проверки актуальности открываются, а не принимаются на слово ----------

class FakeOpener:
    """Открывает то, что ему велено; остальное «не открылось». Считает вызовы."""

    def __init__(self, alive: list[str]):
        self.alive = alive
        self.calls = 0

    async def __call__(self, urls, **kwargs):
        from project_generator_lab.core.web import Page

        self.calls += 1
        return [Page(url=u, title="", text="") for u in urls if u in self.alive]


def run_check(opener, sources):
    async def fake_search(system, payload):
        return answer(), sources, {"model": "perplexity/sonar-pro", "elapsed_ms": 5, "input_tokens": 1, "output_tokens": 2}

    run = new_run()
    run.concept = concept_fixture()
    return asyncio.run(freshness.check(run, opener=opener, search_for=fake_search))


def test_a_source_that_does_not_open_is_named_in_the_report():
    """Модуль контента каждую ссылку открывает, а проверка актуальности принимала на слово.

    Методолог видел в отчёте ссылки, которые не открываются. Теперь они названы отдельно.
    """
    sources = ["https://a.example/1", "https://b.example/2"]
    opener = FakeOpener(alive=["https://a.example/1"])

    report = run_check(opener, sources)

    assert report.unreachable == ["https://b.example/2"]
    assert report.sources == sources, "ссылку не выбрасываем: замечание рекомендательное"
    assert opener.calls == 1


def test_every_source_opening_leaves_nothing_to_report():
    sources = ["https://a.example/1", "https://b.example/2"]

    report = run_check(FakeOpener(alive=sources), sources)

    assert report.unreachable == []


def test_without_sources_nothing_is_opened():
    """Пустой список — не повод ходить в сеть."""
    opener = FakeOpener(alive=[])

    report = run_check(opener, [])

    assert report.unreachable == [] and opener.calls == 0
