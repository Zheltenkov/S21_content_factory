"""The style a project is told in — the quest of a bassin or a working legend: who chooses it, how the assistant
proposes and rewrites it, what the writers are given of it, and how the README is laid out in each.

Decided by the methodologist on 06.10.2026 from the templates of the bassin and the three runs of the intensive. The
model is scripted: what a real one makes of the styles is seen in a run on the server, not here.
"""

import asyncio
import json
from uuid import uuid4

import pytest
import yaml
from project_generator_lab.content import api as content_api
from project_generator_lab.content import render, store
from project_generator_lab.content.checks import blocking, run_checks
from project_generator_lab.content.edits import apply, normalize, revert
from project_generator_lab.content.handoff import card
from project_generator_lab.content.models import (
    BREAK,
    ContentChange,
    ContentDraft,
    ContentRevision,
    Profile,
    Review,
    Style,
    TaskContent,
    TaskDraft,
    TaskOpening,
    example_cells,
    example_items,
    sorted_examples,
)
from project_generator_lab.content.stages import next_step
from project_generator_lab.content.writer import (
    EDITABLE,
    STYLED,
    Writer,
    critic_payload,
    editable,
    project,
    revise_payload,
    snapshot,
    task_payload,
)
from project_generator_lab.core.llm import ModelUnavailable
from project_generator_lab.tests.content.fixtures import handoff_fixture, wait_idle
from project_generator_lab.tests.content.test_parts import complete_draft, propose, task
from project_generator_lab.tests.content.test_route import (  # noqa: F401
    advance,
    begin,
    client,
    model,
    reviewer,
    search,
    walk,
)
from pydantic import ValidationError

STORY = "Комната с терминалом. ИИ по имени Марк открывает двери за починенные модули."


def given(client, monkeypatch, **fields):  # noqa: F811
    """A concept handed over with the style or the story of the series named in the form."""
    monkeypatch.setattr(content_api.settings, "handoff", lambda run_id: handoff_fixture(run_id).model_copy(update=fields))


def style_calls(fake):
    return [payload for name, _, payload in fake.calls if name == "StyleDraft"]


# ---------- who chooses ----------

def test_the_assistant_proposes_a_style_before_any_text_and_says_why(client, model):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    view = wait_idle(client, run_id)
    assert [name for name, _, _ in model.calls] == ["StyleDraft"] and view["summary"] is None
    style = view["style"]
    assert (style["profile"], style["chosen_by"], style["status"]) == ("legend", "assistant", "draft")
    assert style["reason"].startswith("Сюжета серии во входных данных нет") and style["story"].startswith("Ты — новый продакт")
    shown = [m for m in view["messages"] if m["part"] == "style"]
    assert [m["text"] for m in shown] == ["Предлагаю рассказать проект в стиле «рабочая легенда» — ниже почему и какой сюжет."]
    # Written by the model at one asking: the message carries its name and its count, and no note of a second asking.
    assert (shown[0]["model"], shown[0]["input_tokens"], shown[0]["output_tokens"], shown[0]["notes"]) == ("fake-model", 100, 50, [])
    step = view["next_step"]
    assert (step["action"], step["button"]) == ("approve_style", "Стиль подходит — предложить название и аннотацию")
    assert step["alternatives"] == [{"action": "switch_style", "button": "Сменить стиль на «квест»"}]
    assert step["edit_hint"] == "Что поменять в стиле или сюжете…" and "На вопросы по сюжету" not in step["text"]
    # What the assistant chose from: the concept, nothing named by the methodologist, and both styles in full.
    payload = style_calls(model)[0]
    assert (payload["profile_given"], payload["series_story"], payload["current"]) == ("", "", None)
    assert set(payload["profiles"]) == {"quest", "legend"} and payload["profiles"]["quest"].startswith("Стиль проекта: «квест»")
    assert [t["number"] for t in payload["tasks"]] == [1, 2, 3] and "style" not in payload["project"]
    assert payload["tasks"][0] == {"number": 1, "title": "Роли в команде", "action": "Сопоставь роли и зоны ответственности по данным команды",
                                   "deliverable": "Файл задания 1 в src/", "optional": False}


def test_a_style_named_in_the_form_is_kept_whatever_the_model_answers(client, model, monkeypatch):  # noqa: F811
    given(client, monkeypatch, style_profile="quest", series_story=STORY)
    model.questions = ["Как зовут персонажа серии?", "- На чём закончился прошлый проект?"]
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    view = wait_idle(client, run_id)
    # The scripted model answers «legend»: code holds to what the methodologist chose.
    assert (view["style"]["profile"], view["style"]["chosen_by"]) == ("quest", "form")
    assert view["style"]["questions"] == ["Как зовут персонажа серии?", "На чём закончился прошлый проект?"]
    assert view["messages"][-1]["text"] == "Стиль «квест» выбрали вы: вот как я вижу в нём этот проект. Есть вопросы по сюжету."
    payload = style_calls(model)[0]
    assert (payload["profile_given"], payload["series_story"]) == ("quest", STORY)
    rows = {row["label"]: row for row in view["handoff_card"]}
    assert rows["Стиль подачи"]["value"] == "квест" and rows["Сквозной сюжет серии"] == {
        "label": "Сквозной сюжет серии", "value": "задан", "ok": True, "items": [STORY]}
    assert "На вопросы по сюжету ответьте в чате" in view["next_step"]["text"]
    assert view["next_step"]["alternatives"] == [{"action": "switch_style", "button": "Сменить стиль на «рабочая легенда»"}]
    # The two rows stand after the storytelling of the concept; a story of spaces alone is no story.
    labels = [row["label"] for row in view["handoff_card"]]
    assert labels[labels.index("Сторителлинг"):][:4] == ["Сторителлинг", "Стиль подачи", "Сквозной сюжет серии", "Место в учебном плане"]
    blank = {row["label"]: row for row in card(handoff_fixture().model_copy(update={"style_profile": "legend", "series_story": " \n "}))}
    assert blank["Стиль подачи"] == {"label": "Стиль подачи", "value": "рабочая легенда", "ok": True}
    assert blank["Сквозной сюжет серии"] == {"label": "Сквозной сюжет серии", "value": "нет", "ok": True, "items": []}


def test_a_style_the_form_does_not_know_is_no_choice(client, model, monkeypatch):  # noqa: F811
    given(client, monkeypatch, style_profile="комикс")
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    view = wait_idle(client, run_id)
    assert style_calls(model)[0]["profile_given"] == "" and (view["style"]["profile"], view["style"]["chosen_by"]) == ("legend", "assistant")


def test_the_style_is_switched_by_the_chip_and_the_story_is_written_anew_for_it(client, model):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    view = advance(client, run_id, "switch_style")
    assert (view["style"]["profile"], view["style"]["chosen_by"], view["style"]["status"]) == ("quest", "chat", "draft")
    said = [m["text"] for m in view["messages"]]
    assert "Сменить стиль на «квест»" in said and "Меняю стиль на «квест» и пишу сюжет заново…" in said
    assert said[-1] == "Стиль «квест» выбрали вы: вот как я вижу в нём этот проект."
    # The second call was told the style and shown what had been proposed before.
    second = style_calls(model)[1]
    assert second["profile_given"] == "quest" and second["current"]["profile"] == "quest"
    # What was proposed before, as a text to write anew — not the marks of the route (who chose it, whether it is agreed).
    assert set(second["current"]) == {"profile", "reason", "story", "questions", "written_for"} and second["current"]["written_for"] == "legend"
    # Back again: the choice stays the methodologist's.
    view = advance(client, run_id, "switch_style")
    assert (view["style"]["profile"], view["style"]["chosen_by"]) == ("legend", "chat")
    assert style_calls(model)[2]["profile_given"] == "legend"
    # Once agreed, the chip is gone and the server refuses it: the style is changed by words from then on.
    view = advance(client, run_id, "approve_style")
    assert view["style"]["status"] == "approved" and view["next_step"]["action"] == "approve_summary"
    assert client.post(f"/api/content/{run_id}/advance", json={"action": "switch_style", "number": None}).status_code == 409


