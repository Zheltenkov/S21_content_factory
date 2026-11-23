"""How the theory is given is a question the assistant asks before it writes the theory (decided 06.10.2026): a short
chapter in the README by default, or a summary to work from — in the README or in a file of materials/ — and whether
the learners have the internet. A section may get a short excerpt of a primary source in English, found by the search
and checked by code to stand on the page word for word; a quest marks the places of its pictures. A draft begun
before goes on as it began."""

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import date
from uuid import uuid4

import pytest
from project_generator_lab.content import render, research, stages, store, writer
from project_generator_lab.content.checks import run_checks
from project_generator_lab.content.edits import apply, normalize, revert
from project_generator_lab.content.models import (
    PRESENTATIONS,
    ContentChange,
    ContentDraft,
    ContentRevision,
    Excerpt,
    NotedTheoryDraft,
    TheoryDraft,
    TheoryPlan,
)
from project_generator_lab.content.stages import next_step
from project_generator_lab.content.writer import Writer, editable, parts, plan_payload, theory_payload
from project_generator_lab.core.web import Page
from project_generator_lab.tests.content.fixtures import PRIMARY, PRIMARY_TEXT, QUOTE, RETOLD, FakeModel, FakeSearch
from project_generator_lab.tests.content.test_parts import complete_draft, propose
from project_generator_lab.tests.content.test_route import (  # noqa: F401
    advance,
    client,
    model,
    reviewer,
    search,
    walk,
)
from project_generator_lab.tests.content.test_style import PROMPTS_OF_05, styled_draft

NOTES = "Конспект темы {n}: что это и как устроено. Пример на других данных. Типичная ошибка — спешка."


def planned(profile="legend", presentation="chapter", internet="yes", english="no", notes=True, **fields) -> ContentDraft:
    """A whole draft with the plan of its theory agreed, and the summaries of the sections written where asked."""
    draft = styled_draft(profile)
    draft.theory_plan = TheoryPlan(presentation=presentation, internet=internet, english=english, reason="Так удобнее этой аудитории.",
                                   sample="Роль — это набор обязанностей.", status="approved", **fields)
    assert draft.theory is not None
    for topic in draft.theory.topics:
        topic.notes = NOTES.format(n=topic.number) if notes else ""
    return draft


def plan_of(draft: ContentDraft) -> TheoryPlan:
    assert draft.theory_plan is not None
    return draft.theory_plan


def to_the_plan(client, run_id):  # noqa: F811
    """Walk the route until the assistant has proposed how to give the theory."""
    view, pressed = walk(client, run_id, stop="approve_theory_plan")
    assert pressed[-1] == "approve_task" and view["next_step"]["action"] == "approve_theory_plan"
    return view


def calls(model, name):  # noqa: F811
    return [payload for called, _, payload in model.calls if called == name]


def change(part, field, value):
    return ContentChange(part=part, field=field, value=value, reason="Просьба")


# ---------- the question is asked before the theory is written ----------

def test_the_assistant_proposes_how_to_give_the_theory_before_writing_it(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    view = to_the_plan(client, run_id)
    plan = view["theory_plan"]
    assert view["theory"] is None and view["frame"] is None and "TheoryDraft" not in [name for name, _, _ in model.calls]
    assert (plan["presentation"], plan["internet"], plan["english"], plan["status"], plan["chosen_by"], plan["written_for"]) == (
        "chapter", "yes", "no", "draft", "assistant", "chapter")
    assert plan["reason"].startswith("Интернет у участников есть") and plan["sample"].startswith("Роль — это набор обязанностей")
    shown = [m for m in view["messages"] if m["part"] == "theory_plan"]
    assert [m["text"] for m in shown] == ["Предлагаю подать теорию так: короткая глава в README. Ниже — почему и как это будет выглядеть."]
    assert (shown[0]["model"], shown[0]["input_tokens"], shown[0]["notes"]) == ("fake-model", 100, [])
    step = view["next_step"]
    assert (step["button"], step["title"], step["edit_hint"]) == ("Так и сделать — писать теорию и введение", "Как подать теорию", "Что поменять в подаче теории…")
    assert step["text"] == ("Проверьте, подходит ли вариант: от него зависит, что я напишу — короткую справку со ссылками или конспект, "
                            "по которому можно работать без интернета. Сменить вариант можно кнопкой или словами.")
    assert step["alternatives"] == [{"action": "plan_notes", "button": "Выбрать: конспект в README"},
                                    {"action": "plan_materials", "button": "Выбрать: конспект в материалах"},
                                    {"action": "plan_both", "button": "Выбрать: коротко в README и конспект в материалах"}]
    assert "как подать теорию → теория, введение, инструкция" in step["route"]
    # What the assistant chose from: the concept, what each agreed task needs understood, nothing named by the methodologist.
    payload = calls(model, "TheoryPlanDraft")[0]
    assert (payload["presentation_given"], payload["current"], payload["presentations"]) == ("", None, PRESENTATIONS)
    assert payload["tasks"][0] == {"number": 1, "title": "Роли в команде", "goal": "Научиться: сопоставь роли и зоны ответственности по данным команды.",
                                   "needs": ["Понятие для задания 1"]}
    assert payload["project"]["style"]["profile"] == "legend" and "constraints" in payload["project"]
    # Agreed: the theory is written as it always was — a chapter, with links the search found.
    view = advance(client, run_id, "approve_theory_plan")
    assert view["theory_plan"]["status"] == "approved" and view["next_step"]["action"] == "approve_support"
    assert model.schemas[[name for name, _, _ in model.calls].index("TheoryDraft")] is TheoryDraft
    assert calls(model, "TheoryDraft")[0]["plan"] == {"presentation": "chapter", "internet": "yes"}
    assert [kind for kind, _ in search.calls] == ["sources"] and all(t["links"] and not t["notes"] for t in view["theory"]["topics"])
    assert "Подача теории согласована: короткая глава в README. Готовлю теорию и введение…" in [m["text"] for m in view["messages"]]
    # Agreed, it is no step any more, and its chips are refused.
    assert client.post(f"/api/content/{run_id}/advance", json={"action": "plan_notes", "number": None}).status_code == 409


def test_another_presentation_is_chosen_by_a_chip_without_asking_the_model(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    to_the_plan(client, run_id)
    asked = len(model.calls)
    view = advance(client, run_id, "plan_materials")
    plan = view["theory_plan"]
    # The choice is the methodologist's; what the assistant argued and showed is marked as said of the other one.
    assert len(model.calls) == asked and view["busy"] == ""
    assert (plan["presentation"], plan["chosen_by"], plan["written_for"], plan["status"]) == ("materials", "chat", "chapter", "draft")
    said = [m["text"] for m in view["messages"]][-2:]
    assert said == ["Выбрать: конспект в материалах", "Теорию подаём так: конспект в материалах. Дальше — «Так и сделать — писать теорию и введение»."]
    step = view["next_step"]
    assert (step["action"], step["title"]) == ("approve_theory_plan", "Подача теории: конспект в материалах")
    assert step["text"] == "Вариант выбрали вы; пример на карточке написан для другого. Могу показать пример для выбранного — или сразу пишу теорию."
    assert step["alternatives"] == [{"action": "write_theory_plan", "button": "Показать пример для этого варианта"},
                                    {"action": "plan_chapter", "button": "Выбрать: короткая глава в README"},
                                    {"action": "plan_notes", "button": "Выбрать: конспект в README"},
                                    {"action": "plan_both", "button": "Выбрать: коротко в README и конспект в материалах"}]
    # The example on demand: the model is told the presentation and shown what stood before; code keeps the choice.
    view = advance(client, run_id, "write_theory_plan")
    again = calls(model, "TheoryPlanDraft")[1]
    assert again["presentation_given"] == "materials" and (again["current"]["presentation"], again["current"]["written_for"]) == ("materials", "chapter")
    plan = view["theory_plan"]
    assert (plan["presentation"], plan["chosen_by"], plan["written_for"]) == ("materials", "chat", "materials")
    assert view["messages"][-1]["text"] == "Подачу теории выбрали вы — конспект в материалах: вот как это будет выглядеть."
    assert view["next_step"]["title"] == "Как подать теорию"
    # Written with the summaries the presentation shows, and said so.
    view = advance(client, run_id, "approve_theory_plan")
    assert model.schemas[[name for name, _, _ in model.calls].index("TheoryDraft")] is NotedTheoryDraft
    assert calls(model, "TheoryDraft")[0]["plan"] == {"presentation": "materials", "internet": "yes"}
    assert all(topic["notes"].startswith("Понятие") for topic in view["theory"]["topics"]) and view["theory_file"] == "theory.md"
    written = next(m["text"] for m in view["messages"] if m["part"] == "theory")
    assert written == "Теоретический блок: разделов — 3, ссылок — 3, все из страниц, которые открылись. Подача — конспект в материалах."


def test_a_presentation_taken_without_its_example_is_the_methodologists_own(client, model):  # noqa: F811
    run_id = str(uuid4())
    to_the_plan(client, run_id)
    advance(client, run_id, "plan_notes")
    view = advance(client, run_id, "approve_theory_plan")
    plan = view["theory_plan"]
    # The reasons and the sample were of the chapter: they are not kept as said of the summary.
    assert (plan["presentation"], plan["written_for"], plan["reason"], plan["sample"], plan["status"]) == ("notes", "notes", "", "", "approved")
    assert len(calls(model, "TheoryPlanDraft")) == 1


def test_a_plan_that_could_not_be_proposed_is_offered_again(client, model):  # noqa: F811
    run_id = str(uuid4())
    model.fail_on = {"TheoryPlanDraft"}
    view, pressed = walk(client, run_id, stop="write_theory_plan")
    step = view["next_step"]
    assert view["theory_plan"] is None and "Не получилось" in view["messages"][-1]["text"]
    assert (step["action"], step["button"], step["title"]) == ("write_theory_plan", "Предложить, как подать теорию", "Задания и материалы согласованы")
    assert step["text"] == ("Перед теорией договоримся, как её подать: короткой главой в README, конспектом в README или в материалах. "
                            "Я предложу вариант по замыслу, скажу почему и покажу пример.")
    model.fail_on = set()
    assert advance(client, run_id, "write_theory_plan")["theory_plan"]["presentation"] == "chapter"


def test_a_plan_changed_while_the_assistant_was_writing_it_stays_as_the_methodologist_made_it(client, model):  # noqa: F811
    run_id = str(uuid4())
    to_the_plan(client, run_id)
    advance(client, run_id, "plan_notes")
    answer = model.generate

    async def meanwhile(schema, system, payload):
        result = await answer(schema, system, payload)
        if schema.__name__ == "TheoryPlanDraft":
            draft = store.load(run_id)
            plan_of(draft).internet = "no"
            store.save(draft)
        return result

    model.generate = meanwhile
    view = advance(client, run_id, "write_theory_plan")
    plan = view["theory_plan"]
    assert (plan["presentation"], plan["internet"], plan["written_for"]) == ("notes", "no", "chapter") and view["busy"] == ""
    assert view["messages"][-1]["text"] == ("Подачу теории меняли, пока я писал предложение: оставил её такой, какой вы её сделали. "
                                            "Пример для неё — кнопкой ниже.")


def test_what_the_methodologist_set_in_the_plan_is_kept_when_the_example_is_written(client, model):  # noqa: F811
    run_id = str(uuid4())
    to_the_plan(client, run_id)
    draft = store.load(run_id)
    plan_of(draft).internet, plan_of(draft).english = "no", "yes"
    store.save(draft)
    advance(client, run_id, "plan_both")
    # The scripted model answers «yes, no, chapter» again: the answers given in the chat stand.
    view = advance(client, run_id, "write_theory_plan")
    plan = view["theory_plan"]
    assert (plan["presentation"], plan["internet"], plan["english"]) == ("both", "no", "yes")


# ---------- the plan is talked over in the chat ----------

def test_the_plan_is_changed_and_its_questions_answered_by_words(client, model):  # noqa: F811
    run_id = str(uuid4())
    model.plan_questions = ["Есть ли у участников интернет во время проекта?", "Можно ли им пользоваться ИИ-ассистентом?"]
    view = to_the_plan(client, run_id)
    assert view["messages"][-1]["text"].endswith(" Есть вопросы.") and "На вопросы ответьте в чате: я учту ответы." in view["next_step"]["text"]
    model.revisions = [ContentRevision(kind="answer", reply="Без интернета глава со ссылками бесполезна: предлагаю конспект в материалах.", changes=[
        change("theory_plan", "internet", "нет"), change("theory_plan", "presentation", "Конспект в материалах"),
        change("theory_plan", "english", "Да"), change("theory_plan", "questions", ["Можно ли им пользоваться ИИ-ассистентом?"])])]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "интернета не будет, выдержку на английском дай"}).json()
    # The chat model saw the plan as a part, with its reasons while they are of this presentation.
    shown = next(part for part in model.calls[-1][2]["parts"] if part["id"] == "theory_plan")
    assert set(shown["fields"]) == {"presentation", "internet", "english", "questions", "written_for", "reason", "sample"}
    assert model.calls[-1][2]["focus"] == "theory_plan" and "theory_plan" in model.calls[-1][2]["editable"]
    patches = view["messages"][-1]["patches"]
    assert [(p["label"], p["before"], p["after"]) for p in patches] == [
        ("Подача теории · Интернет у участников", "yes", "no"), ("Подача теории · Как подаётся теория", "chapter", "materials"),
        ("Подача теории · Выдержка из первоисточника на английском", "no", "yes"),
        ("Подача теории · Вопросы о подаче теории", ["Есть ли у участников интернет во время проекта?", "Можно ли им пользоваться ИИ-ассистентом?"],
         ["Можно ли им пользоваться ИИ-ассистентом?"])]
    view = advance(client, run_id, "apply_pending")
    plan = view["theory_plan"]
    assert (plan["presentation"], plan["internet"], plan["english"], plan["chosen_by"], plan["written_for"]) == ("materials", "no", "yes", "chat", "chapter")
    assert plan["questions"] == ["Можно ли им пользоваться ИИ-ассистентом?"] and view["next_step"]["title"] == "Подача теории: конспект в материалах"
    # Taken back, the plan is the assistant's proposal again — but for who chose it, which is not undone (as with a style).
    view = client.post(f"/api/content/{run_id}/messages/{view['messages'][-3]['id']}/decision", json={"verdict": "revert"}).json()
    assert (view["theory_plan"]["presentation"], view["theory_plan"]["internet"], view["theory_plan"]["english"]) == ("chapter", "yes", "no")


