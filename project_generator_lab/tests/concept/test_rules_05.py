"""Rules 0.5 from the working group of 25.09.2026: each test names the remark it guards."""

import json

import pytest
from project_generator_lab.concept.models import Material, Passage, StartRequest
from project_generator_lab.concept.planning import PROMPT_VERSION, RULE_FILES, build_evidence, context_payload, prepare
from project_generator_lab.concept.thread import FIELDS, Proposal, ProposedChange
from project_generator_lab.core.intents import is_agreement
from project_generator_lab.tests.concept.test_dialogue import DialogueClient, api, start_thread  # noqa: F401 - fixture
from project_generator_lab.tests.concept.test_validation import WorkflowClient, new_run
from project_generator_lab.tests.concept.test_workbench import request_fixture


@pytest.mark.parametrize(
    ("text", "agreed"),
    [
        ("пойдет", True),
        ("Пойдёт!", True),
        ("да, всё устраивает", True),
        ("ок 👍", True),
        ("+", True),
        ("оставь как есть", True),
        ("в целом норм", True),
        ("всё", False),
        ("так", False),
        ("нет", False),
        ("да, 8 часов", False),
        ("пойдёт, но уменьши часы до 8", False),
        ("Да, но задание 3 заменить", False),
        ("+1", True),
        ("👍", True),
        ("OK...", True),
        ("да да да да да да", True),
        ("да да да да да да да", False),
        ("", False),
        ("   ", False),
        ("ок #3", False),
        ("ок = 8", False),
    ],
)
def test_agreement_is_recognised_only_without_a_subject(text, agreed):
    assert is_agreement(text) is agreed


def test_agreement_changes_nothing_and_does_not_reach_the_model(api, monkeypatch):  # noqa: F811
    # РГ 25.09, 48:43: «пойдёт» was read as a request to edit.
    proposal = Proposal(reply="Меняю", changes=[ProposedChange(field="hours", value=8, reason="—", origin="request")])
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, proposal))
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Пойдёт"}).json()
    reply = result["messages"][-1]
    assert reply["role"] == "assistant" and reply["patches"] == [] and reply["status"] is None
    assert "без изменений" in reply["text"]
    assert result["draft"] == thread["draft"]
    assert not [call for call in model.calls if call[0] == "Proposal"]
    # A message with a subject still goes to the model.
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Пойдёт, но часов 8"})
    assert [call[0] for call in model.calls].count("Proposal") == 1


def test_agreement_after_a_proposal_applies_that_proposal_once(api, monkeypatch):  # noqa: F811
    # Anton's case: the assistant proposed something and he answered «пойдёт» — that means «apply».
    proposal = Proposal(reply="Меняю", changes=[ProposedChange(field="hours", value=8, reason="—", origin="request")])
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, proposal))
    offered = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Сделай 8 часов"}).json()
    assert offered["messages"][-1]["status"] == "pending" and offered["draft"]["hours"] != 8
    agreed = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "пойдёт"}).json()
    assert agreed["draft"]["hours"] == 8
    assert agreed["messages"][-3]["status"] == "applied"
    assert agreed["messages"][-1]["text"] == "Применил правки: «Трудоёмкость, астр. ч»."
    again = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "ок"}).json()
    assert "без изменений" in again["messages"][-1]["text"] and again["draft"]["hours"] == 8
    assert [call[0] for call in model.calls].count("Proposal") == 1


def test_agreement_does_not_apply_a_stale_proposal(api, monkeypatch):  # noqa: F811
    proposal = Proposal(reply="Меняю", changes=[ProposedChange(field="hours", value=8, reason="—", origin="request")])
    thread, _ = start_thread(api, monkeypatch, lambda r: DialogueClient(r, proposal))
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Сделай 8 часов"})
    api.post(f"/api/threads/{thread['id']}/fields", json={"field": "hours", "value": 12})
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "пойдёт"}).json()
    # The manual edit withdrew the proposal, so «пойдёт» has nothing to apply: the methodologist's 12 hours stay.
    assert result["draft"]["hours"] == 12
    assert result["messages"][-1]["text"].startswith("Понял: задание оставляю без изменений.")