def test_the_style_is_changed_and_the_questions_answered_by_words(client, model):  # noqa: F811
    run_id = str(uuid4())
    model.questions = ["Как зовут персонажа?"]
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    model.revisions = [ContentRevision(kind="answer", reply="Вписал имя в сюжет.", changes=[
        ContentChange(part="style", field="story", value="Ты — новый продакт. Персонажа зовут Марк.", reason="Ответ на вопрос о персонаже"),
        ContentChange(part="style", field="questions", value=[], reason="Вопрос отвечен")]),
        ContentRevision(reply="Меняю стиль.", changes=[ContentChange(part="style", field="profile", value="«Квест»", reason="Просьба методолога")]),
        ContentRevision(reply="Попробую.", changes=[ContentChange(part="style", field="profile", value="комикс", reason="Просьба")])]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "Персонажа зовут Марк"}).json()
    name, system, payload = model.calls[-1]
    # The chat is told what the methodologist is looking at and reads the style it edits.
    assert name == "ContentRevision" and payload["focus"] == "style" and "Стиль проекта: «рабочая легенда»" in system
    assert payload["parts"][0]["id"] == "style" and payload["parts"][0]["fields"]["questions"] == ["Как зовут персонажа?"]
    assert [p["label"] for p in view["messages"][-1]["patches"]] == ["Стиль проекта · Сюжет", "Стиль проекта · Вопросы по сюжету"]
    view = advance(client, run_id, "apply_pending")
    assert view["style"]["story"].endswith("Марк.") and view["style"]["questions"] == [] and view["style"]["chosen_by"] == "assistant"
    # The style by its Russian name; the choice becomes the methodologist's.
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "давай всё-таки квест"}).json()
    patch = view["messages"][-1]["patches"][0]
    assert (patch["label"], patch["before"], patch["after"]) == ("Стиль проекта · Стиль подачи", "legend", "quest")
    view = advance(client, run_id, "apply_pending")
    assert (view["style"]["profile"], view["style"]["chosen_by"]) == ("quest", "chat")
    # A style there is none of is refused in words, not written.
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "сделай комиксом"}).json()
    assert view["messages"][-1]["patches"] == []
    assert view["messages"][-1]["notes"] == ["«Стиль проекта · Стиль подачи»: стиль — «квест» или «рабочая легенда» — правка не предложена."]
    assert told(store.load(run_id)).profile == "quest"


@pytest.mark.parametrize("said, read", [("quest", "quest"), ("Квест", "quest"), (" «рабочая  легенда» ", "legend"), ("ЛЕГЕНДА", "legend"),
                                         (["legend"], "legend"), ("комикс", "комикс")])
def test_a_style_is_read_by_any_of_its_names(said, read):
    assert normalize("profile", said) == read


# ---------- found by the independent review of 06.10.2026 ----------

def test_a_bare_yes_under_questions_about_the_story_is_neither_agreement_nor_a_guess(client, model):  # noqa: F811
    run_id = str(uuid4())
    model.questions = ["Уместен ли юмор в сценах?"]
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    calls = len(model.calls)
    for word in ("да", "пойдёт", "ок"):
        view = client.post(f"/api/content/{run_id}/messages", json={"text": word}).json()
        # No paid call for the title, no model asked to guess, and the style still waits.
        assert len(model.calls) == calls and view["style"]["status"] == "draft" and view["summary"] is None
        assert view["messages"][-1]["text"] == (
            "Не понял, это ответ на вопрос о сюжете или согласие со стилем. Ответ напишите словами — я впишу его в сюжет. "
            "Если стиль подходит и вопросы можно оставить без ответа — кнопка «Стиль подходит — предложить название и аннотацию» или «дальше».")
    # An answer in words goes to the model; «дальше» moves on with the questions left open.
    model.revisions = [ContentRevision(kind="answer", reply="Вписал.", changes=[
        ContentChange(part="style", field="story", value="Ты — новый продакт. Юмор уместен.", reason="Ответ")])]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "да, юмор уместен, но без сарказма"}).json()
    assert model.calls[-1][0] == "ContentRevision" and view["messages"][-1]["status"] == "pending"
    advance(client, run_id, "apply_pending")
    client.post(f"/api/content/{run_id}/messages", json={"text": "дальше"})
    view = wait_idle(client, run_id)
    assert view["style"]["status"] == "approved" and view["summary"] is not None


def test_a_switch_that_fails_leaves_a_style_the_route_knows_is_not_written(client, model):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    reason = wait_idle(client, run_id)["style"]["reason"]
    model.fail_on = {"StyleDraft"}
    view = advance(client, run_id, "switch_style")
    style = view["style"]
    # The profile is the one chosen; what stands on the card is marked as written for the other, and the route says so.
    assert (style["profile"], style["written_for"], style["chosen_by"], style["status"]) == ("quest", "legend", "chat", "draft")
    assert style["reason"] == reason and "Не получилось" in view["messages"][-1]["text"]
    step = view["next_step"]
    assert (step["action"], step["button"], step["title"]) == ("write_style", "Написать сюжет для стиля «квест»", "Стиль сменён на «квест»")
    assert step["alternatives"] == [{"action": "approve_style", "button": "Сюжет подходит и так — предложить название и аннотацию"},
                                    {"action": "switch_style", "button": "Вернуть стиль «рабочая легенда»"}]
    # Back by the chip: the story is of this style again, with the reasons it had — nothing to write, nothing lost.
    model.fail_on = set()
    calls = len(style_calls(model))
    view = advance(client, run_id, "switch_style")
    assert (view["style"]["profile"], view["style"]["written_for"]) == ("legend", "legend") and view["next_step"]["action"] == "approve_style"
    assert len(style_calls(model)) == calls + 1
    # The retry: the model is shown that the story it has is of the other style.
    model.fail_on = {"StyleDraft"}
    advance(client, run_id, "switch_style")
    model.fail_on = set()
    view = advance(client, run_id, "write_style")
    asked = style_calls(model)[-1]
    assert asked["profile_given"] == "quest" and (asked["current"]["profile"], asked["current"]["written_for"]) == ("quest", "legend")
    assert (view["style"]["profile"], view["style"]["written_for"]) == ("quest", "quest") and view["next_step"]["action"] == "approve_style"
    # A server stopped in the middle of the switch leaves the same state, and the same way on.
    draft = store.load(run_id)
    told(draft).profile, draft.busy = "legend", "Подбираю стиль проекта"
    store.save(draft)
    from project_generator_lab.content import flow

    flow.recover()
    view = client.get(f"/api/content/{run_id}").json()
    assert view["busy"] == "" and view["next_step"]["action"] == "write_style" and view["style"]["written_for"] == "quest"
    # The story as it stands may be kept: it is the methodologist's from then on, without the reasons for the other style.
    view = advance(client, run_id, "approve_style")
    assert (view["style"]["status"], view["style"]["written_for"], view["style"]["reason"]) == ("approved", "legend", "")
    assert view["summary"] is not None


def test_a_style_changed_by_words_is_marked_and_the_mark_goes_back_with_it(client, model):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    reason = wait_idle(client, run_id)["style"]["reason"]
    model.revisions = [ContentRevision(reply="Меняю стиль.", changes=[ContentChange(part="style", field="profile", value="квест", reason="Просьба")]),
                       ContentRevision(reply="Возвращаю.", changes=[ContentChange(part="style", field="profile", value="рабочая легенда", reason="Просьба")]),
                       ContentRevision(reply="Меняю стиль.", changes=[ContentChange(part="style", field="profile", value="quest", reason="Просьба")]),
                       ContentRevision(kind="answer", reply="Переписал сюжет.", changes=[
                           ContentChange(part="style", field="story", value="Комната с терминалом. ИИ Марк ждёт фактов.", reason="Под квест")])]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "давай квест"}).json()
    first = view["messages"][-1]
    # One change, of the profile: the reasons are not thrown away, they are no longer shown as reasons for this style.
    assert [(p["label"], p["before"], p["after"]) for p in first["patches"]] == [("Стиль проекта · Стиль подачи", "legend", "quest")]
    view = advance(client, run_id, "apply_pending")
    assert (view["style"]["profile"], view["style"]["written_for"], view["style"]["reason"], view["style"]["chosen_by"]) == ("quest", "legend", reason, "chat")
    assert view["next_step"]["action"] == "write_style"
    assert [c["message"] for c in view["checks"] if c["code"] == "style"] == [
        "Сюжет написан для стиля «рабочая легенда», а проект теперь в стиле «квест»: поправьте сюжет в чате или скажите, что он подходит и так."]
    # The chat model is not shown the reasons of the other style as the reasons for this one.
    client.post(f"/api/content/{run_id}/messages", json={"text": "нет, верни легенду"})
    shown = model.calls[-1][2]["parts"][0]["fields"]
    assert "reason" not in shown and (shown["profile"], shown["written_for"]) == ("quest", "legend")
    # There and back by words: the story is of this style again and the reasons with it — no false alarm, nothing lost.
    view = advance(client, run_id, "apply_pending")
    assert (view["style"]["profile"], view["style"]["written_for"], view["style"]["reason"]) == ("legend", "legend", reason)
    assert view["next_step"]["action"] == "approve_style" and not [c for c in view["checks"] if c["code"] == "style"]
    # Changed again, and the story rewritten by words: the mark is lifted by a change of its own, which goes back with the text.
    client.post(f"/api/content/{run_id}/messages", json={"text": "всё-таки квест"})
    advance(client, run_id, "apply_pending")
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "сюжет: комната с терминалом, ИИ Марк"}).json()
    rewrite = view["messages"][-1]
    assert [(p["label"], p["before"], p["after"], p["reason"]) for p in rewrite["patches"]][1] == (
        "Стиль проекта · Для какого стиля написан сюжет", "legend", "quest", "Сюжет переписан под выбранный стиль")
    view = advance(client, run_id, "apply_pending")
    assert (view["style"]["written_for"], view["style"]["story"]) == ("quest", "Комната с терминалом. ИИ Марк ждёт фактов.")
    assert view["next_step"]["action"] == "approve_style"
    view = client.post(f"/api/content/{run_id}/messages/{rewrite['id']}/decision", json={"verdict": "revert"}).json()
    assert view["style"]["written_for"] == "legend" and view["style"]["story"].startswith("Ты — новый продакт")