@pytest.mark.parametrize("field, said, read", [
    ("presentation", "Короткая глава в README", "chapter"), ("presentation", "«конспект в README»", "notes"), ("presentation", "materials", "materials"),
    ("presentation", "  Коротко в README и  конспект в материалах ", "both"), ("presentation", "листки", "notes"), ("presentation", "в материалах", "materials"),
    ("presentation", "комикс", "комикс"), ("internet", "Да", "yes"), ("internet", "нет", "no"), ("internet", "no", "no"), ("internet", "есть", "yes"),
    ("english", "ДА", "yes"), ("english", "не нужна", "no"), ("english", "может быть", "может быть")])
def test_the_plan_is_read_by_the_names_the_card_shows(field, said, read):
    assert normalize(field, said) == read


def test_a_name_given_as_lines_is_read_as_one():
    # A model may answer with a list where a word is expected: the lines are the words of one name.
    assert normalize("presentation", ["Конспект в", "материалах"]) == "materials" and normalize("internet", ["нет"]) == "no"


def test_a_value_the_plan_cannot_take_is_refused_in_words():
    draft = planned()
    said = {("presentation", "комикс"): ("Как подаётся теория", "подача теории — одна из: «короткая глава в README», «конспект в README», "
                                         "«конспект в материалах», «коротко в README и конспект в материалах»"),
            ("internet", "может быть"): ("Интернет у участников", "здесь «да» или «нет»"),
            ("english", "иногда"): ("Выдержка из первоисточника на английском", "здесь «да» или «нет»")}
    for (field, value), (label, words) in said.items():
        message = propose(draft, change("theory_plan", field, value))
        assert message.patches == [] and message.notes == [f"«Подача теории · {label}»: {words} — правка не предложена."], field
    # Applied to an agreed plan by words, a presentation is the methodologist's choice from then on.
    message = propose(draft, change("theory_plan", "presentation", "конспект в README"))
    apply(draft, message)
    assert (plan_of(draft).presentation, plan_of(draft).chosen_by, plan_of(draft).status, plan_of(draft).stale()) == ("notes", "chat", "approved", True)
    revert(draft, message)
    assert plan_of(draft).presentation == "chapter" and not plan_of(draft).stale()


def test_a_bare_yes_under_questions_about_the_theory_is_neither_agreement_nor_a_guess(client, model):  # noqa: F811
    run_id = str(uuid4())
    model.plan_questions = ["Есть ли у участников интернет во время проекта?"]
    to_the_plan(client, run_id)
    asked = len(model.calls)
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "да"}).json()
    # «Да» may answer the question as well as agree: an agreement would start the paid writing of the theory with the
    # answer lost, so code asks which it was — without the model, and agreeing to nothing.
    assert len(model.calls) == asked and view["theory_plan"]["status"] == "draft" and view["theory"] is None and view["busy"] == ""
    assert view["messages"][-1]["text"] == (
        "Не понял, это ответ на вопрос о подаче теории или согласие с ней. Ответ напишите словами — я учту его. "
        "Если вариант подходит и вопросы можно оставить без ответа — кнопка «Так и сделать — писать теорию и введение» или «дальше».")
    # «Дальше» is no answer to anything: the plan is agreed as it stands.
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "дальше"}).json()
    assert view["theory_plan"]["status"] == "approved"


def test_yes_without_questions_agrees_to_the_plan(client, model):  # noqa: F811
    run_id = str(uuid4())
    to_the_plan(client, run_id)
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "да"}).json()
    assert view["theory_plan"]["status"] == "approved"


# ---------- what is written by the plan ----------

