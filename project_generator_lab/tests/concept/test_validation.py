"""Regression tests for approval boundaries, source coverage and grounded criticism."""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.concept import runs as api_module
from project_generator_lab.concept.models import (
    Concept,
    Finding,
    GroundedReview,
    InputCard,
    Material,
    Passage,
    Question,
    Review,
    Run,
)
from project_generator_lab.concept.planning import build_evidence, fingerprint, prepare, readable_export
from project_generator_lab.concept.validation import check_input_card, check_review, check_sources, source_registry
from project_generator_lab.core.access import current_owner
from project_generator_lab.core.llm import CallMetrics, ModelClient
from project_generator_lab.tests.concept.test_workbench import concept_fixture, request_fixture
from project_generator_lab.tests.concept.workflow_fixtures import grounded_review, input_card


class WorkflowClient(ModelClient):
    def __init__(self, request):
        self.request = request
        self.calls = []

    async def generate(self, schema, system, payload):
        self.calls.append((schema.__name__, system, json.loads(payload)))
        response: InputCard | Concept | GroundedReview
        if schema is InputCard:
            response = input_card(self.request)
        elif schema is Concept:
            response = concept_fixture()
        elif schema is GroundedReview:
            response = grounded_review()
        else:
            raise AssertionError(schema)
        return response, CallMetrics("test", 1, 30, 40)


def new_run():
    request = request_fixture()
    return Run(id=str(request.request_id), request=request, fingerprint=fingerprint(request))


def reviewed_run():
    run = new_run()
    run.prompt_version = "0.2"
    run.evidence = build_evidence(run.request)[0]
    run.input_card = input_card(run.request)
    run.input_approved_at = "2026-09-22T10:00:00+00:00"
    run.concept = concept_fixture()
    run.review = Review.model_validate(grounded_review().model_dump())
    run.status = "review"
    return run


def test_workflow_pauses_before_design_and_keeps_frozen_inputs_and_prompts():
    run = new_run()
    client = WorkflowClient(run.request)
    asyncio.run(prepare(run, lambda r: None, client))
    assert run.status == "input_review" and run.concept is None
    assert not run.input_findings
    assert [c[0] for c in client.calls] == ["InputCard"]
    frozen = run.prompt_snapshots.copy()
    run.input_approved_at = "2026-09-22T10:00:00+00:00"
    run.status = "running"
    asyncio.run(prepare(run, lambda r: None, client))
    assert run.status == "review" and not run.review.findings
    assert [c[0] for c in client.calls] == ["InputCard", "Concept", "GroundedReview"]
    assert run.prompt_snapshots == frozen
    assert client.calls[2][2]["input_card"] == run.input_card.model_dump()
    assert client.calls[2][2]["source_registry"] == [r.model_dump() for r in run.source_registry]


def test_demo_card_includes_unread_source_warning_in_model_context():
    run = new_run()
    run.request.materials = [Material(id="bad", name="scan.pdf", sha256="abc", passages=[], read_status="failed")]
    client = WorkflowClient(run.request)
    asyncio.run(prepare(run, lambda r: None, client))
    assert run.status == "input_review" and run.input_card is not None
    assert run.source_registry[-1].name == "scan.pdf"
    assert run.input_findings and all(f.severity == "warning" for f in run.input_findings)
    assert client.calls[0][2]["source_registry"][-1]["read_status"] == "failed"
    assert client.calls[0][2]["input_warnings"]


def test_input_reference_repair_is_bounded_and_preserves_attempts():
    run = new_run()

    class BrokenInput(WorkflowClient):
        async def generate(self, schema, system, payload):
            response, metrics = await super().generate(schema, system, payload)
            response.claims[0].evidence_ids = ["form.*"]
            return response, metrics

    client = BrokenInput(run.request)
    asyncio.run(prepare(run, lambda r: None, client))
    assert len(client.calls) == 2
    assert len(run.input_attempts) == 2
    assert run.status == "input_review" and run.input_findings
    assert run.concept is None