def test_a_task_rewritten_for_the_style_no_longer_stands_apart():
    draft = styled_draft("legend")
    told(draft).profile = "quest"
    assert [c.part for c in run_checks(draft) if c.code == "style"] == ["style", "task:1", "task:2", "task:3"]
    # A word changed in what is expected is not a task rewritten; its situation or its statement rewritten is.
    small = propose(draft, ContentChange(part="task:1", field="expected", value=["В src/task1.md есть таблица"], reason="Точнее"))
    assert [p.field for p in small.patches] == ["expected"]
    message = propose(draft, ContentChange(part="task:2", field="context", value="Марк выводит на экран повестку встречи.", reason="Под квест"),
                      ContentChange(part="task:2", field="steps", value=["Создать src/task2.md с планом встречи."], reason="Под квест"))
    assert [(p.label, p.before, p.after, p.reason) for p in message.patches][-1] == (
        "Quest 2 · В каком стиле написано", "legend", "quest", "Задание переписано под выбранный стиль")
    apply(draft, message)
    assert draft.tasks[1].written_in == "quest" and [c.part for c in run_checks(draft) if c.code == "style"] == ["style", "task:1", "task:3"]
    revert(draft, message)
    assert draft.tasks[1].written_in == "legend" and draft.tasks[1].context == "Ситуация 2."
    # The methodologist may say a task suits the style as it is: the chat lifts the mark by the name of the style.
    kept = propose(draft, ContentChange(part="task:3", field="written_in", value="Квест", reason="Подходит и так"),
                   ContentChange(part="style", field="written_for", value="квест", reason="Сюжет подходит и так"))
    assert [(p.label, p.after) for p in kept.patches] == [("Quest 3 · В каком стиле написано", "quest"),
                                                          ("Стиль проекта · Для какого стиля написан сюжет", "quest")]
    apply(draft, kept)
    assert [c.part for c in run_checks(draft) if c.code == "style"] == ["task:1", "task:2"]
    wrong = propose(draft, ContentChange(part="task:1", field="written_in", value="комикс", reason="?"))
    assert wrong.patches == [] and wrong.notes == ["«Quest 1 · В каком стиле написано»: стиль — «квест» или «рабочая легенда» — правка не предложена."]
    # A task of the style in force gets no such companion, and neither does a draft begun before the styles.
    again = propose(draft, ContentChange(part="task:3", field="steps", value=["Создать src/task3.md."], reason="Короче"))
    assert [p.field for p in again.patches] == ["steps"]
    before = propose(complete_draft(), ContentChange(part="task:1", field="steps", value=["Другой шаг"], reason="Просьба"))
    assert [p.field for p in before.patches] == ["steps"]


def test_the_route_says_what_stands_before_the_text_in_words():
    # The three states of the style, each with its words: nothing proposed, proposed, and changed after it was written.
    from project_generator_lab.content import stages

    draft = styled_draft("legend")
    draft.style, draft.summary, draft.theory, draft.frame, draft.review, draft.tasks, draft.materials = None, None, None, None, None, [], []
    step = next_step(draft)
    assert (step["action"], step["button"], step["title"], step["alternatives"], step["edit_hint"]) == (
        "write_style", "Предложить стиль проекта", "Начнём со стиля", [], "")
    assert step["text"] == ("Сначала договоримся, как рассказать проект: квестом, как в бассейне, или рабочей легендой. "
                            "Я предложу стиль и сюжет по замыслу и скажу почему.")
    assert step["route"] == stages.ROUTE and "стиль проекта → название и аннотация" in stages.ROUTE
    draft.style = Style(profile="quest", reason="Есть сквозной сюжет серии.", story=STORY)
    step = next_step(draft)
    plain = ("Проверьте, как я понял сюжет: по нему будут написаны все части. Стиль можно сменить кнопкой или словами, "
             "сюжет — поправить в чате.")
    assert (step["action"], step["title"], step["text"], step["edit_hint"]) == ("approve_style", "Стиль проекта предложен", plain, "Что поменять в стиле или сюжете…")
    assert step["alternatives"] == [{"action": "switch_style", "button": "Сменить стиль на «рабочая легенда»"}]
    told(draft).questions = ["Как зовут персонажа?"]
    assert next_step(draft)["text"] == plain + " На вопросы по сюжету ответьте в чате: я впишу ответы в сюжет. Без ответов напишу по своим допущениям."
    told(draft).profile = "legend"
    step = next_step(draft)
    assert (step["action"], step["button"], step["title"], step["edit_hint"]) == (
        "write_style", "Написать сюжет для стиля «рабочая легенда»", "Стиль сменён на «рабочая легенда»", "Что поменять в стиле или сюжете…")
    assert step["text"] == "Сюжет на карточке написан для прежнего стиля. Напишу его заново для выбранного — или оставьте как есть, если он подходит."
    assert step["alternatives"] == [{"action": "approve_style", "button": "Сюжет подходит и так — предложить название и аннотацию"},
                                    {"action": "switch_style", "button": "Вернуть стиль «квест»"}]
    # Agreed, the style is no step any more, whatever it was written for: the title comes next.
    told(draft).status = "approved"
    assert next_step(draft)["action"] == "write_summary"
    # Work in hand and a change that waits for a decision come before the style, as before any step.
    told(draft).status = "draft"
    draft.busy = "Подбираю стиль проекта"
    assert (next_step(draft)["action"], next_step(draft)["title"]) == (None, "Подбираю стиль проекта")


def lifted(draft, *changes):
    """The marks a proposal lifts by itself: (label, before, after) of the changes code added to it."""
    return [(p.label, p.before, p.after) for p in propose(draft, *changes).patches if p.field in ("written_for", "written_in")]


def change(part, field, value):
    return ContentChange(part=part, field=field, value=value, reason="Просьба")


def test_a_mark_is_lifted_for_the_style_the_proposal_leaves_the_project_in():
    story, context, steps = change("style", "story", "Комната с терминалом."), change("task:1", "context", "Марк ждёт."), change("task:1", "steps", ["Создать src/a.md."])
    to_quest = change("style", "profile", "квест")
    # «Давай квест, и сюжет такой: …» — the style and the story at once: that story is written for the quest, and a
    # task rewritten in the same breath is written in it. Without the mark the card would offer to write them again.
    assert lifted(styled_draft("legend"), to_quest, story) == [("Стиль проекта · Для какого стиля написан сюжет", "legend", "quest")]
    assert lifted(styled_draft("legend"), to_quest, context) == [("Задание 1 · В каком стиле написано", "legend", "quest")]
    draft = styled_draft("legend")
    message = propose(draft, to_quest, story, steps)
    apply(draft, message)
    assert (told(draft).profile, told(draft).written_for, told(draft).stale(), draft.tasks[0].written_in) == ("quest", "quest", False, "quest")
    assert [c.part for c in run_checks(draft) if c.code == "style"] == ["task:2", "task:3"]
    revert(draft, message)
    assert (told(draft).profile, told(draft).written_for, draft.tasks[0].written_in) == ("legend", "legend", "legend")
    # The style alone: nothing was rewritten, nothing is lifted.
    assert lifted(styled_draft("legend"), to_quest) == []
    # The situation alone or the statement alone is a task rewritten; any other field of it is not.
    stale = styled_draft("legend")
    told(stale).profile = "quest"
    for one in (context, steps):
        assert lifted(stale.model_copy(deep=True), one) == [("Quest 1 · В каком стиле написано", "legend", "quest")]
    for field, value in (("expected", ["Есть файл."]), ("result", "Файл src/new.md."), ("transition", "Дверь открылась."), ("check", "Пир открывает файл.")):
        assert lifted(stale.model_copy(deep=True), change("task:1", field, value)) == []
    assert lifted(stale.model_copy(deep=True), story) == [("Стиль проекта · Для какого стиля написан сюжет", "legend", "quest")]
    assert lifted(stale.model_copy(deep=True), change("style", "questions", ["Как зовут персонажа?"])) == []
    # A mark the proposal sets itself is the proposal's: said once, with the value it names.
    kept = propose(stale.model_copy(deep=True), story, change("style", "written_for", "квест"), context, change("task:1", "written_in", "квест"))
    assert [(p.field, p.after) for p in kept.patches if p.field in ("written_for", "written_in")] == [("written_for", "quest"), ("written_in", "quest")]
    assert [p.reason for p in kept.patches] == ["Просьба"] * 4
    # Back to the style the texts were written for, with the story touched up: they are of that style already.
    assert lifted(stale.model_copy(deep=True), change("style", "profile", "рабочая легенда"), story, context) == []


def test_a_task_is_marked_with_the_style_it_was_asked_in(client, model):  # noqa: F811
    run_id = str(uuid4())
    begin(client, run_id)
    answer = model.generate

    async def meanwhile(schema, system, payload):
        result = await answer(schema, system, payload)
        if issubclass(schema, TaskDraft):
            # The style is changed by words while the task is being written in the other one.
            draft = store.load(run_id)
            told(draft).profile = "quest"
            store.save(draft)
        return result

    model.generate = meanwhile
    view = advance(client, run_id, "approve_summary")
    assert view["style"]["profile"] == "quest" and view["tasks"][0]["written_in"] == "legend"
    assert [c["part"] for c in view["checks"] if c["code"] == "style"] == ["style", "task:1"]


