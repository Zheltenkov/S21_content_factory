"""Rules 0.9: the place in the programme is the participant's path — what they have gone through by the plan before
this project (outcomes, skills, tools of every earlier project) and what later projects will need from it. Code collects
the facts from the plan and checks that the path points at real later projects and real outcomes; the interpreter
summarises; the designer, the reviewer and the content generator read it."""

from typing import Any

from project_generator_lab import bridge
from project_generator_lab.concept.dialogue import dialogue_payload
from project_generator_lab.concept.ingestion import parse_curriculum
from project_generator_lab.concept.input_semantics import learner_facts, program_map
from project_generator_lab.concept.models import LearnerPath, NextNeed, Run
from project_generator_lab.concept.planning import build_evidence, context_payload
from project_generator_lab.concept.thread import MessageRequest, Thread
from project_generator_lab.concept.validation import check_input_card
from project_generator_lab.tests.concept.test_validation import reviewed_run
from project_generator_lab.tests.concept.workflow_fixtures import input_card

PLAN = (
    "Тематический блок;Название проекта;Образовательные результаты;Навыки;Инструменты;Необходимое ПО;Часы\n"
    '"Блок 1.\nВведение";Проекты и процессы;"Оценивать сроки и бюджет\nФормулировать цели проекта";Project planning;"Markdown, таблицы, Miro";;12\n'
    'Подготовка;Сбор требований;"Собирать требования\nПриоритизировать требования";Requirements Analysis;"Markdown, таблицы";Confluence;16\n'
    'Подготовка;User Story, CJM и бэклог;"Составлять User Story Map\nФормировать бэклог";Team work;"Miro, таблицы, Figma";;12\n'
    'Риски;Типы рисков;Выявлять риски;Project management;"Markdown, Jira";;12\n'
    'Планирование;План работ на основе бэклога;Строить план работ по бэклогу;Project planning;"GitLab, таблицы";;12\n'
)


def path_run() -> Run:
    run: Run = reviewed_run()
    run.request.curriculum = parse_curriculum("plan.csv", PLAN.encode())
    run.request.selected_row_id = run.request.curriculum.rows[2].id
    run.evidence = build_evidence(run.request)[0]
    run.input_card = input_card(run.request)
    return run


def test_code_collects_what_the_participant_went_through_and_what_comes_next():
    run = path_run()
    m = program_map(run.request)
    assert m is not None
    learner: Any = m["learner"]
    rows = run.request.curriculum.rows  # type: ignore[union-attr]
    # What the chat card shows for each earlier project: its block on one line, hours, outcomes, skills, tools.
    assert learner["before"] == [
        {"id": rows[0].id, "title": "Проекты и процессы", "block": "Блок 1. Введение", "hours": 12.0,
         "outcomes": ["Оценивать сроки и бюджет", "Формулировать цели проекта"], "skills": ["Project planning"],
         "tools": ["Markdown", "таблицы", "Miro"]},
        {"id": rows[1].id, "title": "Сбор требований", "block": "Подготовка", "hours": 16.0,
         "outcomes": ["Собирать требования", "Приоритизировать требования"], "skills": ["Requirements Analysis"],
         # Tools and software are one list: methodologists do not tell them apart.
         "tools": ["Markdown", "таблицы", "Confluence"]}]
    # Tools: which were used before and in how many projects; which of this project's tools are new.
    assert {t["tool"]: t["projects"] for t in learner["tools_before"]} == {
        "Markdown": ["Проекты и процессы", "Сбор требований"], "таблицы": ["Проекты и процессы", "Сбор требований"],
        "Miro": ["Проекты и процессы"], "Confluence": ["Сбор требований"]}
    assert learner["tools_here"] == [{"tool": "Miro", "before": 1}, {"tool": "таблицы", "before": 2}, {"tool": "Figma", "before": 0}]
    # New tools ahead, from the very next project on; one line per tool, with the project where it first appears.
    assert learner["tools_ahead"] == ["Jira — «Типы рисков»", "GitLab — «План работ на основе бэклога»"]
    assert learner["later"] == [
        {"id": rows[3].id, "title": "Типы рисков", "block": "Риски", "tools": ["Markdown", "Jira"]},
        {"id": rows[4].id, "title": "План работ на основе бэклога", "block": "Планирование", "tools": ["GitLab", "таблицы"]}]
    # No plan, or a row that is not in it: nothing to say about the path.
    assert learner_facts(run.request.model_copy(update={"curriculum": None})) is None
    assert learner_facts(run.request.model_copy(update={"selected_row_id": "нет-такой"})) is None
    # The model now sees the skills and tools of every row: without them it could not say what the participant used.
    curriculum: Any = context_payload(run)["curriculum_map"]
    row = next(r for r in curriculum if r["title"] == "Сбор требований")
    assert (row["skills"], row["tools"]) == ("Requirements Analysis", "Markdown, таблицы")
    evidence = next(e for e in run.evidence if e.id == run.request.curriculum.rows[0].id)  # type: ignore[union-attr]
    assert "Miro" in evidence.text


