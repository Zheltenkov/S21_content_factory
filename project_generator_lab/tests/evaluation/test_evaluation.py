"""Evidence, readiness and ownership contracts; no paid model calls."""
import asyncio
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.content import flow
from project_generator_lab.content import store as content_store
from project_generator_lab.content.writer import Writer
from project_generator_lab.core.llm import CallMetrics, ModelUnavailable
from project_generator_lab.evaluation import api, engine, registry, store
from project_generator_lab.evaluation.models import Context, EvaluationBatch, Evidence, ModelVerdict, OutcomeMapping
from project_generator_lab.evaluation.policy import summary
from project_generator_lab.evaluation.snapshot import build
from project_generator_lab.tests.content.fixtures import FakeModel, FakeSearch, open_pages, read_pages
from project_generator_lab.tests.content.test_corpus_and_batch import new_draft


@pytest.fixture
def draft(tmp_path, monkeypatch):
    monkeypatch.setattr(content_store, "CONTENT_DIR", tmp_path)
    monkeypatch.setattr(api, "context_provider", lambda _: Context(curriculum_available=True, full_sources=True))
    monkeypatch.setattr(api, "model_configuration", lambda: ("private-test-key", "http://unused", "test-reviewer"))
    value = new_draft()
    content_store.save(value)
    asyncio.run(flow.prepare_bundle(value.id, Writer(FakeModel(), reviewer=FakeModel(), search=FakeSearch(), opener=open_pages, reader=read_pages)))
    return content_store.load(value.id)


def report_for(draft):
    return engine.initial(draft, build(draft, Context(curriculum_available=True, full_sources=True)), "test-reviewer", True)


class Evaluator:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    async def generate(self, schema, system, payload):
        data = json.loads(payload)
        numbers = [c["number"] for c in data["supplied_criteria"]]
        self.calls.append(numbers)
        if len(self.calls) == self.fail_at:
            raise ModelUnavailable("temporary outage")
        units = {u["id"]: u["text"] for u in data["units"]}
        witness = Evidence(unit="task:1:steps", quote=units["task:1:steps"][:80])
        outcomes = []
        if 3 in numbers:
            for number in range(1, len(data["project_snapshot"]["outcomes"]) + 1):
                outcomes.append(OutcomeMapping(outcome=number, status="confirmed", tasks=[1], action=[witness],
                    result=[Evidence(unit="task:1:result", quote=units["task:1:result"])],
                    peer=[Evidence(unit="task:1:peer_checks", quote=units["task:1:peer_checks"][:80])], reason="Есть три звена в обязательной работе."))
        return schema(results=[ModelVerdict(criterion=n, state="assessed", mark=1, reason="Механический ответ тестового адаптера.", evidence=[witness]) for n in numbers],
                      outcomes=outcomes), CallMetrics("test-reviewer", 100, 30, 5)


def test_registry_preserves_all_source_rubrics_and_conditional_blocker():
    profile = registry.load()
    assert len(profile.criteria) == 56
    assert [c.number for c in profile.criteria if c.blocker] == [3, 20, 21, 29, 31, 33]
    assert "при ошибке" in profile.criteria[19].title
    assert all(c.fulfilled and c.partial and c.failed for c in profile.criteria)


def test_code_only_is_incomplete_and_never_implies_success(draft):
    report = report_for(draft)
    asyncio.run(engine.run(report, draft, None))
    result = summary(report)
    assert result["incomplete"] and result["recommendation"] != "ready_recommended"
    assert report.verdicts[20].state == "unverified"
    assert report.verdicts[39].state == "no_data"
    assert report.verdicts[52].state == "not_applicable"
    assert all(v.mark is None for v in report.verdicts if v.state != "assessed")