def test_without_the_internet_no_link_is_looked_for_or_shown(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    model.internet, model.presentation = "no", "notes"
    to_the_plan(client, run_id)
    view = advance(client, run_id, "approve_theory_plan")
    assert [kind for kind, _ in search.calls] == [] and "LinkSelection" not in [name for name, _, _ in model.calls]
    assert all(topic["links"] == [] and topic["notes"] for topic in view["theory"]["topics"])
    written = next(m for m in view["messages"] if m["part"] == "theory")
    assert written["text"] == "Теоретический блок: разделов — 3. Ссылки не искал: у участников нет интернета. Подача — конспект в README."
    assert written["notes"] == []
    assert calls(model, "TheoryDraft")[0]["plan"] == {"presentation": "notes", "internet": "no"}
    # A section without links is no gap then, and no second search is offered.
    assert not [c for c in view["checks"] if c["code"] == "theory"] and view["next_step"]["alternatives"] == []
    readme = client.get(f"/api/content/{run_id}/preview").text
    assert "Где изучить" not in readme and "Что поискать" not in readme and "Понятие 1 — это способ договориться о работе." in readme


def presentation_of(readme: str, start: str, end: str) -> str:
    return readme[readme.index(start):readme.index(end, readme.index(start) + 1)]


def test_what_to_look_for_is_said_only_to_those_who_can_look():
    draft = planned("legend", "chapter")
    assert draft.theory is not None
    draft.theory.topics[0].links, draft.theory.topics[0].keywords = [], ["роли", "RACI"]
    # No link was found for the section: with the internet the learner is told what to search for; without it, nothing.
    assert "\n**Что поискать:** роли, RACI.\n" in render.readme(draft)
    plan_of(draft).internet = "no"
    assert "Что поискать" not in render.readme(draft) and "Где изучить" not in render.readme(draft)


def test_a_chapter_is_the_readme_the_generator_always_wrote():
    for profile in ("quest", "legend"):
        plain = styled_draft(profile)
        assert render.readme(planned(profile, "chapter", notes=False)) == render.readme(plain)
        # A summary written and not shown changes nothing a learner reads.
        assert render.readme(planned(profile, "chapter")) == render.readme(plain)
        assert render.theory_path(planned(profile, "chapter")) == ""


def test_a_summary_in_the_readme_stands_where_the_short_text_stood():
    quest = render.readme(planned("quest", "notes"))
    assert "### List 1. Тема 1\n\nКонспект темы 1: что это и как устроено." in quest and "Направление к заданию 1, без ответа." not in quest
    legend = render.readme(planned("legend", "notes"))
    assert "### 1. Тема 1\n\nКонспект темы 1: что это и как устроено." in legend and "materials/theory.md" not in legend
    # A section whose summary is not written yet shows its short text, and the methodologist is told.
    draft = planned("legend", "notes", notes=False)
    assert "### 1. Тема 1\n\nНаправление к заданию 1, без ответа." in render.readme(draft)
    assert [(c.part, c.message) for c in run_checks(draft) if c.code == "theory"][0] == (
        "topic:1", "У темы 1 «Тема 1» нет конспекта, а теория подаётся так: конспект в README. Попросите в чате написать конспект — "
                   "пока на его месте короткий текст раздела.")
    assert not [c for c in run_checks(planned("legend", "notes")) if c.code == "theory"]
    assert not [c for c in run_checks(planned("legend", "chapter", notes=False)) if c.code == "theory"]


def test_summaries_in_the_materials_leave_a_table_in_the_readme():
    quest = planned("quest", "materials")
    text = render.readme(quest)
    chapter = presentation_of(text, "## Chapter II\n", "## Chapter III\n")
    assert chapter == ("## Chapter II\n\nЛистки лежат в материалах: читай их по мере надобности.\n\n| Что изучить | Где найти |\n|---|---|\n"
                       "| Тема 1 | [materials/theory.md](materials/theory.md#1-тема-1) |\n| Тема 2 | [materials/theory.md](materials/theory.md#2-тема-2) |\n"
                       "| Тема 3 | [materials/theory.md](materials/theory.md#3-тема-3) |\n\n\n")
    # No sheet is headed in the README: its contents list none, and a quest points to its sheet in the file.
    contents = presentation_of(text, "## Содержание\n", "## Введение\n")
    assert "List" not in contents and "- [Chapter II](#chapter-ii)\n- [Chapter III](#chapter-iii)\n" in contents
    assert "Теория: [1. Тема 1](materials/theory.md#1-тема-1).\n" in text and "Конспект темы 1" not in text
    legend = render.readme(planned("legend", "materials"))
    assert "\n## Теория\n" not in legend and "- [Теория]" not in legend
    table = presentation_of(legend, "**Материалы:**\n", "## Задание 1.")
    assert table == ("**Материалы:**\n\n| Что нужно | Где найти |\n|---|---|\n| Данные команды | [team.csv](materials/team.csv) |\n"
                     "| Теория: Тема 1 | [materials/theory.md](materials/theory.md#1-тема-1) |\n| Теория: Тема 2 | [materials/theory.md](materials/theory.md#2-тема-2) |\n"
                     "| Теория: Тема 3 | [materials/theory.md](materials/theory.md#3-тема-3) |\n\n")
    assert "Теория: [1. Тема 1](materials/theory.md#1-тема-1).\n" in legend
    # A project with no other file of materials still gets the table: the theory is there.
    bare = planned("legend", "materials")
    bare.materials = []
    assert "| Теория: Тема 1 | [materials/theory.md](materials/theory.md#1-тема-1) |" in render.readme(bare)


def test_the_file_of_the_theory_is_written_by_code_and_its_anchors_are_the_ones_linked():
    draft = planned("legend", "materials")
    assert draft.theory is not None
    draft.theory.topics[1].title = "Роли: кто за что отвечает?"
    text = render.theory_file(draft)
    assert text.startswith("# Теория к проекту «Проект»\n\n## 1. Тема 1\n\nКонспект темы 1: что это и как устроено. Пример на других данных. "
                           "Типичная ошибка — спешка.\n\n**Где изучить:**\n\n- [Источник 1](https://docs.example.org/1)\n\n"
                           "**Вопрос к практике:** Что из темы 1 пригодится в задании?\n\n## 2. ")
    # GitLab makes the anchor of a heading from its text: the link of the README is made by the same rule.
    assert "\n## 2. Роли: кто за что отвечает?\n" in text
    assert "[materials/theory.md](materials/theory.md#2-роли-кто-за-что-отвечает)" in render.readme(draft)
    # In the bundle the file lies in materials/, beside the files the tasks need.
    names = zipfile.ZipFile(io.BytesIO(render.bundle(draft))).namelist()
    assert "materials/theory.md" in names and "materials/team.csv" in names and "materials/.gitkeep" not in names
    assert zipfile.ZipFile(io.BytesIO(render.bundle(draft))).read("materials/theory.md").decode() == text
    assert "materials/theory.md" not in zipfile.ZipFile(io.BytesIO(render.bundle(planned("legend", "notes")))).namelist()
    # Without the internet the file says nothing of where to read on.
    offline = render.theory_file(planned("legend", "materials", internet="no"))
    assert "Где изучить" not in offline and "Конспект темы 3" in offline
    # A section whose summary is not written holds its short text; a project without other files has no empty marker of materials/.
    bare = planned("legend", "materials", notes=False)
    bare.materials = []
    assert "## 1. Тема 1\n\nНаправление к заданию 1, без ответа.\n" in render.theory_file(bare)
    listed = zipfile.ZipFile(io.BytesIO(render.bundle(bare))).namelist()
    assert "materials/theory.md" in listed and "materials/.gitkeep" not in listed
    bare.theory_plan = None
    listed = zipfile.ZipFile(io.BytesIO(render.bundle(bare))).namelist()
    assert "materials/theory.md" not in listed and "materials/.gitkeep" in listed
    # A file of that name among the materials of the tasks is not written over.
    draft.materials[0].path = "theory.md"
    assert render.theory_path(draft) == "theory_notes.md" and "(materials/theory_notes.md#1-тема-1)" in render.readme(draft)
    assert "materials/theory_notes.md" in zipfile.ZipFile(io.BytesIO(render.bundle(draft))).namelist()


def test_both_keeps_the_short_chapter_and_points_to_the_summary():
    for profile, heading in (("quest", "### List 1. Тема 1"), ("legend", "### 1. Тема 1")):
        draft = planned(profile, "both")
        text = render.readme(draft)
        section = presentation_of(text, heading + "\n", "### List 2." if profile == "quest" else "### 2. Тема 2")
        assert section.startswith(heading + "\n\nНаправление к заданию 1, без ответа.\n") and "Конспект темы 1" not in text
        assert section.rstrip().endswith("Подробнее — в материалах: [materials/theory.md](materials/theory.md#1-тема-1)")
        assert "Теория: [" in text and "](#" in presentation_of(text, "Теория: [", ".\n")
        assert "Конспект темы 1: что это и как устроено." in render.theory_file(draft)


def test_the_whole_text_says_how_the_theory_is_given_and_shows_the_summaries():
    whole = render.full_text(planned("legend", "materials", internet="no", english="yes"))
    assert ("## Теоретический блок\n\n_Подача: конспект в материалах (materials/theory.md); интернет у участников: нет; "
            "выдержка из первоисточника на английском: да._\n") in whole
    assert "Направление к заданию 1, без ответа.\n\n**Конспект:**\n\nКонспект темы 1: что это и как устроено." in whole
    plain = render.full_text(styled_draft("legend"))
    assert "_Подача:" not in plain and "**Конспект:**" not in plain


def test_the_plan_is_part_of_what_the_critics_read_only_where_the_learner_sees_it():
    draft = planned("legend", "chapter", notes=False)
    before = draft.digest()
    plan_of(draft).reason, plan_of(draft).sample, plan_of(draft).english = "Другое обоснование.", "Другой пример.", "yes"
    assert draft.digest() == before
    for field, value in (("presentation", "notes"), ("internet", "no")):
        changed = draft.model_copy(deep=True)
        setattr(plan_of(changed), field, value)
        assert changed.digest() != before, field
    assert draft.theory is not None
    draft.theory.topics[0].notes = "Конспект."
    assert draft.digest() != before
    # A draft without a plan counts as it did: what was added later is counted only where it is filled.
    assert styled_draft("legend").digest() == "d078a259d2505329"
    # The fingerprint a review of 0.6 holds, with all that came with the plan: a change of how it is counted would
    # call every such review stale at once.
    whole = planned("legend", "both", internet="no")
    assert whole.theory is not None
    whole.theory.excerpt = Excerpt(topic=2, title="Roles Guide", url=PRIMARY, quote=QUOTE)
    assert whole.digest() == "d9138ab4bb024ff0"


def test_the_writer_and_the_critic_are_told_the_plan():
    draft = planned("quest", "both", internet="no")
    assert theory_payload(draft)["plan"] == {"presentation": "both", "internet": "no"}
    shown = {str(part["id"]): part for part in parts(draft, rationale=True)}
    assert shown["theory_plan"]["fields"]["presentation"] == "both" and shown["topic:1"]["fields"]["notes"].startswith("Конспект темы 1")  # type: ignore[index]
    asked = plan_payload(draft, "notes")
    assert asked["presentation_given"] == "notes" and asked["current"]["presentation"] == "both"  # type: ignore[index]
    assert set(asked["current"]) == {"presentation", "internet", "english", "reason", "sample", "questions", "written_for"}  # type: ignore[call-overload]
    # The reasons of another presentation are not shown to the chat model as the reasons for this one.
    plan_of(draft).presentation = "notes"
    stale = next(part for part in parts(draft) if part["id"] == "theory_plan")
    assert set(stale["fields"]) == {"presentation", "internet", "english", "questions", "written_for"}  # type: ignore[call-overload]


def test_a_summary_is_edited_in_the_chat_as_any_text_of_a_section():
    draft = planned("legend", "notes")
    message = propose(draft, change("topic:2", "notes", "Новый конспект: роль — это набор обязанностей."))
    assert [(p.label, p.before, p.after) for p in message.patches] == [
        ("Раздел 2 · Конспект", "Конспект темы 2: что это и как устроено. Пример на других данных. Типичная ошибка — спешка.",
         "Новый конспект: роль — это набор обязанностей.")]
    apply(draft, message)
    assert "### 2. Тема 2\n\nНовый конспект: роль — это набор обязанностей." in render.readme(draft)
    cleared = propose(draft, change("topic:2", "notes", ""))
    assert [p.after for p in cleared.patches] == [""]


# ---------- an excerpt of a primary source ----------

def excerpt_writer(model_, search_, pages=None):  # noqa: F811
    async def read(urls):
        found = {PRIMARY: Page(PRIMARY, "Roles Guide", PRIMARY_TEXT), RETOLD: Page(RETOLD, "Пересказ", "Пересказ руководства о ролях: роль — это набор обязанностей.")}
        return [found[u] for u in urls if u in found] if pages is None else pages

    return Writer(model_, search=search_, reader=read)


def find(draft, model_, search_, pages=None):
    assert draft.theory is not None
    return asyncio.run(research.find_excerpt(excerpt_writer(model_, search_, pages), draft, draft.theory.topics, date(2026, 10, 6)))


def test_an_excerpt_is_taken_from_a_page_in_english_word_for_word():
    draft, model_, search_ = planned("quest", "notes", english="yes"), FakeModel(), FakeSearch()
    found = find(draft, model_, search_)
    assert found.excerpt == Excerpt(topic=1, title="Руководство по ролям: определение роли", url=PRIMARY, quote=QUOTE)
    assert (found.note, found.live, found.models) == ("", [PRIMARY], ["perplexity/sonar-pro", "fake-model"])
    # The search was asked for primary sources by the prompt frozen with the draft; the model was shown only the page in English.
    kind, query = search_.calls[0]
    topics: list[dict[str, object]] = [{"number": n, "title": f"Тема {n}", "keywords": []} for n in (1, 2, 3)]
    assert kind == "primary" and query == {
        "looking_for": "primary_source", "today": "2026-10-06", "tools": draft.handoff.tools, "audience": draft.handoff.audience,
        "start_level": draft.handoff.start_level, "project": "Проект", "topics": topics}
    name, system, payload = model_.calls[0]
    assert name == "ExcerptPick" and system.endswith(draft.prompt_snapshots["excerpt"])
    assert payload == {"topics": topics, "limit": 400,
                       "search_answer": f"Раздел 1 — пересказ — {RETOLD}\nРаздел 1 — Roles Guide — {PRIMARY} — руководство авторов",
                       "candidates": [{"number": 1, "url": PRIMARY, "page_title": "Roles Guide", "page_text": PRIMARY_TEXT}]}
    # Without a title yet, the search is told the project by the name the concept gave it.
    draft.summary = None
    find(draft, model_, search_)
    assert search_.calls[-1][1]["project"] == draft.handoff.title


def test_what_the_model_leaves_out_of_its_choice_is_filled_from_the_page():
    draft = planned("quest", "notes", english="yes")
    forty = "This sentence has forty characters here."
    longest = ("A maintainer reviews every change. " * 12)[:399] + "."
    assert (len(forty), len(longest)) == (40, 400)
    for quote in (forty, longest):                       # the limits themselves are within the limits
        picked = FakeModel()
        picked.quote = quote
        found = find(draft, picked, FakeSearch(), pages=[Page(PRIMARY, "Guide", f"Head. {forty} {longest} Tail.")])
        assert found.excerpt is not None and found.excerpt.quote == quote, len(quote)

    class Vague(FakeModel):
        candidate = 1

        async def generate(self, schema, system, payload):
            from project_generator_lab.content.models import ExcerptPick

            return ExcerptPick(candidate=self.candidate, topic=9, title="  ", quote=QUOTE), writer_metrics()

    vague = Vague()
    vague.candidate = 1
    found = find(draft, vague, FakeSearch())
    # A section that does not exist is the first one; a page chosen without a name is named as the page names itself.
    assert found.excerpt == Excerpt(topic=1, title="Roles Guide", url=PRIMARY, quote=QUOTE)
    for missing in (0, 2, 7):                            # a number that is no page of those opened
        vague.candidate = missing
        found = find(draft, vague, FakeSearch())
        assert found.excerpt is None and found.note == "Выдержку из первоисточника не взял: среди открывшихся страниц подходящей не нашлось."


NOT_THERE = f"такого текста нет на странице {PRIMARY} дословно, целыми предложениями"


@pytest.mark.parametrize("quote, why", [
    (QUOTE.replace("responsibilities", "duties"), NOT_THERE),                 # a word changed: not the source's words
    (QUOTE.replace(", not", " — not"), NOT_THERE),                            # a mark changed
    (QUOTE.lower(), NOT_THERE),                                               # the case changed
    (QUOTE + " The rest of this guide explains how a team agrees on them", NOT_THERE),    # cut before the sentence ends
    ("role is a set of responsibilities, not a job title. One person may hold several roles.", NOT_THERE),  # begun in mid-sentence
    ("Роль — это набор обязанностей, а не должность в команде проекта.", "это не английский текст"),         # a translation
    ("Roles Guide Contents Search.", "она короче 40 знаков"),
    (None, "на страницах нет места, где автор объясняет идею.")])
def test_an_excerpt_that_is_not_on_the_page_is_not_taken(quote, why):
    draft, model_ = planned("quest", "notes", english="yes"), FakeModel()
    model_.quote = quote
    found = find(draft, model_, FakeSearch())
    assert found.excerpt is None and found.note.startswith("Выдержку из первоисточника не взял: ") and why in found.note
    if quote:
        assert found.note == f"Выдержку из первоисточника не взял: модель предложила выдержку, но {why}. Найти другую — кнопкой «Найти выдержку из первоисточника»."


@pytest.mark.parametrize("quote, stands", [
    # Cut out of a word or of a sentence, a quotation may say the opposite of the page: it is taken whole or not at all.
    ("It is unsafe to force-push to a shared branch without telling the team first.", True),
    ("safe to force-push to a shared branch without telling the team first.", False),
    ("Do not rebase published history, said the maintainers of the project.", True),
    ("rebase published history, said the maintainers of the project.", False),
    ("It is unsafe to force-push to a shared branch", False),
    ("Unsafe to force-push to a shared branch without telling the team first.", False),
    ("\"Talk is cheap. Show me the code.\"", True),
    ("Rebase guide.", True),                                       # at the very head of the page
    ("3 roles are enough for a small team.", True),                # a sentence may begin with a figure
    ("Git 2.", False),                                             # cut inside a number: «Git 2.40 is out.»
    ("Donald said hello to everyone.", False),                     # the tail of «McDonald said hello…»
    ("English text is refused here.", False),                      # the tail of «Non-English text is refused here.»
    ("Safe to use.", True)])                                       # inside «unSafe to use.» first, and whole after it
def test_a_quotation_is_whole_sentences_of_the_page(quote, stands):
    page = ("Rebase guide. It is unsafe to force-push to a shared branch without telling the team first. "
            "Do not rebase published history, said the maintainers of the project. Linus wrote: \"Talk is cheap. Show me the code.\" Indeed. "
            "3 roles are enough for a small team. Git 2.40 is out. McDonald said hello to everyone. Non-English text is refused here. "
            "It is unSafe to use. But this one is Safe to use.")
    assert research.stands_whole(quote, page) is stands


def test_the_pages_the_search_names_are_read_before_those_it_only_looked_through():
    draft, model_, search_ = planned("quest", "notes", english="yes"), FakeModel(), FakeSearch()
    looked = [f"https://tutorials.example.org/{n}" for n in range(1, 8)]
    opened: list[list[str]] = []

    async def text(system, payload):
        return f"Раздел 1 — Roles Guide — {PRIMARY} — руководство авторов.", [*looked, PRIMARY], writer_metrics()

    async def read(urls):
        opened.append(list(urls))
        return [Page(u, "Tutorial", "This tutorial is about the roles of a team and how they are agreed on.") if u != PRIMARY
                else Page(PRIMARY, "Roles Guide", PRIMARY_TEXT) for u in urls[:writer.READ_PAGES]]

    search_.text = text  # type: ignore[method-assign]
    assert draft.theory is not None
    reading = Writer(model_, search=search_, reader=read)  # type: ignore[arg-type]
    found = asyncio.run(research.find_excerpt(reading, draft, draft.theory.topics, date(2026, 10, 6)))
    # The gateway lists the seven pages it looked through first; only six are read, and the one it chose is the first of them.
    assert opened[0][0] == PRIMARY and opened[0][1:] == looked and found.live[0] == PRIMARY
    assert model_.calls[0][2]["candidates"][0]["url"] == PRIMARY and found.excerpt is not None and found.excerpt.url == PRIMARY


def writer_metrics():
    from project_generator_lab.core.llm import CallMetrics

    return CallMetrics("perplexity/sonar-pro", 10, 100, 50)


def test_an_excerpt_longer_than_the_limit_is_not_taken():
    long = " ".join(["The role of a maintainer is to review every change before it is merged."] * 8)
    draft, model_ = planned("quest", "notes", english="yes"), FakeModel()
    model_.quote = long[:600]
    found = find(draft, model_, FakeSearch(), pages=[Page(PRIMARY, "Guide", long)])
    assert found.excerpt is None and "длиннее 400 знаков" in found.note
    # Within the limit, from the same page, it is taken — with the spaces of the page made single.
    model_.quote = "The role of a maintainer is to  review every change\nbefore it is merged."
    found = find(draft, model_, FakeSearch(), pages=[Page(PRIMARY, "Guide", long)])
    assert found.excerpt is not None and found.excerpt.quote == "The role of a maintainer is to review every change before it is merged."
    assert found.excerpt.title == "Руководство по ролям: определение роли"


def test_without_a_page_in_english_there_is_no_excerpt_and_the_model_is_not_asked():
    draft, model_, search_ = planned("quest", "notes", english="yes"), FakeModel(), FakeSearch()
    found = find(draft, model_, search_, pages=[Page(RETOLD, "Пересказ", "Пересказ руководства о ролях: роль — это набор обязанностей.")])
    assert found.excerpt is None and model_.calls == []
    assert found.note == "Выдержку из первоисточника не взял: поиск назвал 2 страниц, текста на английском среди открывшихся нет."
    search_.no_links = True
    assert find(draft, model_, search_).note == "Выдержку из первоисточника не взял: поиск не назвал ни одной страницы."


@pytest.mark.parametrize("text, verdict", [
    (PRIMARY_TEXT, True), ("A naïve reading of the résumé of a maintainer is not enough for this role.", True),
    ("A role is a set of duties — роль, набор обязанностей.", False), ("Роль — это набор обязанностей, а не должность.", False),
    # Latin letters alone are not English: another language, a transliteration and a line of code have none of its common words.
    ("Eine Rolle ist eine Menge von Verantwortlichkeiten, kein Berufstitel im Team.", False),
    ("Un rôle est un ensemble de responsabilités, pas un titre de poste dans une équipe.", False),
    ("Rol eto nabor obyazannostey a ne dolzhnost v komande proekta segodnya.", False),
    ("git rebase --interactive --autosquash HEAD~3 origin/main feature/login", False),
    ("git rebase -i HEAD~3 переписывает историю последних трёх коммитов", False), ("OK", False), ("12345 67890 !!! ??? ...", False),
    # Where the lines are drawn: twenty letters are enough; a tenth of the letters from another alphabet is too many;
    # two common words are enough when they are an eighth of the words, and not when they are lost among the rest.
    ("It is the end of a long day", True), ("It is the end of a löng däy", False), ("Review the code of others", True),
    ("Die Rolle in einem Team an einem Projekt ist eine Menge von Verantwortlichkeiten und Pflichten jeder Person.", False)])
def test_a_text_is_english_by_its_letters_and_its_words(text, verdict):
    assert research.english(text) is verdict


def test_a_page_to_quote_from_is_read_as_its_reader_sees_it():
    from project_generator_lab.core.web import visible

    markup = ("<html><head><script>var x=\"<b>no</b>\";</script><style>p{}</style></head><body><h1>Rebase</h1><p>Use <code>git rebase</code>, "
              "which moves the tip<sup>1</sup>. It is un<em>safe</em> to <a href=\"/x\">force-push</a>.</p><p>Next &amp; last<br>line</p></body></html>")
    # For the links a page is only looked at; to be quoted from, the marks inside a sentence leave no seam in it.
    assert visible(markup, 500) == "Rebase Use git rebase , which moves the tip 1 . It is un safe to force-push . Next & last line"
    assert visible(markup, 500, prose=True) == "Rebase Use git rebase, which moves the tip1. It is unsafe to force-push. Next & last line"
    assert visible(markup, 20, prose=True) == "Rebase Use git rebas"


def test_the_theory_gets_its_excerpt_and_shows_it_as_a_quotation(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    model.english = "yes"
    to_the_plan(client, run_id)
    view = advance(client, run_id, "approve_theory_plan")
    assert [kind for kind, _ in search.calls] == ["sources", "primary"]
    whole = {"topic": 1, "title": "Руководство по ролям: определение роли", "url": PRIMARY, "quote": QUOTE, "removed": False}
    assert view["theory"]["excerpt"] == whole
    written = next(m for m in view["messages"] if m["part"] == "theory")
    assert written["text"] == ("Теоретический блок: разделов — 3, ссылок — 3, все из страниц, которые открылись. "
                               "Выдержка из первоисточника — в разделе 1: она стоит на странице дословно.")
    assert written["notes"] == ["Поиск: perplexity/sonar-pro, fake-model"]
    assert PRIMARY in store.load(run_id).sources
    readme = client.get(f"/api/content/{run_id}/preview").text
    assert f"**Первоисточник (на английском):** [Руководство по ролям: определение роли](<{PRIMARY}>)\n\n> {QUOTE}\n" in readme
    assert f"Выдержка из первоисточника (Руководство по ролям: определение роли — {PRIMARY}):\n\n> {QUOTE}\n" in client.get(f"/api/content/{run_id}/text").text
    # The chat sees the excerpt as a quotation it may take out and not rewrite.
    model.revisions = [ContentRevision(reply="Перевёл выдержку.", changes=[change("theory", "excerpt", "Роль — это набор обязанностей.")]),
                       ContentRevision(reply="Убираю выдержку.", changes=[change("theory", "excerpt", "")])]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "переведи выдержку на русский"}).json()
    shown = next(part for part in model.calls[-1][2]["parts"] if part["id"] == "theory")
    assert shown == {"id": "theory", "status": "draft", "fields": {"excerpt": QUOTE, "source": PRIMARY},
                     "note": "Выдержка из первоисточника: дословная цитата страницы. Её можно убрать (пустое значение), но не переписать."}
    assert view["messages"][-1]["patches"] == [] and view["messages"][-1]["notes"][0] == (
        "«Теория · Выдержка из первоисточника»: это дословная цитата страницы — её можно убрать или вернуть, но не переписать. "
        "Другую найдёт поиск: попросите в чате найти выдержку заново или нажмите «Найти выдержку из первоисточника».")
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "убери выдержку"}).json()
    taken_out = view["messages"][-1]
    assert [(p["label"], p["before"], p["after"]) for p in taken_out["patches"]] == [("Теория · Выдержка из первоисточника", QUOTE, "")]
    # «Изменить» of that change is no way to type a quotation in: under the name and the address of the real source
    # any sentence would read as the source's (independent review 06.10.2026). The words of the page are never written over.
    typed = client.post(f"/api/content/{run_id}/messages/{taken_out['id']}/decision",
                        json={"verdict": "apply", "values": {"0": "Linus Torvalds wrote in 1987 that roles are a waste of time."}})
    assert typed.status_code == 422 and typed.json()["detail"] == (
        "Правки не прошли проверку: это дословная цитата страницы — её можно убрать или вернуть, но не переписать")
    assert store.load(run_id).model_dump()["theory"]["excerpt"] == whole
    view = advance(client, run_id, "apply_pending")
    assert view["theory"]["excerpt"] == {**whole, "removed": True} and "Первоисточник" not in client.get(f"/api/content/{run_id}/preview").text
    assert "theory" not in [part["id"] for part in parts(store.load(run_id))]
    # Taken out, it is asked for by the plan and missing: said, with the way to find one.
    assert [(c["code"], c["severity"], c["message"]) for c in view["checks"] if c["part"] == "theory"] == [
        ("theory", "warn", "По плану подачи теории нужна выдержка из первоисточника на английском, а её нет. Найдите её кнопкой "
                           "«Найти выдержку из первоисточника» — или скажите в чате, что она не нужна.")]
    # Put back when the change is undone: the words and where they came from were kept.
    view = client.post(f"/api/content/{run_id}/messages/{taken_out['id']}/decision", json={"verdict": "revert"}).json()
    assert view["theory"]["excerpt"] == whole and not [c for c in view["checks"] if c["part"] == "theory"]


