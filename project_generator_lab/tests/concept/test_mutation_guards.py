"""Exact-value tests for deterministic code, written against mutants that survived mutmut.

Each test pins a boundary, a number or a branch that a plausible code change would alter silently.
"""

import json

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.concept import runs as api_module
from project_generator_lab.concept.answers import numbered_questions
from project_generator_lab.concept.dialogue import dialogue_payload
from project_generator_lab.concept.ingestion import parse_curriculum
from project_generator_lab.concept.input_semantics import input_semantics, program_position
from project_generator_lab.concept.models import Brief, InputClaim, Question, TaskPlan, TaskWorkload
from project_generator_lab.concept.patches import build_patches
from project_generator_lab.concept.thread import FIELDS, Message, MessageRequest, ProposedChange, Thread, readable
from project_generator_lab.concept.workload import Norms, TaskHours, WorkloadQuery, budget, check, load_norms
from project_generator_lab.tests.concept.test_validation import new_run
from project_generator_lab.tests.concept.test_workbench import concept_fixture
from project_generator_lab.tests.concept.workflow_fixtures import input_card
from pydantic import ValidationError


def brief(**values) -> Brief:
    return Brief(
        **{"title": "Проект", "description": "Описание проекта", "audience": "", "outcomes": "Делать", **values}
    )


def task(number: int, hours: float, workload: TaskWorkload | None, optional: bool = False) -> TaskPlan:
    return TaskPlan(
        title=f"Задание {number}",
        action="Сделать",
        outcome_numbers=[1],
        deliverable="Файл",
        peer_check="Пир",
        independent_choice="Выбор",
        support="Опора",
        evidence_ids=["form.outcomes"],
        hours=hours,
        workload=workload,
        optional=optional,
    )


def split(hours: float, overhead: float, setup: float = 0.0) -> TaskWorkload:
    """Breakdown with an exact overhead share; setup is carved out of reading."""
    extra = hours * overhead
    return TaskWorkload(
        reading=round(extra / 2 - setup, 4),
        setup=setup,
        doing=round(hours - extra, 4),
        debugging=round(extra / 4, 4),
        submission=round(extra / 4, 4),
    )


# ---------- budget ----------

# The arithmetic of budget() and check() in this file is pinned on norms written here, not on config/workload.json:
# the file's numbers are the methodologists' to change (they did on 05.10.2026). These norms have no cap on the number
# of tasks and no typical number, as the file had none before that date. Two things still come from the file: the
# tolerance of the hours sum, which check() reads itself, and the default path of budget(), which
# test_dialogue.py and test_workload_norms.py run on the file as it is.
PLAIN = Norms(
    task_hours=TaskHours(min=1.5, max=6),
    overhead_min_share={"zero": 0.4, "basic": 0.3, "intermediate": 0.25, "advanced": 0.2, "unknown": 0.3},
    first_task_setup_min_hours={"zero": 1.0, "basic": 0.5, "intermediate": 0.25, "advanced": 0.25, "unknown": 0.5},
)


@pytest.mark.parametrize(("requested", "noted"), [(1, True), (2, False), (8, False), (9, True)])
def test_task_count_range_is_inclusive_on_both_ends(requested, noted):
    plan = budget(WorkloadQuery(hours=12, tasks_count=requested), PLAIN)
    assert (plan.tasks_min, plan.tasks_max, plan.tasks_recommended, plan.requested_tasks) == (2, 8, 3, requested)
    assert bool(plan.notes) is noted
    if requested == 9:
        assert plan.notes == ["9 заданий не помещаются в 12 ч при норме 1,5–6 ч на задание: подходит 2–8."]


