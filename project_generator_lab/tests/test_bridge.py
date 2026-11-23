"""The bridge is the only code that knows both modules: an approved run becomes the content handoff."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab import bridge
from project_generator_lab.concept import runs
from project_generator_lab.concept.models import Brief, Concept, Material, Passage, TaskPlan
from project_generator_lab.content import store
from project_generator_lab.content.handoff import HandoffUnavailable
from project_generator_lab.tests.concept.test_validation import reviewed_run


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(store, "CONTENT_DIR", tmp_path / "content")


def approved_run():
    run = reviewed_run()
    run.status, run.approved_at = "approved", "2026-09-29T10:00:00+00:00"
    run.prompt_snapshots = {"designer": "…", "program_rules": "# Правила программы", "platform": "# Ограничения платформы"}
    run.request.brief.learner_term = "студент"
    run.request.brief.start_level = "zero"
    runs.save_run(run)
    return run


def test_approved_run_becomes_handoff_with_rules_and_numbered_outcomes(folders):
    run = approved_run()
    handoff = bridge.handoff(run.id)
    assert handoff.run_id == run.id and handoff.outcomes == ["Выделять факты", "Обосновывать выводы"]
    assert handoff.learner_term == "студент" and handoff.start_level == "нулевой"
    assert handoff.rules == {"program_rules": "# Правила программы", "platform": "# Ограничения платформы"}
    assert [(t.number, t.title, t.outcome_numbers) for t in handoff.tasks] == [(1, "Выделить факты", [1, 2])]
    assert "Учебный план не загружен" in handoff.program_context


def test_run_under_review_is_not_handed_over(folders):
    run = reviewed_run()
    runs.save_run(run)
    with pytest.raises(HandoffUnavailable):
        bridge.handoff(run.id)
    with TestClient(app_module.app) as client:
        response = client.get(f"/api/content/{run.id}")
    assert response.status_code == 409 and "согласуйте замысел" in response.json()["detail"]


def test_app_serves_the_start_step_for_an_approved_concept(folders):
    run = approved_run()
    with TestClient(app_module.app) as client:
        view = client.get(f"/api/content/{run.id}").json()
    assert view["exists"] is False and view["next_step"]["action"] == "start"


# Fields the content stage deliberately does not receive, each with its reason. A new field in the brief or the
# concept fails this test until someone decides whether the generator needs it: nothing is dropped silently.
NOT_HANDED_OVER = {
    "brief": {"block": "уже в program_context", "bonus": "выражен бонусными заданиями", "concept_feedback": "замечания к прошлой версии замысла",
              "direction": "устаревшее поле", "tasks_count": "задания переданы списком",
              "curriculum_policy": "применена при построении program_context и замысла, исходная строка УП не передаётся писателю",
              "block_scope": "решение методолога уже в раскладке целей карточки — передаётся через program_context"},
    "concept": set(),
    "task": {"evidence_ids": "служебные ссылки замысла",
             "skills": "веса навыков — для расчёта развесовки: передаётся посчитанный блок (skill_table), писателю заданий веса не нужны"},
}


def test_the_skills_divided_between_the_tasks_are_handed_over_as_the_lines_of_the_tab(folders):
    from project_generator_lab.concept.models import SkillBlock, SkillPart, SkillTask
    from project_generator_lab.content.handoff import card

    run = approved_run()
    assert bridge.handoff(run.id).skill_table == []  # a concept of earlier rules has nothing divided
    run.request.brief.skills = "Git\nPython"
    run.skill_block = SkillBlock(code="P1", main=SkillPart(shares={"Git": 0.2, "Python": 0.8}, tasks=[SkillTask(label="01", shares={"Git": 1.0, "Python": 1.0})]))
    runs.save_run(run)
    handoff = bridge.handoff(run.id)
    assert handoff.skill_table == [["Проект", "Задание", "Git", "Python", "check"], ["P1", "all", "0,2", "0,8", "1"], ["", "01", "1", "1", ""], ["", "check", "1", "1", ""]]
    rows = {row["label"]: row for row in card(handoff)}
    assert rows["Навыки"] == {"label": "Навыки", "value": "2, разложены по заданиям: блок развесовки для паспорта готов", "ok": True,
                              "items": ["Git — 0,2", "Python — 0,8"]}
    assert handoff.skill_gaps == []
    # What is wrong with the block is found where the block was made and goes with it; the card only shows it.
    run.request.brief.skills = "Git\nPython\nSQL"
    runs.save_run(run)
    lost = bridge.handoff(run.id)
    assert lost.skill_gaps == ["ни одному заданию не достались: «SQL»"]
    assert {r["label"]: r for r in card(lost)}["Навыки"] == {
        "label": "Навыки", "value": "3, блок развесовки требует правки", "ok": False, "items": ["ни одному заданию не достались: «SQL»"]}
    many = SkillBlock(code="P1", main=SkillPart(shares={"Git": 1}, tasks=[SkillTask(label=f"{n:02}", shares={f"S{n}": 1.0}) for n in range(1, 51)]))
    run.skill_block = many
    runs.save_run(run)
    assert len(bridge.handoff(run.id).skill_gaps) == 40  # the list is cut for the card, and the handoff is not refused for it
    # No block in the handoff is a gap — said of the handoff, so that it is true of a draft begun before blocks were
    # handed over, whose run may have one. No skills at all is a gap too.
    assert {r["label"]: r for r in card(handoff.model_copy(update={"skill_table": []}))}["Навыки"] == {
        "label": "Навыки", "value": "2, блок развесовки по заданиям не передан", "ok": False, "items": ["Git", "Python"]}
    assert {r["label"]: r for r in card(handoff.model_copy(update={"skill_table": [], "skills": ""}))}["Навыки"] == {
        "label": "Навыки", "value": "не заданы", "ok": False, "items": []}


def test_the_row_of_skills_is_green_only_when_the_stage_that_made_the_block_found_nothing_wrong_with_it():
    """Two independent checks, 05.10.2026. The first row said «готов» with a green mark over a block that had lost a
    skill and counted the columns of the table instead of the skills of the project. The second, which looked for
    every skill in some task line, was still green over blocks the reader of a passport would complain of: a share
    rounded to nothing, a skill in the part the plan does not give it to. So the row does not judge the block: the
    stage that made it does, by the checks a passport's own blocks are held to."""
    from project_generator_lab.concept.models import SkillBlock, SkillPart, SkillTask
    from project_generator_lab.concept.skills import gaps, names_of, table
    from project_generator_lab.content.handoff import skills_row

    def part(shares: dict[str, float], *tasks: dict[str, float], bonus: str = "") -> SkillPart:
        return SkillPart(shares=shares, tasks=[SkillTask(label=f"{bonus}{n:02}", shares=own) for n, own in enumerate(tasks, 1)])

    def row(form: str, main: SkillPart, bonus: SkillPart | None = None) -> dict[str, object]:
        block = SkillBlock(code="P1", main=main, bonus=bonus or SkillPart())
        return skills_row(form, table(block), gaps(block, names_of(form)))

    ready, edit = "разложены по заданиям: блок развесовки для паспорта готов", "блок развесовки требует правки"
    plan, extra = {"Git": 0.2, "Python": 0.8}, {"Python": 0.5, "SQL": 0.5}
    shown = ["Git — 0,2", "Python — 0,8", "бонус: Python — 0,5", "бонус: SQL — 0,5"]
    # Ready: every skill earned in the part the plan gives it to. The shares of the project are shown, the bonus part apart.
    assert row("Git\nPython\nSQL", part(plan, {"Git": 1, "Python": 1}), part(extra, {"Python": 1, "SQL": 1}, bonus="Bonus ")) == {
        "label": "Навыки", "value": f"3, {ready}", "ok": True, "items": shown}
    assert row(" git ,PYTHON\nGit", part(plan, {"Git": 1, "Python": 1})) == {  # a name said twice is one; the spelling is the form's own
        "label": "Навыки", "value": f"2, {ready}", "ok": True, "items": ["Git — 0,2", "Python — 0,8"]}
    assert row("Problem   solving", part({"Problem solving": 1}, {"Problem solving": 1})) == {
        "label": "Навыки", "value": f"1, {ready}", "ok": True, "items": ["Problem solving — 1"]}
    # The concept gave no weights: there is a block, and nothing in it.
    assert row("Git\nPython", part({}, {})) == {"label": "Навыки", "value": f"2, {edit}", "ok": False, "items": [
        "ни одному заданию не достались: «Git», «Python»", "основная часть: заданий 1, а долей навыков нет"]}
    # A skill of the form no task practises is named, not dropped from the count.
    assert row("Git, Python; SQL", part({"Git": 0.6, "Python": 0.4}, {"Git": 1, "Python": 1})) == {
        "label": "Навыки", "value": f"3, {edit}", "ok": False, "items": ["ни одному заданию не достались: «SQL»"]}
    # The plan's share alone does not make a skill earned …
    assert row("Git\nPython", part(plan, {"Git": 1}))["items"] == ["основная часть: «Python» — доля 0,8, по заданиям сумма 0 вместо 1"]  # said once
    # A skill of a part that has no tasks at all is not the block's fault to name: the project's.
    assert row("Git\nPython\nSQL", part(plan, {"Git": 1, "Python": 1}), part(extra))["items"] == ["ни одному заданию не достались: «SQL»"]
    # … and a bonus task does not earn a skill of the main part: it is what everyone does that the main shares pay for.
    assert row("Git\nPython\nSQL", part(plan, {"Git": 1}), part(extra, {"Python": 1, "SQL": 1}, bonus="Bonus ")) == {
        "label": "Навыки", "value": f"3, {edit}", "ok": False, "items": ["основная часть: «Python» — доля 0,8, по заданиям сумма 0 вместо 1"]}
    # A share that rounded to nothing, a skill practised in the part the plan does not give it to, a line that does not add up.
    assert row("Git\nPython", part({"Git": 1}, {"Git": 1}, {"Python": 1}))["items"] == [
        "основная часть: «Python» — доли в проекте нет, а задания дают 1", "основная часть: без навыков проекта остаются задания 02"]
    assert row("Git\nPython\nSQL", part(plan, {"Git": 1, "Python": 1, "SQL": 1}), part(extra))["items"] == [
        "основная часть: «SQL» — доли в проекте нет, а задания дают 1"]
    assert row("Git\nPython", part({"Git": 0.1, "Python": 0.4}, {"Git": 1, "Python": 1}))["items"] == ["основная часть: сумма долей навыков 0,5 вместо 1"]
    # No block in the handoff, no skills in the form.
    assert skills_row("Problem   solving\nGit", [], []) == {
        "label": "Навыки", "value": "2, блок развесовки по заданиям не передан", "ok": False, "items": ["Problem solving", "Git"]}
    assert skills_row(" ;\n,", [["x"]], ["что-то"]) == {"label": "Навыки", "value": "не заданы", "ok": False, "items": []}
    # A stored table that is not a table (a file edited by hand) is not a failure of the whole draft: nothing is shown under the row.
    head = ["Проект", "Задание", "Git", "Python", "check"]
    tables: list[list[list[str]]] = [[head], [head, ["P1", "all", "1"]], [[], []], [[]], [["Проект", "Задание", "check"], ["P1", "all", ""]]]
    for broken in tables:
        assert skills_row("Git\nPython", broken, []) == {"label": "Навыки", "value": f"2, {ready}", "ok": True, "items": []}
    assert skills_row("Git", [head, ["P1", "all", "1", "", "1"], ["", "01", "1", "", ""], ["", "check", "1", "", ""]], [])["items"] == ["Git — 1"]