def test_tools_and_software_are_one_field_for_the_methodologist():
    # РГ 25.09, 27:24: methodologists do not tell required tools from required software.
    assert FIELDS["tools"] == "Инструменты и ПО" and "software" not in FIELDS
    assert {"learner_term", "concept_feedback"} <= set(FIELDS)


async def run_to_review(run, client):
    saved: list[object] = []
    await prepare(run, saved.append, client)
    run.input_approved_at = "2026-09-26T10:00:00+00:00"
    await prepare(run, saved.append, client)
    return run


@pytest.mark.anyio
async def test_rules_are_frozen_with_the_run_and_passed_to_every_model():
    run = new_run()
    client = WorkflowClient(run.request)
    await run_to_review(run, client)
    assert run.prompt_version == PROMPT_VERSION == "1.2"
    assert set(RULE_FILES) <= set(run.prompt_snapshots)
    assert "клонирует репозиторий" in run.prompt_snapshots["program_rules"]
    assert "ARM" in run.prompt_snapshots["platform"]
    for name, _, payload in client.calls:
        assert payload["fixed_rules"] == {k: run.prompt_snapshots[k] for k in RULE_FILES}, name
        assert payload["computed_state"]["learner_term"] == "участник"
        assert list(payload)[-1] == "computed_state"
    # A run keeps its rules even if the files change later.
    run.prompt_snapshots["platform"] = "старые ограничения"
    assert json.loads(json.dumps(context_payload(run)))["fixed_rules"]["platform"] == "старые ограничения"


def test_learner_term_comes_from_the_brief():
    # РГ 25.09, 09:33: the word depends on the programme type.
    run = new_run()
    run.request.brief.learner_term = "студент"
    assert json.loads(json.dumps(context_payload(run)))["computed_state"]["learner_term"] == "студент"


@pytest.mark.anyio
async def test_workload_is_checked_for_new_and_04_runs():
    # The current version is in the list on purpose: raising PROMPT_VERSION once dropped this check silently.
    for version in ("0.4", "0.5", PROMPT_VERSION):
        run = new_run()
        run.request.brief.hours = 10
        client = WorkflowClient(run.request)
        saved: list[object] = []
        await prepare(run, saved.append, client)
        run.prompt_version, run.input_approved_at = version, "2026-09-26T10:00:00+00:00"
        await prepare(run, saved.append, client)
        messages = [f.message for f in run.review.findings if f.rule_id == "constraints" and f.origin == "system"]
        assert "Основные задания занимают 4 ч из 10: 6 ч не распределены." in messages, version


def material(role, count):
    passages = [
        Passage(id=f"m.{i}", location=f"стр. {i}", text=f"Фрагмент {i} про что-то другое") for i in range(count)
    ]
    return Material(
        id="m", name="brief.docx", role=role, sha256="0" * 64, passages=passages, read_status="text_extracted"
    )


def test_customer_brief_is_passed_whole_like_a_requirement():
    # РГ 25.09, 19:32: the customer's brief in free form goes into the context in full.
    request = request_fixture()
    for role, expected in (("customer", 14), ("requirement", 14), ("reference", 10)):
        evidence, warnings = build_evidence(StartRequest(**{**request.model_dump(), "materials": [material(role, 14)]}))
        assert len([e for e in evidence if e.source == "brief.docx"]) == expected, role
        assert bool(warnings) is (expected < 14)