def test_reviewer_reference_repair_keeps_original_report_and_fixes_only_once():
    run = new_run()

    class RepairClient(WorkflowClient):
        async def generate(self, schema, system, payload):
            response, metrics = await super().generate(schema, system, payload)
            if schema is GroundedReview and sum(call[0] == "GroundedReview" for call in self.calls) == 1:
                response.checks[0].evidence_ids.append("tasks.1")
            return response, metrics

    client = RepairClient(run.request)
    asyncio.run(prepare(run, lambda r: None, client))
    run.input_approved_at = "2026-09-22T10:00:00+00:00"
    run.status = "running"
    asyncio.run(prepare(run, lambda r: None, client))
    assert len(run.review_attempts) == 2
    assert "tasks.1" in run.review_attempts[0].checks[0].evidence_ids
    assert not run.review.findings
    assert [c[0] for c in client.calls] == ["InputCard", "Concept", "GroundedReview", "GroundedReview"]


def test_registry_distinguishes_extracted_selected_and_unread():
    run = new_run()
    run.request.materials = [Material(id="ref", name="reference.md", role="reference", sha256="abc", read_status="partial",
                                     passages=[Passage(id=f"p{i}", location=str(i), text="Контекст") for i in range(15)], unread_locations=["Страница 3"])]
    evidence, _ = build_evidence(run.request)
    registry = source_registry(run.request, evidence)
    assert registry[-1].extracted_count == 15 and registry[-1].selected_count == 10
    assert registry[-1].unread_locations == ["Страница 3"]
    assert not check_sources(run.request, evidence, registry)
    run.request.materials[0].role = "requirement"
    evidence, _ = build_evidence(run.request)
    registry = source_registry(run.request, evidence)
    assert registry[-1].selected_count == 15
    assert check_sources(run.request, evidence, registry)


def test_missing_input_outcome_and_invented_source_cannot_be_approved():
    run = new_run()
    card = input_card(run.request)
    card.claims.pop(0)
    card.claims[0].evidence_ids = ["imaginary"]
    findings = check_input_card(card, run.request, build_evidence(run.request)[0])
    assert len(findings) >= 2
    assert all(f.origin == "system" for f in findings)


@pytest.mark.parametrize("defect", ["missing_outcome", "fake_task", "fake_source", "fake_location", "missing_rule", "no_action", "bonus", "false_deferred", "hidden_problem"])
def test_grounded_review_requires_real_and_complete_evidence(defect):
    run = reviewed_run()
    review = run.review
    if defect == "missing_outcome":
        review.outcomes.pop()
    elif defect == "fake_task":
        review.outcomes[0].task_numbers = [99]
    elif defect == "fake_source":
        review.checks[0].evidence_ids = ["fake"]
    elif defect == "fake_location":
        review.checks[0].concept_locations = ["tasks.99"]
    elif defect == "missing_rule":
        review.checks.pop()
    elif defect == "no_action":
        run.concept.tasks[0].action = ""
    elif defect == "bonus":
        run.concept.tasks[0].optional = True
    elif defect == "false_deferred":
        review.outcomes[0].status = "deferred"
    elif defect == "hidden_problem":
        review.checks[0].status = "problem"
    assert check_review(review, run)


def test_future_files_are_allowed_to_remain_unverified():
    run = reviewed_run()
    run.review.checks[-1].status = "deferred"
    run.review.checks[-1].explanation = "Данные создаются после замысла; запуск проверяется перед публикацией."
    assert not check_review(run.review, run)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(api_module, "model_configuration", lambda: ("test-key", "unused", "test"))
    with TestClient(app_module.app) as client:
        yield client


def test_upload_failure_is_visible_and_persisted_in_registry(api):
    response = api.post("/api/materials", files={"file": ("scan.pdf", b"not pdf")})
    assert response.status_code == 200
    assert response.json()["read_status"] == "failed"
    assert response.json()["name"] == "scan.pdf"
    assert response.json()["passages"] == []


def test_input_confirmation_is_idempotent_and_does_not_rerun_interpreter(api, monkeypatch):
    run = new_run()
    model = WorkflowClient(run.request)
    asyncio.run(prepare(run, api_module.save_run, model))
    monkeypatch.setattr(api_module, "ModelClient", lambda: model)
    first = api.post(f"/api/runs/{run.id}/confirm-inputs")
    assert first.status_code == 202
    second = api.post(f"/api/runs/{run.id}/confirm-inputs")
    assert second.status_code == 202
    assert first.json()["input_approved_at"] == second.json()["input_approved_at"]
    assert [call[0] for call in model.calls] == ["InputCard", "Concept", "GroundedReview"]
    assert api.get(f"/api/runs/{run.id}").json()["status"] == "review"