def test_every_approved_field_is_handed_over_or_excluded_on_purpose():
    source = (Path(bridge.__file__)).read_text(encoding="utf-8")
    read = {prefix: set(re.findall(rf"\b{prefix}\.(\w+)", source)) for prefix in ("brief", "concept", "t")}
    assert set(Brief.model_fields) - read["brief"] == set(NOT_HANDED_OVER["brief"])
    assert set(Concept.model_fields) - read["concept"] == set(NOT_HANDED_OVER["concept"])
    assert set(TaskPlan.model_fields) - read["t"] == set(NOT_HANDED_OVER["task"])


def test_attached_files_are_handed_over_with_limits_and_reviewer_files_stay_with_the_critic(folders):
    run = approved_run()
    run.request.brief.customer_requirements = "Только отечественные сервисы"
    run.request.materials = [
        Material(id="m1", name="бриф.docx", role="customer", sha256="1", passages=[Passage(id=f"p{i}", location=f"стр. {i}", text="Бриф " * 2000) for i in range(3)]),
        Material(id="m2", name="ответы.docx", role="reviewer", sha256="2", passages=[Passage(id="p2", location="стр. 1", text="Ответ: 42")]),
    ]
    runs.save_run(run)
    handoff = bridge.handoff(run.id)
    brief, answers = handoff.sources
    assert handoff.customer_requirements == "Только отечественные сервисы"
    assert brief.cropped and len(brief.text) == 15000 and brief.total_chars > 15000
    assert [s.name for s in handoff.writer_sources()] == ["бриф.docx"] and answers.role == "reviewer"
    assert handoff.decisions == ["предложено: Результат — Отчёт позволяет проверить основания вывода"]
    assert handoff.tasks[0].workload is not None and handoff.tasks[0].workload.doing == 2.25