def test_an_excerpt_is_searched_for_again_by_a_chip_or_by_words(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    model.english, model.quote = "yes", None
    to_the_plan(client, run_id)
    view = advance(client, run_id, "approve_theory_plan")
    # None was found with the theory: the plan still asks for one, and the search is a chip away — at every later step.
    assert view["theory"]["excerpt"] is None and {"action": "find_excerpt", "button": "Найти выдержку из первоисточника"} in view["next_step"]["alternatives"]
    model.quote = QUOTE
    asked = len(search.calls)
    view = advance(client, run_id, "find_excerpt")
    assert [kind for kind, _ in search.calls[asked:]] == ["primary"] and view["theory"]["excerpt"]["quote"] == QUOTE and view["busy"] == ""
    found = view["messages"][-1]
    assert (found["text"], found["part"], found["notes"]) == (
        "Нашёл выдержку из первоисточника — в разделе 1: она стоит на странице дословно.", "theory", ["Поиск: perplexity/sonar-pro, fake-model"])
    assert {"action": "find_excerpt", "button": "Найти другую выдержку из первоисточника"} in view["next_step"]["alternatives"]
    # By words: code starts the same search — the chat model has no such action and is not asked.
    calls_before = len(model.calls)
    model.quote = "One person may hold several roles, and a role may be shared by several people."
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "найди другую выдержку из первоисточника"}).json()
    from project_generator_lab.tests.content.fixtures import wait_idle

    view = wait_idle(client, run_id)
    assert "ContentRevision" not in [name for name, _, _ in model.calls[calls_before:]]
    assert [m["text"] for m in view["messages"]][-3:-1] == ["найди другую выдержку из первоисточника", "Ищу выдержку из первоисточника…"]
    assert view["theory"]["excerpt"]["quote"] == "One person may hold several roles, and a role may be shared by several people."
    # Nothing found the next time: what stood stays, and why is said — with the models that looked.
    model.quote = None
    view = advance(client, run_id, "find_excerpt")
    assert view["theory"]["excerpt"]["quote"].startswith("One person may hold") and view["messages"][-1]["text"] == (
        "Выдержку из первоисточника не взял: на страницах нет места, где автор объясняет идею.")
    assert (view["messages"][-1]["notes"], view["messages"][-1]["part"]) == (["Поиск: perplexity/sonar-pro, fake-model"], "")
    # The search itself failed: no model looked, and none is named.
    search.fail = True
    view = advance(client, run_id, "find_excerpt")
    assert view["messages"][-1]["text"] == "Выдержку из первоисточника не взял: Шлюз поисковой модели ответил ошибкой 404."
    assert view["messages"][-1]["notes"] == [] and view["busy"] == "" and view["theory"]["excerpt"]["quote"].startswith("One person may hold")
    search.fail = False
    # «Первоисточник» is not «источник»: the search for links does not start on it, and a message that takes the excerpt out is the chat's.
    from project_generator_lab.content.api import LINKS, wants_excerpt

    assert not LINKS.search("найди другой первоисточник") and LINKS.search("найди источники по теме")
    assert wants_excerpt("Поищи, пожалуйста, выдержку заново") and wants_excerpt("нужен другой первоисточник")
    assert not wants_excerpt("убери выдержку и найди ссылки") and not wants_excerpt("выдержка не нужна, найди ссылки") and not wants_excerpt("замени цитату в задании 2")