def test_what_later_projects_need_must_point_at_real_later_projects_and_real_outcomes():
    run = path_run()
    rows = run.request.curriculum.rows  # type: ignore[union-attr]
    assert run.input_card is not None
    run.input_card.learner_path = LearnerPath(
        before="По УП участник прошёл оценку сроков и бюджета, сбор и приоритизацию требований; работал в Markdown, таблицах и Miro.",
        builds_on=["приоритеты требований — «Сбор требований»", "оценка сроков — «Проекты и процессы»"],
        next_needs=[
            NextNeed(project="План работ на основе бэклога", needs="Строить план по бэклогу", teach_here="Оценённый и упорядоченный бэклог",
                     outcome_numbers=[1, 2], evidence_ids=[rows[4].id]),
            NextNeed(project="Сбор требований", needs="…", teach_here="…"),
            NextNeed(project="Бюджет проекта", needs="…", teach_here="…"),
            NextNeed(project="Типы рисков", needs="…", teach_here="…", outcome_numbers=[0, 3], evidence_ids=["выдумано"]),
        ])
    found = {(f.message, f.suggestion or "") for f in check_input_card(run.input_card, run.request, run.evidence) if "Понадобится дальше" in f.message}
    details = dict(found)
    assert len(found) == 3
    assert "«Сбор требований» стоит в УП до этого проекта." in details["«Понадобится дальше» ссылается на проект, который идёт раньше"]
    assert "«Бюджет проекта»" in details["«Понадобится дальше» ссылается на проект, которого нет в УП после этого"]
    # ОР 1 and 2 are the brief's two outcomes; 0 and 3 are not; the invented source is named.
    assert "«Типы рисков»: ОР [0, 3], ссылки ['выдумано']." in details["«Понадобится дальше» с неверными номерами ОР или ссылками"]
    run.input_card.learner_path.next_needs[3] = NextNeed(project="Типы рисков", needs="…", teach_here="…", outcome_numbers=[3])
    [(_, detail)] = [(m, d) for m, d in {(f.message, f.suggestion or "") for f in check_input_card(run.input_card, run.request, run.evidence)}
                     if m == "«Понадобится дальше» с неверными номерами ОР или ссылками"]
    assert detail.endswith("«Типы рисков»: ОР [3], ссылки —.")
    run.input_card.learner_path.next_needs[3] = NextNeed(project="Типы рисков", needs="…", teach_here="…", outcome_numbers=[1], evidence_ids=["выдумано"])
    [detail] = [f.suggestion for f in check_input_card(run.input_card, run.request, run.evidence)
                if f.message == "«Понадобится дальше» с неверными номерами ОР или ссылками"]
    assert detail == "«Типы рисков»: ОР —, ссылки ['выдумано']."
    # The generator of content reads the same path: what not to teach again and what to leave for later projects.
    run.input_card.learner_path.next_needs = run.input_card.learner_path.next_needs[:1]
    context = bridge.program_context(run)
    assert "До этого проекта (по УП): По УП участник прошёл" in context
    assert "Опора из прошлых проектов, заново не учить: приоритеты требований — «Сбор требований»; оценка сроков — «Проекты и процессы»." in context
    assert "Понадобится в «План работ на основе бэклога»: Строить план по бэклогу Здесь: Оценённый и упорядоченный бэклог" in context
    # The chat sees the path and the facts behind it, so «Miro они ещё не проходили» can be answered and recorded.
    thread = Thread(id=run.id, owner_id="local", runs=[run.id], draft=run.request.brief)
    payload = dialogue_payload(thread, run, MessageRequest(text="Miro они ещё не проходили"))
    assert payload["learner_path"]["builds_on"][0] == "приоритеты требований — «Сбор требований»"  # type: ignore[index]
    # A run without a card has no path to pass on.
    assert bridge.path_lines(run.model_copy(update={"input_card": None})) == []
    assert [p["title"] for p in payload["learner_facts"]["before"]] == ["Проекты и процессы", "Сбор требований"]  # type: ignore[index]


