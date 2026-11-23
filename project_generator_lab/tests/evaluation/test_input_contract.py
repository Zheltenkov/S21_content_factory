"""General input, source visibility, delivery, and complete data-read invariants; no paid calls."""
import asyncio
import json
import os
import subprocess
import sys

import pytest
from project_generator_lab.content import store as content_store
from project_generator_lab.core.llm import CallMetrics
from project_generator_lab.evaluation import engine
from project_generator_lab.evaluation.context import ContextLimit, pack, retrieve
from project_generator_lab.evaluation.inputs import Fact, FileRole, Inputs, contract
from project_generator_lab.evaluation.models import EvaluationBatch, Evidence, EvidenceRequest, ModelVerdict
from project_generator_lab.evaluation.policy import summary
from project_generator_lab.evaluation.profiles import csv_units
from project_generator_lab.evaluation.repository import Identity, read
from project_generator_lab.tests.evaluation.test_repository import source


def report_for(snapshot):
    return engine.initial(Identity("00000000-0000-4000-8000-000000000002", "test"),snapshot,"fake",True)


def test_missing_inputs_are_not_negative_grades_or_invented_targets(tmp_path):
    source(tmp_path)
    report = report_for(read(tmp_path).snapshot)
    for n in [1,2,3,5,6,7,8,35,40,53,54]:
        assert report.verdicts[n-1].state == "no_data", n
        assert report.verdicts[n-1].mark is None
    assert summary(report)["score"] is None
    assert summary(report)["stage"] == "preflight"


def test_declared_inputs_are_source_bound_and_strict(tmp_path):
    source(tmp_path)
    inputs = Inputs(origin="repository",outcomes=Fact(state="declared",value='["Обосновать выбор"]',sources=["input:provided"]))
    snapshot = read(tmp_path,inputs=inputs).snapshot
    assert snapshot.outcomes == ["Обосновать выбор"]
    assert report_for(snapshot).verdicts[0].state == "pending"
    inputs.audience = Fact(state="confirmed",value="Знакомы с файлами",sources=["nonexistent"])
    with pytest.raises(ValueError, match="Неизвестный источник"):
        report_for(read(tmp_path,inputs=inputs).snapshot)
    inputs.outcomes.value = '{"invented":"not an array"}'
    with pytest.raises(ValueError, match="JSON-массив"):
        read(tmp_path,inputs=inputs)


def test_author_folder_and_reviewer_intent_do_not_prove_student_delivery(tmp_path):
    source(tmp_path)
    (tmp_path / "reference.md").write_text("Reviewer only. Reference implementation.",encoding="utf-8")
    unknown = contract(read(tmp_path).snapshot)
    assert unknown.files["reference.md"].purpose == "reviewer"
    assert unknown.files["reference.md"].delivery == "unknown"
    inputs=Inputs(origin="repository",package_kind="student")
    assert contract(read(tmp_path,inputs=inputs).snapshot).files["reference.md"].delivery == "issued"
    inputs.files["reference.md"] = FileRole(purpose="reviewer",delivery="not_issued",basis="manifest",sources=["input:provided"])
    assert contract(read(tmp_path,inputs=inputs).snapshot).files["reference.md"].delivery == "not_issued"
    report = report_for(read(tmp_path).snapshot)
    engine.accept(report,EvaluationBatch(results=[ModelVerdict(criterion=17,state="assessed",mark=-1,reason="Участник якобы видит ответ.",
        evidence=[Evidence(unit="file:reference.md",quote="Reference implementation.")],delivery_claims=["reference.md"])]),[17])
    assert report.verdicts[16].state == "unverified" and report.verdicts[16].mark is None
    author=report_for(read(tmp_path,inputs=Inputs(origin="repository",package_kind="author")).snapshot)
    assert author.verdicts[34].state == "no_data"


