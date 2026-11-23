"""External files are evaluated as read-only evidence, without re-rendering or invented approved inputs."""
import asyncio
import hashlib

from project_generator_lab.content import store as content_store
from project_generator_lab.core.llm import CallMetrics
from project_generator_lab.evaluation import engine
from project_generator_lab.evaluation.models import EvaluationBatch, Evidence, ModelVerdict
from project_generator_lab.evaluation.repository import Identity, read


def source(root):
    (root / "README.md").write_text("# Учебный проект\n\nВведение.\n\n## Задание 1. Анализ\n\nОткрой materials/input.csv и объясни выбор.\n\n**Сдать:** src/report.md\n",encoding="utf-8",newline="\n")
    (root / "check-list.yml").write_text("questions:\n- name: Задание 1\n  checking: Объясни, почему этот выбор подходит.\n",encoding="utf-8",newline="\n")
    (root / "materials").mkdir()
    (root / "materials/input.csv").write_text("value\n1\n2\n",encoding="utf-8",newline="\n")


def test_external_snapshot_preserves_files_and_does_not_invent_outcomes(tmp_path):
    source(tmp_path)
    before = {p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    imported = read(tmp_path)
    snapshot = imported.snapshot
    assert snapshot.outcomes == [] and not snapshot.curriculum_available and snapshot.approved_files is None
    assert snapshot.format == "unknown"
    assert next(u.text for u in snapshot.units if u.id == "file:README.md") == before["README.md"].decode()
    assert not any(u.id == "handoff" for u in snapshot.units)
    assert next(f.sha256 for f in snapshot.files if f.path == "materials/input.csv") == hashlib.sha256(before["materials/input.csv"]).hexdigest()
    assert {p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert read(tmp_path).snapshot.digest == snapshot.digest


def test_case_labels_and_excluded_reviews_do_not_enter_model_context(tmp_path):
    project = tmp_path / "Плохой проект"
    project.mkdir()
    source(project)
    (project / "review.md").write_text("SECRET_EXPECTED_NEGATIVE_SCORE",encoding="utf-8")
    imported = read(project,skip=("review.md",))
    payload = imported.snapshot.model_dump_json()
    assert "Плохой проект" not in payload and "SECRET_EXPECTED_NEGATIVE_SCORE" not in payload
    assert any(f["status"] == "excluded_by_case_manifest" for f in imported.manifest)
    assert not any(f.path == "review.md" for f in imported.snapshot.files)


def test_large_dataset_is_not_silently_cropped_to_fit_context(tmp_path):
    source(tmp_path)
    content = "value\n" + "10000000\n" * 50000
    (tmp_path / "materials/input.csv").write_text(content,encoding="utf-8",newline="\n")
    snapshot = read(tmp_path).snapshot
    assert next(u.text for u in snapshot.units if u.id == "file:materials/input.csv") == content
    assert len(snapshot.model_dump_json()) > engine.MAX_CONTEXT_CHARS


def test_external_path_uses_same_acceptance_and_aggregation(tmp_path,monkeypatch):
    source(tmp_path)
    imported = read(tmp_path)
    monkeypatch.setattr(content_store,"CONTENT_DIR",tmp_path / "isolated-results")
    report = engine.initial(Identity("00000000-0000-4000-8000-000000000001","test"),imported.snapshot,"fake",True)

    class Client:
        async def generate(self,schema,system,payload):
            import json
            data=json.loads(payload)
            evidence=Evidence(unit="file:README.md",quote=next(u.text for u in imported.snapshot.units if u.id=="file:README.md")[:80])
            return EvaluationBatch(results=[ModelVerdict(criterion=c["number"],state="assessed",mark=1,
                reason="Синтетический ответ теста.",evidence=[evidence]) for c in data["supplied_criteria"]]),CallMetrics("fake",1,1,1)

    asyncio.run(engine.run(report,None,Client(),code_findings=imported.code_findings))
    assert report.state=="complete" and report.calls>0
    assert report.verdicts[2].mark is None  # No approved outcomes: a fabricated positive chain cannot pass.
    assert all(v.mark is None for v in report.verdicts[40:])
