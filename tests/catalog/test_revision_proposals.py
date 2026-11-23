import json

import requests

from content_factory.catalog.pipeline.curriculum import revision_proposals as revision


def test_generated_updates_require_real_evidence_and_cannot_approve_themself(monkeypatch):
    spec = {
        "requirements_text": "## Актуализация\n- Добавить ручную проверку допуска измерительного прибора.",
        "source_curriculum": [
            {"source_id": "u1", "description": "Чтение показаний прибора."},
            {"source_id": "u2", "description": "Подготовка заключения о серии измерений."},
        ],
    }
    proposal = {
        "source_ids": ["u1"], "disposition": "revise", "title": "Проверка измерений",
        "participant_action": "Сверить отклонение с допуском", "artifact": "Журнал измерений",
        "validation_criteria": "Напарник повторяет расчёт и получает тот же вывод.",
        "rationale": "В исследовании выявлен пропуск проверки допуска.",
        "evidence": [{"ref": "L2", "quote": "ручную проверку допуска измерительного прибора"}],
        "approved": True,
    }
    unsupported = {**proposal, "source_ids": ["u2"], "evidence": [{"ref": "L2", "quote": "Несуществующая цитата."}]}
    def fake_chat(*args, **kwargs):
        return {"choices": [{"message": {"content": json.dumps({"decisions": [proposal, unsupported]})}}]}

    monkeypatch.setattr(revision.llm, "chat", fake_chat)
    result = revision.propose_curriculum_revisions(spec)
    assert [d["source_ids"] for d in result["revision_decisions"]] == [["u1"]]
    assert result["revision_decisions"][0]["approved"] is False
    assert result["revision_generation"]["state"] == "partial"
    assert any("u2" in issue for issue in result["revision_generation"]["issues"])