@pytest.mark.parametrize("defect", ["empty_field_reference", "critical_question", "missing_area"])
def test_pending_legacy_card_can_continue_with_visible_warnings(api, monkeypatch, defect):
    run = new_run()
    model = WorkflowClient(run.request)
    asyncio.run(prepare(run, api_module.save_run, model))
    if defect == "empty_field_reference":
        run.input_card.claims[-1].kind = "unknown"
        run.input_card.claims[-1].evidence_ids = ["form.nonexistent"]
    elif defect == "critical_question":
        run.input_card.questions = [Question(question="Какая аудитория?", why="Нужно обсудить уровень", blocking=True)]
    else:
        run.input_card.claims = [c for c in run.input_card.claims if c.area != "materials"]
    # Reproduce a persisted 0.2 card whose original diagnostics blocked the button.
    run.input_findings = check_input_card(run.input_card, run.request, run.evidence)
    assert run.input_findings and all(f.severity == "blocker" for f in run.input_findings)
    api_module.save_run(run)
    snapshot = (api_module.RUNS_DIR / f"{run.id}.json").read_bytes()
    restored = api.get(f"/api/runs/{run.id}").json()
    assert restored["input_findings"] and all(f["severity"] == "warning" for f in restored["input_findings"])
    assert restored["input_card"]["claims"] == run.input_card.model_dump()["claims"]
    assert [q["question"] for q in restored["input_card"]["questions"]] == [q.question for q in run.input_card.questions]
    assert (api_module.RUNS_DIR / f"{run.id}.json").read_bytes() == snapshot
    monkeypatch.setattr(api_module, "ModelClient", lambda: model)
    assert api.post(f"/api/runs/{run.id}/confirm-inputs").status_code == 202
    result = api.get(f"/api/runs/{run.id}").json()
    assert result["status"] == "review"
    assert result["input_findings"] == restored["input_findings"]
    assert model.calls[-2][2]["input_warnings"] == restored["input_findings"]
    assert [call[0] for call in model.calls] == ["InputCard", "Concept", "GroundedReview"]


@pytest.mark.parametrize("has_card", [True, False])
def test_demo_can_resume_with_unread_files_and_retains_registry(api, monkeypatch, has_card):
    run = new_run()
    model = WorkflowClient(run.request)
    asyncio.run(prepare(run, api_module.save_run, model))
    run.request.materials.append(Material(id="missing", name="required.docx", sha256="failed-file-hash", role="requirement", read_status="failed", passages=[]))
    if not has_card:
        run.input_card = None
    api_module.save_run(run)
    restored = api.get(f"/api/runs/{run.id}").json()
    assert restored["input_findings"] and all(f["severity"] == "warning" for f in restored["input_findings"])
    monkeypatch.setattr(api_module, "ModelClient", lambda: model)
    assert api.post(f"/api/runs/{run.id}/confirm-inputs").status_code == 202
    if not has_card:
        card = api.get(f"/api/runs/{run.id}").json()
        assert card["status"] == "input_review" and card["input_card"]
        assert card["source_registry"][-1]["read_status"] == "failed"
        assert not card["input_approved_at"]
        assert api.post(f"/api/runs/{run.id}/confirm-inputs").status_code == 202
    assert api.get(f"/api/runs/{run.id}").json()["status"] == "review"