def test_budget_edges_small_and_exact_hours():
    tiny = budget(WorkloadQuery(hours=1), PLAIN)
    assert (tiny.tasks_min, tiny.tasks_max, tiny.tasks_recommended) == (1, 1, 1)
    assert tiny.notes == ["1 ч меньше нормы одного задания (1,5 ч): возможно только одно короткое задание."]
    exact = budget(WorkloadQuery(hours=1.5), PLAIN)
    assert (exact.tasks_min, exact.tasks_max, exact.notes) == (1, 1, [])
    six = budget(WorkloadQuery(hours=6), PLAIN)
    assert (six.tasks_min, six.tasks_max, six.tasks_recommended) == (1, 4, 2)
    empty = budget(WorkloadQuery())
    assert (empty.tasks_min, empty.tasks_max, empty.tasks_recommended) == (None, None, None)
    assert empty.notes == ["Трудоёмкость не задана: число и объём заданий проверить нельзя."]


def test_budget_levels_setup_flag_and_norm_fallbacks():
    unknown = budget(WorkloadQuery(hours=10))
    assert (unknown.start_level, unknown.overhead_min_share, unknown.first_task_setup_min, unknown.needs_setup) == (
        "",
        0.3,
        0.5,
        False,
    )
    advanced = budget(WorkloadQuery(hours=10, start_level="advanced", software="IDE"))
    assert (advanced.start_level, advanced.overhead_min_share, advanced.first_task_setup_min, advanced.needs_setup) == (
        "advanced",
        0.2,
        0.25,
        True,
    )
    assert unknown.norms_status == load_norms().status and unknown.norms_status
    partial = Norms(
        task_hours=TaskHours(min=1, max=4),
        overhead_min_share={"unknown": 0.33},
        first_task_setup_min_hours={"unknown": 0.7},
    )
    fallback = budget(WorkloadQuery(hours=8, start_level="basic"), partial)
    assert (
        fallback.overhead_min_share,
        fallback.first_task_setup_min,
        fallback.min_task_hours,
        fallback.max_task_hours,
    ) == (0.33, 0.7, 1, 4)
    bare = Norms(task_hours=TaskHours(min=1, max=4), overhead_min_share={}, first_task_setup_min_hours={})
    assert (
        budget(WorkloadQuery(hours=8), bare).overhead_min_share,
        budget(WorkloadQuery(hours=8), bare).first_task_setup_min,
    ) == (0, 0)


# ---------- check ----------


def run_check(tasks, **brief_values):
    concept = concept_fixture()
    concept.tasks = tasks
    current = brief(**brief_values)
    return check(concept, current, budget(current, PLAIN))


def test_every_task_is_checked_with_its_own_number_and_exact_arithmetic():
    findings = run_check(
        [
            task(1, 4, None),
            task(2, 4, TaskWorkload(reading=1, setup=1, doing=1, debugging=0, submission=0)),
            task(3, 7, split(7, 0.5)),
        ],
        hours=15,
    )
    assert [(f.severity, f.message, f.concept_locations) for f in findings] == [
        ("blocker", "Задание 1: нет разбивки часов на чтение, настройку, выполнение, ошибки и сдачу.", ["tasks.1"]),
        ("blocker", "Задание 2: разбивка даёт 3 ч, а указано 4 ч.", ["tasks.2"]),
        ("warning", "Задание 3: 7 ч вне нормы 1,5–6 ч.", ["tasks.3"]),
    ]
    assert {(f.origin, f.rule_id) for f in findings} == {("system", "constraints")}
    assert all(f.suggestion and f.consequence for f in findings)


def test_norm_boundaries_are_inclusive():
    exact = [task(1, 1.5, split(1.5, 0.3)), task(2, 6, split(6, 0.3))]
    assert run_check(exact, hours=7.5) == []
    outside = run_check([task(1, 1.48, split(1.48, 0.3)), task(2, 6.02, split(6.02, 0.3))], hours=7.5)
    assert [f.message for f in outside] == [
        "Задание 1: 1,48 ч вне нормы 1,5–6 ч.",
        "Задание 2: 6,02 ч вне нормы 1,5–6 ч.",
    ]
    optional_long = run_check([task(1, 4, split(4, 0.3)), task(2, 9, split(9, 0.3), optional=True)], hours=4)
    assert optional_long == []


