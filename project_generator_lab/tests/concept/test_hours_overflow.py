"""Hours more than one project holds are a decision of the methodologist, not a warning (29.10.2026). By the norms a
project has up to 8 main tasks of 1–6 h — up to 48 h; a project of 60 h was made as eight tasks like any other, each
longer than the norm, with a line among the notes. Now the route stops at the inputs and asks: fewer hours, two
projects, or keep the hours with longer tasks. Nothing else moves it on — not «дальше», not a proposal applied, not a
new version asked to go straight on, not the route of the classic page."""

from types import SimpleNamespace
from typing import Any

import pytest
from project_generator_lab.concept import route
from project_generator_lab.concept.models import WorkloadBudget
from project_generator_lab.concept.planning import context_payload
from project_generator_lab.concept.runs import load_run
from project_generator_lab.concept.workload import WorkloadQuery, budget, overflow, overflow_text
from project_generator_lab.tests.concept import test_dialogue
from project_generator_lab.tests.concept.test_dialogue import DialogueClient, api  # noqa: F401 - fixture
from project_generator_lab.tests.concept.test_route_06 import patch_proposal, thread_for, wait_for
from project_generator_lab.tests.concept.test_validation import new_run
from project_generator_lab.tests.concept.test_workbench import request_fixture

KEEP = "Оставить 60 ч — задания длиннее нормы"


def plan(hours, cap=8, high=6.0):
    return WorkloadBudget(total_hours=hours, min_task_hours=1, max_task_hours=high, main_tasks_max=cap, overhead_min_share=0.3,
                          first_task_setup_min=0.5)


def test_the_hours_a_project_holds_are_its_tasks_at_the_upper_norm():
    assert [overflow(plan(h)) for h in (6, 47.5, 48, 48.5, 60)] == [None, None, None, 48, 48]
    # Nothing to compare: no hours, no cap on the number of tasks, no budget at all.
    assert overflow(plan(None)) is None and overflow(plan(500, cap=None)) is None and overflow(None) is None
    # The file's norms say the same: 8 tasks of up to 6 h.
    assert overflow(budget(WorkloadQuery(hours=60))) == 48 and overflow(budget(WorkloadQuery(hours=48))) is None


def test_the_question_names_the_room_the_ways_out_and_what_keeping_costs():
    assert overflow_text(plan(60), 48) == (
        "60 ч не помещаются в один проект: по нормам в нём до 8 основных заданий по 1–6 ч, то есть до 48 ч. Варианты: уменьшить "
        "трудоёмкость (напишите, например: «трудоёмкость 48 часов»), разделить проект на два — этот и следующий, с остатком часов, — "
        "или оставить 60 ч: тогда каждое задание выйдет примерно по 7,5 ч, длиннее нормы.")
    assert "по 12,5 ч" in overflow_text(plan(100), 48)


def run_at_inputs(hours, kept=False):
    run = new_run()
    run.request.brief.hours = hours
    run.status, run.workload_budget, run.hours_kept = "input_review", budget(run.request.brief), kept
    return run


def test_at_the_inputs_the_route_asks_about_the_hours_before_the_concept():
    run = run_at_inputs(60)
    step = route.next_step(thread_for(run), run)
    assert (step["action"], step["button"], step["title"]) == ("keep_hours", KEEP, "Часы не помещаются в один проект")
    assert step["text"] == overflow_text(run.workload_budget, 48) and step["edit_hint"] == "Новая трудоёмкость или как разделить проект…"
    # Kept, or hours that fit: the usual step.
    for other in (run_at_inputs(60, kept=True), run_at_inputs(48)):
        assert route.next_step(thread_for(other), other)["action"] == "confirm_inputs"
    # A changed brief is a new version first, whatever its hours.
    assert route.next_step(thread_for(run, hours=40), run)["action"] == "new_version"


def start(api, monkeypatch, hours, client=None):  # noqa: F811
    request = request_fixture()
    request.brief.hours = hours
    monkeypatch.setattr(test_dialogue, "request_fixture", lambda: request)
    return test_dialogue.start_thread(api, monkeypatch, client or (lambda r: DialogueClient(r)))