def test_asked_for_by_words_where_the_plan_said_no_the_excerpt_is_searched_and_the_plan_says_yes(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    to_the_plan(client, run_id)
    view = advance(client, run_id, "approve_theory_plan")
    assert view["theory_plan"]["english"] == "no" and not [a for a in view["next_step"]["alternatives"] if a["action"] == "find_excerpt"]
    assert client.post(f"/api/content/{run_id}/advance", json={"action": "find_excerpt", "number": None}).status_code == 409
    client.post(f"/api/content/{run_id}/messages", json={"text": "найди выдержку из первоисточника"})
    from project_generator_lab.tests.content.fixtures import wait_idle

    view = wait_idle(client, run_id)
    assert view["theory_plan"]["english"] == "yes" and view["theory"]["excerpt"]["quote"] == QUOTE


def test_links_the_learners_cannot_open_are_not_searched_for_in_the_chat_either(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    model.internet, model.presentation = "no", "notes"
    to_the_plan(client, run_id)
    advance(client, run_id, "approve_theory_plan")
    model.revisions = [ContentRevision(reply="Поищу ссылки.", find_links=True)]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "найди ссылки для теории"}).json()
    assert search.calls == [] and view["busy"] == ""
    # Said in place of the model's «поищу»: a reply that promised the search would contradict what is done.
    assert view["messages"][-1]["text"] == "Ссылки не ищу: по плану подачи теории у участников нет интернета. Если он есть — скажите, я поправлю план."
    assert view["messages"][-1]["patches"] == []