def test_provider_outage_preserves_unchecked_requirements_without_repeated_calls(monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout()

    monkeypatch.setattr(revision.llm, "chat", unavailable)
    spec = {
        "requirements_text": "- Опубликовать протокол проверки выборки.",
        "source_curriculum": [{"source_id": str(i)} for i in range(12)],
    }
    result = revision.propose_curriculum_revisions(spec)
    assert len(calls) == 1
    assert result["revision_decisions"] == []
    assert result["revision_generation"]["state"] == "unavailable"
    assert result["revision_generation"]["unassigned_requirements"] == ["L1"]


def _revision_item(source):
    return {
        "source_ids": [source], "disposition": "revise", "title": f"Контроль {source}",
        "participant_action": "Измерить температуру", "artifact": "Журнал показаний",
        "validation_criteria": "Повторная проверка даёт те же показания.",
        "rationale": "Бриф требует независимую проверку.",
        "evidence": [{"ref": "L1", "quote": "Независимая проверка показаний"}],
    }


def test_complete_batches_can_fill_a_whole_program_gap_once(monkeypatch):
    work = {**_revision_item("u1"), "effort_hours": 8}
    calls = []
    def reply(model, messages, **kwargs):
        payload = json.loads(messages[-1]["content"])
        calls.append(payload)
        if "missing" not in payload:
            value = {"decisions": [work]}
        else:
            value = {"additions": [{"project_title": work["title"], "requirement_ref": "L2",
                                    "quote": "Повторный замер другим прибором", "participant_action": "Сверить второй замер.",
                                    "artifact": "Второй протокол.", "test": {"check": "Сравнить показания",
                                    "expected_result": "Отклонение в пределах допуска."}, "rationale": "Проверка переноса навыка.",
                                    "workload": [{"activity": "Повторить измерение", "additional_hours": 2}],
                                    "is_capstone": True}], "open_questions": []}
        return {"choices": [{"message": {"content": json.dumps(value)}}]}
    monkeypatch.setattr(revision.llm, "chat", reply)
    result = revision.propose_curriculum_revisions({"requirements_text":
        "- Независимая проверка показаний.\n- Повторный замер другим прибором.", "source_curriculum": [{"source_id": "u1"}]})
    assert len(calls) == 2 and result["revision_generation"]["state"] == "complete"
    [updated] = result["revision_decisions"]
    assert updated["effort_hours"] == 10 and updated["requirement_refs"] == ["L1", "L2"]
    assert updated["artifact"].startswith(work["artifact"]) and not updated["approved"]


def test_program_review_quarantines_id_title_mismatch_and_preserves_the_suggestion(monkeypatch):
    from content_factory.catalog.pipeline.curriculum.revision_program_review import reconcile_program_requirements
    project = {**_revision_item("u1"), "requirement_refs": ["L1"], "source_ref": "Первоначальное решение"}
    addition = {"source_id": "u1", "project_title": "Иная работа", "requirement_ref": "L2",
                "quote": "Повторный замер другим прибором", "participant_action": "Сверить два замера.",
                "artifact": "Второй протокол", "test": {"check": "Сверить результаты", "expected_result": "Расхождение объяснено"},
                "rationale": "Участник повторяет анализ.", "workload": [{"activity": "Повторный замер", "additional_hours": 0}],
                "is_capstone": False}
    monkeypatch.setattr(revision.llm, "chat", lambda *a, **kw: {"choices": [{"message": {
        "content": json.dumps({"additions": [addition], "open_questions": []})}}]})
    result, issues, trace = reconcile_program_requirements([project], [{"ref": "L2", "text": addition["quote"]}], 80)
    assert result == [project] and not trace["accepted"] and issues
    assert trace["pending_suggestions"][0]["proposal"]["participant_action"] == addition["participant_action"]


def test_schema_error_repairs_only_missing_work_and_preserves_parent_context(monkeypatch):
    seen = []
    def reply(model, messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        seen.append([item["source_id"] for item in payload["works"]])
        assert json.loads(messages[0]["content"].split("Схема возвращаемого JSON:\n")[1]) == revision.ProposalBatch.model_json_schema()
        assert revision.llm._USAGE_CONTEXT.get()["job_id"] == "batch-test"
        if len(seen) == 1:
            items = [_revision_item("u1"), {**_revision_item("u2"), "group_size": "private-raw-value"}]
        else:
            error = payload["repair"]["errors"][0]
            assert (error["path"], error["source_ids"]) == ("group_size", ["u2"])
            assert "private-raw-value" not in json.dumps(error)
            items = [{**_revision_item("u2"), "prerequisite_source_ids": ["u1"]}]
        return {"choices": [{"message": {"content": json.dumps({"decisions": items})}}]}

    monkeypatch.setattr(revision.llm, "chat", reply)
    with revision.llm.scoped_usage_context(job_id="batch-test", stage="parent"):
        result = revision.propose_curriculum_revisions({
            "requirements_text": "- Независимая проверка показаний.",
            "source_curriculum": [{"source_id": "u1"}, {"source_id": "u2"}],
        })
        assert revision.llm._USAGE_CONTEXT.get()["stage"] == "parent"
    assert seen == [["u1", "u2"], ["u2"]]
    assert result["revision_generation"]["state"] == "complete"
    assert not result["revision_generation"]["pending_source_ids"]
    assert all(d["approved"] is False for d in result["revision_decisions"])


def test_invalid_overlapping_decision_quarantines_both_claims():
    items = [_revision_item("u1"), {"source_ids": ["u1", "u2"]}, _revision_item("u3")]
    response = {"choices": [{"message": {"content": json.dumps({"decisions": items})}}]}
    checked = revision._check_reply(response, {s: {} for s in ("u1", "u2", "u3")},
                                    {"L1": "Независимая проверка показаний"}, {"u1", "u2", "u3"})
    assert [d["source_ids"] for d in checked.decisions] == [["u3"]]
    assert any(e["code"] == "duplicate" and e["source_ids"] == ["u1"] for e in checked.errors)


def test_repair_outage_keeps_first_valid_item_and_defers_future_batches(monkeypatch):
    seen = []
    def reply(model, messages, **kwargs):
        seen.append(json.loads(messages[1]["content"])["works"])
        if len(seen) > 1:
            raise requests.Timeout()
        return {"choices": [{"message": {"content": json.dumps({"decisions": [_revision_item("u0")]})}}]}

    monkeypatch.setattr(revision.llm, "chat", reply)
    result = revision.propose_curriculum_revisions({
        "requirements_text": "- Независимая проверка показаний.",
        "source_curriculum": [{"source_id": f"u{i}"} for i in range(7)],
    })
    assert [len(batch) for batch in seen] == [6, 5]
    assert [d["source_ids"] for d in result["revision_decisions"]] == [["u0"]]
    assert result["revision_generation"]["state"] == "partial"
    assert result["revision_generation"]["pending_source_ids"] == [f"u{i}" for i in range(1, 7)]


def test_confirmed_review_updates_unresolved_inventory_only_for_the_same_brief():
    from content_factory.catalog.viewer import curriculum_revision_review as review

    spec = {
        "requirements_text": "- Независимая проверка показаний.",
        "source_curriculum": [{"source_id": "u1"}, {"source_id": "u2"}],
        "revision_generation": {"pending_source_ids": ["u1", "u2"]},
    }
    decision = {**_revision_item("u1"), "requirement_refs": ["L1"], "approved": True}
    review.apply_reviewed_revisions(spec, [{"source_hash": "expired", "decision": decision}])
    assert spec["revision_generation"]["pending_source_ids"] == ["u1", "u2"]
    fingerprint = review._digest([spec["source_curriculum"], spec["requirements_text"]])
    review.apply_reviewed_revisions(spec, [{"source_hash": fingerprint, "decision": decision}])
    assert spec["revision_generation"]["pending_source_ids"] == ["u2"]
    assert not spec["revision_generation"]["unassigned_requirements"]
