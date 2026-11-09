"""Dialogue edits the brief explicitly; workload, CSV mapping and stopping are computed by code."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.concept import runs as api_module
from project_generator_lab.concept.ingestion import InputError, parse_curriculum
from project_generator_lab.concept.models import Brief, Concept, GroundedReview, InputCard, Question, TaskWorkload
from project_generator_lab.concept.planning import context_payload, prepare
from project_generator_lab.concept.thread import AnswerCheck, Proposal, ProposedChange
from project_generator_lab.concept.workload import WorkloadQuery, budget, check
from project_generator_lab.core.access import current_owner
from project_generator_lab.core.llm import CallMetrics, ModelUnavailable
from project_generator_lab.tests.concept.test_validation import WorkflowClient, new_run
from project_generator_lab.tests.concept.test_workbench import concept_fixture, request_fixture
from project_generator_lab.tests.concept.workflow_fixtures import grounded_review, input_card

CHAT_TEXT = "Кейс готовит генератор, сдают таблицу договорённостей"


class DialogueClient(WorkflowClient):
    """Workflow fixtures plus a scripted dialogue answer; records every payload it receives."""

    def __init__(self, request, proposal=None, questions=()):
        super().__init__(request)
        self.proposal = proposal or Proposal(reply="Предлагаю две правки.", changes=[])
        self.questions = list(questions)
        self.check = AnswerCheck()  # the narrow second look at open questions; finds nothing unless a test says so

    async def generate(self, schema, system, payload):
        if schema is Proposal:
            self.calls.append((schema.__name__, system, json.loads(payload)))
            return self.proposal, CallMetrics("dialogue-test", 1, 10, 10)
        if schema is AnswerCheck:
            self.calls.append((schema.__name__, system, json.loads(payload)))
            if isinstance(self.check, Exception):
                raise self.check
            return self.check, CallMetrics("dialogue-test", 1, 5, 5)
        response, metrics = await super().generate(schema, system, payload)
        if schema is InputCard:
            response.questions = self.questions
        return response, metrics


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(api_module, "model_configuration", lambda: ("test-key", "unused", "test"))
    with TestClient(app_module.app) as client:
        yield client


def start_thread(api, monkeypatch, client_factory):
    request = request_fixture()
    model = client_factory(request)
    monkeypatch.setattr(api_module, "ModelClient", lambda: model)
    response = api.post("/api/runs", content=request.model_dump_json(), headers={"Content-Type": "application/json"})
    assert response.status_code == 202
    run_id = response.json()["id"]
    assert api.get(f"/api/runs/{run_id}").json()["status"] == "input_review"
    thread = api.post("/api/threads", json={"run_id": run_id}).json()
    return thread, model


def answer_proposal():
    return Proposal(reply="Внесу ответ в задание.", changes=[
        ProposedChange(field="output_package", value="Кейс переговоров и две роли", reason="Ответ на вопрос 1", origin="answer", question_numbers=[1]),
        ProposedChange(field="tools", value="Таблицы", reason="Сдача таблицы требует инструмента", origin="proposal"),
        ProposedChange(field="hours", value=-3, reason="Ошибочное значение", origin="request"),
    ])


def test_message_becomes_validated_patches_and_invalid_values_are_reported(api, monkeypatch):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, answer_proposal(), [Question(question="Кто готовит кейс?", why="Комплект", blocking=True)]))
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": CHAT_TEXT}).json()
    user, reply = result["messages"][-2:]
    assert user["role"] == "methodologist" and user["text"] == CHAT_TEXT
    assert reply["status"] == "pending"
    assert [p["field"] for p in reply["patches"]] == ["output_package", "tools"]
    assert reply["patches"][0]["before"] == "README и чек-лист" and reply["patches"][0]["question_numbers"] == [1]
    assert any("Трудоёмкость" in note for note in reply["notes"])
    assert result["pending_fields"] == ["tools", "output_package"] or set(result["pending_fields"]) == {"tools", "output_package"}
    payload = model.calls[-1][2]
    assert payload["questions"][0]["number"] == 1 and payload["draft"]["title"] == "Разбор интервью"


def test_apply_reject_revert_and_new_version_keep_chat_out_of_design(api, monkeypatch):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, answer_proposal()))
    thread = api.post(f"/api/threads/{thread['id']}/messages", json={"text": CHAT_TEXT}).json()
    message = thread["messages"][-1]
    applied = api.post(f"/api/threads/{thread['id']}/messages/{message['id']}/decision", json={"verdict": "apply", "values": {"1": "Google Таблицы"}}).json()
    assert applied["draft"]["output_package"] == "Кейс переговоров и две роли"
    assert applied["draft"]["tools"] == "Google Таблицы" and applied["messages"][-1]["patches"][1]["edited"]
    assert set(applied["draft_changes"]) == {"output_package", "tools"}
    assert api.post(f"/api/threads/{thread['id']}/messages/{message['id']}/decision", json={"verdict": "apply"}).status_code == 409
    reverted = api.post(f"/api/threads/{thread['id']}/messages/{message['id']}/decision", json={"verdict": "revert"}).json()
    assert reverted["draft_changes"] == [] and reverted["messages"][-1]["status"] == "pending"
    api.post(f"/api/threads/{thread['id']}/messages/{message['id']}/decision", json={"verdict": "apply"})
    version = api.post(f"/api/threads/{thread['id']}/versions")
    assert version.status_code == 200
    thread = version.json()
    assert len(thread["runs"]) == 2 and thread["draft_changes"] == []
    child = api.get(f"/api/runs/{thread['runs'][1]}").json()
    assert child["request"]["parent_run_id"] == thread["runs"][0]
    assert {c["location"] for c in child["changes"]} == {"brief.output_package", "brief.tools"}
    # Only the dialogue model saw the chat; interpretation of the new version received the brief alone.
    design_payloads = [json.dumps(call[2], ensure_ascii=False) for call in model.calls if call[0] != "Proposal"]
    assert design_payloads and not any(CHAT_TEXT in payload for payload in design_payloads)
    assert api.post(f"/api/threads/{thread['id']}/versions").status_code == 409


def test_conflicting_manual_edit_blocks_stale_patch_and_is_logged(api, monkeypatch):
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r, answer_proposal()))
    thread = api.post(f"/api/threads/{thread['id']}/messages", json={"text": CHAT_TEXT}).json()
    message = thread["messages"][-1]
    edited = api.post(f"/api/threads/{thread['id']}/fields", json={"field": "tools", "value": "Excel"}).json()
    assert edited["messages"][-1]["patches"][0]["origin"] == "manual" and edited["draft"]["tools"] == "Excel"
    # The proposal touching the edited field could never be applied any more: it is withdrawn, not left as a dead end
    # that «Применить правки и продолжить» would fail on (audit 30.09.2026).
    withdrawn = next(m for m in edited["messages"] if m["id"] == message["id"])
    assert withdrawn["status"] == "rejected" and "Заменено следующим предложением." in withdrawn["notes"]
    assert edited["messages"][-1]["notes"][0].startswith("Ожидавшее предложение (")
    assert edited["next_step"]["action"] != "apply_and_continue"
    conflict = api.post(f"/api/threads/{thread['id']}/messages/{message['id']}/decision", json={"verdict": "apply"})
    assert conflict.status_code == 409
    # Nor can it be brought back to «waiting»: it is stale.
    back = api.post(f"/api/threads/{thread['id']}/messages/{message['id']}/decision", json={"verdict": "revert"})
    assert back.status_code == 409 and "изменилось" in back.json()["detail"]
    assert api.post(f"/api/threads/{thread['id']}/fields", json={"field": "hours", "value": 0}).status_code == 422


def test_model_failure_keeps_methodologist_message_and_threads_are_owner_scoped(api, monkeypatch):
    class FailingDialogue(DialogueClient):
        async def generate(self, schema, system, payload):
            if schema is Proposal:
                raise ModelUnavailable("провайдер недоступен")
            return await super().generate(schema, system, payload)

    thread, _ = start_thread(api, monkeypatch, lambda r: FailingDialogue(r))
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": CHAT_TEXT}).json()
    assert [m["role"] for m in result["messages"]] == ["methodologist", "system"]
    token = current_owner.set("someone-else")
    try:
        assert api.get(f"/api/threads/{thread['id']}").status_code == 404
    finally:
        current_owner.reset(token)


def test_csv_report_names_missing_and_unmapped_columns():
    data = "Название проекта;Результаты проекта;Длительность;Комментарий\nПроект;Делать выводы;4;x\n".encode()
    result = parse_curriculum("plan.csv", data)
    assert "Образовательные результаты: колонка не найдена" in result.missing_fields
    assert "Трудоёмкость: колонка не найдена" in result.missing_fields
    assert result.unmapped_columns == ["Результаты проекта", "Длительность", "Комментарий"]
    assert result.columns["title"] == ["Название проекта"]
    with pytest.raises(InputError, match="В файле: «Проект»"):
        parse_curriculum("plan.csv", "Проект;ОР\nА;Б\n".encode())


def test_workload_budget_and_checks_are_arithmetic_not_model_opinion():
    plan = budget(WorkloadQuery(hours=12, tasks_count=10, start_level="zero", tools="Git"))
    assert (plan.tasks_min, plan.tasks_max, plan.tasks_recommended) == (2, 8, 3)  # 12 h / 3,5 h a task (29.10.2026)
    assert plan.needs_setup and plan.overhead_min_share == 0.4 and plan.notes
    brief = Brief(title="Т", description="Описание проекта", audience="", outcomes="Делать", hours=12, start_level="zero", tools="Git")
    concept = concept_fixture()
    concept.tasks[0].hours = 12
    concept.tasks[0].workload = TaskWorkload(reading=1, setup=0.25, doing=9.5, debugging=1, submission=0.25)
    messages = [f.message for f in check(concept, brief, budget(brief))]
    assert any("вне нормы" in m for m in messages)
    assert any("норма для уровня «нулевой»" in m for m in messages)
    assert any("на настройку" in m for m in messages)
    concept.tasks[0].workload = None
    assert any(f.severity == "blocker" and "нет разбивки" in f.message for f in check(concept, brief, budget(brief)))


def test_models_receive_position_and_budget_computed_by_code():
    run = new_run()
    run.request.curriculum = parse_curriculum("plan.csv", "Тематический блок;Название проекта\nБ1;А\n;Б\nБ2;В\n".encode())
    run.request.selected_row_id = run.request.curriculum.rows[1].id
    state = json.loads(json.dumps(context_payload(run)["computed_state"], ensure_ascii=False))
    assert state["program_position"]["position"] == 2 and state["program_position"]["blocks_total"] == 2
    assert state["workload_budget"]["total_hours"] == 4


def test_stop_keeps_saved_work_and_resume_skips_finished_stages(api, monkeypatch):
    class Hanging(DialogueClient):
        async def generate(self, schema, system, payload):
            await asyncio.sleep(3600)

    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    run_id = thread["runs"][0]
    assert api.post(f"/api/runs/{run_id}/cancel").status_code == 409
    monkeypatch.setattr(api_module, "ModelClient", lambda: Hanging(request_fixture()))
    assert api.post(f"/api/runs/{run_id}/confirm-inputs").status_code == 202
    stopped = api.post(f"/api/runs/{run_id}/cancel").json()
    assert stopped["status"] == "interrupted" and stopped["input_card"] and "Остановлено" in stopped["error"]

    # Design finished before the stop: resuming must repeat only the review.
    run = new_run()
    model = WorkflowClient(run.request)
    asyncio.run(prepare(run, api_module.save_run, model))
    run.input_approved_at, run.status = "2026-09-26T10:00:00+00:00", "interrupted"
    run.concept = concept_fixture()
    api_module.save_run(run)
    monkeypatch.setattr(api_module, "ModelClient", lambda: model)
    assert api.post(f"/api/runs/{run.id}/resume").status_code == 202
    assert api.get(f"/api/runs/{run.id}").json()["status"] == "review"
    assert [c[0] for c in model.calls] == ["InputCard", "GroundedReview"]


def test_reviewer_can_run_on_a_separate_model(monkeypatch):
    class Recording(WorkflowClient):
        async def generate(self, schema, system, payload):
            self.calls.append((schema.__name__, system, json.loads(payload)))
            if schema is GroundedReview:
                return grounded_review(), CallMetrics("reviewer-family", 1, 1, 1)
            if schema is Concept:
                return concept_fixture(), CallMetrics("designer", 1, 1, 1)
            return input_card(self.request), CallMetrics("designer", 1, 1, 1)

    run = new_run()
    main, reviewer = Recording(run.request), Recording(run.request)
    asyncio.run(prepare(run, lambda r: None, main, reviewer))
    run.input_approved_at, run.status = "2026-09-26T10:00:00+00:00", "running"
    asyncio.run(prepare(run, lambda r: None, main, reviewer))
    assert [c[0] for c in main.calls] == ["InputCard", "Concept"]
    assert [c[0] for c in reviewer.calls] == ["GroundedReview"]
    assert run.events[-1].model == "reviewer-family"


def test_a_newer_proposal_for_the_same_field_replaces_the_older_and_the_model_sees_both(api, monkeypatch):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    model.proposal = Proposal(reply="Запишу проверку.", changes=[
        ProposedChange(field="checking", value="Пир сверяет выводы с цитатами", reason="Просьба", origin="request")])
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "проверка — по цитатам"})
    model.proposal = Proposal(reply="Уточнил проверку.", changes=[
        ProposedChange(field="checking", value="Пир сверяет каждый вывод с двумя цитатами", reason="Уточнение", origin="request")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "нет, с двумя цитатами"}).json()
    proposals = [m for m in view["messages"] if m["patches"]]
    assert [m["status"] for m in proposals] == ["rejected", "pending"]
    assert proposals[0]["notes"][-1] == "Заменено следующим предложением." and "заменено этим" in proposals[1]["notes"][0]
    # The second call saw the first proposal waiting, with the value it proposed.
    history = model.calls[-1][2]["recent_messages"]
    assert history[-1]["status"] == "pending" and history[-1]["changes"] == [{"field": "checking", "after": "Пир сверяет выводы с цитатами"}]
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "пойдёт"}).json()
    assert view["draft"]["checking"] == "Пир сверяет каждый вывод с двумя цитатами"


def test_an_answer_that_changes_no_field_is_still_written_into_the_brief(api, monkeypatch):
    """Production, 30.09.2026: «Вопрос 1: да» confirmed the start level, changed no field and left no trace, so the next
    version asked the same question again. Now the answer goes to «Уточнения методолога» with its question."""
    questions = [Question(question="Достаточно ли базового уровня без дополнительных входных умений?", why="Уровень", blocking=False),
                 Question(question="Нужен ли отдельный шаблон бэклога?", why="Комплект", blocking=False)]
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=questions))
    model.proposal = Proposal(reply="Шаблон не нужен.", changes=[
        ProposedChange(field="output_package", value="Кейс, ориентиры и критерии проверки", reason="Ответ на вопрос 2", origin="answer", question_numbers=[2])])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Вопрос 1: да\nВопрос 2: не нужен"}).json()
    patches = view["messages"][-1]["patches"]
    assert [(p["field"], p["question_numbers"]) for p in patches] == [("output_package", [2]), ("notes", [1])]
    assert patches[1]["after"].endswith("Ответ на вопрос «Достаточно ли базового уровня без дополнительных входных умений?»: да")
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "пойдёт"}).json()
    assert "Ответ на вопрос «Достаточно ли базового уровня" in view["draft"]["notes"]
    # Both questions now count as answered: none is offered again.
    assert "Вопросов без ответа" not in view["next_step"]["text"]


def test_a_free_form_answer_is_recorded_when_the_model_says_which_question_it_answers(api, monkeypatch):
    from project_generator_lab.concept.thread import Answered

    questions = [Question(question="Достаточно ли базового уровня без дополнительных входных умений?", why="Уровень", blocking=False)]
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=questions))
    # No «Вопрос 1:» in the text: code cannot read the number, the model names it, code writes the answer down.
    model.proposal = Proposal(reply="Понял, уровень оставляю.", answered=[Answered(number=1, answer="базового уровня хватит, доп. умений не нужно")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "по уровню: базового хватит, ничего сверху не требуем"}).json()
    [patch] = view["messages"][-1]["patches"]
    assert (patch["field"], patch["question_numbers"], patch["origin"]) == ("notes", [1], "answer")
    assert patch["after"].endswith("»: базового уровня хватит, доп. умений не нужно")
    # A number the card does not have is ignored rather than written down.
    model.proposal = Proposal(reply="Ок.", answered=[Answered(number=7, answer="что-то")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "и ещё про седьмой"}).json()
    assert view["messages"][-1]["patches"] == []


PROGRAM_QUESTION = Question(question="Каковы утверждённые название и общие образовательные результаты программы?",
                            why="Они не заданы; по карте УП можно описать последовательность проектов, но нельзя приписать программе "
                                "отсутствующие формулировки результатов.", blocking=False)


def test_a_request_to_explain_a_question_with_its_text_pasted_is_not_an_answer(api, monkeypatch):
    from project_generator_lab.concept.thread import Answered

    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[PROGRAM_QUESTION]))
    # As in the local run of 30.09: the methodologist pasted why the question is asked and asked to explain it; the
    # model took the pasted text for his answer.
    text = f"{PROGRAM_QUESTION.why}\n\nПоясни этот вопрос я не совсем понимаю"
    model.proposal = Proposal(reply="Вы пояснили, что формулировок нет: зафиксирую.", answered=[Answered(number=1, answer=PROGRAM_QUESTION.why)])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": text}).json()
    last = view["messages"][-1]
    assert last["patches"] == [] and last["status"] is None
    assert last["notes"][0].startswith("Ответ на вопрос 1 не записан")
    # The narrow second look is not asked either: a request to explain answers nothing.
    assert [c[0] for c in model.calls if c[0] == "AnswerCheck"] == []
    # The card's wording is a quote even without a request to explain.
    model.proposal = Proposal(reply="Ок.", answered=[Answered(number=1, answer="Каковы утверждённые название и общие образовательные результаты программы")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Каковы утверждённые название и общие ОР программы — вот это"}).json()
    assert view["messages"][-1]["patches"] == []


def test_the_second_look_records_an_answer_the_first_call_missed_and_asks_only_about_open_questions(api, monkeypatch):
    from project_generator_lab.concept.thread import Answered

    level = Question(question="Достаточно ли базового уровня без дополнительных входных умений?", why="Уровень", blocking=False)
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[PROGRAM_QUESTION, level]))
    # The first call understood the answer but left answered empty — what gpt-5.4-mini did on the server 30.09.
    model.proposal = Proposal(reply="Предлагаю зафиксировать базовый уровень.")
    model.check = AnswerCheck(answered=[Answered(number=2, answer="базового уровня хватит"), Answered(number=9, answer="?")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "по уровню: базового хватит, ничего сверху не требуем"}).json()
    [patch] = view["messages"][-1]["patches"]
    assert (patch["field"], patch["question_numbers"]) == ("notes", [2])
    assert patch["after"].endswith("»: базового уровня хватит")
    [(_, prompt, payload)] = [c for c in model.calls if c[0] == "AnswerCheck"]
    assert [q["number"] for q in payload["questions"]] == [1, 2] and "Не ответ" in prompt
    # Tokens of both calls are counted on the message.
    assert (view["messages"][-1]["input_tokens"], view["messages"][-1]["output_tokens"]) == (15, 15)
    # A question the first call already named is not asked again.
    model.proposal = Proposal(reply="Ок.", answered=[Answered(number=1, answer="формулировок нет, не выдумывать")])
    model.calls.clear()
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "ОР программы нет, не выдумывай их"})
    # An answer received in a pending edit is not a reason to ask the question a second time.
    assert not [c for c in model.calls if c[0] == "AnswerCheck"]


def test_a_question_changes_nothing_and_an_off_topic_message_gets_a_way_back_to_the_project(api, monkeypatch):
    from project_generator_lab.concept.thread import Answered

    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[PROGRAM_QUESTION]))
    model.proposal = Proposal(kind="question", reply="P2P-проверка — это…", answered=[Answered(number=1, answer="нет")],
                              changes=[ProposedChange(field="checking", value="Пир", reason="—", origin="request")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Как в Школе 21 устроена P2P-проверка?"}).json()
    last = view["messages"][-1]
    assert (last["patches"], last["kind"]) == ([], "question")
    assert last["notes"][0] == "Сообщение понято как вопрос: правок не предлагаю. Если нужно изменить задание, напишите, что поменять."
    # A question is not an answer to the card's questions either: the narrow second look is not asked.
    assert not [c for c in model.calls if c[0] == "AnswerCheck"]
    # Off topic, the model's «answer» to question 1 is cleared too: nothing of it reaches the brief.
    model.proposal = Proposal(kind="off_topic", reply="Я помогаю только с подготовкой этого проекта.", proceed=True,
                              answered=[Answered(number=1, answer="нет")],
                              changes=[ProposedChange(field="notes", value="анекдот", reason="—", origin="request")])
    model.calls.clear()
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "расскажи анекдот"}).json()
    last = view["messages"][-1]
    assert last["patches"] == [] and last["kind"] == "off_topic"
    assert last["text"].startswith("Я помогаю только с подготовкой этого проекта.\n\nВернёмся к проекту: ")
    # «proceed» from an off-topic answer is not obeyed: no transition text, no new version, no second look.
    assert last["text"].count("\n\n") == 1 and len(view["runs"]) == 1
    assert [c[0] for c in model.calls] == ["Proposal"]
    assert view["draft"]["notes"] == ""


LEVEL_QUESTION = Question(question="Достаточно ли базового уровня без дополнительных входных умений?", why="Уровень", blocking=False)


def test_what_counts_as_an_answer_is_decided_by_code_at_the_edges():
    from project_generator_lab.concept.answers import asks_to_explain, quotes_card

    card: dict[str, object] = {"question": "Каков уровень?", "why": "Уровень ещё не задан точно"}
    # Four of five words are the card's (with «ё» read as «е»): a quote. Three of five: the methodologist's own words.
    assert quotes_card("уровень еще задан точно вовсе", card)
    assert not quotes_card("уровень еще задан совсем вовсе", card)
    # A request to explain is short: twenty words of his own at most.
    assert asks_to_explain("поясни " + "слово " * 19, [])
    assert not asks_to_explain("поясни " + "слово " * 20, [])


def test_a_request_to_explain_is_not_an_answer_even_when_the_model_words_it_as_one(api, monkeypatch):
    from project_generator_lab.concept.thread import Answered

    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[PROGRAM_QUESTION]))
    model.proposal = Proposal(reply="Поясняю.", answered=[Answered(number=1, answer="у программы нет своих ОР")])
    # The pasted «why» is not his words: without it the message is a short request to explain.
    ask = "Поясни этот вопрос, я не совсем понимаю, что тут от меня нужно"
    for text in (f"{PROGRAM_QUESTION.why}\n\n{ask}", f"{PROGRAM_QUESTION.question}\n{ask} сейчас"):
        view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": text}).json()
        assert view["messages"][-1]["patches"] == [], text
    # The card's «why» given back as the answer, without any request to explain: a quote, not an answer.
    model.proposal = Proposal(reply="Ок.", answered=[Answered(number=1, answer=PROGRAM_QUESTION.why)])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": f"{PROGRAM_QUESTION.why} — вот так"}).json()
    assert view["messages"][-1]["patches"] == []
    # Labelled a question, with an «answer» and no edits: still nothing is written.
    model.proposal = Proposal(kind="question", reply="Это про ОР программы.", answered=[Answered(number=1, answer="нет")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "А это вообще про что?"}).json()
    assert view["messages"][-1]["patches"] == [] and view["messages"][-1]["notes"][0].startswith("Сообщение понято как вопрос")


def test_an_answer_joins_the_models_own_edit_of_the_notes_and_a_bad_number_does_not_stop_the_rest(api, monkeypatch):
    from project_generator_lab.concept.thread import Answered

    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[PROGRAM_QUESTION, LEVEL_QUESTION]))
    model.proposal = Proposal(reply="Записал.", answered=[Answered(number=7, answer="?"), Answered(number=1, answer="ОР программы нет")],
                              changes=[ProposedChange(field="notes", value="Путь участника: Miro не проходили\n", reason="Просьба",
                                                      origin="answer", question_numbers=[2])])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "ОР программы нет. И Miro они не проходили, уровень базовый"}).json()
    [patch] = view["messages"][-1]["patches"]
    assert patch["after"] == f"Путь участника: Miro не проходили\nОтвет на вопрос «{PROGRAM_QUESTION.question}»: ОР программы нет"
    assert (patch["question_numbers"], patch["origin"]) == ([1, 2], "answer")
    assert patch["reason"] == "Ответ методолога на вопрос разбора: следующая версия читает только задание"
    # No open question left for the narrow second look, and only the first call's tokens are counted.
    assert not [c for c in model.calls if c[0] == "AnswerCheck"]
    assert (view["messages"][-1]["input_tokens"], view["messages"][-1]["output_tokens"]) == (10, 10)
    # A quoted «answer» is dropped and the next one is still read: one bad item does not end the list.
    model.proposal = Proposal(reply="Ок.", answered=[Answered(number=1, answer=PROGRAM_QUESTION.question),
                                                     Answered(number=2, answer="базового хватит")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "по уровню — базового хватит"}).json()
    [patch] = view["messages"][-1]["patches"]
    # The answer to question 1 still waiting is carried over, not lost with the proposal it was in (29.10.2026).
    assert patch["question_numbers"] == [1, 2] and patch["after"].endswith("»: базового хватит")
    assert f"Ответ на вопрос «{PROGRAM_QUESTION.question}»: ОР программы нет" in patch["after"]


def test_open_questions_are_those_of_this_version_without_an_applied_answer():
    from project_generator_lab.concept.answers import unanswered
    from project_generator_lab.concept.thread import Message, Patch
    from project_generator_lab.tests.concept.test_validation import new_run
    from project_generator_lab.tests.concept.workflow_fixtures import input_card

    run = new_run()
    run.input_card = input_card(run.request)
    run.input_card.questions = [PROGRAM_QUESTION, LEVEL_QUESTION, Question(question="Третий?", why="—", blocking=False)]

    def answered(number, status, run_id):
        patch = Patch(field="notes", label="Уточнения", before="", after="x", origin="answer", question_numbers=[number])
        return Message(role="assistant", text="", run_id=run_id, patches=[patch], status=status)

    from project_generator_lab.concept.thread import Thread
    thread = Thread(id=run.id, owner_id="local", runs=[run.id], draft=run.request.brief,
                    messages=[answered(1, "applied", run.id), answered(2, "pending", run.id), answered(3, "applied", "older-run")])
    assert [q["number"] for q in unanswered(thread, run)] == [3]


def test_a_failed_second_look_leaves_the_first_calls_proposal_as_it_was(api, monkeypatch):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[PROGRAM_QUESTION]))
    model.proposal = Proposal(reply="Добавлю инструмент.", changes=[ProposedChange(field="tools", value="Miro", reason="Просьба", origin="request")])
    model.check = ModelUnavailable("шлюз не ответил")
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Добавь Miro в инструменты"}).json()
    last = view["messages"][-1]
    assert [(p["field"], p["after"]) for p in last["patches"]] == [("tools", "Miro")]
    # The failed call was made, but it adds no tokens and no note: only the first call is counted.
    assert [c[0] for c in model.calls][-1] == "AnswerCheck"
    assert (last["input_tokens"], last["output_tokens"], last["notes"]) == (10, 10, [])


def test_reverting_an_edit_the_brief_can_no_longer_take_is_refused_not_a_server_error(api, monkeypatch):
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    size = api.post(f"/api/threads/{thread['id']}/fields", json={"field": "group_size", "value": 5}).json()["messages"][-1]
    api.post(f"/api/threads/{thread['id']}/fields", json={"field": "format", "value": "group"})
    # A group project needs its size: taking the size back alone would leave a brief the contract refuses.
    answer = api.post(f"/api/threads/{thread['id']}/messages/{size['id']}/decision", json={"verdict": "revert"})
    assert answer.status_code == 409 and answer.json()["detail"] == "Отменить нельзя: Укажите размер группы"
    assert api.get(f"/api/threads/{thread['id']}").json()["draft"]["group_size"] == 5


def test_a_new_answer_to_the_same_question_replaces_the_old_one_and_the_same_answer_is_not_written_twice(api, monkeypatch):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[PROGRAM_QUESTION, LEVEL_QUESTION]))
    model.proposal = Proposal(reply="Записал.")
    first = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Вопрос 2: базового хватит"}).json()["messages"][-1]
    api.post(f"/api/threads/{thread['id']}/messages/{first['id']}/decision", json={"verdict": "apply"})
    # Changed mind: the brief keeps one answer to question 2, the new one; other lines of notes stay.
    api.post(f"/api/threads/{thread['id']}/fields", json={"field": "notes",
                                                          "value": f"Путь участника: Miro не проходили\nОтвет на вопрос «{LEVEL_QUESTION.question}»: базового хватит"})
    changed = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Вопрос 2: нужен средний уровень"}).json()["messages"][-1]
    assert changed["patches"][0]["after"] == f"Путь участника: Miro не проходили\nОтвет на вопрос «{LEVEL_QUESTION.question}»: нужен средний уровень"
    # The same answer again: nothing to write.
    same = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Вопрос 2: базового хватит"}).json()["messages"][-1]
    assert same["patches"] == []


def test_a_bare_yes_after_the_assistants_question_goes_to_the_model_as_an_answer(api, monkeypatch):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[LEVEL_QUESTION]))
    model.proposal = Proposal(reply="Это про то, что сдаёт участник, или про комплект генератора?")
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "добавь таблицу"})
    model.proposal = Proposal(kind="answer", reply="Понял: участник сдаёт таблицу.",
                              changes=[ProposedChange(field="student_result", value="Таблица", reason="Ответ", origin="request")])
    model.calls.clear()
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "да"}).json()
    assert [c[0] for c in model.calls][0] == "Proposal" and view["messages"][-1]["patches"][0]["field"] == "student_result"
    # Retention: with a proposal waiting, «да» accepts it by code, without the model.
    model.calls.clear()
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "да"}).json()
    assert model.calls == [] and view["draft"]["student_result"] == "Таблица"


def test_yes_answers_what_was_said_last_and_never_an_earlier_proposal(api, monkeypatch):
    # Independent review 01.10.2026: «да» to «Добавить это в ограничения?» applied an earlier, unrelated proposal.
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    model.proposal = Proposal(reply="Добавлю Excel и таблицу в сдачу. Подходит?", changes=[
        ProposedChange(field="tools", value="Excel", reason="—", origin="request"),
        ProposedChange(field="student_result", value="Таблица в Excel", reason="—", origin="request")])
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "добавь Excel"})
    model.proposal = Proposal(kind="question", reply="Часов хватит на 3 задания. Добавить это в ограничения?")
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "а часов хватит?"})
    model.proposal = Proposal(kind="answer", reply="Записал в ограничения.",
                              changes=[ProposedChange(field="constraints", value="Три основных задания", reason="Ответ", origin="request")])
    model.calls.clear()
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "да"}).json()
    # The model got «да» as the answer to its question; the Excel proposal still waits, untouched.
    assert [c[0] for c in model.calls][0] == "Proposal" and view["draft"]["tools"] == ""
    assert view["messages"][-1]["patches"][0]["field"] == "constraints"
    # «ок» after a reply that asked nothing changes nothing and names what waits.
    model.proposal = Proposal(kind="question", reply="Три задания — обычный объём для 12 часов.")
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "это много?"})
    model.calls.clear()
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "ок"}).json()
    assert model.calls == [] and view["draft"]["tools"] == "" and view["draft"]["constraints"] == ""
    assert view["messages"][-1]["text"] == ("Понял: задание оставляю без изменений. Дальше — «Применить правки и продолжить»: "
                                            "кнопка ниже или напишите «дальше».")


def test_yes_to_a_proposal_with_two_edits_applies_both_and_names_them(api, monkeypatch):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    model.proposal = Proposal(reply="Добавлю Excel и таблицу в сдачу. Подходит?", changes=[
        ProposedChange(field="tools", value="Excel", reason="—", origin="request"),
        ProposedChange(field="student_result", value="Таблица в Excel", reason="—", origin="request")])
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "добавь Excel"})
    model.calls.clear()
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "да"}).json()
    assert model.calls == [] and view["draft"]["tools"] == "Excel"
    assert view["messages"][-1]["text"] == "Применил правки: «Инструменты и ПО», «Что участник сдаёт в итоге»."


def test_after_a_failed_run_the_edited_brief_goes_into_a_new_version_not_the_old_one(api, monkeypatch):
    from project_generator_lab.concept.models import InputCard

    class FailingCard(DialogueClient):
        fail = True

        async def generate(self, schema, system, payload):
            if schema is InputCard and FailingCard.fail:
                raise ModelUnavailable("Модель не завершила ответ (length).")
            return await super().generate(schema, system, payload)

    request = request_fixture()
    monkeypatch.setattr(api_module, "ModelClient", lambda: FailingCard(request))
    run_id = api.post("/api/runs", content=request.model_dump_json(), headers={"Content-Type": "application/json"}).json()["id"]
    assert api.get(f"/api/runs/{run_id}").json()["status"] == "failed"
    thread = api.post("/api/threads", json={"run_id": run_id}).json()
    # Retention: nothing edited — «Продолжить» resumes, as before.
    assert thread["next_step"]["action"] == "resume"
    edited = api.post(f"/api/threads/{thread['id']}/fields", json={"field": "notes", "value": "Сократить материалы"}).json()
    # Audit 30.09.2026: «Продолжить» reran the old brief and failed the same way; the edit must go into the next attempt.
    assert edited["next_step"]["action"] == "new_version"
    FailingCard.fail = False
    moved = api.post(f"/api/threads/{thread['id']}/advance", json={"action": "new_version"}).json()
    assert len(moved["runs"]) == 2
    assert api.get(f"/api/runs/{moved['runs'][1]}").json()["request"]["brief"]["notes"] == "Сократить материалы"


def test_an_unreadable_run_or_thread_is_a_clear_refusal_and_someone_elses_is_not_found(api, monkeypatch):
    # Audit 30.09.2026: a damaged or older file gave 500 on every screen that opened it (content drafts already had 409).
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    path = api_module.RUNS_DIR / "threads" / f"{thread['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "messages": "не список"}), encoding="utf-8")
    answer = api.get(f"/api/threads/{thread['id']}")
    assert answer.status_code == 409 and "не читается текущей версией" in answer.json()["detail"]
    # The owner is read first: someone else's file is «not found», readable or not.
    path.write_text(json.dumps({**data, "owner_id": "someone-else", "messages": "не список"}), encoding="utf-8")
    assert api.get(f"/api/threads/{thread['id']}").status_code == 404
    run_path = api_module.RUNS_DIR / f"{thread['runs'][0]}.json"
    run_path.write_text("{обрыв", encoding="utf-8")
    assert api.get(f"/api/runs/{thread['runs'][0]}").status_code == 409


def test_ok_after_a_decided_proposal_is_agreement_and_a_quoted_question_mark_is_not_the_assistant_asking(api, monkeypatch):
    from project_generator_lab.core.intents import asked

    assert not asked("Записал ответ на вопрос «Нужен ли SQL?» в уточнения.")
    assert asked("Это про то, что сдаёт участник, или про комплект?")
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    model.proposal = Proposal(reply="Добавлю Excel. Подходит?",
                              changes=[ProposedChange(field="tools", value="Excel", reason="—", origin="request")])
    message = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "добавь Excel"}).json()["messages"][-1]
    api.post(f"/api/threads/{thread['id']}/messages/{message['id']}/decision", json={"verdict": "apply"})
    model.calls.clear()
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "ок"}).json()
    assert model.calls == [] and view["messages"][-1]["text"].startswith("Понял: задание оставляю без изменений.")


def test_declining_and_moving_on_in_one_message_does_not_apply_the_declined_proposal_in_the_concept_chat(api, monkeypatch):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    model.proposal = Proposal(reply="Добавлю Excel.", changes=[ProposedChange(field="tools", value="Excel", reason="—", origin="request")])
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "добавь Excel"})
    model.proposal = Proposal(reply="Хорошо, без Excel.", proceed=True)
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Нет, Excel не нужен, давай дальше"}).json()
    assert view["draft"]["tools"] == "" and len(view["runs"]) == 1
    assert view["messages"][-1]["notes"][-1].startswith("Сначала решите по предложенным правкам выше")


def test_a_multi_line_answer_is_written_on_one_line_so_it_can_be_replaced_whole(api, monkeypatch):
    from project_generator_lab.concept.thread import Answered

    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[LEVEL_QUESTION]))
    model.proposal = Proposal(reply="Записал.", answered=[Answered(number=1, answer="базового хватит\nи основы Python")])
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "по уровню: базового хватит и основы Python"}).json()
    assert view["messages"][-1]["patches"][0]["after"].endswith("»: базового хватит и основы Python")


def test_a_proposal_made_stale_by_a_revert_is_withdrawn_in_the_concept_chat(api, monkeypatch):
    # Independent review 30.09.2026: apply M1, the model proposes M2 on the same field, revert M1 — M2 was stale and
    # «Применить правки и продолжить» failed on it every time.
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    model.proposal = Proposal(reply="Excel.", changes=[ProposedChange(field="tools", value="Excel", reason="—", origin="request")])
    m1 = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "добавь Excel"}).json()["messages"][-1]
    api.post(f"/api/threads/{thread['id']}/messages/{m1['id']}/decision", json={"verdict": "apply"})
    model.proposal = Proposal(reply="И Miro.", changes=[ProposedChange(field="tools", value="Excel, Miro", reason="—", origin="request")])
    m2 = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "и Miro"}).json()["messages"][-1]
    view = api.post(f"/api/threads/{thread['id']}/messages/{m1['id']}/decision", json={"verdict": "revert"}).json()
    statuses = {m["id"]: (m["status"], m["notes"]) for m in view["messages"]}
    assert statuses[m1["id"]][0] == "pending" and statuses[m2["id"]][0] == "rejected"
    assert statuses[m2["id"]][1][-1].startswith("Поле изменилось после предложения — предложение снято.")
    assert view["next_step"]["action"] == "apply_and_continue"
    # The waiting one can be applied: the route is not blocked.
    assert api.post(f"/api/threads/{thread['id']}/advance", json={"action": "apply_and_continue"}).status_code == 200


def test_two_answers_in_a_row_without_applying_keep_both_and_a_newer_answer_to_the_same_question_wins(api, monkeypatch):
    """Complaint 29.10.2026: an answered question was asked again. The methodologist answered question 1, then question
    2 before applying: the second notes proposal was built from the applied notes and replaced the first one waiting,
    so the first answer was lost and the next version asked it again. Now the waiting answers are carried over."""
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, questions=[PROGRAM_QUESTION, LEVEL_QUESTION]))
    model.proposal = Proposal(reply="Записал.")
    first = f"Ответ на вопрос «{PROGRAM_QUESTION.question}»:"
    second = f"Ответ на вопрос «{LEVEL_QUESTION.question}»:"
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Вопрос 1: ответ один"})
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Вопрос 2: ответ два"}).json()
    proposals = [m for m in view["messages"] if m["patches"]]
    assert [m["status"] for m in proposals] == ["rejected", "pending"]
    assert proposals[-1]["patches"][0]["after"] == f"{first} ответ один\n{second} ответ два"
    assert proposals[-1]["patches"][0]["question_numbers"] == [1, 2]
    # A newer answer to question 1, still before applying: one line per question, the newer one.
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Вопрос 1: ответ один, уточнённый"}).json()
    assert view["messages"][-1]["patches"][0]["after"] == f"{second} ответ два\n{first} ответ один, уточнённый"
    view = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "пойдёт"}).json()
    assert view["draft"]["notes"] == f"{second} ответ два\n{first} ответ один, уточнённый"
    assert "Вопросов без ответа" not in view["next_step"]["text"]
