"""Regressions from independent critics: resources, evidence, recovery and cancellation."""
import asyncio
import json
import os
import subprocess
import sys
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab import bridge
from project_generator_lab.concept.models import Material as InputMaterial
from project_generator_lab.concept.models import Passage, SourceRecord
from project_generator_lab.content import store as content_store
from project_generator_lab.content.models import Material
from project_generator_lab.core.llm import ModelUnavailable
from project_generator_lab.evaluation import api, engine, store
from project_generator_lab.evaluation.models import Context, EvaluationBatch, Evidence, ModelVerdict, OutcomeMapping
from project_generator_lab.evaluation.policy import summary
from project_generator_lab.evaluation.snapshot import build
from project_generator_lab.tests.concept.test_validation import new_run
from project_generator_lab.tests.evaluation.test_evaluation import draft as draft  # noqa: F401
from project_generator_lab.tests.evaluation.test_evaluation import report_for, wait


def test_deleted_material_is_detected_from_task_dependencies(draft):
    draft.tasks[0].steps.append("Открой материалы materials/mandatory.csv и рассчитай итог.")
    report = report_for(draft)
    missing = next(f for f in report.snapshot.files if f.path == "materials/mandatory.csv")
    assert not missing.present and missing.required is True and missing.tasks == [1]
    assert report.verdicts[30].mark == -1 and report.verdicts[30].confirmed_blocker
    assert report.verdicts[31].mark == -1
    # Editing the actual source to remove the dependency removes the finding, not a hardcoded exception.
    draft.tasks[0].steps.pop()
    assert not report_for(draft).verdicts[30].confirmed_blocker


def test_empty_file_and_real_zip_manifest_are_not_missing(draft):
    draft.materials.append(Material(path="empty.txt", kind="data", purpose="Проверка пустого ввода",
                                    contains="Намеренно пустой файл", content="", tasks=[1], status="approved"))
    snapshot = build(draft, Context(full_sources=True))
    empty = next(f for f in snapshot.files if f.path == "materials/empty.txt")
    assert empty.present and empty.empty
    manifest = [f.path for f in snapshot.files if f.present]
    report = engine.initial(draft, build(draft, Context(full_sources=True), manifest), "", False)
    assert "src/.gitkeep" in manifest
    assert report.verdicts[30].mark == 1 and not report.verdicts[30].confirmed_blocker
    assert "расходится" not in report.verdicts[39].reason


def test_generated_bundle_uses_the_same_complete_csv_and_dependency_contract(draft):
    draft.materials.append(Material(path="events.csv", kind="data", purpose="Учебные события",
        contains="CSV",content="id,value\n1,42\n2,17\n",tasks=[1],status="approved"))
    draft.tasks[0].steps.append("Запусти python helpers/validate_events.py.")
    snapshot = build(draft, Context(full_sources=True))
    profile = next(u for u in snapshot.units if u.id == "data:materials/events.csv:profile")
    assert json.loads(profile.text)["rows_read"] == 2
    missing = next(f for f in snapshot.files if f.path == "helpers/validate_events.py")
    assert missing.required is True and not missing.present


def test_optional_dependency_cannot_block_or_cover_a_mandatory_task(draft):
    draft.handoff.tasks[0].optional = True
    draft.tasks[0].steps.append("Прочитай [данные](materials/bonus.csv) для дополнительной работы.")
    report = report_for(draft)
    assert report.verdicts[30].mark == 0 and not report.verdicts[30].confirmed_blocker
    assert report.verdicts[31].mark == 0
    # The same dependency in a compulsory task changes its requiredness.
    draft.handoff.tasks[0].optional = False
    assert report_for(draft).verdicts[30].confirmed_blocker


def test_declared_learner_outputs_and_external_clone_are_not_missing_inputs(draft):
    draft.tasks[0].result = "Созданный тобой файл materials/answer.csv и src/result.csv."
    draft.tasks[0].steps.append("Создай materials/answer.csv, сохрани ответ в src/result.csv.")
    draft.handoff.repository = "https://git.example.org/team/project.git"
    draft.tasks[0].steps.append("Выполни git clone https://git.example.org/team/project.git и открой репозиторий.")
    report = report_for(draft)
    assert not any(f.path == "materials/answer.csv" for f in report.snapshot.files)
    assert report.verdicts[31].mark == 0
    assert not report.verdicts[30].confirmed_blocker


@pytest.mark.parametrize("mark", [0, 1])
def test_a_gap_and_nonnegative_outcome_grade_require_rechecking(draft, mark):
    report = report_for(draft)
    unit = next(u for u in report.snapshot.units if u.id == "task:1:steps")
    engine.accept(report, EvaluationBatch(results=[ModelVerdict(criterion=3, state="assessed", mark=mark,
        reason="Все обязательные ОР охвачены.", evidence=[Evidence(unit=unit.id, quote=unit.text[:80])])],
        outcomes=[OutcomeMapping(outcome=1, status="gap", reason="ОР не проверяется.")]), [3])
    assert report.verdicts[2].state == "unverified" and report.verdicts[2].mark is None
    assert not report.verdicts[2].confirmed_blocker