@pytest.mark.anyio
async def test_concept_feedback_reaches_the_designer_with_the_previous_concept(api, monkeypatch):  # noqa: F811
    # РГ 25.09, 35:25: replace one task and keep the rest; the verdict travels in the brief, not the chat.
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    first = thread["runs"][0]
    assert api.post(f"/api/runs/{first}/confirm-inputs").status_code == 202
    assert api.get(f"/api/runs/{first}").json()["status"] == "review"
    verdict = "Задание 1 «Выделить факты»: заменить на разбор одной записи"
    edited = api.post(
        f"/api/threads/{thread['id']}/fields", json={"field": "concept_feedback", "value": verdict}
    ).json()
    assert edited["draft"]["concept_feedback"] == verdict
    version = api.post(f"/api/threads/{thread['id']}/versions").json()
    second = version["runs"][1]
    assert api.post(f"/api/runs/{second}/confirm-inputs").status_code == 202
    designer = [payload for name, _, payload in model.calls if name == "Concept"][-1]
    assert designer["brief"]["concept_feedback"] == verdict
    assert designer["previous_concept"]["tasks"][0]["title"] == "Выделить факты"
    assert json.dumps(designer, ensure_ascii=False).count(verdict) == 2  # brief + its evidence record, never the chat


def test_a_check_applies_from_its_version_on_including_versions_not_yet_written():
    from project_generator_lab.concept.planning import since

    assert since("0.9", "0.4") and since("0.4", "0.4") and since("0.10", "0.9") and since("1.0", "0.4")
    # Retention: runs made before a check existed are not judged by it.
    assert not since("0.3", "0.4") and not since("0.1", "0.3")


def test_two_preparations_at_a_time_per_person_not_for_everyone(monkeypatch):
    # Audit 30.09.2026: the limit of two was global — one person holding two runs blocked every other methodologist.
    from fastapi import HTTPException
    from project_generator_lab.concept import runs
    from project_generator_lab.core.access import current_owner

    monkeypatch.setattr(runs, "active", {"r1": object(), "r2": object()})
    monkeypatch.setattr(runs, "owners", {"r1": "anna", "r2": "anna"})
    token = current_owner.set("anna")
    try:
        with pytest.raises(HTTPException) as error:
            runs.room()
        assert error.value.status_code == 429 and "У вас уже готовятся два замысла" in error.value.detail
    finally:
        current_owner.reset(token)
    # One run of her own leaves room for the second.
    monkeypatch.setattr(runs, "active", {"r1": object()})
    token = current_owner.set("anna")
    try:
        runs.room()
    finally:
        current_owner.reset(token)
    monkeypatch.setattr(runs, "active", {"r1": object(), "r2": object()})
    token = current_owner.set("boris")
    try:
        runs.room()  # someone else is not blocked by Anna's runs
        monkeypatch.setattr(runs, "active", {f"r{i}": object() for i in range(runs.RUNS_ALL)})
        with pytest.raises(HTTPException) as error:
            runs.room()
        assert error.value.status_code == 429 and "Сервер сейчас готовит много замыслов" in error.value.detail
    finally:
        current_owner.reset(token)


def test_writing_jobs_are_limited_per_person_and_for_the_server(monkeypatch):
    from fastapi import HTTPException
    from project_generator_lab.content import flow

    monkeypatch.setattr(flow, "jobs", {"d1": object(), "d2": object()})
    monkeypatch.setattr(flow, "job_owners", {"d1": "anna", "d2": "anna"})
    with pytest.raises(HTTPException) as error:
        flow.room("anna")
    assert error.value.status_code == 429 and "У вас уже пишутся два текста" in error.value.detail
    flow.room("boris")
    monkeypatch.setattr(flow, "jobs", {"d1": object()})
    flow.room("anna")  # one of her own leaves room for the second
    monkeypatch.setattr(flow, "jobs", {f"d{i}": object() for i in range(flow.JOBS_ALL)})
    with pytest.raises(HTTPException) as error:
        flow.room("boris")
    assert error.value.status_code == 429 and "Сервер сейчас пишет много текстов" in error.value.detail