def test_explicit_manifest_missing_input_is_a_real_dependency(tmp_path):
    source(tmp_path)
    inputs=Inputs(origin="repository",files={"assets/required.json":FileRole(purpose="input",delivery="issued",basis="manifest",sources=["input:provided"])})
    report=report_for(read(tmp_path,inputs=inputs).snapshot)
    assert report.verdicts[30].mark == -1
    assert "assets/required.json" in report.verdicts[30].reason


def test_wrapped_repository_and_sensitive_configs_are_handled_explicitly(tmp_path):
    folder=tmp_path / "wrapper"
    folder.mkdir()
    source(folder)
    (folder / ".env.local").write_text("DO_NOT_PASS_THIS_SECRET",encoding="utf-8")
    imported=read(tmp_path)
    assert any(f.path == "materials/input.csv" for f in imported.snapshot.files)
    assert "DO_NOT_PASS_THIS_SECRET" not in imported.snapshot.model_dump_json()
    assert any(f["status"]=="excluded_sensitive_config" for f in imported.manifest)


def test_snapshot_roles_and_digest_are_stable_across_hash_seeds(tmp_path):
    source(tmp_path)
    path=tmp_path / "README.md"
    path.write_text(path.read_text(encoding="utf-8")+"\nЗапусти python helpers/two.py и python helpers/one.py.\n",encoding="utf-8")
    code="from pathlib import Path; import sys; from project_generator_lab.evaluation.repository import read; print(read(Path(sys.argv[1])).snapshot.digest)"
    digests=[subprocess.check_output([sys.executable,"-c",code,str(tmp_path)],env={**os.environ,"PYTHONHASHSEED":str(seed)},text=True).strip() for seed in [1,31]]
    assert digests[0] == digests[1]


def test_compound_spec_is_not_lost_without_numbered_tasks(tmp_path):
    source(tmp_path)
    text="# Исследование\n\n## Requirements\n\nПострой инструмент анализа.\n\n### Ограничения\n\nСохрани объяснение выбора в отчёте.\n"
    (tmp_path / "README.md").write_text(text,encoding="utf-8")
    snapshot = read(tmp_path).snapshot
    assert snapshot.task_numbers == [1]
    assert contract(snapshot).task_mode == "whole_project"
    assert next(u.text for u in snapshot.units if u.id == "file:README.md") == text
    assert any("Сохрани объяснение" in u.text for u in snapshot.units)
    assert report_for(snapshot).verdicts[3].state == "no_data"
    inputs=Inputs(origin="repository",outcomes=Fact(state="declared",value='["Обосновать выбор"]',sources=["input:provided"]))
    # Unknown mandatory/bonus boundaries cannot prove that an outcome is covered by mandatory work.
    assert report_for(read(tmp_path,inputs=inputs).snapshot).verdicts[2].state == "no_data"


def test_required_tool_can_live_anywhere_and_generated_output_is_not_missing(tmp_path):
    source(tmp_path)
    (tmp_path / "README.md").write_text("# Проверка\n\n## Задание 1. Работа\n\nСоздай work/analysis.py используя materials/input.csv.\nЗапусти python helpers/verify.py.\n\n**Сдать:** work/analysis.py\n",encoding="utf-8")
    snapshot = read(tmp_path).snapshot
    files={f.path:f for f in snapshot.files}
    assert files["helpers/verify.py"].required and not files["helpers/verify.py"].present
    assert files["materials/input.csv"].role == "issued"
    assert not any(f.path == "work/analysis.py" and not f.present for f in snapshot.files)
    assert report_for(snapshot).verdicts[30].mark == -1