def test_yes_to_the_assistants_own_question_is_an_answer_even_while_the_story_is_asked_about(client, model):  # noqa: F811
    run_id = str(uuid4())
    model.questions = ["Уместен ли юмор в сценах?"]
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    model.revisions = [ContentRevision(kind="question", reply="Сделать его роботом по имени Марк?"),
                       ContentRevision(kind="answer", reply="Вписал.", changes=[
                           ContentChange(part="style", field="story", value="Ты — новый продакт. Тебе помогает робот Марк.", reason="Ответ")])]
    client.post(f"/api/content/{run_id}/messages", json={"text": "а кто персонаж?"})
    calls = len(model.calls)
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "да"}).json()
    # The chat asked, and «да» answers it: the model reads it, as it would with no questions on the card.
    assert len(model.calls) == calls + 1 and model.calls[-1][0] == "ContentRevision"
    assert view["messages"][-1]["text"] == "Вписал." and view["style"]["status"] == "draft"


def test_a_style_changed_while_the_assistant_was_writing_it_is_written_once_more(client, model):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    answer = model.generate
    meddled: list[bool] = []

    async def meddling(schema, system, payload):
        result = await answer(schema, system, payload)
        if schema.__name__ == "StyleDraft" and not meddled:
            # The methodologist's edit of the story is applied while the model is still writing.
            meddled.append(True)
            draft = store.load(run_id)
            told(draft).story = "Исправлено методологом: персонажа зовут Марк."
            store.save(draft)
        return result

    model.generate = meddling
    view = advance(client, run_id, "switch_style")
    calls = style_calls(model)
    # Asked for the switch, and asked again from what stood by then: the correction was read, not overwritten unseen.
    assert len(calls) == 3 and calls[1]["current"]["story"].startswith("Ты — новый продакт")
    assert calls[2]["current"]["story"] == "Исправлено методологом: персонажа зовут Марк." and calls[2]["profile_given"] == "quest"
    assert view["style"]["profile"] == "quest" and len([m for m in view["messages"] if m["part"] == "style"]) == 2
    # The call that was thrown away was paid for too: both are in the count of the message, and it says why.
    last = view["messages"][-1]
    assert (last["input_tokens"], last["output_tokens"]) == (200, 100)
    assert last["notes"] == ["Стиль меняли, пока я писал: спросил модель ещё раз, от того, что стало."]


def test_a_style_changed_during_the_second_call_too_is_left_as_the_methodologist_made_it(client, model):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    answer = model.generate
    edits: list[str] = []

    async def meddling(schema, system, payload):
        result = await answer(schema, system, payload)
        if schema.__name__ == "StyleDraft":
            edits.append(f"Правка методолога {len(edits) + 1}.")
            draft = store.load(run_id)
            told(draft).story = edits[-1]
            store.save(draft)
        return result

    model.generate = meddling
    view = advance(client, run_id, "switch_style")
    # Asked twice, and twice the story was corrected meanwhile: the correction stands, nothing is written over it.
    assert len(edits) == 2 and view["style"]["story"] == "Правка методолога 2." and view["busy"] == ""
    assert view["messages"][-1]["text"] == ("Стиль меняли, пока я писал сюжет: оставил его таким, каким вы его сделали. "
                                            "Если сюжет нужно написать заново — кнопка ниже.")
    assert view["next_step"]["action"] == "write_style"


def test_a_task_written_in_the_other_style_is_no_model_for_the_next():
    draft = styled_draft("legend")
    assert [t.written_in for t in draft.tasks] == ["legend"] * 3
    shown = task_payload(draft, 2)["exemplar"]
    assert isinstance(shown, dict) and shown["written_in"] == "legend" and shown["title"] == "Роли в команде"
    told(draft).profile = "quest"
    assert task_payload(draft, 2)["exemplar"] is None
    # Where it was written is told to the writers of a styled draft, and is no part of the text the critics signed.
    before = draft.digest()
    draft.tasks[0].written_in = "quest"
    assert draft.digest() == before and isinstance(task_payload(draft, 2)["exemplar"], dict)
    # A draft begun before the styles: its first task is the model it always was.
    assert isinstance(task_payload(complete_draft(), 2)["exemplar"], dict)


def test_an_example_is_data_not_a_note_left_in_the_text():
    draft = styled_draft("legend")
    draft.tasks[1] = task(2, written_in="legend", examples=["TODO: купить хлеб → 1 дело"], check="Пир вводит своё дело.",
                          peer_checks=["Пункт 1", "Пункт 2"])
    reviewed(draft).digest = draft.digest()
    assert not blocking(draft)
    draft.tasks[1] = task(2, written_in="legend", check="TODO: дописать", peer_checks=["Пункт 1", "Пункт 2"])
    reviewed(draft).digest = draft.digest()
    assert [c.message for c in blocking(draft)] == ["Осталась заглушка «TODO» (task:2)."]


def test_the_check_list_of_a_draft_without_tasks_is_a_file_that_reads():
    draft = styled_draft("quest")
    draft.tasks = []
    assert yaml.safe_load(render.checklist(draft))["sections"][0]["questions"] == []


# ---------- what the writers are given ----------

def test_the_writers_read_the_style_the_project_is_told_in(client, model, reviewer):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    advance(client, run_id, "switch_style")
    walk(client, run_id)
    frozen = store.load(run_id).prompt_snapshots
    assert STYLED == {"summary", "task", "theory_plan", "theory", "frame", "material", "critic", "revise"}
    for name, system, payload in [*model.calls, *reviewer.calls]:
        # Only prose-writing calls get the new presentation preference; critics keep their frozen instructions.
        suffix = "\n" + store.load(run_id).handoff.generation.instruction()
        if name in {"StyleDraft", "SummaryDraft", "TaskDraft", "TheoryDraft", "FrameDraft", "MaterialDraft"}:
            assert system.endswith(suffix)
            system = system.removesuffix(suffix)
        if name == "StyleDraft":
            # The call that chooses reads both styles as data, and is told of neither as the one in use.
            assert system == frozen["system"] + "\n\n" + frozen["style"]
        elif name in {"SummaryDraft", "TaskDraft", "TheoryPlanDraft", "TheoryDraft", "FrameDraft", "MaterialDraft", "CriticReport"}:
            task_prompt = {"SummaryDraft": "summary", "TaskDraft": "task", "TheoryPlanDraft": "theory_plan", "TheoryDraft": "theory",
                           "FrameDraft": "frame", "MaterialDraft": "material", "CriticReport": "critic"}[name]
            assert system == "\n\n".join([frozen["system"], frozen["style_quest"], frozen[task_prompt]]), name
            assert payload["project"]["style"]["profile"] == "quest" and payload["project"]["style"]["name"] == "квест", name
            assert payload["project"]["style"]["story"].startswith("Ты — новый продакт"), name
        else:
            # The search and the choice among its links write no text of the learner's: no style for them.
            assert "Стиль проекта:" not in system, name
    # The quest: the check-list names the tasks as the README heads them.
    checklist = yaml.safe_load(client.get(f"/api/content/{run_id}/check-list").text)
    assert [q["name"] for q in checklist["sections"][0]["questions"]] == [
        "Quest 1. Роли в команде", "Quest 2. План встречи", "Quest 3. Бэклог первого спринта"]
    view = client.get(f"/api/content/{run_id}").json()
    assert view["task_headings"] == {"1": "Quest 1. Роли в команде", "2": "Quest 2. План встречи", "3": "Quest 3. Бэклог первого спринта"}
    assert view["styled"] is True


def test_the_styles_are_frozen_with_the_draft_both_of_them():
    frozen = snapshot()
    assert {"style", "style_quest", "style_legend"} <= set(frozen)
    assert frozen["style_quest"].startswith("Стиль проекта: «квест»") and frozen["style_legend"].startswith("Стиль проекта: «рабочая легенда»")
    # What each style says of every field of a task: a field it left out would be written by guess.
    for name in ("style_quest", "style_legend"):
        for field in ("context", "steps", "expected", "constraints", "examples", "result", "check", "transition"):
            assert f"  - {field} — " in frozen[name], (name, field)
    # The samples are written for the styles, on other subjects: a writer told «не копируй» is given nothing of the school's.
    assert "фразы не копируй" in frozen["style_quest"] and "фразы не копируй" in frozen["style_legend"]


# ---------- a draft begun before the styles ----------