def test_overhead_share_equal_to_norm_passes_and_below_is_reported_with_level():
    assert run_check([task(1, 4, split(4, 0.3))], hours=4) == []
    low = run_check([task(1, 4, split(4, 0.29))], hours=4)
    assert [f.message for f in low] == [
        "Задание 1: на чтение, настройку, ошибки и сдачу 29% времени; норма для уровня «не указан» — от 30%."
    ]
    assert low[0].severity == "warning"
    zero = run_check([task(1, 4, split(4, 0.35))], hours=4, start_level="zero")
    assert [f.message for f in zero] == [
        "Задание 1: на чтение, настройку, ошибки и сдачу 35% времени; норма для уровня «нулевой» — от 40%."
    ]


def test_first_task_setup_boundary_and_trigger():
    assert run_check([task(1, 4, split(4, 0.3, setup=0.5))], hours=4, tools="Git") == []
    short = run_check([task(1, 4, split(4, 0.3, setup=0.4))], hours=4, tools="Git")
    assert [(f.message, f.concept_locations) for f in short] == [
        (
            "Первое задание: на настройку 0,4 ч при обязательных инструментах; норма для уровня «не указан» — от 0,5 ч.",
            ["tasks.1"],
        )
    ]
    assert run_check([task(1, 4, split(4, 0.3, setup=0.0))], hours=4) == []


def test_main_tasks_fill_the_project_hours_within_a_quarter_hour():
    # РГ 25.09, 09:03: seven tasks added up to 9 of 10 hours and the lost hour went unnoticed.
    lost_hour = run_check([task(1, 4.5, split(4.5, 0.3)), task(2, 4.5, split(4.5, 0.3))], hours=10)
    assert [(f.severity, f.message) for f in lost_hour] == [
        ("blocker", "Основные задания занимают 9 ч из 10: 1 ч не распределены.")
    ]
    assert run_check([task(1, 4.75, split(4.75, 0.3)), task(2, 5, split(5, 0.3))], hours=10) == []
    short = run_check([task(1, 4.7, split(4.7, 0.3)), task(2, 5, split(5, 0.3))], hours=10)
    assert [f.message for f in short] == ["Основные задания занимают 9,7 ч из 10: 0,3 ч не распределены."]
    # A bonus task is extra content for faster learners and cannot cover the lost hour.
    with_bonus = run_check(
        [task(1, 4.5, split(4.5, 0.3)), task(2, 4.5, split(4.5, 0.3)), task(3, 2, split(2, 0.3), optional=True)],
        hours=10,
    )
    assert [f.message for f in with_bonus] == ["Основные задания занимают 9 ч из 10: 1 ч не распределены."]


def test_task_count_boundaries():
    eight = [task(i, 1.5, split(1.5, 0.3)) for i in range(1, 9)]
    assert run_check(eight, hours=12) == []
    nine = run_check([task(i, 1.5, split(1.5, 0.3)) for i in range(1, 10)], hours=13.5)
    assert nine == []
    one = run_check([task(1, 4, split(4, 0.3))], hours=12)
    assert ("blocker", "Основных заданий 1; при 12 ч и норме 1,5–6 ч подходит 2–8.") in [
        (f.severity, f.message) for f in one
    ]


# ---------- program position ----------


@pytest.mark.parametrize(
    ("index", "stage"),
    [
        (0, "первый проект программы"),
        (1, "начало программы"),
        (2, "начало программы"),
        (3, "середина программы"),
        (5, "середина программы"),
        (6, "конец программы"),
        (7, "конец программы"),
        (8, "последний проект программы"),
    ],
)
def test_program_stage_thresholds(index, stage):
    rows = "\n".join(f"{'Б' + str(i // 3 + 1) if i % 3 == 0 else ''};П{i}" for i in range(9))
    run = new_run()
    run.request.curriculum = parse_curriculum("plan.csv", f"Тематический блок;Название проекта\n{rows}\n".encode())
    run.request.selected_row_id = run.request.curriculum.rows[index].id
    position = program_position(run.request)
    assert position is not None
    assert (position["position"], position["total"], position["stage"]) == (index + 1, 9, stage)
    assert (position["block"], position["block_position"], position["blocks_total"]) == (
        f"Б{index // 3 + 1}",
        index // 3 + 1,
        3,
    )
    assert position["previous"] == (f"П{index - 1}" if index else "")
    assert position["next"] == (f"П{index + 1}" if index < 8 else "")


