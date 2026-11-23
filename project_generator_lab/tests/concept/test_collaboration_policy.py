"""Source precedence and durable decisions across unrelated edits and real revisions."""

import asyncio
from uuid import UUID

import pytest
from project_generator_lab.concept.collaboration import Resolution, basis, resolved
from project_generator_lab.concept.ingestion import parse_curriculum
from project_generator_lab.concept.input_semantics import program_map
from project_generator_lab.concept.models import Question
from project_generator_lab.concept.planning import build_evidence, context_payload, prepare
from project_generator_lab.concept.source_policy import effective_request
from project_generator_lab.concept.thread import Proposal, ProposedChange, load_thread, save_thread
from project_generator_lab.tests.concept.test_dialogue import DialogueClient, start_thread
from project_generator_lab.tests.concept.test_dialogue import api as api
from project_generator_lab.tests.concept.test_validation import WorkflowClient, new_run
from project_generator_lab.tests.concept.test_workbench import request_fixture


def test_reworded_decision_stays_closed_until_its_actual_dependency_changes():
    values = {"start_skills": "Python и Git", "checking": "P2P"}
    answer = Resolution(key="audience.prerequisites", question="Что уже умеют?", answer="Python и Git",
                        basis=basis(values, ["start_skills"]))
    assert resolved("Какая подготовка на входе?", answer.key, {**values, "checking": "Демонстрация пиру"}, [answer])
    assert not resolved("Какая подготовка на входе?", answer.key, {**values, "start_skills": "Без Python"}, [answer])
    # Similar topics cannot hide different questions when no dependency was identified.
    assert not resolved("Нужен ли SQL?", "audience.prerequisites", values, [answer.model_copy(update={"basis": {}})])


def test_source_policy_replaces_only_the_selected_obsolete_project_without_mutating_the_upload():
    request = request_fixture()
    request.curriculum = parse_curriculum("plan.csv", "Название проекта;Краткое описание;Инструменты\nРанее;Основы;Python\nУстаревший агент;Старое требование;OldTool\nДальше;Практика;Python\n".encode())
    request.selected_row_id = request.curriculum.rows[1].id
    request.brief.curriculum_policy = "context_only"
    request.brief.tools = "CurrentTool"
    effective = effective_request(request)
    assert effective.curriculum.rows[1].title == request.brief.title
    assert effective.curriculum.rows[1].tools == "CurrentTool"
    assert request.curriculum.rows[1].title == "Устаревший агент"
    assert [r.title for r in effective.curriculum.rows][::2] == ["Ранее", "Дальше"]
    evidence, _ = build_evidence(request)
    assert "Старое требование" not in str([e.model_dump() for e in evidence])
    request.brief.curriculum_policy = "ignore"
    assert program_map(request) is None
    assert not [e for e in build_evidence(request)[0] if e.role == "curriculum"]


def test_an_already_answered_blocking_question_does_not_stop_the_next_version():
    run = new_run()
    run.auto_continue = True
    run.request.resolved_questions = [Resolution(question="Нужны ли роли?", answer="Две роли")]

    class Client(WorkflowClient):
        async def generate(self, schema, system, payload):
            result, metrics = await super().generate(schema, system, payload)
            from project_generator_lab.concept.models import InputCard
            if schema is InputCard:
                result.questions = [Question(question="Нужны ли роли?", why="Комплект", blocking=True)]
            return result, metrics

    asyncio.run(prepare(run, lambda r: None, Client(run.request)))
    assert run.status == "review"
    assert not run.input_card.questions
    assert context_payload(run)["resolved_questions"][0]["answer"] == "Две роли"


@pytest.mark.parametrize("origin,kind,expected", [("request", "request", "applied"), ("answer", "answer", "applied"),
                                                ("proposal", "request", "pending")])
def test_assisted_mode_applies_explicit_instructions_but_not_the_models_own_additions(api, monkeypatch, origin, kind, expected):
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r))
    saved = load_thread(UUID(thread["id"]))
    saved.draft.collaboration_mode = "assisted"
    save_thread(saved)
    model.proposal = Proposal(kind=kind, reply="Обновляю инструменты.", changes=[ProposedChange(field="tools", value="Python", reason="Указание", origin=origin)])
    response = api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Используем Python"})
    assert response.status_code == 200
    result = response.json()
    message = result["messages"][-1]
    assert message["status"] == expected
    if expected == "applied":
        assert result["draft"]["tools"] == "Python"
        restored = api.post(f"/api/threads/{thread['id']}/messages/{message['id']}/decision", json={"verdict": "revert"})
        assert restored.status_code == 200 and restored.json()["draft"]["tools"] == ""