def test_a_draft_begun_before_the_styles_goes_on_as_it_began():
    draft = complete_draft()
    assert draft.styled() is False and draft.style is None
    # Recorded from the code before the styles (content 0.4): the fingerprint the critics' review holds, the README, the
    # whole text, the check-list and what a writer and the critic are sent — all to the letter.
    assert draft.digest() == "c16928c9655fbce7"
    assert [render_digest(text) for text in (render.readme(draft), render.full_text(draft), render.checklist(draft))] == [
        "e6487bb03eb9a9b4", "e723ce0fe0b24868", "47588cbfa84a7794"]
    assert [payload_digest(task_payload(draft, 2)), payload_digest(critic_payload(draft))] == ["1f7b42582872f184", "0fd1639766ae523c"]
    assert "style" not in project(draft) and "examples" not in str(task_payload(draft, 2)["written"])
    # The chat of such a draft is sent what it was sent, and its card does not promise a style.
    assert payload_digest(revise_payload(complete_draft(), "сократи задание 2", "task:2")) == "ff91327692e6589a"
    assert render_digest(json.dumps(card(handoff_fixture("d1"), draft.styled()), ensure_ascii=False, sort_keys=True)) == "f58fe398fd068e42"
    assert not {"Стиль подачи", "Сквозной сюжет серии"} & {row["label"] for row in card(draft.handoff, draft.styled())}
    # With a bonus task too: headed and numbered as that template did.
    draft.handoff = handoff_fixture("d1", optional_last=True)
    assert [render_digest(text) for text in (render.readme(draft), render.full_text(draft), render.checklist(draft))] == [
        "a915060afc170643", "68b1417eafb0d7c1", "8151aef8dbc0d1e2"]
    # No step of the route asks it for a style, nothing stops its README for the lack of one, and it is not told of the step.
    draft.summary = None
    step = next_step(draft)
    assert step["action"] == "write_summary" and "стиль проекта" not in str(step["route"])
    assert not [c for c in blocking(complete_draft()) if c.part == "style"]
    assert Writer.system(complete_draft().model_copy(update={"prompt_snapshots": {"system": "S", "task": "T"}}), "task") == "S\n\nT"


# The prompts a draft of 0.4 was begun with, and those of 0.5: what was added later it does not hold.
PROMPTS_OF_04 = {"system", "summary", "task", "theory", "sources", "links", "frame", "material", "critic", "facts", "facts_select", "revise"}
PROMPTS_OF_05 = PROMPTS_OF_04 | {"style", "style_quest", "style_legend"}


def test_a_draft_begun_before_the_styles_walks_its_route_to_the_end(client, model):  # noqa: F811
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    # As a draft of 0.4 lies in its file: no style, and the prompts frozen without the texts of the styles.
    draft = store.load(run_id)
    draft.style, draft.content_version = None, "0.4"
    draft.prompt_snapshots = {name: text for name, text in draft.prompt_snapshots.items() if name in PROMPTS_OF_04}
    draft.messages = [m for m in draft.messages if m.part != "style"]
    store.save(draft)
    model.calls.clear()
    model.schemas.clear()
    model.examples = {1: ["3 5 -> 8"]}
    view = client.get(f"/api/content/{run_id}").json()
    assert view["styled"] is False and view["next_step"]["action"] == "write_summary" and "стиль" not in view["next_step"]["route"]
    pressed: list[str] = []
    while view["next_step"]["action"]:
        assert len(pressed) < 40, (pressed[-3:], view["messages"][-1]["text"])
        pressed.append(view["next_step"]["action"])
        view = advance(client, run_id, view["next_step"]["action"], view["next_step"]["number"])
    # Every step is taken by the code of today, and none of them knows of a style: the tasks are asked for in the
    # shape of that time and stored without the fields that came later.
    assert not {"write_style", "approve_style", "switch_style"} & set(pressed) and pressed[-1] == "assemble"
    assert "StyleDraft" not in [name for name, _, _ in model.calls]
    assert {schema.__name__ for schema in model.schemas if issubclass(schema, TaskOpening)} == {"PlainTaskDraft"}
    assert [(t["written_in"], t["examples"], t["check"]) for t in view["tasks"]] == [("", [], "")] * 3
    readme = client.get(f"/api/content/{run_id}/readme").text
    assert "## Глава 3. Практический блок" in readme and "### Задание 1. " in readme and "Quest" not in readme and "## Что сдаётся" not in readme
    assert store.load(run_id).style is None and not [c for c in view["checks"] if c["code"] == "style"]


def render_digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:16]


def payload_digest(payload: dict[str, object]) -> str:
    from project_generator_lab.content.writer import dumps

    return render_digest(dumps(payload))


# The answer schema goes to the model with the prompt (core/llm.py), after the point where a scripted model is called:
# a comparison of prompts and payloads does not see it. Recorded from the code before the styles (independent review
# 06.10.2026: the task schema of such a draft had gained the two new fields, which its frozen prompt says nothing of).
SCHEMAS_BEFORE = {"SummaryDraft": "b85644681bb4af8a", "PlainTaskDraft": "b88d2bcf2d5bbb75", "TheoryDraft": "3d93777a39120fa1",
                  "LinkSelection": "ea6eb1e8eb0b82e8", "FrameDraft": "06b58b48aca0d519", "MaterialDraft": "7950519c6d3663be",
                  "CriticReport": "fc2aee361e4f554a", "FactReport": "25fe14f0f540b959", "ContentRevision": "435f991c8530b2dc"}


def test_a_draft_begun_before_the_styles_is_asked_for_the_answers_it_was_asked_for():
    from project_generator_lab.content import models

    for name, recorded in SCHEMAS_BEFORE.items():
        assert render_digest(json.dumps(getattr(models, name).model_json_schema(), ensure_ascii=False)) == recorded, name
    # Named to the model as it was, and without the fields that came with the styles; a draft with a style is asked for them.
    assert models.PlainTaskDraft.model_json_schema()["title"] == "TaskDraft" and "description" not in models.PlainTaskDraft.model_json_schema()
    assert set(models.TaskDraft.model_fields) - set(models.PlainTaskDraft.model_fields) == {"examples", "check"}
    assert "с одной стрелкой →" in models.TaskDraft.model_json_schema()["properties"]["examples"]["description"]


def test_the_writer_asks_each_draft_for_the_task_of_its_time():
    asked = []

    class Catching:
        async def generate(self, schema, system, payload):
            asked.append(schema)
            raise ModelUnavailable("стоп")

    before = complete_draft().model_copy(update={"prompt_snapshots": {"system": "S", "task": "T"}})
    for draft, schema in ((before, "PlainTaskDraft"), (styled_draft("legend"), "TaskDraft")):
        with pytest.raises(ModelUnavailable):
            asyncio.run(Writer(Catching()).task(draft, 2))  # type: ignore[arg-type]
        assert asked[-1].__name__ == schema
    # The model writes its answer in the order of the form: the examples and the check stand before the points of
    # the check-list, which must take a value the examples do not show (independent review 06.10.2026).
    from project_generator_lab.content import models

    order = list(models.TaskDraft.model_json_schema()["properties"])
    assert order.index("examples") < order.index("check") < order.index("peer_checks") < order.index("rationale")
    assert list(models.PlainTaskDraft.model_json_schema()["properties"]) == [name for name in order if name not in ("examples", "check")]


def test_such_a_draft_is_offered_neither_a_style_nor_examples():
    draft = complete_draft()
    assert "style" not in editable(draft) and not {"examples", "check"} & set(editable(draft)["task"])
    assert {"examples", "check"} <= set(editable(styled_draft("quest"))["task"])
    assert {"target_words", "contains"} <= set(EDITABLE["material"])
    # A model that proposes them all the same is told there is no such field: nothing its template would not show is stored.
    message = propose(draft, ContentChange(part="task:1", field="examples", value=["3 5 → 8"], reason="Пример"),
                      ContentChange(part="style", field="profile", value="quest", reason="Стиль"))
    assert message.patches == [] and message.notes == ["Правку «task:1 · examples» не могу применить: такого поля нет.",
                                                       "Правку «style · profile» не могу применить: такого поля нет."]
    assert "| Ввод | Вывод |" not in render.readme(draft)


# ---------- a styled draft: checks ----------

def told(draft: ContentDraft) -> Style:
    assert draft.style is not None
    return draft.style


def reviewed(draft: ContentDraft) -> Review:
    assert draft.review is not None
    return draft.review


def styled_draft(profile: Profile, **fields) -> ContentDraft:
    draft = complete_draft()
    draft.prompt_snapshots = snapshot()
    draft.handoff = handoff_fixture("d1", optional_last=True)
    draft.style = Style(profile=profile, reason="Так яснее для этого проекта.", story=STORY, status="approved", **fields)
    draft.tasks = [task(n, written_in=profile, peer_checks=[f"Файл {n} открывается", f"Попроси объяснить решение {n}"]) for n in (1, 2, 3)]
    draft.tasks[0] = task(1, examples=["3 5 → 8", "пустой ввод → n/a", f"a|b{BREAK}c → ошибка"], transition="Сумма понадобится в задании 2.",
                          check="`python3 src/a.py` печатает сумму; проверяющий вводит свои числа.", written_in=profile,
                          constraints=["Только стандартная библиотека. Правило действует и дальше."],
                          peer_checks=["`python3 src/a.py`, ввод `3 5` — `8`", "Попроси назвать сумму своих чисел до запуска"])
    reviewed(draft).digest = draft.digest()
    return draft