def test_the_same_words_of_the_excerpt_are_no_change_however_they_are_spaced():
    draft = planned("legend", "notes")
    assert draft.theory is not None
    draft.theory.excerpt = Excerpt(topic=1, title="Roles Guide", url=PRIMARY, quote=QUOTE)
    # The model echoes the quotation with its own line breaks, or as a list of lines: the same words are not a rewrite.
    for echoed in (QUOTE.replace(". One", ".\n\nOne").replace(", not", ",  not"), QUOTE.split(". ", 1)[0] + ".\n" + QUOTE.split(". ", 1)[1],
                   [QUOTE.split(". ", 1)[0] + ".", QUOTE.split(". ", 1)[1]]):
        message = propose(draft, change("theory", "excerpt", echoed))
        assert message.patches == [] and message.notes == ["«Теория · Выдержка из первоисточника» уже содержит это значение."]
    # A draft with no excerpt has nothing to take out or to put back.
    draft.theory.excerpt = None
    none = propose(draft, change("theory", "excerpt", ""))
    assert none.patches == [] and none.notes == ["«Теория» ещё не написано — правка не применена."]


def test_a_search_for_the_excerpt_that_fails_does_not_cost_the_theory(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    model.english, model.internet, model.presentation = "yes", "no", "notes"
    to_the_plan(client, run_id)
    search.fail = True
    view = advance(client, run_id, "approve_theory_plan")
    assert view["theory"]["excerpt"] is None and len(view["theory"]["topics"]) == 3 and view["next_step"]["action"] == "approve_support"
    written = next(m["text"] for m in view["messages"] if m["part"] == "theory")
    assert written == ("Теоретический блок: разделов — 3. Ссылки не искал: у участников нет интернета. Подача — конспект в README. "
                       "Выдержку из первоисточника не взял: Шлюз поисковой модели ответил ошибкой 404.")
    # Without the internet only the excerpt was searched for.
    assert [kind for kind, _ in search.calls] == ["primary"]


def test_an_excerpt_is_in_the_file_of_the_theory_too_and_in_what_the_critics_read():
    draft = planned("legend", "materials")
    assert draft.theory is not None
    before = draft.digest()
    draft.theory.excerpt = Excerpt(topic=2, title="Roles Guide", url=PRIMARY, quote=QUOTE)
    assert f"**Первоисточник (на английском):** [Roles Guide](<{PRIMARY}>)\n\n> {QUOTE}\n" in render.theory_file(draft)
    # Once, in the section it belongs to.
    filed = render.theory_file(draft)
    assert filed.count(QUOTE) == 1 and filed.index("## 2. Тема 2") < filed.index(QUOTE) < filed.index("## 3. Тема 3")
    both = planned("quest", "both")
    assert both.theory is not None
    both.theory.excerpt = draft.theory.excerpt
    text = render.readme(both)
    assert text.count(QUOTE) == 1 and text.index("### List 2. Тема 2") < text.index(QUOTE) < text.index("### List 3. Тема 3")
    assert QUOTE not in render.readme(draft) and draft.digest() != before
    # Taken out, it is as if there were none.
    draft.theory.excerpt.removed = True
    assert "Первоисточник" not in render.theory_file(draft) and draft.digest() == before


def test_a_quotation_is_shown_to_the_letter_whatever_marks_it_holds():
    # The words of a page are not Markdown: «[<options>]» poured in as it is came out as «[]», «__init__» as a bold
    # «init», a line that begins with «#» as a heading (independent review 06.10.2026).
    draft = planned("legend", "notes")
    assert draft.theory is not None
    quote = "# git rebase [-i | --interactive] [<options>] calls __init__ after *new* & `old` ~~x~~ 1 < 2"
    draft.theory.excerpt = Excerpt(topic=1, title="git-rebase(1) [docs] <b>", url="https://example.org/wiki/Git_(software) x", quote=quote)
    text = render.readme(draft)
    assert ("**Первоисточник (на английском):** [git-rebase(1) \\[docs\\] \\<b\\>](<https://example.org/wiki/Git_(software)%20x>)\n\n"
            "> \\# git rebase \\[-i \\| --interactive\\] \\[\\<options\\>\\] calls \\_\\_init\\_\\_ after \\*new\\* \\& \\`old\\` \\~\\~x\\~\\~ 1 \\< 2\n") in text
    assert render.address("https://example.org/a<b>c d") == "<https://example.org/a%3Cb%3Ec%20d>"
    for typed, shown in (("- a list item", "\\- a list item"), ("1. First", "1\\. First"), ("2) Second", "2\\) Second"), ("+ plus", "\\+ plus"),
                         ("A plain sentence, with a comma.", "A plain sentence, with a comma."), ("two\nlines  of it", "two lines of it")):
        assert render.literal(typed) == shown


# ---------- the places of pictures ----------

def test_a_place_for_a_picture_is_shown_as_such_and_counted():
    draft = planned("quest", "notes")
    assert draft.frame is not None and draft.theory is not None
    draft.frame.introduction = "Ты входишь в комнату.\n\n[картинка: тёмная комната, на столе терминал]\n\nЭкран мигает."
    draft.tasks[1].context = "Марк выводит схему.\n\n  [ Рисунок : схема — кто за что отвечает ]  \n\nСтрока в тексте [картинка: не на своей строке] остаётся."
    draft.theory.topics[0].notes = "Конспект.\n\n[иллюстрация — слои роли]"
    text = render.readme(draft)
    assert "Ты входишь в комнату.\n\n> **Место для картинки:** тёмная комната, на столе терминал\n\nЭкран мигает." in text
    assert "\n> **Место для картинки:** схема — кто за что отвечает\n" in text and "> **Место для картинки:** слои роли" in text
    # A mark is a line of its own: the same words inside a sentence are the sentence's.
    assert "Строка в тексте [картинка: не на своей строке] остаётся." in text
    assert [c.message for c in run_checks(draft) if c.code == "pictures"] == [
        "В тексте отмечено мест для картинок: 3 (введение, задание 2, раздел теории 1). Перед выдачей участникам положите картинки в "
        "misc/images/ и замените пометки ссылками на них — или уберите пометки."]
    # The methodologist reads the text as it was written, with the marks as marks.
    assert "[картинка: тёмная комната, на столе терминал]" in render.full_text(draft)
    # A legend shows a mark the same way if one is written; the file of the theory too.
    legend = planned("legend", "materials")
    assert legend.theory is not None
    legend.theory.topics[0].notes = "Конспект.\n\n[картинка: схема ролей]"
    assert "> **Место для картинки:** схема ролей" in render.theory_file(legend)
    # No mark — nothing to say.
    assert not [c for c in run_checks(planned("quest", "notes")) if c.code == "pictures"]


def test_a_mark_of_a_picture_stands_apart_from_the_text_around_it():
    said = "Место для картинки:"
    # The line after a quotation is read as its continuation, and two marks in a row as one: an empty line parts them.
    assert render.pictured("До.\n[картинка: первая]\n[картинка: вторая]\nПосле.", said) == (
        "До.\n\n> **Место для картинки:** первая\n\n> **Место для картинки:** вторая\n\nПосле.")
    assert render.pictured("[картинка: одна]", said) == "> **Место для картинки:** одна"
    assert render.pictured("До.\n\n[картинка: с текстом сразу после]\nПосле.", said) == "До.\n\n> **Место для картинки:** с текстом сразу после\n\nПосле."
    assert render.pictured("До.\n[картинка: с пустой строкой после]\n\nПосле.", said) == "До.\n\n> **Место для картинки:** с пустой строкой после\n\nПосле."
    # The ends of lines of another system are read as lines.
    assert render.pictured("До.\r\n\r\n[картинка: с переводом строки]\r\n\r\nПосле.", said) == "До.\n\n> **Место для картинки:** с переводом строки\n\nПосле."
    assert render.pictured("До.\r[картинка: со старым концом строки]\rПосле.", said) == "До.\n\n> **Место для картинки:** со старым концом строки\n\nПосле."
    # Inside a block of code a line in brackets is what the program reads or prints; two marks in one line are no mark.
    fenced = "Ввод:\n\n```\n[картинка: это данные]\n```\n\n~~~\n[рисунок: и это]\n~~~"
    assert render.pictured(fenced, said) == fenced
    # After the block is closed a mark is a mark again; a fence of the other kind inside a block does not close it.
    assert render.pictured(fenced + "\n\n[картинка: после кода]", said) == fenced + "\n\n> **Место для картинки:** после кода"
    mixed = "```\n~~~\n[картинка: всё ещё данные]\n```"
    assert render.pictured(mixed + "\n[картинка: уже нет]", said) == mixed + "\n\n> **Место для картинки:** уже нет"
    two = "[картинка: а] [картинка: б]"
    assert render.pictured(two, said) == two and render.pictured("- [картинка: в списке]", said) == "- [картинка: в списке]"
    # A text without marks is not touched at all.
    plain = "Строка.\r\nВторая строка.  \n\n\nТретья."
    assert render.pictured(plain, said) is plain


def test_the_marks_counted_are_the_marks_a_learner_is_shown():
    def counted(draft):
        return [c.message.split(". ")[0] for c in run_checks(draft) if c.code == "pictures"]

    mark = "\n\n[картинка: схема]"
    for shown, expected in (("chapter", ["В тексте отмечено мест для картинок: 1 (раздел теории 1)"]), ("notes", []), ("materials", []),
                            ("both", ["В тексте отмечено мест для картинок: 1 (раздел теории 1)"])):
        draft = planned("quest", shown)
        assert draft.theory is not None
        draft.theory.topics[0].text += mark          # the short text: shown by the chapter and by «both»
        assert counted(draft) == expected, shown
        assert ("> **Место для картинки:** схема" in render.readme(draft)) is bool(expected), shown
    for shown, expected in (("chapter", []), ("notes", ["В тексте отмечено мест для картинок: 1 (раздел теории 2)"]),
                            ("materials", ["В тексте отмечено мест для картинок: 1 (раздел теории 2)"]),
                            ("both", ["В тексте отмечено мест для картинок: 1 (раздел теории 2)"])):
        draft = planned("quest", shown)
        assert draft.theory is not None
        draft.theory.topics[1].notes += mark         # the summary: shown by «notes» in the README, by the others in the file
        assert counted(draft) == expected, shown
        where = render.readme(draft) + (render.theory_file(draft) if render.theory_path(draft) else "")
        assert ("> **Место для картинки:** схема" in where) is bool(expected), shown
    # A mark left in a field the README does not turn into a place stays a line in brackets there — and is counted, to be found.
    draft = planned("legend", "chapter")
    draft.tasks[0].steps = ["Создай файл.", "[картинка: снимок экрана]"]
    draft.tasks[2].result = "src/task3.md\n[картинка: пример таблицы]"
    assert counted(draft) == ["В тексте отмечено мест для картинок: 2 (задание 1, задание 3)"] and "[картинка: снимок экрана]" in render.readme(draft)
    # A line of code is not a mark for the count either.
    draft = planned("legend", "chapter")
    draft.tasks[0].context = "Данные:\n\n```\n[картинка: строка файла]\n```"
    assert counted(draft) == []
    # The annotation is a text a learner reads too; and where the summary of a section is not written, its short text
    # stands in its place — in the README and in the count.
    draft = planned("legend", "notes", notes=False)
    assert draft.summary is not None and draft.theory is not None
    draft.summary.annotation += "\n[картинка: обложка]"
    draft.theory.topics[2].text += mark
    assert counted(draft) == ["В тексте отмечено мест для картинок: 2 (аннотация, раздел теории 3)"]
    assert "> **Место для картинки:** схема" in render.readme(draft)


def test_a_line_in_brackets_of_a_draft_begun_before_the_marks_is_its_own_text():
    # A draft of 0.5 or 0.4 was never told of the marks: «[картинка: …]» there was typed by its author and went into
    # the README as typed. It still does — the same README, and nothing is counted (independent review 06.10.2026).
    for draft in (of_05("quest"), of_05("legend"), complete_draft()):
        assert draft.frame is not None
        before = render.readme(draft)
        draft.frame.introduction += "\n\n[картинка: стойка с мигающими лампами]"
        draft.tasks[0].context += "\n\n[Рисунок: схема]"
        text = render.readme(draft)
        assert "[картинка: стойка с мигающими лампами]" in text and "[Рисунок: схема]" in text and "Место для картинки" not in text
        assert len(text) == len(before) + len("\n\n[картинка: стойка с мигающими лампами]") + len("\n\n[Рисунок: схема]")
        assert not [c for c in run_checks(draft) if c.code == "pictures"]


def test_the_file_of_the_theory_never_takes_the_name_of_a_file_of_a_task():
    draft = planned("legend", "materials")
    names = []
    first = draft.materials[0]
    for taken in ((), ("theory.md",), ("Theory.MD", "theory_notes.md"), ("theory.md", "theory_notes.md", "theory_notes_2.md")):
        draft.materials = [first.model_copy(update={"path": path}) for path in taken]
        names.append(render.theory_path(draft))
        listed = zipfile.ZipFile(io.BytesIO(render.bundle(draft))).namelist()
        assert len(listed) == len(set(listed)) and f"materials/{names[-1]}" in listed and all(f"materials/{path}" in listed for path in taken)
        assert f"(materials/{names[-1]}#1-тема-1)" in render.readme(draft)
    assert names == ["theory.md", "theory_notes.md", "theory_notes_2.md", "theory_notes_3.md"]


def test_a_preview_before_the_theory_is_written_shows_no_empty_table():
    for profile in ("quest", "legend"):
        draft = planned(profile, "materials")
        draft.theory = None
        text = render.readme(draft)
        assert "Где найти |\n|---|---|\n\n" not in text and "Листки лежат в материалах" not in text and render.theory_path(draft) == ""
        assert "materials/theory.md" not in zipfile.ZipFile(io.BytesIO(render.bundle(draft))).namelist()


def test_the_writer_of_a_quest_is_told_how_to_mark_a_picture():
    frozen = writer.snapshot()
    assert "[картинка: что на ней]" in frozen["style_quest"] and "картинк" not in frozen["style_legend"]
    assert "[картинка: …]" in frozen["critic"] and "[картинка: что на ней]" in frozen["revise"]
    assert {"theory_plan", "primary", "excerpt"} <= set(frozen) and writer.CONTENT_VERSION == "0.11"


# ---------- a draft begun before the plan goes on as it began ----------

PINNED_05 = {
    "legend": {"readme": "fa83c9477f5a97fb", "full": "5087dd20ef0480be", "checklist": "fece76aeb8ea6e91", "digest": "d078a259d2505329",
               "theory_payload": "2612549fbd316f7a", "critic_payload": "9a654370bb788e17", "revise_payload": "679a96e334ed15e4",
               "task_payload": "d7efaf0c958cb1af", "frame_payload": "200c169250fd35a1", "editable": "0660cd6ef74ef0e7", "parts": "eaaac5c2a4aad7aa"},
    "quest": {"readme": "91dae8006007cf79", "full": "80b7c77daf31b10e", "checklist": "278a87279f1406c4", "digest": "171e2e7757ba7ee6",
              "theory_payload": "4eaaa9eb7bd2f8eb", "critic_payload": "18bc7e92703c3cd2", "revise_payload": "29100f3a4f129b7a",
              "task_payload": "c433c4d536316422", "frame_payload": "2c650f1f917ad3a7", "editable": "0660cd6ef74ef0e7", "parts": "914d15fd4bba64c9"}}


def digest(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def of_05(profile) -> ContentDraft:
    draft = styled_draft(profile)
    draft.prompt_snapshots = {name: text for name, text in draft.prompt_snapshots.items() if name in PROMPTS_OF_05}
    return draft


@pytest.mark.parametrize("profile", ["legend", "quest"])
def test_a_draft_begun_at_05_is_sent_and_shown_what_it_was(profile):
    # Recorded from the code of content 0.5 (06.10.2026), before the plan of the theory: what a learner would read, what
    # the critics' review holds, and what the writers, the critic and the chat model are sent — to the letter.
    draft = of_05(profile)
    assert draft.styled() and not draft.plans_theory() and draft.presentation() == "chapter" and draft.online()
    found = {"readme": digest(render.readme(draft)), "full": digest(render.full_text(draft)), "checklist": digest(render.checklist(draft)),
             "digest": draft.digest(), "theory_payload": digest(writer.theory_payload(draft)), "critic_payload": digest(writer.critic_payload(draft)),
             "revise_payload": digest(writer.revise_payload(draft, "сократи задание 2", "task:2")), "task_payload": digest(writer.task_payload(draft, 2)),
             "frame_payload": digest(writer.frame_payload(draft)), "editable": digest(editable(draft)), "parts": digest(parts(draft))}
    assert found == PINNED_05[profile]
    # It is offered neither the plan nor what came with it, and is not told of the step.
    assert "theory_plan" not in editable(draft) and "theory" not in editable(draft) and "notes" not in editable(draft)["topic"]
    refused = propose(draft, change("theory_plan", "presentation", "notes"), change("topic:1", "notes", "Конспект."))
    assert refused.patches == [] and refused.notes == ["Правку «theory_plan · presentation» не могу применить: такого поля нет.",
                                                       "Правку «topic:1 · notes» не могу применить: такого поля нет."]
    draft.theory = draft.frame = draft.review = None
    step = next_step(draft)
    assert step["action"] == "write_support" and step["route"] == stages.ROUTE_BEFORE_PLAN and "как подать теорию" not in str(step["route"])
    assert str(step["text"]).startswith("Теоретический блок — 2–5 коротких разделов со ссылками")


def test_a_draft_begun_at_05_walks_its_route_without_the_question(client, model, search):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    view = advance(client, run_id, "approve_style")
    draft = store.load(run_id)
    draft.content_version = "0.5"
    draft.prompt_snapshots = {name: text for name, text in draft.prompt_snapshots.items() if name in PROMPTS_OF_05}
    store.save(draft)
    view = client.get(f"/api/content/{run_id}").json()
    assert view["plans_theory"] is False
    pressed: list[str] = []
    while view["next_step"]["action"]:
        assert len(pressed) < 40, (pressed[-3:], view["messages"][-1]["text"])
        pressed.append(view["next_step"]["action"])
        if view["next_step"]["action"] == "approve_task" and view["next_step"]["number"] == 3:
            assert view["next_step"]["button"] == "Задание 3 подходит — теория и введение"
        view = advance(client, run_id, view["next_step"]["action"], view["next_step"]["number"])
    assert not {"write_theory_plan", "approve_theory_plan"} & set(pressed) and pressed[-1] == "assemble"
    assert "TheoryPlanDraft" not in [name for name, _, _ in model.calls] and view["theory_plan"] is None
    assert model.schemas[[name for name, _, _ in model.calls].index("TheoryDraft")] is TheoryDraft and "plan" not in calls(model, "TheoryDraft")[0]
    assert [kind for kind, _ in search.calls] == ["sources", "facts"]
    written = next(m["text"] for m in view["messages"] if m["part"] == "theory")
    assert written == "Теоретический блок: разделов — 3, ссылок — 3, все из страниц, которые открылись."
    assert "Задание 3 согласовано. Готовлю теорию и введение…" in [m["text"] for m in view["messages"]]


@pytest.mark.parametrize("quote, page, stands", [
    # Whole where it stands first and cut out of a word further on: the first place is enough.
    ("Read the manual of the tool.", "Read the manual of the tool. Later: Read the manual of the tool.x", True),
    # Cut where it stands first and last, whole between them: every place is looked at, not the first and the last.
    ("Read the manual of the tool.", "unRead the manual of the tool. Read the manual of the tool. unRead the manual of the tool.", True),
    # A letter right after the quote cuts it out of a word, whatever follows that letter.
    ("Read the manual of the tool.", "Read the manual of the tool.x and more", False),
    ("Read the manual of the tool.", "Read the manual of the tool.x", False)])
def test_every_place_of_a_quotation_on_the_page_is_looked_at(quote, page, stands):
    assert research.stands_whole(quote, page) is stands


def test_a_text_with_an_eighth_of_common_words_is_english_at_the_line_itself():
    rare = "maintainer reviews change merges branch commits pushes rebases squashes tags releases"
    at_the_line = f"the of and {rare} {rare}"                       # 3 common words of 25: twelve in a hundred exactly
    assert len(at_the_line.split()) == 25 and research.english(at_the_line) is True
    assert research.english(f"{at_the_line} deploys") is False     # 3 of 26: below the line


def test_the_search_for_a_primary_source_is_asked_in_the_words_of_the_project():
    draft, model_, search_ = planned("quest", "notes", english="yes"), FakeModel(), FakeSearch()
    asked: list[tuple[str, str]] = []
    answer = "Раздел 2 — Roles Guide — " + PRIMARY + " — " + "о" * 5000

    async def text(system, payload):
        asked.append((system, payload))
        return answer, [PRIMARY], writer_metrics()

    search_.text = text  # type: ignore[method-assign]
    model_.excerpt_topic = 2
    found = find(draft, model_, search_)
    # The prompt is the one frozen with the draft; Russian words go to the search as they are, not as escape codes.
    system, payload = asked[0]
    assert system == draft.prompt_snapshots["primary"] and "Тема 1" in payload and "\\u" not in payload
    # The model that chooses is shown the head of a long answer, and the section it names is the section of the excerpt.
    shown = calls(model_, "ExcerptPick")[0]["search_answer"]
    assert len(shown) == 4000 and shown == answer[:4000]
    assert found.excerpt is not None and found.excerpt.topic == 2


def test_the_bundle_keeps_a_file_as_written_with_one_newline_at_its_end():
    draft = planned("legend", "chapter")
    path = f"materials/{draft.materials[0].path}"
    for written in ("name,role\nАня,аналитик", "name,role\nАня,аналитик\n"):
        draft.materials[0].content = written
        assert zipfile.ZipFile(io.BytesIO(render.bundle(draft))).read(path).decode() == "name,role\nАня,аналитик\n"
    # The folders a learner fills are kept by an empty marker; the archive is compressed.
    archive = zipfile.ZipFile(io.BytesIO(render.bundle(draft)))
    assert archive.read("src/.gitkeep") == b"" and archive.read("misc/images/.gitkeep") == b""
    assert {info.compress_type for info in archive.infolist()} == {zipfile.ZIP_DEFLATED}
    draft.materials, draft.theory_plan = [], None
    assert zipfile.ZipFile(io.BytesIO(render.bundle(draft))).read("materials/.gitkeep") == b""