def test_expert_41_cannot_override_a_high_main_score_and_na_has_no_denominator(draft):
    report = report_for(draft)
    for v in report.verdicts:
        v.state, v.mark = "assessed", 1
    report.state = "complete"
    report.verdicts[40].mark, report.verdicts[40].confirmed_blocker = -1, True
    report.verdicts[0].state, report.verdicts[0].mark = "not_applicable", None
    totals = summary(report)
    assert totals["score"] is None and totals["base_assessed"] == 39
    assert totals["fulfilled"] == 39 and totals["finding_count"] == 0
    assert totals["blockers"] == [] and totals["recommendation"] == "ready_recommended"
    assert totals["applicable"] == totals["assessed"] == 39
    assert summary(report, stale=True)["recommendation"] == "stale"


def test_fabricated_quotes_and_missing_verdicts_cannot_be_positive(draft):
    report = report_for(draft)
    engine.accept(report, EvaluationBatch(results=[ModelVerdict(criterion=1, state="assessed", mark=1, reason="Якобы всё есть.",
        evidence=[Evidence(unit="task:1:steps", quote="Этого предложения в проекте не существует.")])]), [1, 2])
    assert report.verdicts[0].state == "unverified" and report.verdicts[0].mark is None
    assert not report.verdicts[0].evidence_valid
    assert report.verdicts[1].state == "error"


def test_an_outcome_chain_cannot_use_a_bonus_or_the_approved_plan_as_actual_work(draft):
    snapshot = build(draft, Context(full_sources=True))
    snapshot.optional_tasks = [1]
    witness = Evidence(unit="handoff", quote=snapshot.units[0].text[:60])
    mapping = OutcomeMapping(outcome=1, status="confirmed", tasks=[1], action=[witness], result=[witness], peer=[witness], reason="Неверная ссылка.")
    checked = engine.validated_outcomes([mapping], snapshot)
    assert len(checked) == len(snapshot.outcomes)
    assert all(m.status == "uncertain" for m in checked)


def test_practical_claims_are_not_confirmed_by_prose_and_20_is_conditional(draft):
    report = report_for(draft)
    witness = Evidence(unit="task:1:steps", quote=next(u.text for u in report.snapshot.units if u.id == "task:1:steps")[:40])
    engine.accept(report, EvaluationBatch(results=[ModelVerdict(criterion=n, state="assessed", mark=1, reason="Текст выглядит убедительно.", evidence=[witness]) for n in [7, 29]]), [7, 29])
    assert report.verdicts[6].mark == report.verdicts[28].mark == 0
    for conditional in [False, True]:
        engine.accept(report, EvaluationBatch(results=[ModelVerdict(criterion=20, state="assessed", mark=-1, reason="Есть ошибка.",
            evidence=[witness], checked_answer_error=conditional)]), [20])
        assert report.verdicts[19].confirmed_blocker is conditional


def test_failure_checkpoints_resume_without_repeating_successful_batches(draft):
    first = report_for(draft)
    writer = Evaluator(fail_at=2)
    asyncio.run(engine.run(first, draft, writer))
    assert first.state == "partial" and len(writer.calls) == 2
    second = report_for(draft)
    resumed = Evaluator()
    asyncio.run(engine.run(second, draft, resumed, first))
    assert second.state == "complete"
    assert set(writer.calls[0]).isdisjoint({n for batch in resumed.calls for n in batch})
    assert any(v.reused for v in second.verdicts)
    assert all(m.status == "confirmed" for m in second.outcomes)


def test_snapshot_covers_requirements_checklist_and_issued_files_without_changing_the_draft(draft):
    before = draft.model_dump_json()
    first = build(draft, Context(full_sources=True), ["README.md", "materials/missing.csv"])
    assert draft.model_dump_json() == before
    missing = next(f for f in first.files if f.path == "materials/missing.csv")
    assert not missing.present
    assert all(not f.path.startswith("src/") for f in first.files if f.role == "issued")
    draft.handoff.customer_requirements += " Нужно обоснование выбора."
    assert build(draft, Context(full_sources=True), first.approved_files).digest != first.digest
    draft.checklist = None
    draft.tasks[0].peer_checks.append("Покажи дополнительный граничный случай.")
    assert build(draft, Context(full_sources=True), first.approved_files).digest != first.digest


