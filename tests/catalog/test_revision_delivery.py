from content_factory.catalog.pipeline.curriculum.revision_workload import refresh_revision_workload
from content_factory.catalog.pipeline.models import IndicatorSpec, SkillCandidate
from content_factory.catalog.pipeline.stage_dag_to_up import run


def test_one_required_choice_is_counted_once_when_study_paths_differ():
    plan = {"summary": {}, "rows": [
        {"route": "core", "effort_hours": 50},
        {"route": "specialization", "effort_hours": 10, "choose_one_group": "Лаборатория"},
        {"route": "specialization", "effort_hours": 20, "choose_one_group": "Лаборатория"},
        {"route": "specialization", "effort_hours": 90},
    ]}
    refresh_revision_workload(plan, {"hours_per_week": 10, "target_total_hours": 65})
    budget = plan["summary"]["revision_workload"]
    assert (budget["min_route_hours"], budget["max_route_hours"]) == (60, 70)
    assert (budget["difference_hours"], budget["max_route_difference"]) == (-5, 5)


def test_short_team_task_keeps_group_size_and_verifiable_output():
    source = {"source_id": "u1", "title": "Дозиметрия", "source_ref": "Лист!B3",
              "description": "Калибровка дозиметра.", "team_mode": "team"}
    accepted = SkillCandidate(tmp_id="s", name="Калибровка дозиметра", group="Калибровка",
                              indicators=[IndicatorSpec(text="Проверяет шкалу", bloom="apply")])
    decision = {
        "source_ids": ["u1"], "disposition": "revise", "title": "Калибровка по эталону",
        "participant_action": "Снять показания в паре.", "artifact": "Таблица отклонений.",
        "validation_criteria": "Пир проверяет отклонения по исходным показаниям.", "source_ref": "L2",
        "rationale": "Введение перекрёстной проверки.", "effort_hours": 6, "group_size": 2,
        "acceptance_test": {"check": "Пересчитать разницу двух показаний.", "expected_result": "Расхождение не более 0,5 единицы."},
        "learning_outcomes": {"know": ["Объясняет цену деления шкалы."], "can": ["Калибрует датчик."]},
    }
    plan = run({"source_curriculum": [source], "revision_decisions": [decision]}, [accepted],
               {"order": [{"id": "s"}], "waves": [[{"id": "s"}]], "final_edges": []})
    row = next(r for r in plan["rows"] if r["source_unit_ids"])
    assert (row["group_size"], row["p2p_checks"], row["effort_hours"]) == (2, 1, 6)
    assert row["artifact_contract"]["acceptance_criteria"][0]["expected_result"].startswith("Расхождение")
    assert "цену деления" in row["outcomes_know"]