def test_position_is_unknown_without_selected_row():
    run = new_run()
    assert program_position(run.request) is None


def test_semantics_expose_every_new_field_to_models():
    fields = input_semantics(new_run().request)["fields"]
    assert isinstance(fields, dict)
    assert {"start_level", "start_skills", "program_outcomes", "customer_requirements", "program"} <= set(fields)


# ---------- dialogue helpers ----------


def test_numbered_questions_continue_from_card_to_concept():
    run = new_run()
    run.input_card = input_card(run.request)
    run.input_card.questions = [
        Question(question="А?", why="1", blocking=True),
        Question(question="Б?", why="2", blocking=False),
    ]
    run.concept = concept_fixture()
    run.concept.questions = [Question(question="В?", why="3", blocking=False)]
    assert numbered_questions(run) == [
        {"number": 1, "stage": "входы", "question": "А?", "why": "1"},
        {"number": 2, "stage": "входы", "question": "Б?", "why": "2"},
        {"number": 3, "stage": "замысел", "question": "В?", "why": "3"},
    ]
    run.input_card = None
    assert numbered_questions(run) == [{"number": 1, "stage": "замысел", "question": "В?", "why": "3"}]


def test_readable_strips_pydantic_prefix():
    with pytest.raises(ValidationError) as error:
        brief(format="group")
    assert readable(error.value) == "Укажите размер группы"


def test_noop_change_does_not_stop_later_changes_and_cross_field_changes_pass_together():
    current = brief(tools="Git")
    patches, notes = build_patches(
        current,
        [
            ProposedChange(field="tools", value="Git", reason="без изменений", origin="request"),
            ProposedChange(
                field="checking", value="Пир сверяет таблицу", reason="ответ", origin="answer", question_numbers=[2]
            ),
        ],
    )
    assert notes == ["«Инструменты и ПО» уже содержит это значение."]
    assert [(p.field, p.before, p.after, p.origin, p.question_numbers) for p in patches] == [
        ("checking", "", "Пир сверяет таблицу", "answer", [2])
    ]
    group, notes = build_patches(
        current,
        [
            ProposedChange(field="format", value="group", reason="групповой", origin="request"),
            ProposedChange(field="group_size", value=4, reason="размер", origin="request"),
        ],
    )
    assert notes == [] and [(p.field, p.after) for p in group] == [("format", "group"), ("group_size", 4)]
    alone, notes = build_patches(
        current, [ProposedChange(field="format", value="group", reason="групповой", origin="request")]
    )
    assert alone == [] and notes == ["Правка «Тип проекта» не прошла проверку: Укажите размер группы"]


def test_dialogue_payload_structure_is_what_the_prompt_describes():
    run = new_run()
    run.input_card = input_card(run.request)
    run.input_card.claims.append(
        InputClaim(area="audience", text="Уровень не указан", kind="unknown", evidence_ids=[], outcome_numbers=[])
    )
    run.concept = concept_fixture()
    thread = Thread(
        id=run.id,
        owner_id="local",
        runs=[run.id],
        draft=run.request.brief,
        messages=[
            Message(role="system", text="служебное"),
            Message(role="methodologist", text="ранний вопрос"),
            Message(role="assistant", text="ответ", status="applied"),
            Message(role="methodologist", text="текущий"),
        ],
    )
    raw = dialogue_payload(thread, run, MessageRequest(text="текущий", target_field="checking"))
    payload = json.loads(json.dumps(raw, ensure_ascii=False))
    assert set(payload) == {
        "message",
        "target_field",
        "draft",
        "fields",
        "questions",
        "resolved_questions",
        "input_card",
        "concept",
        "review_findings",
        "off_topic",
        "block_goals",
        "doubtful_skills",
        "learner_path",
        "learner_facts",
        "freshness",
        "recent_messages",
        "computed_state",
    }
    assert (payload["message"], payload["target_field"]) == ("текущий", "checking")
    assert (
        payload["fields"]["start_level"]["label"] == FIELDS["start_level"]
        and payload["fields"]["start_level"]["meaning"]
    )
    assert payload["input_card"]["open_claims"] == [
        {"area": "audience", "text": "Уровень не указан", "kind": "unknown"}
    ]
    assert payload["concept"]["tasks"][0] == {
        "number": 1,
        "title": "Выделить факты",
        "hours": 4.0,
        "outcome_numbers": [1, 2],
        "deliverable": "Отчёт",
        "peer_check": "Проверить связь выводов с цитатами",
    }
    assert [m["text"] for m in payload["recent_messages"]] == ["ранний вопрос", "ответ"]
    assert payload["recent_messages"][1]["status"] == "applied"
    assert payload["freshness"] == []
    assert payload["computed_state"] == {
        "program_position": None,
        "run_status": "running",
        "next_step": {
            "title": "Идёт разбор входов",
            "text": "Готовые этапы сохраняются. Можно писать в чат: правки войдут в задание для следующей версии.",
            "button": "",
        },
    }