def test_canceled_sources_do_not_poison_active_source_coverage(draft, monkeypatch):
    run = new_run()
    run.request.brief.curriculum_policy = "ignore"
    material = InputMaterial(id="m1", name="Excluded.txt", sha256="fixture", read_status="partial",
                             passages=[Passage(id="p1", location="Текст", text="Удалённый методологом текст")])
    run.request.materials = [material]
    run.request.brief.excluded_sources = "p1"
    run.source_registry = [SourceRecord(id="curriculum", name="Ignored UP", role="curriculum", sha256="fixture",
        read_status="failed", extracted_count=0, selected_count=0),
        SourceRecord(id="m1", name=material.name, role="reference", sha256="fixture", read_status="partial", extracted_count=1, selected_count=0)]
    monkeypatch.setattr(bridge.runs, "load_run", lambda _: run)
    context = bridge.evaluation_context(run.id)
    assert not context.limitations and not context.curriculum_available
    assert all(not s["used_in_check"] for s in json.loads(next(u.text for u in context.units if u.id == "input:registry")))
    run.request.brief.excluded_sources = ""
    assert any(material.name in text for text in bridge.evaluation_context(run.id).limitations)


def test_corrupt_history_does_not_prevent_get_start_stop_or_export(draft):
    previous = report_for(draft)
    store.save(previous)
    broken = store.folder(draft.id) / "broken.json"
    broken.write_text("not JSON", encoding="utf-8")
    assert store.latest(draft.id).id == previous.id
    assert store.latest(draft.id).history_warnings
    assert summary(store.latest(draft.id))["incomplete"]
    with TestClient(app_module.app) as client:
        assert client.get(f"/api/content/{draft.id}/evaluation").status_code == 200
        result = client.post(f"/api/content/{draft.id}/evaluation", json={"use_model": False, "force": True})
        assert result.status_code == 202
        assert wait(client, draft.id)["id"] != previous.id
        assert client.post(f"/api/content/{draft.id}/evaluation/stop").status_code == 200
        assert client.get(f"/api/content/{draft.id}/evaluation/export/md").status_code == 200
    assert broken.read_text(encoding="utf-8") == "not JSON"
    # A valid foreign owner is never treated as a corrupt record to skip.
    foreign = report_for(draft)
    foreign.owner_id = "another-owner"
    store.save(foreign)
    with pytest.raises(HTTPException) as error:
        store.latest(draft.id)
    assert error.value.status_code == 404


def test_markdown_keeps_limitations_notes_and_outcome_evidence(draft):
    report = report_for(draft)
    report.snapshot.limitations = ["Источник передан сокращённо."]
    report.notes = ["Проверка остановлена после сбоя."]
    unit = next(u for u in report.snapshot.units if u.id == "task:1:steps")
    report.outcomes = [OutcomeMapping(outcome=1, status="gap", tasks=[1],
        action=[Evidence(unit=unit.id, quote=unit.text[:80])], reason="Результат не проверяется.")]
    text = api.markdown(report, True)
    assert all(s in text for s in [*report.notes, *report.snapshot.limitations, report.outcomes[0].reason,
                                  unit.id, unit.text[:80], "Свидетельство не предъявлено", "устарело"])


def test_failed_call_preserves_known_usage_and_attempt(draft):
    class Failed:
        async def generate(self, *args):
            error = ModelUnavailable("Synthetic failure")
            error.input_tokens, error.output_tokens = 100, 25
            raise error

    report = report_for(draft)
    asyncio.run(engine.run(report, draft, Failed()))
    assert report.calls == report.failed_calls == 1
    assert (report.input_tokens, report.output_tokens) == (100, 25)
    assert report.state == "partial"


def test_concurrent_stop_cannot_interrupt_a_new_start(draft):
    async def scenario():
        report = report_for(draft)
        store.save(report)
        entered, canceled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def worker():
            entered.set()
            try:
                await asyncio.Future()
            finally:
                canceled.set()
                await release.wait()
                report.state = "interrupted"
                store.save(report)

        old_task = asyncio.create_task(worker())
        api.jobs[draft.id] = old_task
        await entered.wait()
        stop = asyncio.create_task(api.stop_evaluation(UUID(draft.id)))
        await canceled.wait()
        start = asyncio.create_task(api.start_evaluation(UUID(draft.id), api.Start(use_model=False, force=True)))
        await asyncio.sleep(0)
        assert not start.done(), "start must wait until the old task checkpoint is saved"
        release.set()
        stopped, started = await asyncio.gather(stop, start)
        assert stopped["id"] == report.id
        assert started["id"] != report.id and started["state"] == "running"
        task = api.jobs.get(draft.id)
        if task is not None:
            await task
        assert store.latest(draft.id).state == "complete"
        assert content_store.load(draft.id).summary.status == "draft"

    asyncio.run(scenario())


def test_snapshot_digest_is_stable_across_python_processes(draft, tmp_path):
    draft.tasks[0].steps.append("Используй materials/a.csv, materials/b.csv, materials/c.csv, materials/d.csv.")
    source = tmp_path / "draft.json"
    source.write_text(draft.model_dump_json(), encoding="utf-8")
    code = """import sys
from pathlib import Path
from project_generator_lab.content.models import ContentDraft
from project_generator_lab.evaluation.models import Context
from project_generator_lab.evaluation.snapshot import build
draft = ContentDraft.model_validate_json(Path(sys.argv[1]).read_text(encoding='utf-8'))
print(build(draft, Context(full_sources=True)).digest)
"""
    digests = [subprocess.check_output([sys.executable, "-c", code, str(source)],
               env={**os.environ, "PYTHONHASHSEED": str(seed)}, text=True).strip() for seed in (1, 23)]
    assert len(set(digests)) == 1