def test_the_style_is_part_of_the_draft_the_readme_waits_for():
    draft = styled_draft("legend")
    assert not blocking(draft)
    told(draft).status = "draft"
    assert [c.message for c in blocking(draft)] == ["Стиль проекта не согласован."]
    draft.style = None
    assert [c.message for c in blocking(draft)][0] == "Стиль проекта не предложен."
    assert next_step(draft.model_copy(update={"summary": None}))["action"] == "write_style"


def test_a_change_of_style_makes_the_review_stale():
    draft = styled_draft("legend")
    before = draft.digest()
    told(draft).story = "Другая история."      # the story is how the text is written, not the text
    assert draft.digest() == before
    told(draft).profile = "quest"              # the frame of the README is another: what the critics read is another
    assert draft.digest() != before
    # The fingerprint a review of 0.5 holds: a change of how it is counted makes every such review stale at once.
    assert (before, draft.digest()) == ("d078a259d2505329", "171e2e7757ba7ee6")
    draft.tasks[1].written_in = "quest"        # a mark of the route, not a word the critics read
    assert draft.digest() == "171e2e7757ba7ee6"
    for field, value in (("check", "Пир открывает файл."), ("examples", ["1 → 2"])):
        changed = draft.model_copy(deep=True)
        setattr(changed.tasks[1], field, value)
        assert changed.digest() != draft.digest(), field


def test_what_a_style_asks_of_a_task_is_told_as_advice():
    draft = styled_draft("legend")
    draft.tasks[1] = task(2, written_in="legend", peer_checks=[f"Пункт {n}" for n in range(1, 6)])
    draft.tasks[2] = task(3, written_in="legend", check="Пир открывает файл.")  # the helper gives it one point for the peer
    found = [(c.part, c.severity, c.message) for c in run_checks(draft) if c.code == "style"]
    assert found == [
        ("task:2", "warn", "В задании 2 пунктов для пира 5 — решено держать 2–4: длинный список проверяют наспех."),
        ("task:3", "warn", "В задании 3 один пункт для пира — решено держать 2–4: нужен и пункт-действие при проверяющем."),
        ("task:2", "warn", "В задании 2 не сказано, как его проверят («Проверка»): участник не знает, что увидит проверяющий.")]
    # Two and four points are within what was decided.
    draft.tasks[1] = task(2, written_in="legend", peer_checks=[f"Пункт {n}" for n in range(1, 5)], check="Пир открывает файл.")
    draft.tasks[2] = task(3, written_in="legend", peer_checks=["Пункт 1", "Пункт 2"], check="Пир открывает файл.")
    assert [c for c in run_checks(draft) if c.code == "style"] == []
    # A quest tells of the check in its check-list: the line is not asked of it. A draft without a style is not measured.
    draft.tasks[2] = task(3, written_in="quest", peer_checks=["Пункт 1", "Пункт 2"])
    told(draft).profile = "quest"
    assert [(c.part, c.message) for c in run_checks(draft) if c.code == "style"] == [
        ("style", "Сюжет написан для стиля «рабочая легенда», а проект теперь в стиле «квест»: поправьте сюжет в чате или скажите, что он подходит и так."),
        ("task:1", "Задание 1 написано в стиле «рабочая легенда», а проект теперь в стиле «квест»: попросите в чате переписать его или скажите, что оно подходит и так."),
        ("task:2", "Задание 2 написано в стиле «рабочая легенда», а проект теперь в стиле «квест»: попросите в чате переписать его или скажите, что оно подходит и так.")]
    assert [c for c in run_checks(complete_draft()) if c.code == "style"] == []


# ---------- examples ----------

def test_an_example_is_a_line_with_an_arrow_and_reads_the_same_every_time():
    lines = ["3 5 -> 8", "пусто => n/a", " → n/a", "x⇒y", "тихо →", "p->next → 5", "1 -> 2 → 2 -> 1", "- 5 3 → 2", "* * * → звёзды", "5. → 5.0",
             "  5   3  →  8  ", "a → b → c"]
    stored = example_items(lines)
    assert stored == ["3 5 → 8", "пусто → n/a", "пустой ввод → n/a", "x → y", "тихо → пустой вывод", "p->next → 5", "1 -> 2 → 2 -> 1",
                      "- 5 3 → 2", "* * * → звёзды", "5. → 5.0", "5   3 → 8", "a → b → c"]
    # A stored line is read again on every load: what it reads as does not drift, and nothing of an input is taken for
    # a list mark (the first version ate a leading «-» and «5.», once more on each load).
    assert example_items(stored) == stored and example_items(example_items(stored)) == stored
    assert [example_cells(line) for line in stored[5:8]] == [("p->next", "5"), ("1 -> 2", "2 -> 1"), ("- 5 3", "2")]
    assert example_cells("a → b → c") == ("a", "b → c") and example_cells("пустой ввод → n/a") == ("пустой ввод", "n/a")
    assert example_items(["", "   "]) == []
    # Cut once, at the first arrow: what follows it, arrows and all, is the output.
    assert example_items(["→ b → c", "a -> b -> c", "a => b ⇒ c"]) == ["пустой ввод → b → c", "a → b -> c", "a → b ⇒ c"]
    with pytest.raises(ValueError, match="^пример «" + "я" * 60 + "» пишется"):
        example_items(["я" * 80])
    for wrong in (["нет стрелки"], ["3 5 → 8", "и ещё что-то"]):
        with pytest.raises(ValueError, match="пишется как «ввод → вывод»"):
            example_items(wrong)
    assert sorted_examples(["3 5 -> 8", " просто текст ", "", "x → y"]) == (["3 5 → 8", "x → y"], ["просто текст"])
    # What is stored is held to the form; what the model returns is taken as written and sorted where it is stored.
    base = task(1).model_dump(exclude={"number", "status", "written_in"})
    with pytest.raises(ValidationError, match="пишется как «ввод → вывод»"):
        TaskContent(**{**base, "number": 1, "examples": ["просто текст"]})
    assert TaskContent(**{**base, "number": 1, "examples": ["1 -> 2"]}).examples == ["1 → 2"]
    assert TaskDraft(**{**base, "examples": [" просто текст ", "", "1 -> 2"]}).examples == ["просто текст", "1 -> 2"]


def test_a_line_of_the_model_that_is_no_example_does_not_cost_the_task(client, model):  # noqa: F811
    run_id = str(uuid4())
    begin(client, run_id)
    long = "строка без стрелки " * 6
    model.examples = {1: ["3 5 -> 8", "просто текст без стрелки", "x → y", long]}
    view = advance(client, run_id, "approve_summary")
    first = view["tasks"][0]
    assert first["examples"] == ["3 5 → 8", "x → y"] and first["written_in"] == "legend"
    message = next(m for m in view["messages"] if m["part"] == "task:1")
    said = message["text"]
    # Said after what the message says of the task and its materials, not instead of it; the model and its count stay.
    assert said.startswith("Задание 1 «") and "Понадобятся материалы: " in said
    assert (message["model"], message["input_tokens"], message["output_tokens"]) == ("fake-model", 100, 50)
    assert said.endswith(f"В примеры не взял строки, написанные не как «ввод → вывод»: «просто текст без стрелки»; «{long.strip()[:80]}». "
                         "Нужны — пришлите их в чат в этом виде.")



def test_an_example_that_is_no_example_is_refused_in_the_chat_in_words():
    draft = styled_draft("legend")
    message = propose(draft, ContentChange(part="task:2", field="examples", value=["просто текст"], reason="Просьба"))
    assert message.patches == [] and message.notes == [
        "«Задание 2 · Примеры «ввод → вывод»»: пример «просто текст» пишется как «ввод → вывод» — правка не предложена."]
    # The examples can be taken away: a task may have none.
    cleared = propose(draft, ContentChange(part="task:1", field="examples", value=[], reason="Пример и есть решение"))
    assert [p.after for p in cleared.patches] == [[]]


def test_an_example_proposed_in_the_chat_is_shown_as_it_will_be_stored():
    # What the card shows is what the file keeps: shown as typed and stored with the arrow of the table, the change
    # could not be taken back — the text it was to be taken back from was never there.
    for typed in (["3 5 -> 8", "  ", "x => y"], "3 5 -> 8\n\nx => y"):
        draft = styled_draft("legend")
        message = propose(draft, ContentChange(part="task:2", field="examples", value=typed, reason="Просьба"))
        assert [(p.before, p.after) for p in message.patches] == [([], ["3 5 → 8", "x → y"])]
        apply(draft, message)
        assert draft.tasks[1].examples == ["3 5 → 8", "x → y"]
        revert(draft, message)
        assert draft.tasks[1].examples == []
    assert normalize("examples", ["нет стрелки", " 1 -> 2 "]) == ["нет стрелки", "1 -> 2"]


def test_a_writer_is_told_when_the_story_it_reads_is_of_the_other_style():
    from project_generator_lab.content import writer

    draft = styled_draft("quest")
    assert writer.told(draft) == {"style": {"profile": "quest", "name": "квест", "story": STORY}}
    told(draft).profile = "legend"
    assert writer.told(draft) == {"style": {
        "profile": "legend", "name": "рабочая легенда", "story": STORY,
        "story_note": "Сюжет написан для стиля «квест»: возьми из него факты и персонажей, а голос и вид текста — по выбранному стилю."}}
    assert writer.project(draft)["style"] == writer.told(draft)["style"] and writer.told(complete_draft()) == {}