def test_missing_thread_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(api_module, "RUNS_DIR", tmp_path)
    with TestClient(app_module.app) as client:
        response = client.get("/api/threads/00000000-0000-4000-8000-000000000000")
    assert (response.status_code, response.json()["detail"]) == (404, "Диалог не найден")


def test_task_hours_tolerance_is_one_hundredth_inclusive():
    assert run_check([task(1, 1.49, split(1.49, 0.3)), task(2, 6.01, split(6.01, 0.31))], hours=7.5) == []


def test_stage_thresholds_use_thirds_not_quarters():
    rows = "\n".join(f"П{i}" for i in range(12))
    run = new_run()
    run.request.curriculum = parse_curriculum("plan.csv", f"Название проекта\n{rows}\n".encode())
    stages = []
    for row in run.request.curriculum.rows:
        run.request.selected_row_id = row.id
        position = program_position(run.request)
        assert position is not None
        stages.append(position["stage"])
    assert stages[1:] == ["начало программы"] * 3 + ["середина программы"] * 4 + ["конец программы"] * 3 + [
        "последний проект программы"
    ]


def test_concept_chat_keeps_the_numbers_the_methodologist_sees_and_a_fixed_window():
    """«Учти пункт 2 актуальности» and «замечание 3» refer to numbers in the chat: the payload must use the same ones.
    Found by the mutation run: numbering from 2 and a window of 9 turns survived before this test."""
    from project_generator_lab.concept.models import CheckedItem, Finding, Freshness, Review

    run = new_run()
    run.input_card = input_card(run.request)
    run.concept = concept_fixture()
    run.review = Review(summary="s", findings=[Finding(severity="warning", message=f"Замечание {i}", suggestion="") for i in range(1, 15)])
    run.freshness = Freshness(model="m", summary="s", items=[
        CheckedItem(topic=f"Тема {i}", status="outdated", finding="Устарело", sources=["https://a", "https://b", "https://c"]) for i in (1, 2)])
    messages = [Message(role="methodologist" if i % 2 else "assistant", text=f"реплика {i}") for i in range(1, 13)]
    thread = Thread(id=run.id, owner_id="local", runs=[run.id], draft=run.request.brief, messages=[*messages, Message(role="methodologist", text="текущий")])
    payload = json.loads(json.dumps(dialogue_payload(thread, run, MessageRequest(text="текущий")), ensure_ascii=False))
    assert [x["number"] for x in payload["freshness"]] == [1, 2] and payload["freshness"][1]["topic"] == "Тема 2"
    assert payload["freshness"][0]["sources"] == ["https://a", "https://b"]
    assert payload["review_findings"] == [f"Замечание {i}" for i in range(1, 13)]
    assert [m["text"] for m in payload["recent_messages"]] == [f"реплика {i}" for i in range(5, 13)]
    assert payload["input_card"]["summary"] == run.input_card.summary and payload["concept"]["title"] == run.concept.title
    assert all(field["meaning"] is not None for field in payload["fields"].values())