def test_the_models_get_the_path_without_repeating_the_plan_and_a_48_project_plan_fits():
    # Audit 30.09.2026: the path repeated every earlier project's outcomes already in curriculum_map (+25k characters
    # on 24 projects) and a 48-project plan no longer fitted the payload limit.
    import json
    from pathlib import Path
    from uuid import uuid4

    from project_generator_lab.concept.models import Brief, StartRequest
    from project_generator_lab.concept.planning import IN_MAP, PROMPT_VERSION, fingerprint

    lines = (Path(__file__).parents[2] / "concept/examples/curriculum.csv").read_text(encoding="utf-8").splitlines()
    plan = parse_curriculum("plan.csv", "\n".join([lines[0], *lines[1:], *lines[1:]]).encode())
    assert len(plan.rows) == 48
    request = StartRequest(request_id=uuid4(), brief=Brief(title="Проект", description="Описание проекта", audience="a", outcomes="Выделять факты"),
                           curriculum=plan, selected_row_id=plan.rows[-1].id)
    run = Run(id=str(request.request_id), fingerprint=fingerprint(request), request=request, prompt_version=PROMPT_VERSION)
    run.evidence = build_evidence(request)[0]
    payload = context_payload(run)
    size = len(json.dumps(payload, ensure_ascii=False))
    assert size < 180000
    # Each plan row once: in curriculum_map. The evidence names the other rows by id (still citable), the project's own
    # row keeps its full text (it has columns the map leaves out).
    evidence: Any = payload["evidence"]
    rows = {e["id"]: e for e in evidence if e["role"] == "curriculum"}
    allowed: Any = payload["allowed_evidence_ids"]
    assert set(rows) == {r.id for r in plan.rows} and set(allowed) >= set(rows)
    assert rows[plan.rows[0].id]["text"] == IN_MAP and rows[plan.rows[-1].id]["text"] == run.evidence[-1].text != IN_MAP
    full = len(json.dumps({**payload, "evidence": [e.model_dump() for e in run.evidence]}, ensure_ascii=False))
    assert full - size > 30000  # what the second copy of the plan cost
    # A run resumed with rules frozen before 1.0 keeps the rows in evidence: its prompts do not say where they went.
    older = run.model_copy(update={"prompt_version": "0.9"})
    assert context_payload(older)["evidence"] == [e.model_dump() for e in run.evidence]
    learner: Any = payload["computed_state"]["program_map"]["learner"]  # type: ignore[index]
    # Only what code adds: which projects, and tool counts; the outcomes stay in curriculum_map once.
    assert set(learner["before"][0]) == {"id", "title"} and "outcomes" not in json.dumps(learner["before"])
    assert set(learner["tools_before"][0]) == {"tool", "projects", "first"}
    assert learner["later"] == [] and learner["tools_here"] and "tools_ahead" in learner
    assert learner["meaning"].endswith("ОР, навыки и инструменты каждого проекта — в curriculum_map по id.")
    curriculum: Any = payload["curriculum_map"]
    assert curriculum[0]["outcomes"]
    # Retention: the chat card still shows each earlier project's outcomes.
    card: Any = program_map(request)
    assert card["learner"]["before"][0]["outcomes"]


def test_a_title_repeated_before_and_after_counts_as_a_later_project():
    # The real plan has «Публичные выступления» twice; a later one must not be reported as «идёт раньше».
    run: Run = reviewed_run()
    plan = PLAN + 'Подготовка;Сбор требований;Собрать ещё раз;Requirements Analysis;"Markdown";;8\n'
    run.request.curriculum = parse_curriculum("plan.csv", plan.encode())
    run.request.selected_row_id = run.request.curriculum.rows[2].id
    run.evidence = build_evidence(run.request)[0]
    run.input_card = input_card(run.request)
    run.input_card.learner_path = LearnerPath(before="По УП участник прошёл сбор требований.",
                                              next_needs=[NextNeed(project="Сбор требований", needs="…", teach_here="…")])
    assert not [f for f in check_input_card(run.input_card, run.request, run.evidence) if "Понадобится дальше" in f.message]
