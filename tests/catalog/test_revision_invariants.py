"""A methodologist can repair a partial intake through the plan's normal entry point."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lxml import html

from content_factory.catalog.pipeline import brief_persistence, stage_dag_to_up, storage
from content_factory.catalog.pipeline.models import SkillCandidate
from content_factory.catalog.viewer import curriculum_revision_review as review
from content_factory.catalog.viewer import intake_dag, intake_reviews
from content_factory.catalog.viewer.intake_dag import load_brief_spec_for_plan
from content_factory.catalog.web.routers import up


def _spec() -> dict[str, Any]:
    return {
        "source_curriculum": [{"source_id": n, "title": "Работа " + n, "team_mode": team,
                               "description": "Собрать и обработать образцы.", "source_ref": n, "outcomes": []}
                              for n, team in (("a", "team"), ("b", "individual"))],
        "requirements_text": "# Исследование\n- Проверить качество образцов.\nВ лаборатории есть микроскоп.",
        "revision_generation": {"state": "unavailable", "issues": ["Сервис недоступен."]},
    }


def _form(version: str = "snapshot") -> dict[str, str]:
    return {"version": version, "confirmed": "yes", "title": "Проверка образцов", "disposition": "merge",
            "participant_action": "Оценить качество", "artifact": "Карта наблюдений",
            "validation_criteria": "Другой участник повторяет оценку образцов.", "target_team_mode": "team",
            "rationale": "Объединить операции в одну проверку."}


class Capture:
    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []

    def execute(self, sql: str, params: tuple[str, int]) -> None:
        assert "UPDATE profile_brief" in sql and params[1] == 3
        self.history = json.loads(params[0])["revision_reviews"]

    def rollback(self) -> None:
        self.history.clear()


def test_human_merge_replays_all_covered_work_and_only_counts_listed_requirements() -> None:
    spec = _spec()
    db = Capture()
    editor = review.RevisionEditor(3, 13, spec, {}, "snapshot")
    review.confirm_revision(db, editor, _form(), ["a", "b"], ["L2", "L3"], actor="7")
    assert db.history[0]["actor"] == "7"
    assert db.history[0]["decision"]["approved"] is True
    assert db.history[0]["decision"]["requirement_refs"] == ["L2"]
    assert "микроскоп" in db.history[0]["decision"]["source_ref"]
    changed = deepcopy(spec)
    changed["requirements_text"] += "\n- Новая строка"
    review.apply_reviewed_revisions(changed, db.history)
    assert changed["revision_generation"]["state"] == "unavailable"
    review.apply_reviewed_revisions(spec, db.history)
    assert spec["revision_generation"]["unassigned_requirements"] == []
    assert spec["revision_generation"]["state"] == "complete"
    assert spec["revision_generation"]["provider_issues"] == ["Сервис недоступен."]
    assert [d["source_ids"] for d in spec["revision_decisions"]] == [["a", "b"]]


@pytest.mark.parametrize(("changes", "ids", "refs"), [
    ({"confirmed": ""}, ["a", "b"], ["L2"]),
    ({"version": "older"}, ["a", "b"], ["L2"]),
    ({"disposition": "revise"}, ["missing"], ["L2"]),
    ({}, ["a", "b"], ["L99"]),
    ({"target_team_mode": ""}, ["a", "b"], ["L2"]),
    ({"disposition": "omit"}, ["a"], ["L2"]),
    ({"disposition": "revise", "artifact": ""}, ["a"], ["L2"]),
])
def test_invalid_or_stale_decision_leaves_no_history(
    changes: dict[str, str], ids: list[str], refs: list[str],
) -> None:
    db = Capture()
    editor = review.RevisionEditor(3, 13, _spec(), {}, "snapshot")
    with pytest.raises(ValueError):
        review.confirm_revision(db, editor, {**_form(), **changes}, ids, refs, actor="7")
    assert not db.history


def test_review_http_form_recovers_after_provider_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Capture()
    spec = _spec()
    editor = review.RevisionEditor(3, 13, spec, {}, "snapshot")
    app = FastAPI()
    app.include_router(up.router)
    app.dependency_overrides[up.get_conn] = lambda: db
    monkeypatch.setenv("DISABLE_AUTH", "true")
    monkeypatch.setattr(up, "ensure_intake_runtime_schema", lambda *_: None)
    monkeypatch.setattr(up, "_sync_up_curriculum", lambda: None)
    monkeypatch.setattr(up, "load_revision_editor", lambda conn, plan_id, *, for_update: editor)

    def rebuild(conn: Capture, brief_id: int, *, require_current_dag: bool) -> dict[str, int]:
        assert brief_id == 3 and require_current_dag
        review.apply_reviewed_revisions(spec, conn.history)
        assert spec["revision_generation"]["state"] == "complete"
        return {"plan_id": 14}

    monkeypatch.setattr(up, "build_curriculum_plan_for_brief", rebuild)
    client = TestClient(app)
    path = "/app/spravochnik/up/plans/13/revision"
    page = client.get(path)
    assert page.status_code == 200 and "Подтвердить и пересобрать" in page.text
    token = re.search(r'name="version" value="([^"]+)"', page.text)
    assert token
    invalid = client.post(path, data={**_form(token[1]), "source_ids": ["a", "b"], "refs": ["L100"]})
    assert invalid.status_code == 422 and "неизвестная строка" in invalid.text and not db.history
    saved = client.post(path, data={**_form(token[1]), "source_ids": ["a", "b"], "refs": ["L2"]},
                        follow_redirects=False)
    assert saved.status_code == 303 and "/plans/14" in saved.headers["location"]


@pytest.mark.parametrize("legacy", [False, True])
def test_reconfirming_rendered_form_preserves_teaching_contract_and_exact_excerpts(legacy: bool) -> None:
    spec = _spec()
    spec["requirements_text"] += " Сохранить «сырые образцы» до сравнения."
    teaching = {
        "case_context": "В лаборатории нужно перепроверить поставку.", "inputs": ["Набор проб", "Протокол"],
        "required_tools": ["Микроскоп"], "is_capstone": True, "choose_one_group": "Практикум",
        "prerequisite_source_ids": ["b"], "group_size": 3, "peer_reviews": 0, "effort_hours": 17.5,
        "acceptance_test": {"check": "Повторить сверку", "expected_result": "Одинаковые значения"},
        "learning_outcomes": {"know": ["Условия хранения"], "can": ["Сверить пару проб"], "skills": ["Вести протокол"]},
    }
    excerpts = [{"ref": "L3", "quote": "есть микроскоп"}, {"ref": "a", "quote": "обработать образцы"},
                {"ref": "L2", "quote": "Проверить качество"}, {"ref": "L3", "quote": "«сырые образцы»"}]
    current = {**_form(), **teaching, "disposition": "specialize", "source_ids": ["a"],
               "source_ref": " / ".join(f"{e['ref']} «{e['quote']}»" for e in excerpts),
               "requirement_refs": ["L2"]}
    if not legacy:
        current["evidence"] = excerpts
    spec["revision_decisions"] = [current]
    db = Capture()
    editor = review.RevisionEditor(3, 13, spec, {}, "snapshot")
    context = review.editor_context(editor, "a")
    document = html.fromstring(up.render("up_revision_edit.html", context, request_path="/up/plans/13/revision"))
    # Submit successful browser controls without manually copying decision fields into a POST.
    controls = document.forms[-1].form_values()
    form = dict(controls)
    form["confirmed"] = "yes"
    refs = [value for key, value in controls if key == "refs"]
    ids = [value for key, value in controls if key == "source_ids"]
    review.confirm_revision(db, editor, form, ids, refs, actor="7")
    saved = db.history[0]["decision"]
    assert {key: saved[key] for key in teaching} == teaching
    assert saved["source_ref"] == current["source_ref"] and saved["evidence"] == excerpts
    assert saved["requirement_refs"] == ["L2"]


def test_review_refuses_deferred_planner_result_before_replacing_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = SkillCandidate(tmp_id="S7", name="Проверка образцов", group="Лаборатория", indicators=[])
    monkeypatch.setattr(intake_reviews, "count_open_prerequisite_edge_reviews_for_brief", lambda *_: 0)
    monkeypatch.setattr(intake_dag, "load_accepted_skill_candidates", lambda *_: ([candidate], {"S7": 7}))
    monkeypatch.setattr(intake_dag, "load_latest_brief_dag_payload", lambda *_: {"order": [{"id": "S7"}]})
    monkeypatch.setattr(intake_dag, "load_brief_spec_for_plan", lambda *_: _spec())
    monkeypatch.setattr(stage_dag_to_up, "run", lambda *_: {"status": "deferred", "rows": []})
    monkeypatch.setattr(storage, "save_curriculum_plan", lambda *_: pytest.fail("An empty replacement reached storage"))
    with pytest.raises(ValueError, match="Прежний УП сохранён"):
        intake_dag.build_curriculum_plan_for_brief(Capture(), 3, require_current_dag=True)


def test_sql_reviewer_precedes_stale_job_snapshot_and_rejects_old_version(catalog_conn: Any) -> None:
    spec = _spec()
    brief_id = brief_persistence.save_brief(catalog_conn, spec["requirements_text"], spec)
    catalog_conn.execute("INSERT INTO intake_job(source_kind, brief_text, status, result_payload) VALUES (?,?,?,?)",
                         ("text", "бриф", "succeeded", json.dumps({"brief_id": brief_id, "spec": spec})))
    cur = catalog_conn.execute("""INSERT INTO curriculum_plan(brief_id, title, status, created_at, updated_at)
                                  VALUES (?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",
                               (brief_id, "Проверка образцов", "draft"))
    plan_id = int(cur.lastrowid)
    catalog_conn.commit()
    initial = review.load_revision_editor(catalog_conn, plan_id)
    edit = review.load_revision_editor(catalog_conn, plan_id, for_update=True)
    review.confirm_revision(catalog_conn, edit, _form(initial.token), ["a", "b"], ["L2"], actor="7")
    catalog_conn.commit()
    reloaded = load_brief_spec_for_plan(catalog_conn, brief_id)
    assert reloaded["revision_decisions"][0]["approved"] is True
    assert reloaded["revision_generation"]["state"] == "complete"
    stale = review.load_revision_editor(catalog_conn, plan_id, for_update=True)
    with pytest.raises(review.StaleRevisionError):
        review.confirm_revision(catalog_conn, stale, _form(initial.token), ["a", "b"], ["L2"], actor="7")