def test_the_chat_is_shown_the_style_with_its_story():
    from project_generator_lab.content.writer import parts

    draft = styled_draft("quest")
    told(draft).questions = ["Как зовут персонажа?"]
    shown = parts(draft)[0]
    assert (shown["id"], shown["status"]) == ("style", "approved")
    assert shown["fields"] == {"profile": "quest", "reason": "Так яснее для этого проекта.", "story": STORY,
                               "questions": ["Как зовут персонажа?"], "written_for": "quest"}
    # The reasons of the other style are not shown as the reasons for this one.
    told(draft).profile = "legend"
    assert parts(draft)[0]["fields"] == {"profile": "legend", "story": STORY, "questions": ["Как зовут персонажа?"], "written_for": "quest"}
    assert [part["id"] for part in parts(complete_draft())][0] == "summary"


# ---------- the README of each style ----------

def test_the_readme_of_a_quest():
    text = render.readme(styled_draft("quest"))
    order = ["## Содержание", "## Введение", "### Рекомендации к проекту", "## Chapter I", "## Chapter II", "### List 1. Тема 1",
             "## Chapter III", "### Quest 1. Роли в команде", "### Quest 2. План встречи", "### Bonus Quest 3. Бэклог первого спринта", "## Chapter IV"]
    assert [text.index(f"\n{h}\n") for h in order] == sorted(text.index(f"\n{h}\n") for h in order)
    assert "- [Chapter III](#chapter-iii)\n  - [Quest 1. Роли в команде](#quest-1-роли-в-команде)\n" in text
    assert "  - [Bonus Quest 3. Бэклог первого спринта](#bonus-quest-3-бэклог-первого-спринта)\n- [Chapter IV](#chapter-iv)\n" in text
    # The school's rules stand under the recommendations, the project's own after them.
    assert "**Как учиться в «Школе 21»:**\n\n- Здесь тебя ждет" in text
    assert "- В твоей директории не должно быть иных файлов, кроме тех, что обозначены в заданиях.\n- Обязательно: таблицы.\n" in text
    assert "## Chapter I\n\nЗачем проект. Ситуация: ты новый продакт в команде.\n" in text
    # A quest: the story, the statement as one paragraph after the words the code puts, what is asked, the rule, the
    # table, where to look, what to hand in, how the scene ends, and the pause.
    quest = text[text.index("### Quest 1."):text.index("### Quest 2.")]
    assert quest == """### Quest 1. Роли в команде

Ситуация 1.

**Получен Quest 1.** Сделай шаг задания 1. Сохрани src/task1.md.

- В src/task1.md есть решение

**Внимание:** Только стандартная библиотека. Правило действует и дальше.

| Входные данные | Выходные данные |
|---|---|
| `3 5` | `8` |
| пустой ввод | `n/a` |
| `a\\|b`<br>`c` | `ошибка` |


Материалы: [team.csv](materials/team.csv).

Теория: [List 1. Тема 1](#list-1-тема-1).


**Сдать:** src/task1.md

`python3 src/a.py` печатает сумму; проверяющий вводит свои числа.

Сумма понадобится в задании 2.

***LOADING…***

"""
    assert "**Получен Quest 3.** Сделай шаг задания 3. Сохрани src/task3.md.\n" in text
    assert text.count("***LOADING…***") == 3 and text.endswith("## Chapter IV\n\nТы прошёл путь от ролей до бэклога.\n")
    # Nothing of the other frames, and nothing for the methodologist only.
    for absent in ("Глава 1", "Что нужно сделать", "Старт работы", "Что сдаётся", "Цель 1", "Пункт замысла"):
        assert absent not in text, absent


def test_the_readme_of_a_working_legend():
    draft = styled_draft("legend")
    text = render.readme(draft)
    # The legend stands before the contents; then the start, the theory, the tasks one after another, what came of it all.
    assert text.startswith("# Проект\n\nАннотация проекта для участника.\n\nЗачем проект. Ситуация: ты новый продакт в команде.\n\n## Содержание\n")
    order = ["## Содержание", "## Старт работы", "## Теория", "### 1. Тема 1", "## Задание 1. Роли в команде", "## Задание 2. План встречи",
             "## Задание 3 (дополнительное). Бэклог первого спринта", "## Итог", "## Что сдаётся"]
    assert [text.index(f"\n{h}\n") for h in order] == sorted(text.index(f"\n{h}\n") for h in order)
    assert "- [Задание 3 (дополнительное). Бэклог первого спринта](#задание-3-дополнительное-бэклог-первого-спринта)\n- [Итог](#итог)\n" in text
    assert "**Материалы:**\n\n| Что нужно | Где найти |\n|---|---|\n| Данные команды | [team.csv](materials/team.csv) |\n" in text
    first = text[text.index("## Задание 1."):text.index("## Задание 2.")]
    assert first == """## Задание 1. Роли в команде

Ситуация 1.

Материалы: [team.csv](materials/team.csv).

Теория: [1. Тема 1](#1-тема-1).


1. Сделай шаг задания 1
2. Сохрани src/task1.md

Должно получиться:

- В src/task1.md есть решение

| Ввод | Вывод |
|---|---|
| `3 5` | `8` |
| пустой ввод | `n/a` |
| `a\\|b`<br>`c` | `ошибка` |


Условия:

- Только стандартная библиотека. Правило действует и дальше.

**Результат:** src/task1.md

**Проверка:** `python3 src/a.py` печатает сумму; проверяющий вводит свои числа.

**Зачем:** Сумма понадобится в задании 2.

"""
    # A task without the line of the check or of the reason has no empty label in their place.
    second = text[text.index("## Задание 2."):text.index("## Задание 3 ")]
    assert "**Результат:** src/task2.md\n" in second and "**Проверка:**" not in second and "**Зачем:**" not in second
    assert text.endswith("""## Что сдаётся

| Задание | Что сдаётся | Как проверяется |
|---|---|---|
| 1 | src/task1.md | `python3 src/a.py` печатает сумму; проверяющий вводит свои числа. |
| 2 | src/task2.md |  |
| 3 (дополнительное) | src/task3.md |  |
""")
    for absent in ("Chapter", "Quest", "LOADING", "Что нужно сделать", "Цель 1"):
        assert absent not in text, absent
    # Without theory and materials there are no empty sections for them.
    draft.theory, draft.materials = None, []
    bare = render.readme(draft)
    assert "## Теория" not in bare and "**Материалы:**" not in bare and "- [Старт работы](#старт-работы)\n- [Задание 1." in bare


def test_the_check_list_and_the_whole_text_name_the_tasks_as_the_readme_does():
    named: list[tuple[Profile, list[str]]] = [
        ("quest", ["Quest 1. Роли в команде", "Quest 2. План встречи", "Bonus Quest 3. Бэклог первого спринта"]),
        ("legend", ["Задание 1. Роли в команде", "Задание 2. План встречи", "Задание 3 (дополнительное). Бэклог первого спринта"])]
    for profile, names in named:
        draft = styled_draft(profile)
        assert [q["name"] for q in yaml.safe_load(render.checklist(draft))["sections"][0]["questions"]] == names
        whole = render.full_text(draft)
        for name in names:
            assert f"\n### {name}\n" in whole and f"\n**{name}**\n" in whole
        name = {"quest": "квест", "legend": "рабочая легенда"}[profile]
        assert f"## Стиль проекта: {name}\n\nТак яснее для этого проекта.\n\n**Сюжет:** {STORY}\n" in whole
        assert "**Примеры «ввод → вывод»**\n\n- 3 5 → 8\n" in whole and "**Проверка:** `python3 src/a.py` печатает сумму" in whole


def test_a_cell_of_a_table_keeps_its_row():
    assert render.cell("a|b") == "a\\|b" and render.cell(f"строка 1{BREAK}строка 2\nстрока 3") == "строка 1<br>строка 2<br>строка 3"
    assert render.cell("  с краёв  ") == "с краёв" and render.cell(7) == "7"
    assert render.cell("a\rb") == "a<br>b" and render.cell("a\r\nb") == "a<br>b"


@pytest.mark.parametrize("typed, shown", [
    ("3 5", "`3 5`"), ("2*x+3*x", "`2*x+3*x`"), ("2**3**2", "`2**3**2`"), ("<N> <M>", "`<N> <M>`"), ("_x_", "`_x_`"), ("5   3", "`5   3`"),
    ("a|b", "`a\\|b`"), ("`print`", "`print`"), ("a`b", "``a`b``"), ("`ls` и `cd`", "`` `ls` и `cd` ``"), ("x`", "`` x` ``"),
    (f"строка 1{BREAK}строка 2", "`строка 1`<br>`строка 2`"), (f"a{BREAK}{BREAK}b", "`a`<br><br>`b`"), ("a\nb", "`a`<br>`b`"),
    ("пустой ввод", "пустой ввод"), ("пустой вывод", "пустой вывод"), ("", ""),
    # A side the model wrapped in backticks as a whole is that side without them, however many lines it runs over.
    (f"`1{BREAK}2`", "`1`<br>`2`"), ("`a\nb`", "`a`<br>`b`"), ("``3 5``", "`3 5`"), ("`` a`b ``", "``a`b``"),
    # A return of the carriage alone ends a line as a new line does: left in a cell, it would end the row of the table.
    ("a\rb", "`a`<br>`b`"), ("a\r\nb", "`a`<br>`b`"),
    # Wrapped by one backtick, the side keeps the spaces inside it; each line wrapped apart is code as it stands.
    ("` a `", "`  a  `"), (f"`a`{BREAK}`b`", "`a`<br>`b`"), ("`x", "`` `x ``")])
