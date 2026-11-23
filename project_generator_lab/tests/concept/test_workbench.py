"""Meaningful regressions for source fidelity, readiness and failure recovery."""

import asyncio
import io
from pathlib import Path
from uuid import uuid4

import pytest
from docx import Document
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.concept import runs as api_module
from project_generator_lab.concept.ingestion import InputError, extract_material, parse_curriculum
from project_generator_lab.concept.models import (
    Brief,
    Concept,
    Decision,
    Material,
    Passage,
    Review,
    Run,
    StartRequest,
    TaskPlan,
    TaskWorkload,
)
from project_generator_lab.concept.planning import build_evidence, check_concept, fingerprint, prepare
from project_generator_lab.core.llm import CallMetrics, ModelClient, ModelUnavailable
from pydantic import ValidationError

ROOT = Path(__file__).parents[3]


def request_fixture() -> StartRequest:
    return StartRequest(request_id=uuid4(), brief=Brief(
        title="Разбор интервью", description="Сформулировать проблему клиента по интервью.",
        audience="Начинающие аналитики", hours=4, outcomes="Выделять факты\nОбосновывать выводы",
        checking="Пир проверяет связь выводов с данными", student_result="Отчёт", output_package="README и чек-лист",
    ))


def concept_fixture() -> Concept:
    return Concept(
        title="Разбор интервью", program_summary="Программа анализа проблем клиента", project_role="Первый проект",
        student_result="Отчёт с основаниями", prerequisites=["Чтение текста"], later_use=["Проверка гипотез"],
        out_of_scope=["Разработка продукта"], tasks=[TaskPlan(title="Выделить факты", action="Сопоставить фрагменты интервью",
            outcome_numbers=[1, 2], deliverable="Отчёт", peer_check="Проверить связь выводов с цитатами",
            independent_choice="Выбор существенных наблюдений", support="Краткое описание анализа", evidence_ids=["form.outcomes"], hours=4,
            workload=TaskWorkload(reading=0.75, setup=0, doing=2.25, debugging=0.75, submission=0.25))],
        decisions=[Decision(title="Результат", explanation="Отчёт позволяет проверить основания вывода", kind="proposal", evidence_ids=["form.outcomes"])],
        questions=[], material_gaps=[],
    )


def test_both_real_curriculum_formats_preserve_context_and_raw():
    for name in (ROOT / "project_generator_lab/concept/examples/curriculum.csv", Path(__file__).parent / "fixtures/legacy_curriculum.csv"):
        result = parse_curriculum(name.name, name.read_bytes())
        assert len(result.rows) > 3
        assert result.rows[0].block != "Без блока"
        assert result.rows[0].raw
        assert result.rows[0].outcomes
        assert result.rows[0].hours


def test_semicolon_and_quoted_multiline_csv():
    data = 'Тематический блок;№;Название проекта;Краткое описание\nБлок;1;Тема;"Строка один\nСтрока два"\n;2;Другая;Описание'.encode()
    result = parse_curriculum("plan.csv", data)
    assert result.rows[0].description == "Строка один\nСтрока два"
    assert result.rows[1].block == "Блок"


def test_distant_curriculum_rows_are_valid_evidence():
    request = request_fixture()
    request.curriculum = parse_curriculum("plan.csv", (ROOT / "project_generator_lab/concept/examples/curriculum.csv").read_bytes())
    request.selected_row_id = request.curriculum.rows[0].id
    evidence, _ = build_evidence(request)
    assert all(row.id in {e.id for e in evidence} for row in request.curriculum.rows)
    concept = concept_fixture()
    concept.decisions[0].evidence_ids = [request.curriculum.rows[-1].id]
    assert not check_concept(concept, request, evidence)


def test_docx_preserves_table_and_paragraph_locations():
    document = Document()
    document.add_paragraph("Требование: итоговая проверка без изменения решения")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Результат"
    table.cell(0, 1).text = "Отчёт"
    buffer = io.BytesIO()
    document.save(buffer)
    result = extract_material("rules.docx", buffer.getvalue())
    assert any("Таблица 1" in p.location and "Отчёт" in p.text for p in result.passages)
    assert result.role == "reference"
    assert result.warnings


def test_script_is_only_text(tmp_path):
    target = tmp_path / "must-not-exist"
    script = f"from pathlib import Path\nPath({str(target)!r}).touch()"
    result = extract_material("reference.py", script.encode())
    assert "touch" in result.passages[0].text
    assert not target.exists()


def test_empty_and_unsupported_files_have_clear_errors():
    with pytest.raises(InputError):
        extract_material("empty.txt", b"")
    with pytest.raises(InputError):
        extract_material("archive.exe", b"not an input")
    with pytest.raises(InputError):
        parse_curriculum("bad.csv", b"a,b\n1,2")


def test_requirements_are_never_silently_sampled():
    request = request_fixture()
    passages = [Passage(id=f"s-{i}", location=f"p{i}", text="требование") for i in range(15)]
    request.materials = [Material(id="rules", name="rules.md", sha256="a", role="requirement", passages=passages)]
    evidence, _ = build_evidence(request)
    assert sum(e.source == "rules.md" for e in evidence) == 15
    request.materials[0].role = "reference"
    evidence, warnings = build_evidence(request)
    assert sum(e.source == "rules.md" for e in evidence) == 10
    assert any("10 из 15" in w for w in warnings)


