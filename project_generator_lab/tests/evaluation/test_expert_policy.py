"""Expert review cannot affect the scored result, including legacy reports and late failures."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.core.llm import ModelUnavailable
from project_generator_lab.evaluation import engine, store
from project_generator_lab.evaluation.models import EvaluationBatch, Evidence, ModelVerdict
from project_generator_lab.evaluation.policy import presentation, summary
from project_generator_lab.tests.evaluation.test_evaluation import Evaluator, report_for
from project_generator_lab.tests.evaluation.test_evaluation import draft as draft  # noqa: F401


def finished_base(draft):
    report = report_for(draft)
    for verdict in report.verdicts[:40]:
        verdict.state, verdict.mark, verdict.confirmed_blocker = "assessed", 1, False
    report.state = "complete"
    return report


@pytest.mark.parametrize("state", ["pending", "assessed", "not_applicable", "no_data", "unverified", "error"])
@pytest.mark.parametrize("job_state", ["running", "complete", "partial", "interrupted"])
def test_expert_states_and_legacy_negative_marks_do_not_change_result(draft, state, job_state):
    report = finished_base(draft)
    before = summary(report)
    report.state = job_state
    for verdict in report.verdicts[40:]:
        verdict.state, verdict.mark, verdict.confirmed_blocker = state, -1 if state == "assessed" else None, True
    after = summary(report)
    for key in ("recommendation", "score", "assessed", "applicable", "unverified", "counts", "incomplete", "blockers", "problems", "partial"):
        assert before[key] == after[key], key
    assert after["recommendation"] == "ready_recommended"
    assert "extra_sum" not in after and "extra_assessed" not in after


def test_main_gap_and_main_blocker_still_control_result(draft):
    report = finished_base(draft)
    report.verdicts[0].state, report.verdicts[0].mark = "unverified", None
    assert summary(report)["incomplete"] and summary(report)["unverified"] == 1
    assert summary(report)["recommendation"] == "preliminary"
    report.verdicts[2].mark, report.verdicts[2].confirmed_blocker = -1, True
    assert summary(report)["blockers"] == [3] and summary(report)["recommendation"] == "rework"


@pytest.mark.parametrize("mark", [-1, 0, 1, None])
def test_expert_model_output_has_evidence_but_no_numeric_grade(draft, mark):
    report = report_for(draft)
    unit = next(u for u in report.snapshot.units if u.id == "task:1:steps")
    engine.accept(report, EvaluationBatch(results=[ModelVerdict(criterion=41, state="assessed", mark=mark,
        reason="Предварительное наблюдение о разнообразии данных.",
        evidence=[Evidence(unit=unit.id, quote=unit.text[:80])], suggestion="Сопоставить с примерами методолога.")]), [41])
    verdict = report.verdicts[40]
    assert verdict.state == "assessed" and verdict.mark is None and not verdict.confirmed_blocker
    assert verdict.evidence and verdict.suggestion
    engine.accept(report, EvaluationBatch(results=[ModelVerdict(criterion=41, state="assessed", mark=None,
        reason="Наблюдение без проверяемой цитаты.")]), [41])
    assert report.verdicts[40].state == "unverified" and not report.verdicts[40].evidence_valid


def test_old_expert_scores_are_hidden_in_api_and_exports_without_mutating_history(draft):
    report = finished_base(draft)
    for verdict in report.verdicts[40:]:
        verdict.state, verdict.mark, verdict.confirmed_blocker = "assessed", -1, True
        verdict.reason = "EXPERT-NOTE-PRESERVED"
    report.profile.criteria[40].blocker = True
    report.profile.criteria[40].title += " [блок]"
    store.save(report)
    path = store.folder(draft.id) / f"{report.id}.json"
    before = path.read_bytes()
    assert presentation(report).verdicts[40].mark is None
    assert report.verdicts[40].mark == -1
    with TestClient(app_module.app) as client:
        body = client.get(f"/api/content/{draft.id}/evaluation").json()
        exported = client.get(f"/api/content/{draft.id}/evaluation/export/json").json()
        text = client.get(f"/api/content/{draft.id}/evaluation/export/md").text
        for data in (body, exported):
            assert all(v["mark"] is None and not v["confirmed_blocker"] for v in data["verdicts"][40:])
            assert not data["profile"]["criteria"][40]["blocker"]
            assert "[блок]" not in data["profile"]["criteria"][40]["title"]
            assert data["summary"]["blockers"] == []
        expert_text = text.split("## 41.", 1)[1]
        assert "оценка:" not in expert_text and "[блок]" not in expert_text
        assert "EXPERT-NOTE-PRESERVED" in expert_text and "без баллов" in expert_text
        assert "дополнительная сумма" not in text
    assert path.read_bytes() == before


def test_successful_model_route_also_stores_expert_marks_as_null(draft):
    report = report_for(draft)
    evaluator = Evaluator()
    asyncio.run(engine.run(report, draft, evaluator))
    assert all(all(n <= 40 for n in batch) or all(n > 40 for n in batch) for batch in evaluator.calls)
    assert any(v.state == "assessed" for v in report.verdicts[40:])
    assert all(v.mark is None and not v.confirmed_blocker for v in report.verdicts[40:])
    assert all(v["mark"] is None for v in json.loads((store.folder(draft.id) / f"{report.id}.json").read_text())["verdicts"][40:])


def test_failure_of_expert_batch_does_not_change_completed_main_verdicts(draft):
    class FailingReview(Evaluator):
        async def generate(self, schema, system, payload):
            if all(c["number"] > 40 for c in json.loads(payload)["supplied_criteria"]):
                raise ModelUnavailable("Expert review unavailable")
            return await super().generate(schema, system, payload)

    completed, partial = report_for(draft), report_for(draft)
    asyncio.run(engine.run(completed, draft, Evaluator()))
    asyncio.run(engine.run(partial, draft, FailingReview()))
    assert partial.state == "partial"
    assert [v.model_dump() for v in completed.verdicts[:40]] == [v.model_dump() for v in partial.verdicts[:40]]
    assert summary(completed)["recommendation"] == summary(partial)["recommendation"]
    assert summary(completed)["incomplete"] == summary(partial)["incomplete"]