def test_a_side_of_an_example_is_shown_to_the_letter(typed, shown):
    # Written as text, «2*x+3*x» comes out in italics without its stars, «<N>» is not shown, and spaces run together.
    assert render.example(typed) == shown


@pytest.mark.parametrize("typed, shown", [
    # Code as it is written: a span of one backtick each side, with none inside, is left alone — and only that.
    ("`a`", "`a`"), ("``", "``` `` ```"), ("``a`", "``` ``a` ```"), ("`a``", "``` `a`` ```"), ("a`", "`` a` ``"), ("`a", "`` `a ``"),
    # A span that both begins and ends with a space would lose one on each side; one that does so on one side would not.
    (" a ", "`  a  `"), (" a", "` a`"), ("a ", "`a `"), ("  a", "`  a`"), (" a b", "` a b`"), ("  ", "`  `"), ("a|b", "`a\\|b`"), ("", "")])
def test_a_line_as_code_reads_back_as_it_was_typed(typed, shown):
    assert render.code(typed) == shown


def test_the_contents_of_a_readme_follow_its_layout():
    def contents(profile):
        text = render.readme(styled_draft(profile))
        return text[text.index("## Содержание\n"):text.index("\n## ", text.index("## Содержание\n") + 5)].splitlines()[2:]

    assert contents("quest") == [
        "- [Введение](#введение)", "  - [Рекомендации к проекту](#рекомендации-к-проекту)", "- [Chapter I](#chapter-i)", "- [Chapter II](#chapter-ii)",
        "  - [List 1. Тема 1](#list-1-тема-1)", "  - [List 2. Тема 2](#list-2-тема-2)", "  - [List 3. Тема 3](#list-3-тема-3)",
        "- [Chapter III](#chapter-iii)", "  - [Quest 1. Роли в команде](#quest-1-роли-в-команде)", "  - [Quest 2. План встречи](#quest-2-план-встречи)",
        "  - [Bonus Quest 3. Бэклог первого спринта](#bonus-quest-3-бэклог-первого-спринта)", "- [Chapter IV](#chapter-iv)"]
    assert contents("legend") == [
        "- [Старт работы](#старт-работы)", "- [Теория](#теория)", "  - [1. Тема 1](#1-тема-1)", "  - [2. Тема 2](#2-тема-2)", "  - [3. Тема 3](#3-тема-3)",
        "- [Задание 1. Роли в команде](#задание-1-роли-в-команде)", "- [Задание 2. План встречи](#задание-2-план-встречи)",
        "- [Задание 3 (дополнительное). Бэклог первого спринта](#задание-3-дополнительное-бэклог-первого-спринта)",
        "- [Итог](#итог)", "- [Что сдаётся](#что-сдаётся)"]


def test_the_whole_text_says_whose_the_story_is():
    draft = styled_draft("quest")
    told(draft).profile = "legend"
    # Written for the other style: said so by its name, and its reasons are not given as the reasons for this one.
    whole = render.full_text(draft)
    assert f"## Стиль проекта: рабочая легенда\n\n_Стиль сменили: обоснование и сюжет ниже написаны для стиля «квест»._\n\n**Сюжет:** {STORY}\n" in whole
    assert "Так яснее для этого проекта." not in whole
    told(draft).written_for, told(draft).reason = "legend", ""
    assert ("## Стиль проекта: рабочая легенда\n\n_Стиль и сюжет приняты методологом; ассистент этот стиль не обосновывал._\n\n"
            f"**Сюжет:** {STORY}\n") in render.full_text(draft)
    told(draft).story = ""
    assert "**Сюжет:** _не задан_\n" in render.full_text(draft)


def test_a_value_the_style_cannot_take_is_refused_in_words():
    draft = styled_draft("legend")
    said = {("questions", ("а?", "б?", "в?", "г?", "д?")): "слишком много пунктов — не больше 4", ("story", "я" * 3001): "слишком длинно — не больше 3000 знаков",
            ("written_for", "комикс"): "стиль — «квест» или «рабочая легенда»", ("profile", "комикс"): "стиль — «квест» или «рабочая легенда»"}
    for (field, value), words in said.items():
        message = propose(draft, ContentChange(part="style", field=field, value=list(value) if isinstance(value, tuple) else value, reason="Просьба"))
        assert message.patches == [] and len(message.notes) == 1 and message.notes[0].endswith(f": {words} — правка не предложена."), field


def test_the_statement_of_a_quest_is_one_paragraph_unless_an_item_runs_over_lines():
    assert render.sentences(["Стой!", "Дальше…", "Список;", "Конец"]) == "Стой! Дальше… Список; Конец."
    assert render.sentences(["Создать программу", " Она печатает сумму. ", "", "Что при ошибке?", "Итог:"]) == (
        "Создать программу. Она печатает сумму. Что при ошибке? Итог:")
    assert render.statement(["Создать программу src/a.c", "Она печатает сумму."]) == "Создать программу src/a.c. Она печатает сумму."
    # A step that ends in a fence of code is left as it is: a dot put after the fence would keep the code open to the
    # end of the README (independent review 06.10.2026).
    fenced = "Создать программу src/a.c:\n\n```c\nint main(void);\n```"
    assert render.statement([fenced, "Она печатает сумму"]) == fenced + "\n\nОна печатает сумму"
    draft = styled_draft("quest")
    draft.tasks[0] = task(1, written_in="quest", steps=[fenced, "Она печатает сумму"])
    text = render.readme(draft)
    assert f"**Получен Quest 1.** {fenced}\n\nОна печатает сумму\n" in text and "```.\n" not in text
    assert "\n### Quest 2. План встречи\n" in text and "\n## Chapter IV\n" in text


def test_the_schools_block_on_how_to_learn_is_shown_where_the_school_asks(monkeypatch):
    fixed = render.school()
    profiles: tuple[Profile, ...] = ("quest", "legend")
    for profile in profiles:
        draft = styled_draft(profile)
        assert "**Как учиться в «Школе 21»:**" in render.readme(draft)
        monkeypatch.setattr(render, "school", lambda: {**fixed, "how_to_learn_in_readme": False})
        text = render.readme(draft)
        assert "Как учиться" not in text and "**Как работать с проектом:**\n\n- Перед выполнением проект" in text
        monkeypatch.setattr(render, "school", lambda: fixed)


def test_a_field_goes_by_one_word_on_the_card_in_an_edit_and_in_the_whole_text():
    named = {"quest": ("Quest 1 · Формулировка", "Quest 1 · Сдать", "List 2 · Текст"),
             "legend": ("Задание 1 · Шаги", "Задание 1 · Результат", "Раздел 2 · Текст")}
    for profile, labels in named.items():
        draft = styled_draft(profile)  # type: ignore[arg-type]
        message = propose(draft, ContentChange(part="task:1", field="steps", value=["Другой шаг"], reason="Просьба"),
                          ContentChange(part="task:1", field="result", value="src/b.md", reason="Просьба"),
                          ContentChange(part="topic:2", field="text", value="Другое направление к заданию, без ответа.", reason="Просьба"))
        assert tuple(p.label for p in message.patches) == labels
        words = render.field_words(draft)
        whole = render.full_text(draft)
        assert f"**{words['steps']}**\n" in whole and f"**{words['result']}:** src/task1.md" in whole and f"**{words['expected']}**\n" in whole
        assert f"### {render.topic_name(draft, 2)}. Тема 2 · задания 2" in whole and "Что нужно сделать" not in whole and "### 2.2." not in whole
    before = complete_draft()
    message = propose(before, ContentChange(part="task:1", field="steps", value=["Другой шаг"], reason="Просьба"),
                      ContentChange(part="topic:2", field="text", value="Другое направление к заданию, без ответа.", reason="Просьба"))
    assert [p.label for p in message.patches] == ["Задание 1 · Что нужно сделать", "Раздел 2.2 · Текст"]
    assert set(render.FIELD_WORDS) == {"quest", "legend", "classic"} and len({tuple(words) for words in render.FIELD_WORDS.values()}) == 1


def test_the_words_of_a_style_are_the_methodologists_to_change():
    words = render.words("quest")
    assert (words["statement"], words["pause"], words["bonus"]) == ("Получен Quest {n}.", "LOADING…", "Bonus Quest {n}. {title}")
    assert render.words("legend")["handed_columns"] == ["Задание", "Что сдаётся", "Как проверяется"]
    assert render.layout(complete_draft()) == "classic" and render.layout(styled_draft("quest")) == "quest"
