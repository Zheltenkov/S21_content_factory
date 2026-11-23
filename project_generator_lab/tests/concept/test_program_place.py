"""Rules 0.8: the block goals are written for the whole block. The interpreter places each one on a project of the block,
explains what the place in the plan means for the tasks, and flags skills of the plan that do not fit; code checks that
the placement is complete and real, and that the concept closes this project's goals without re-teaching earlier ones."""

from project_generator_lab import bridge
from project_generator_lab.concept import runs
from project_generator_lab.concept.dialogue import dialogue_payload
from project_generator_lab.concept.ingestion import parse_curriculum
from project_generator_lab.concept.input_semantics import goal_lines, program_map
from project_generator_lab.concept.models import DoubtfulSkill, GoalPlacement, ProgramPoint, Run
from project_generator_lab.concept.planning import build_evidence, check_block_goals
from project_generator_lab.concept.thread import MessageRequest, Thread
from project_generator_lab.concept.validation import check_input_card
from project_generator_lab.tests.concept.test_validation import reviewed_run
from project_generator_lab.tests.concept.test_workbench import concept_fixture
from project_generator_lab.tests.concept.workflow_fixtures import input_card

PLAN = (
    "Тематический блок;Цели блока;Название проекта;Навыки;Часы\n"
    'Риски;"- Понятие риска\n- SWOT-анализ\n- Методы реагирования\n- План митигирования";Типы рисков;Analytical thinking;12\n'
    ';;План митигирования рисков;"Project management, Types and data structures";12\n'
    "Планирование;;Как эффективно планировать;Project planning;12\n"
)


def risk_run() -> Run:
    run: Run = reviewed_run()
    run.request.curriculum = parse_curriculum("plan.csv", PLAN.encode())
    run.request.selected_row_id = run.request.curriculum.rows[1].id
    run.evidence = build_evidence(run.request)[0]
    run.input_card = input_card(run.request)
    row = run.request.selected_row_id
    run.input_card.block_goals = [
        GoalPlacement(number=1, owner="previous", project="Типы рисков", why="Тема первого проекта блока"),
        GoalPlacement(number=2, owner="previous", project="Типы рисков", why="Выявление рисков — первый проект"),
        GoalPlacement(number=3, owner="this", why="Реагирование — основа плана"),
        GoalPlacement(number=4, owner="this", why="Название проекта")]
    run.input_card.program_reasoning = [ProgramPoint(fact="До него — «Типы рисков»", so_what="Начинать с готового реестра рисков, не учить SWOT заново",
                                                     kind="given", evidence_ids=[row])]
    run.input_card.doubtful_skills = [DoubtfulSkill(skill="Types and data structures", why="Не относится к рискам")]
    return run


def test_block_goals_are_numbered_lines_of_the_cell():
    assert goal_lines("- Понятие риска\n2) Типы\n\n• SWOT\n3. P-I матрица") == ["Понятие риска", "Типы", "SWOT", "P-I матрица"]
    m = program_map(risk_run().request)
    assert m and m["block_goal_items"] == [{"number": 1, "text": "Понятие риска"}, {"number": 2, "text": "SWOT-анализ"},
                                           {"number": 3, "text": "Методы реагирования"}, {"number": 4, "text": "План митигирования"}]


def test_a_complete_real_placement_passes_and_gaps_are_named():
    run = risk_run()
    assert run.input_card
    assert not [f for f in check_input_card(run.input_card, run.request, run.evidence) if "Цел" in f.message or "навык" in f.message]
    card = run.input_card.model_copy(deep=True)
    card.block_goals = card.block_goals[:3] + [GoalPlacement(number=3, owner="later", project="Бюджет", why="?")]
    card.doubtful_skills.append(DoubtfulSkill(skill="Выпечка", why="?"))
    card.program_reasoning.append(ProgramPoint(fact="Без основания", so_what="?", kind="given", evidence_ids=[]))
    messages = [f.message for f in check_input_card(card, run.request, run.evidence)]
    assert "Цели блока разложены не все или не по одному разу" in messages
    assert "Цель блока отдана проекту, которого нет в блоке" in messages
    assert "Сомнительный навык не найден в колонке навыков УП" in messages
    assert "Рассуждение о месте в программе без проверяемого основания" in messages


def test_concept_must_close_this_projects_goals_and_not_reteach_earlier_ones():
    run = risk_run()
    concept = concept_fixture()
    findings = [f.message for f in check_block_goals(concept, run.request, run.input_card)]
    assert findings == ["Не закрыты цели блока, отданные этому проекту: «Методы реагирования»; «План митигирования»"]
    concept.tasks[0].block_goal_numbers = [2, 3, 4, 9]
    findings = [f.message for f in check_block_goals(concept, run.request, run.input_card)]
    assert findings == ["Задание 1 ссылается на несуществующие цели блока [9]",
                        "Задание 1 заново учит цель предыдущего проекта «Типы рисков»: «SWOT-анализ»"]
    concept.tasks[0].block_goal_numbers = [3, 4]
    assert not check_block_goals(concept, run.request, run.input_card)
    # Without a curriculum or a placement there is nothing to check.
    assert not check_block_goals(concept, run.request, None)


def test_the_chat_sees_the_placement_and_the_content_generator_gets_it_in_words(tmp_path, monkeypatch):
    run = risk_run()
    thread = Thread(id=run.id, owner_id="local", runs=[run.id], draft=run.request.brief)
    payload = dialogue_payload(thread, run, MessageRequest(text="цель 2 — наша"))
    assert payload["block_goals"][1] == {"number": 2, "text": "SWOT-анализ", "owner": "предыдущий проект", "project": "Типы рисков"}  # type: ignore[index]
    assert payload["doubtful_skills"] == [{"skill": "Types and data structures", "why": "Не относится к рискам"}]
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    assert run.concept
    run.concept.tasks[0].block_goal_numbers, run.concept.tasks[0].program_link = [3, 4], "Опирается на реестр из «Типов рисков»"
    run.status, run.approved_at = "approved", "2026-09-30T10:00:00+00:00"
    runs.save_run(run)
    handoff = bridge.handoff(run.id)
    assert handoff.tasks[0].block_goals == ["Методы реагирования", "План митигирования"]
    assert handoff.tasks[0].program_link == "Опирается на реестр из «Типов рисков»"
    assert "Цели блока этого проекта: Методы реагирования; План митигирования." in handoff.program_context
    assert "«SWOT-анализ» — закрывает предыдущий проект «Типы рисков»" in handoff.program_context
    assert "До него — «Типы рисков» → Начинать с готового реестра рисков, не учить SWOT заново" in handoff.program_context