def test_the_methodologist_keeps_the_hours_and_the_design_is_told(api, monkeypatch):  # noqa: F811
    thread, model = start(api, monkeypatch, 60)
    assert thread["next_step"]["action"] == "keep_hours"
    run_id = thread["runs"][0]
    # The step of a page that does not know of the question is refused, by the chat and by the route of runs alike.
    assert api.post(f"/api/threads/{thread['id']}/advance", json={"action": "confirm_inputs"}).status_code == 409
    refused = api.post(f"/api/runs/{run_id}/confirm-inputs")
    assert refused.status_code == 409 and refused.json()["detail"].startswith("60 ч не помещаются в один проект")
    assert load_run(run_id).status == "input_review" and not load_run(run_id).hours_kept
    view = api.post(f"/api/threads/{thread['id']}/advance", json={"action": "keep_hours"}).json()
    pressed, said = view["messages"][-2:]
    assert (pressed["role"], pressed["text"]) == ("methodologist", KEEP)
    assert said["text"] == "Оставляю 60 ч: основных заданий будет 8, примерно по 7,5 ч — длиннее нормы. Входы приняты, готовлю замысел."
    done = wait_for(api, run_id, "review")
    assert done["hours_kept"] and done["input_approved_at"]
    designed = [payload for name, _, payload in model.calls if name == "Concept"][-1]
    assert "Методолог оставил трудоёмкость больше" in designed["computed_state"]["hours_kept"]


def test_a_project_that_fits_is_told_nothing_new(api, monkeypatch):  # noqa: F811
    thread, model = start(api, monkeypatch, 48)
    assert thread["next_step"]["action"] == "confirm_inputs"
    api.post(f"/api/threads/{thread['id']}/advance", json={"action": "confirm_inputs"})
    wait_for(api, thread["runs"][0], "review")
    designed = [payload for name, _, payload in model.calls if name == "Concept"][-1]
    assert "hours_kept" not in designed["computed_state"] and not load_run(thread["runs"][0]).hours_kept


@pytest.mark.parametrize("text", ["Дальше", "Генерируй задания то"])
def test_go_on_in_the_chat_is_no_answer_to_the_question(api, monkeypatch, text):  # noqa: F811
    thread, model = start(api, monkeypatch, 60)
    result = api.post(f"/api/threads/{thread['id']}/messages", json={"text": text}).json()
    assert result["messages"][-1]["text"].startswith("Сначала нужно ваше решение по часам. 60 ч не помещаются в один проект")
    assert load_run(thread["runs"][0]).status == "input_review" and not [c for c in model.calls if c[0] == "Concept"]


def test_a_proposal_applied_and_a_version_asked_to_go_on_stop_at_the_question(api, monkeypatch):  # noqa: F811
    thread, model = start(api, monkeypatch, 60, lambda r: DialogueClient(r, patch_proposal(proceed=True)))
    thread = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Материалов нет давай дальше пойдем"}).json()
    moved = api.post(f"/api/threads/{thread['id']}/advance", json={"action": "apply_and_continue"}).json()
    child = wait_for(api, moved["runs"][1], "input_review")
    # The new version was asked to go straight on; the hours stop it at the inputs, and the chat asks.
    assert child["auto_continue"] and not child["input_approved_at"] and not child["concept"]
    assert api.get(f"/api/threads/{thread['id']}").json()["next_step"]["action"] == "keep_hours"


def test_fewer_hours_said_in_the_chat_make_a_version_that_needs_no_decision(api, monkeypatch):  # noqa: F811
    hours = SimpleNamespace(field="hours", value=40, reason="Методолог уменьшил трудоёмкость", origin="request")
    proposal = patch_proposal()
    proposal.changes = [type(proposal.changes[0])(**vars(hours))]
    thread, _ = start(api, monkeypatch, 60, lambda r: DialogueClient(r, proposal))
    thread = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "трудоёмкость 40 часов"}).json()
    moved = api.post(f"/api/threads/{thread['id']}/advance", json={"action": "apply_and_continue"}).json()
    child = wait_for(api, moved["runs"][1], "review")
    assert child["request"]["brief"]["hours"] == 40 and not child["hours_kept"]


def test_a_run_stored_before_the_decision_is_asked_too():
    # Runs at the inputs from before 29.10.2026 have no such field: they read as not kept, and are asked.
    run = run_at_inputs(60)
    stored = run.model_dump(mode="json")
    stored.pop("hours_kept")
    again = type(run).model_validate(stored)
    assert again.hours_kept is False and route.next_step(thread_for(again), again)["action"] == "keep_hours"
    # The design of a run kept says so; of one not kept, nothing.
    state: Any = context_payload(again)["computed_state"]
    assert "hours_kept" not in state
    again.hours_kept = True
    state = context_payload(again)["computed_state"]
    assert state["hours_kept"].startswith("Методолог оставил трудоёмкость больше")