def wait(client, run_id):
    for _ in range(150):
        response = client.get(f"/api/content/{run_id}/evaluation")
        assert response.status_code == 200
        if response.json().get("state") != "running":
            return response.json()
        time.sleep(.01)
    raise AssertionError("Evaluation did not finish")


def test_api_owner_cache_exports_staleness_and_no_secret_in_metadata(draft, monkeypatch):
    evaluator = Evaluator()
    monkeypatch.setattr(api, "client_factory", lambda: evaluator)
    with TestClient(app_module.app) as client:
        empty = client.get(f"/api/content/{draft.id}/evaluation")
        assert empty.json()["model_ready"] is True and "private-test-key" not in empty.text
        assert client.get(f"/api/content/{uuid4()}/evaluation").status_code == 404
        started = client.post(f"/api/content/{draft.id}/evaluation", json={"use_model": True})
        assert started.status_code == 202 and "private-test-key" not in started.text
        result = wait(client, draft.id)
        assert result["state"] == "complete" and result["summary"]["incomplete"]
        assert "private-test-key" not in json.dumps(result)
        calls = len(evaluator.calls)
        again = client.post(f"/api/content/{draft.id}/evaluation", json={"use_model": True}).json()
        assert again["id"] == result["id"] and len(evaluator.calls) == calls
        for kind in ["md", "json"]:
            assert client.get(f"/api/content/{draft.id}/evaluation/export/{kind}").status_code == 200
        draft.handoff.constraints += " Работа только офлайн."
        content_store.save(draft)
        assert client.get(f"/api/content/{draft.id}/evaluation").json()["summary"]["stale"]
        draft.owner_id = "another-owner"
        content_store.save(draft)
        assert client.get(f"/api/content/{draft.id}/evaluation").status_code == 404
        assert client.post(f"/api/content/{draft.id}/evaluation", json={"use_model": False}).status_code == 404
        assert client.get(f"/api/content/{draft.id}/evaluation/export/json").status_code == 404


def test_restart_marks_running_reports_interrupted_without_touching_content(draft):
    report = report_for(draft)
    store.save(report)
    before = content_store.path(draft.id).read_bytes()
    store.recover()
    assert store.latest(draft.id).state == "interrupted"
    assert content_store.path(draft.id).read_bytes() == before
    bad = store.folder(draft.id) / "broken.json"
    bad.write_text("not json", encoding="utf-8")
    store.recover()  # A broken report must not prevent the entire application from starting.


def test_unexpected_criteria_in_a_model_batch_are_not_accepted(draft):
    report = report_for(draft)
    engine.accept(report, EvaluationBatch(results=[ModelVerdict(criterion=56, state="assessed", mark=1, reason="Ответ не на тот критерий.")]), [1])
    assert report.verdicts[0].state == "error" and report.verdicts[0].mark is None


def test_a_large_snapshot_is_not_silently_truncated_or_sent_to_the_model(draft):
    draft.handoff.constraints = "длинный вход " * 40000
    report = report_for(draft)
    evaluator = Evaluator()
    asyncio.run(engine.run(report, draft, evaluator))
    assert not evaluator.calls
    assert any("лимит" in note for note in report.notes)
    assert summary(report)["incomplete"]


def test_duplicate_start_and_cancel_leave_a_resumable_report(draft, monkeypatch):
    class Slow:
        calls = 0

        async def generate(self, *args):
            self.calls += 1
            await asyncio.sleep(30)

    slow = Slow()
    monkeypatch.setattr(api, "client_factory", lambda: slow)
    with TestClient(app_module.app) as client:
        first = client.post(f"/api/content/{draft.id}/evaluation", json={"use_model": True}).json()
        second = client.post(f"/api/content/{draft.id}/evaluation", json={"use_model": True}).json()
        assert first["id"] == second["id"] and slow.calls <= 1
        stopped = client.post(f"/api/content/{draft.id}/evaluation/stop").json()
        assert stopped["state"] == "interrupted"
        assert content_store.load(draft.id).summary.status == "draft"