def test_oversized_required_context_is_rejected_not_truncated():
    request = request_fixture()
    request.materials = [Material(id="rules", name="rules.md", sha256="a", role="requirement", passages=[Passage(id=str(i), location="p", text="x" * 10000) for i in range(12)])]
    with pytest.raises(ValueError, match="не были обрезаны"):
        build_evidence(request)


def test_missing_coverage_fake_sources_and_excess_hours_block():
    request = request_fixture()
    concept = concept_fixture()
    concept.tasks[0].outcome_numbers = [1]
    concept.tasks[0].hours = 5
    concept.tasks[0].evidence_ids = ["invented"]
    findings = check_concept(concept, request, build_evidence(request)[0])
    assert len(findings) == 3
    assert all(f.severity == "blocker" for f in findings)


def test_bonus_cannot_cover_required_outcome():
    request = request_fixture()
    concept = concept_fixture()
    concept.tasks[0].optional = True
    findings = check_concept(concept, request, build_evidence(request)[0])
    assert any("Не покрыты" in f.message for f in findings)
    assert any("бонус" in f.message for f in findings)


def test_group_size_and_selected_row_are_validated():
    with pytest.raises(ValidationError):
        Brief(title="Группа", description="Работа в группе", audience="Начальный", outcomes="Анализировать", format="group")
    request = request_fixture().model_dump(mode="json")
    request["curriculum"] = parse_curriculum("plan.csv", (ROOT / "project_generator_lab/concept/examples/curriculum.csv").read_bytes()).model_dump()
    with pytest.raises(ValidationError):
        StartRequest.model_validate(request)


def test_model_failure_preserves_concept_without_fake_review():
    from project_generator_lab.concept.models import InputCard
    from project_generator_lab.tests.concept.workflow_fixtures import input_card

    class FailingReviewer(ModelClient):
        async def generate(self, schema, system, payload):
            if schema is InputCard:
                return input_card(request), CallMetrics("test", 1, 20, 20)
            if schema is Concept:
                return concept_fixture(), CallMetrics("test", 1, 20, 20)
            raise ModelUnavailable("Проверка недоступна")

    request = request_fixture()
    run = Run(id=str(request.request_id), fingerprint=fingerprint(request), request=request)
    snapshots = []
    asyncio.run(prepare(run, lambda r: snapshots.append(r.model_copy(deep=True)), FailingReviewer()))
    assert run.status == "input_review"
    run.input_approved_at = "2026-09-22T10:00:00+00:00"
    run.status = "running"
    asyncio.run(prepare(run, lambda r: snapshots.append(r.model_copy(deep=True)), FailingReviewer()))
    assert run.status == "failed"
    assert run.concept is not None
    assert run.review is None
    assert run.events[-1].status == "failed"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RUNS_DIR", tmp_path)
    with TestClient(app_module.app) as client:
        yield client


def test_api_upload_body_survives_guard_and_rejects_cross_origin(client):
    response = client.post("/api/materials", files={"file": ("notes.md", b"project requirements", "text/plain")})
    assert response.status_code == 200
    assert response.json()["passages"][0]["text"] == "project requirements"
    response = client.post("/api/materials", headers={"Origin": "https://unrelated.example"}, files={"file": ("notes.md", b"data")})
    assert response.status_code == 403


def test_approval_requires_review_and_is_idempotent(client):
    request = request_fixture()
    run = Run(id=str(request.request_id), fingerprint=fingerprint(request), request=request, concept=concept_fixture())
    api_module.save_run(run)
    assert client.post(f"/api/runs/{run.id}/approve").status_code == 409
    run.status = "review"
    run.review = Review(summary="Проверено", findings=[])
    api_module.save_run(run)
    approved = client.post(f"/api/runs/{run.id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert client.post(f"/api/runs/{run.id}/approve").json()["approved_at"] == approved.json()["approved_at"]
    assert "Замысел проекта" in client.get(f"/api/runs/{run.id}/export/md").text


def test_duplicate_start_reuses_snapshot_and_mismatch_is_conflict(client):
    request = request_fixture()
    run = Run(id=str(request.request_id), fingerprint=fingerprint(request), request=request, status="review")
    api_module.save_run(run)
    assert client.post("/api/runs", json=request.model_dump(mode="json")).json()["id"] == run.id
    request.brief.title = "Другая задача"
    assert client.post("/api/runs", json=request.model_dump(mode="json")).status_code == 409


def test_restart_marks_pending_runs_interrupted(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RUNS_DIR", tmp_path)
    request = request_fixture()
    run = Run(id=str(request.request_id), fingerprint=fingerprint(request), request=request)
    api_module.save_run(run)
    with TestClient(app_module.app) as client:
        assert client.get(f"/api/runs/{run.id}").json()["status"] == "interrupted"


def test_a_bonus_task_does_not_count_against_the_project_hours():
    # Audit 30.09.2026: main tasks filling 4 of 4 hours plus an allowed bonus were flagged «План превышает…».
    from project_generator_lab.concept.planning import check_concept

    request = request_fixture()
    request.brief.bonus = True
    concept = concept_fixture()
    bonus = concept.tasks[0].model_copy(update={"title": "Бонус", "optional": True, "hours": 2})
    concept.tasks.append(bonus)
    messages = [f.message for f in check_concept(concept, request, [])]
    assert "План превышает заданную трудоёмкость" not in messages
    # Retention: main tasks over the hours are still flagged.
    concept.tasks[0] = concept.tasks[0].model_copy(update={"hours": 5})
    assert "План превышает заданную трудоёмкость" in [f.message for f in check_concept(concept, request, [])]