def test_sql_http_review_rebuild_requires_current_persisted_graph(
    catalog_conn: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec()
    brief_id = brief_persistence.save_brief(catalog_conn, spec["requirements_text"], spec)
    original = brief_persistence.save_curriculum_plan(catalog_conn, brief_id, {
        "status": "built", "rows": [{"row_number": 1, "project_name": "Исходная строка"}],
    })["plan_id"]
    path = f"/app/spravochnik/up/plans/{original}/revision"
    app = FastAPI()
    app.include_router(up.router)
    app.dependency_overrides[up.get_conn] = lambda: catalog_conn
    monkeypatch.setenv("DISABLE_AUTH", "true")
    monkeypatch.setattr(up, "_sync_up_curriculum", lambda: None)
    client = TestClient(app)
    token = html.fromstring(client.get(path).text).forms[-1].fields["version"]
    form = {**_form(token), "source_ids": ["a", "b"], "refs": ["L2"]}

    # A committed SQL edit invalidates a form even while the stored payload still has the old title.
    catalog_conn.execute("UPDATE curriculum_plan_row SET project_name = ? WHERE plan_id = ?", ("Живая строка", original))
    catalog_conn.commit()
    assert client.post(path, data=form).status_code == 409
    form["version"] = html.fromstring(client.get(path).text).forms[-1].fields["version"]

    def assert_refused() -> None:
        response = client.post(path, data=form)
        assert response.status_code == 422 and "актуальный DAG" in response.text
        assert catalog_conn.execute("SELECT project_name FROM curriculum_plan_row WHERE plan_id = ?",
                                    (original,)).fetchone()[0] == "Живая строка"
        metadata = catalog_conn.execute("SELECT metadata_json FROM profile_brief WHERE id = ?", (brief_id,)).fetchone()[0]
        assert not json.loads(metadata).get("revision_reviews")

    assert_refused()
    skill_id = catalog_conn.execute("""INSERT INTO skill_suggestion
        (brief_id, suggested_name, bloom, decision, entity_type, atomicity)
        VALUES (?, 'Проверка образцов', 'apply', 'accepted', 'skill', 'atomic')""", (brief_id,)).lastrowid
    catalog_conn.commit()
    assert_refused()
    payload = {"brief_id": brief_id, "dag": {"order": [{"id": "S99999"}], "final_edges": []}}
    job = catalog_conn.execute("INSERT INTO intake_job(source_kind,brief_text,status,result_payload) VALUES (?,?,?,?)",
                               ("text", spec["requirements_text"], "succeeded", json.dumps(payload))).lastrowid
    catalog_conn.commit()
    assert_refused()

    # The ordinary POST now reloads both the accepted skill and the matching graph from storage.
    payload["dag"] = {"order": [{"id": f"S{skill_id}"}], "final_edges": []}
    catalog_conn.execute("UPDATE intake_job SET result_payload = ? WHERE id = ?", (json.dumps(payload), job))
    catalog_conn.commit()
    saved = client.post(path, data=form, follow_redirects=False)
    assert saved.status_code == 303
    new_plan = int(saved.headers["location"].split("/plans/")[-1].split("#")[0])
    plan = up.get_curriculum_plan(catalog_conn, new_plan)
    assert plan and any(row["project_name"] == "Проверка образцов" for row in plan["rows"])
    assert client.post(path, data=form).status_code == 409