def test_complete_profile_covers_tail_unicode_categories_and_finite_numbers():
    text="id,channel,amount\n" + "".join(f"{n},email,{n}\n" for n in range(1,4001)) + '4001,Чат,-42\n4002,Chat,1e308\n4003,chat,1e308\n'
    units = csv_units("records.csv",text,"hash")
    profile,sample=[json.loads(u.text) for u in units]
    assert profile["rows_read"] == 4003 and profile["all_rows_read"]
    assert profile["columns"][1]["value_counts"] == {"email":4000,"Чат":1,"Chat":1,"chat":1}
    assert profile["columns"][2]["min"] == -42
    assert any(row["record"] == 4003 for row in sample["rows"])
    assert "Infinity" not in units[0].text
    assert not profile["sample_is_exhaustive"]
    assert "Infinity" not in csv_units("large.csv","value\n1e308\n1e308\n","hash")[0].text


def test_bulk_context_is_explicit_and_unseen_quotes_are_rejected(tmp_path):
    source(tmp_path)
    content="value\n" + "12345678\n"*50000 + "unique_tail_value\n"
    (tmp_path / "materials/input.csv").write_text(content,encoding="utf-8")
    snapshot=read(tmp_path).snapshot
    packed=pack(snapshot,[11],[],engine.MAX_CONTEXT_CHARS)
    assert len(packed.text([],engine.MAX_CONTEXT_CHARS)) < engine.MAX_CONTEXT_CHARS
    assert "file:materials/input.csv" not in packed.visible
    evidence=Evidence(unit="file:materials/input.csv",quote="unique_tail_value")
    assert not engine.evidence_errors([evidence],snapshot)
    assert engine.evidence_errors([evidence],snapshot,packed.visible)
    fragment=retrieve(EvidenceRequest(unit=evidence.unit,mode="search",query="unique_tail_value"),snapshot)
    packed.visible[evidence.unit]=[fragment["text"]]
    assert not engine.evidence_errors([evidence],snapshot,packed.visible)
    with pytest.raises(ContextLimit):
        pack(snapshot,[11],[],100)


def test_csv_retrieval_is_exact_for_multiline_records_and_immutable(tmp_path):
    source(tmp_path)
    text='id,note\n1,"first\nsecond"\n2,end\n'
    path=tmp_path / "materials/input.csv"
    path.write_text(text,encoding="utf-8",newline="\n")
    snapshot=read(tmp_path).snapshot
    path.write_text("MODIFIED AFTER SNAPSHOT",encoding="utf-8")
    fragment=retrieve(EvidenceRequest(unit="file:materials/input.csv",mode="csv_records",start=1,end=1),snapshot)
    assert fragment["text"] == '1,"first\nsecond"\n'
    assert "error" in retrieve(EvidenceRequest(unit="../../outside"),snapshot)
    assert "error" in retrieve(EvidenceRequest(unit="file:README.md",start=1,end=200),snapshot)


@pytest.mark.parametrize("finish",[True,False])
def test_requested_evidence_rounds_are_bounded_and_recorded(tmp_path,monkeypatch,finish):
    source(tmp_path)
    monkeypatch.setattr(content_store,"CONTENT_DIR",tmp_path / "results")
    snapshot=read(tmp_path).snapshot
    report=report_for(snapshot)
    for verdict in report.verdicts:
        verdict.state="no_data"
    report.verdicts[10].state="pending"
    class Client:
        count=0
        async def generate(self,schema,system,payload):
            self.count+=1
            data=json.loads(payload)
            if self.count==1 or not finish:
                return EvaluationBatch(results=[],requests=[EvidenceRequest(unit="file:README.md",mode="search",query="Учебный проект")]),CallMetrics("fake",1,1,1)
            assert data["evidence_fragments"]
            return EvaluationBatch(results=[ModelVerdict(criterion=11,state="assessed",mark=0,reason="Проверяемая цитата.",evidence=[Evidence(unit="file:README.md",quote="Учебный проект")])]),CallMetrics("fake",1,1,1)
    client=Client()
    asyncio.run(engine.run(report,None,client,code_findings=[]))
    assert report.calls == client.count == (2 if finish else 3)
    assert report.verdicts[10].state == ("assessed" if finish else "unverified")
    assert any("Дополнительное чтение" in note for note in report.notes)