def test_feedback_controls_approval_and_keeps_decision_history(api):
    run = reviewed_run()
    run.review.findings = [Finding(severity="blocker", message="Нет данных", suggestion="Добавить данные", rule_id="materials", origin="model")]
    api_module.save_run(run)
    assert api.get(f"/api/runs/{run.id}").json()["review"]["findings"][0]["severity"] == "warning"
    url = f"/api/runs/{run.id}/feedback"
    assert api.post(url, json={"finding_index":0, "verdict":"dismissed", "reason":"ok"}).status_code == 422
    dismissed = {"finding_index":0, "verdict":"dismissed", "reason":"Данные создаются на следующем этапе"}
    assert api.post(url, json=dismissed).status_code == 200
    assert len(api.post(url, json=dismissed).json()["finding_decisions"]) == 1
    assert api.post(url, json={**dismissed, "verdict":"confirmed"}).status_code == 200
    assert api.get(f"/api/runs/{run.id}").json()["review"]["findings"][0]["severity"] == "warning"
    assert len(api.post(url, json=dismissed).json()["finding_decisions"]) == 3
    assert api.post(f"/api/runs/{run.id}/approve").status_code == 200
    assert api.post(url, json=dismissed).status_code == 409


@pytest.mark.parametrize("origin", ["system", "model"])
def test_demo_approval_preserves_findings_without_requiring_feedback(api, origin):
    run = reviewed_run()
    run.review.findings = [Finding(severity="blocker", message="Несуществующий источник", suggestion="Исправить", origin=origin)]
    run.concept.questions = [Question(question="Нужен ли кейс?", why="Уточнить комплект", blocking=True)]
    api_module.save_run(run)
    before = (api_module.RUNS_DIR / f"{run.id}.json").read_bytes()
    displayed = api.get(f"/api/runs/{run.id}").json()
    assert displayed["review"]["findings"][0]["severity"] == "warning"
    assert not displayed["concept"]["questions"][0]["blocking"]
    assert before == (api_module.RUNS_DIR / f"{run.id}.json").read_bytes()
    approved = api.post(f"/api/runs/{run.id}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert approved.json()["review"]["findings"][0]["message"] == "Несуществующий источник"
    assert approved.json()["finding_decisions"] == []
    assert api.get(f"/api/runs/{run.id}").json()["approved_at"] == approved.json()["approved_at"]


def test_revision_preserves_approved_parent_and_records_changes(api, monkeypatch):
    parent = reviewed_run()
    parent.status = "approved"
    parent.approved_at = "2026-09-22T10:05:00+00:00"
    api_module.save_run(parent)
    original = (api_module.RUNS_DIR / f"{parent.id}.json").read_bytes()
    request = parent.request.model_copy(deep=True)
    request.request_id = uuid4()
    request.parent_run_id = parent.id
    request.brief.hours = 6
    monkeypatch.setattr(api_module, "ModelClient", lambda: WorkflowClient(request))
    result = api.post("/api/runs", json=request.model_dump(mode="json"))
    assert result.status_code == 202
    child = api.get(f"/api/runs/{request.request_id}").json()
    assert child["changes"][0] == {"location":"brief.hours", "before":"4.0", "after":"6.0"}
    assert child["baseline_approved_at"] == parent.approved_at
    assert (api_module.RUNS_DIR / f"{parent.id}.json").read_bytes() == original
    assert api.post(f"/api/runs/{request.request_id}/approve").status_code == 409


def test_foreign_parent_and_feedback_are_not_accessible(api):
    parent = reviewed_run()
    parent.owner_id = "someone-else"
    api_module.save_run(parent)
    assert current_owner.get() == "local"
    request = request_fixture()
    request.parent_run_id = parent.id
    assert api.post("/api/runs", json=request.model_dump(mode="json")).status_code == 404
    assert api.post(f"/api/runs/{parent.id}/confirm-inputs").status_code == 404
    assert api.post(f"/api/runs/{parent.id}/feedback", json={"finding_index":0,"verdict":"dismissed","reason":"не моя версия"}).status_code == 404


def test_legacy_run_and_full_export_remain_readable():
    run = reviewed_run()
    legacy = run.model_dump(exclude={"input_card", "source_registry", "input_findings", "input_approved_at", "changes", "finding_decisions", "baseline_concept", "baseline_approved_at"})
    legacy["review"] = {"summary":"Старая проверка", "findings":[]}
    loaded = Run.model_validate(legacy)
    assert loaded.review is not None and loaded.review.outcomes == []
    text = readable_export(run)
    assert "Подтверждение образовательных результатов" in text
    assert "form.outcomes" in text and "Понимание входных данных" in text
