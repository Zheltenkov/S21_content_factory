"""What the content chat model is given to keep the thread of a conversation, and what code does around it.

The model itself is scripted here: these tests pin the context it receives (focus, earlier turns and what became of
their proposals, numbered critic findings, part statuses) and the decisions code takes without it (agreement,
«дальше», «покажи весь текст», an older proposal replaced by a newer one). How a real model uses this context is
measured separately, by the chat scenarios in chat_eval.py.
"""

from uuid import uuid4

import pytest
from project_generator_lab.content import store
from project_generator_lab.content.models import ContentChange, ContentRevision
from project_generator_lab.core.intents import is_agreement, is_proceed, wants_to_see
from project_generator_lab.tests.content.fixtures import wait_idle
from project_generator_lab.tests.content.test_route import advance, client, model, reviewer, search, walk  # noqa: F401

TRICKY = [
    # (message, agreement, proceed, show) — a wrong verdict here either changes text nobody asked to change,
    # or throws away a request the model never sees.
    ("пойдёт", True, False, ""), ("да, в целом всё верно", True, False, ""), ("👍", True, False, ""),
    ("не пойдёт", False, False, ""), ("пойдёт, но срок поменяй на две недели", False, False, ""),
    ("да нет, не так", False, False, ""), ("согласен с замечанием 3", False, False, ""),
    ("спасибо, но задание 2 слабое", False, False, ""), ("дальше", False, True, ""), ("ок, давай дальше", False, True, ""),
    ("дальше не надо", False, False, ""), ("дальше сделай задание короче", False, False, ""),
    ("покажи весь текст", False, False, "full"), ("Где весь текст?", False, False, "full"), ("открой README", False, False, "readme"),
    ("покажи чек-лист", False, False, "checklist"), ("весь текст задания 2 сократи", False, False, ""),
    ("покажи задание 2", False, False, ""),
]


@pytest.mark.parametrize("text, agreement, proceed, show", TRICKY)
def test_short_replies_are_read_by_code_only_when_they_carry_nothing_else(text, agreement, proceed, show):
    assert (is_agreement(text), is_proceed(text), wants_to_see(text)) == (agreement, proceed, show)


def revise_calls(fake):
    return [payload for name, _, payload in fake.calls if name == "ContentRevision"]


def test_the_model_sees_earlier_turns_and_what_became_of_their_proposals(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    model.revisions = [
        ContentRevision(reply="Сократил цель.", changes=[ContentChange(part="task:1", field="goal", value="Короткая цель.", reason="Просили короче")]),
        ContentRevision(reply="Сделал мягче.", changes=[ContentChange(part="task:1", field="goal", value="Мягкая цель.", reason="Мягче")]),
        ContentRevision(reply="Уточните, что именно?"),
    ]
    client.post(f"/api/content/{run_id}/messages", json={"text": "сократи цель"})
    client.post(f"/api/content/{run_id}/messages", json={"text": "нет, лучше мягче"})
    view = wait_idle(client, run_id)
    first, second = revise_calls(model)
    # The first request is about the task on the screen; the second answers the proposal still waiting.
    assert first["focus"] == "task:1" and second["focus"] == "task:1"
    history = second["recent_messages"]
    assert [m["text"] for m in history] == [*[m["text"] for m in history[:-2]], "сократи цель", "Сократил цель."]
    assert history[-1]["status"] == "pending" and history[-1]["changes"] == [{"part": "task:1", "field": "goal"}]
    assert "нет, лучше мягче" == second["message"] and all(m["text"] != "нет, лучше мягче" for m in history)
    # The newer proposal for the same field replaces the older one: applying the old one later would undo it.
    proposals = [m for m in view["messages"] if m["patches"]]
    assert [m["status"] for m in proposals] == ["rejected", "pending"] and "Заменено следующим предложением." in proposals[0]["notes"]
    assert "заменено этим" in proposals[1]["notes"][0]
    advance(client, run_id, "apply_pending")
    client.post(f"/api/content/{run_id}/messages", json={"text": "а что с цифрами?"})
    third = revise_calls(model)[-1]
    assert [m["status"] for m in third["recent_messages"] if m["changes"]][-2:] == ["rejected", "applied"]
    assert store.load(run_id).task(1).goal == "Мягкая цель."  # type: ignore[union-attr]


def test_history_is_a_window_of_real_turns(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    model.revisions = [ContentRevision(reply=f"Ответ {i}") for i in range(12)]
    for i in range(12):
        client.post(f"/api/content/{run_id}/messages", json={"text": f"вопрос {i}"})
    history = revise_calls(model)[-1]["recent_messages"]
    # Eight turns before the current message, no system lines; the current message is passed separately.
    assert len(history) == 8 and history[-1]["text"] == "Ответ 10" and history[-2]["text"] == "вопрос 10"
    assert all(m["role"] != "system" for m in history)


def test_findings_keep_their_numbers_and_parts_for_the_chat(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="assemble")
    model.revisions = [ContentRevision(reply="Учту.")]
    client.post(f"/api/content/{run_id}/messages", json={"text": "учти замечание 2"})
    payload = revise_calls(model)[-1]
    assert payload["focus"] == "review"
    assert [(f["number"], f["part"]) for f in payload["review"]][:2] == [(1, "task:2"), (2, "task:3")]
    statuses = {p["id"]: p["status"] for p in payload["parts"]}
    assert statuses["task:1"] == "approved" and statuses["checklist"] == "code"


def test_asking_to_see_a_document_needs_no_model(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    calls = len(model.calls)
    client.post(f"/api/content/{run_id}/messages", json={"text": "покажи весь текст"})
    view = wait_idle(client, run_id)
    assert len(model.calls) == calls and view["messages"][-1]["part"] == "full"
    # A longer message goes to the model, which may still ask code to show a document.
    model.revisions = [ContentRevision(reply="Показываю.", show="readme")]
    client.post(f"/api/content/{run_id}/messages", json={"text": "хочу посмотреть, как это будет выглядеть в README целиком для участника"})
    view = wait_idle(client, run_id)
    assert view["messages"][-1]["part"] == "readme" and "README по текущему черновику" in view["messages"][-1]["text"]


def test_focus_follows_what_is_on_the_screen(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    model.revisions = [ContentRevision(reply="Поменял название.", changes=[ContentChange(part="summary", field="title", value="Новое", reason="r")]),
                       ContentRevision(reply="Мягче."), ContentRevision(reply="Ок."), ContentRevision(reply="Ок."), ContentRevision(reply="Ок.")]
    client.post(f"/api/content/{run_id}/messages", json={"text": "поменяй название"})
    # Task 1 is on the screen, but the answer to a waiting proposal is about the part that proposal changes.
    client.post(f"/api/content/{run_id}/messages", json={"text": "сделай мягче"})
    assert revise_calls(model)[-1]["focus"] == "summary"
    advance(client, run_id, "apply_pending")
    advance(client, run_id, "approve_task", 1)
    client.post(f"/api/content/{run_id}/messages", json={"text": "в файле поменяй имена"})
    assert revise_calls(model)[-1]["focus"] == "material:team.csv"
    view, _ = walk(client, run_id, stop="approve_support")
    client.post(f"/api/content/{run_id}/messages", json={"text": "короче"})
    assert revise_calls(model)[-1]["focus"] == "theory, frame"


def test_agreement_applies_the_waiting_proposal_and_otherwise_says_what_is_next(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    model.revisions = [ContentRevision(reply="Сократил.", changes=[ContentChange(part="task:1", field="goal", value="Короткая цель.", reason="r")])]
    client.post(f"/api/content/{run_id}/messages", json={"text": "сократи цель"})
    view = wait_idle(client, run_id)
    client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"})
    view = wait_idle(client, run_id)
    assert store.load(run_id).task(1).goal == "Короткая цель." and view["messages"][-1]["text"].startswith("Применил правки")  # type: ignore[union-attr]
    walk(client, run_id, stop="run_review")
    client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"})
    view = wait_idle(client, run_id)
    assert view["messages"][-1]["text"] == "Понял, ничего не меняю. Дальше — «Проверить критиками»: кнопка ниже или напишите «дальше»."


def test_moving_on_explains_what_blocks_the_readme(client):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="assemble")
    draft = store.load(run_id)
    draft.materials[0].content = "role,person\nCEO,Петя,лишняя колонка\n"
    store.save(draft)
    client.post(f"/api/content/{run_id}/messages", json={"text": "дальше"})
    reply = wait_idle(client, run_id)["messages"][-1]["text"]
    assert reply.startswith("Перед переносом в README нужно поправить.") and "team.csv" in reply


def test_readme_request_before_any_text_is_answered_plainly(client, model):  # noqa: F811
    run_id = str(uuid4())
    model.fail_on = {"SummaryDraft"}
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    client.post(f"/api/content/{run_id}/messages", json={"text": "покажи README"})
    last = wait_idle(client, run_id)["messages"][-1]
    assert (last["part"], last["text"]) == ("", "README собирается из текста, а текста пока нет: начнём с названия и аннотации.")
    client.post(f"/api/content/{run_id}/messages", json={"text": "покажи чек-лист"})
    last = wait_idle(client, run_id)["messages"][-1]
    assert (last["role"], last["part"], last["text"]) == ("assistant", "checklist", "Чек-лист проверки — ниже.")


def test_only_waiting_proposals_for_the_same_field_are_replaced(client, model):  # noqa: F811
    from project_generator_lab.content.api import supersede
    from project_generator_lab.content.models import ContentMessage, Patch

    def message(part, name, status, label):
        return ContentMessage(role="assistant", text="", status=status,
                              patches=[Patch(part=part, field=name, label=label, before="a", after="b")])

    messages = [message("task:1", "goal", "applied", "старая"), message("task:1", "goal", "pending", "первая"),
                message("task:2", "goal", "pending", "другое поле"), message("task:1", "goal", "pending", "вторая")]
    assert supersede(messages, {("task:1", "goal")}) == "первая, вторая"
    assert [m.status for m in messages] == ["applied", "rejected", "pending", "rejected"]
    assert messages[0].notes == [] and messages[2].notes == []


def test_a_question_changes_nothing_and_an_off_topic_message_gets_a_way_back(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    model.revisions = [
        # Labelled a question, yet with an edit: the label wins — the methodologist asked, he did not ask to change.
        ContentRevision(kind="question", reply="Таксономия Блума помогает выбрать глагол ОР по уровню.",
                        changes=[ContentChange(part="task:1", field="goal", value="Другая цель.", reason="—")]),
        ContentRevision(kind="off_topic", reply="Я помогаю только с контентом этого проекта.", show="full", proceed=True, find_links=True,
                        changes=[ContentChange(part="task:1", field="goal", value="Анекдот.", reason="—")]),
    ]
    client.post(f"/api/content/{run_id}/messages", json={"text": "Как по Блуму выбирать глагол для ОР?"})
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "Расскажи анекдот про программистов"}).json()
    question, off = [m for m in view["messages"] if m["role"] == "assistant"][-2:]
    assert question["patches"] == [] and question["kind"] == "question"
    assert question["notes"] == ["Сообщение понято как вопрос: правок не предлагаю. Если нужно изменить текст, напишите, что поменять."]
    assert off["patches"] == [] and off["part"] == "" and off["kind"] == "off_topic"
    assert "\n\nВернёмся к проекту:" in off["text"]
    assert store.load(run_id).task(1).goal != "Анекдот."  # type: ignore[union-attr]
    # Nothing is started from an off-topic answer: no search for links, no move to the next step.
    assert off["text"].count("\n\n") == 1 and not view.get("busy")
    assert view["messages"][-1]["id"] == off["id"]


def test_an_off_topic_message_does_not_start_a_search_for_links_even_when_the_theory_is_there(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="assemble")
    assert store.load(run_id).theory is not None
    model.revisions = [ContentRevision(kind="off_topic", reply="Я помогаю только с контентом этого проекта.", find_links=True, proceed=True)]
    # Even with words that ask for links and for moving on: an off-topic message starts nothing.
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "найди мне ссылки на рецепт борща, давай дальше"}).json()
    assert not view["busy"] and view["messages"][-1]["kind"] == "off_topic" and view["messages"][-1]["text"].count("\n\n") == 1


def test_a_part_is_approved_by_the_methodologists_words_not_by_the_models_flag(client, model):  # noqa: F811
    run_id = str(uuid4())
    view = walk(client, run_id, stop="approve_task")[0]
    step = view["next_step"]
    model.revisions = [ContentRevision(reply="Хорошо.", proceed=True), ContentRevision(reply="Хорошо.", proceed=True)]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "Цель задания мне нравится, формулировки понятные"}).json()
    # Nothing approved: the same step is still offered, with a note how to move on.
    assert view["next_step"]["action"] == step["action"] and store.load(run_id).task(1).status == "draft"  # type: ignore[union-attr]
    assert view["messages"][-1]["notes"][-1].startswith("Перехожу дальше только по вашему слову: кнопка «")
    # His own «давай дальше» inside a longer message moves on.
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "Материалов больше не нужно, давай дальше"}).json()
    assert store.load(run_id).task(1).status == "approved"  # type: ignore[union-attr]


def test_yes_to_the_assistants_question_is_an_answer_not_an_approval(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    model.revisions = [ContentRevision(reply="Сократить цель или шаги?"),
                       ContentRevision(kind="answer", reply="Сокращаю цель.",
                                       changes=[ContentChange(part="task:1", field="goal", value="Короткая цель задания.", reason="Ответ")])]
    client.post(f"/api/content/{run_id}/messages", json={"text": "сделай короче"})
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "да"}).json()
    # The task is not approved by «да»: the model got it and proposed the edit the answer asked for.
    assert store.load(run_id).task(1).status == "draft"  # type: ignore[union-attr]
    assert view["messages"][-1]["patches"][0]["field"] == "goal" and len(revise_calls(model)) == 2


def test_yes_answers_what_was_said_last_and_never_an_earlier_proposal(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    model.revisions = [ContentRevision(reply="Предлагаю новую цель.",
                                       changes=[ContentChange(part="task:1", field="goal", value="Совсем новая цель задания", reason="Точнее")]),
                       ContentRevision(kind="question", reply="Шагов пять. Сократить их до трёх?"),
                       ContentRevision(kind="answer", reply="Сокращаю шаги.")]
    client.post(f"/api/content/{run_id}/messages", json={"text": "поменяй цель задания 1"})
    client.post(f"/api/content/{run_id}/messages", json={"text": "а шагов сколько?"})
    calls = len(revise_calls(model))
    client.post(f"/api/content/{run_id}/messages", json={"text": "да"})
    # «Да» answered the question about the steps: the model got it, the goal proposal still waits.
    task = store.load(run_id).task(1)
    assert len(revise_calls(model)) == calls + 1 and task is not None and task.goal != "Совсем новая цель задания"
    # «ок» after a reply that asked nothing does not decide the earlier proposal either.
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "ок"}).json()
    assert len(revise_calls(model)) == calls + 1 and store.load(run_id).task(1).goal != "Совсем новая цель задания"  # type: ignore[union-attr]
    assert view["messages"][-1]["text"] == "Понял, ничего не меняю. Дальше — «Применить правки»: кнопка ниже или напишите «дальше»."


def test_ok_after_a_decided_proposal_approves_and_a_mixed_message_never_applies_a_waiting_one(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    # «Применить?» asked about the edit; once applied, «пойдёт» is agreement handled by code: the task is approved.
    model.revisions = [ContentRevision(reply="Предлагаю новую цель. Применить?",
                                       changes=[ContentChange(part="task:1", field="goal", value="Совсем новая цель задания", reason="Точнее")])]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "поменяй цель задания 1"}).json()
    client.post(f"/api/content/{run_id}/messages/{view['messages'][-1]['id']}/decision", json={"verdict": "apply"})
    calls = len(revise_calls(model))
    client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"})
    assert len(revise_calls(model)) == calls and store.load(run_id).task(1).status == "approved"  # type: ignore[union-attr]


def test_declining_and_moving_on_in_one_message_does_not_apply_the_declined_proposal(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_task")
    model.revisions = [ContentRevision(reply="Предлагаю новую цель.",
                                       changes=[ContentChange(part="task:1", field="goal", value="Совсем новая цель задания", reason="Точнее")]),
                       ContentRevision(reply="Хорошо, без новой цели.", proceed=True)]
    client.post(f"/api/content/{run_id}/messages", json={"text": "поменяй цель задания 1"})
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "Нет, цель не меняем, давай дальше"}).json()
    task = store.load(run_id).task(1)
    assert task is not None and task.goal != "Совсем новая цель задания" and task.status == "draft"
    assert view["messages"][-1]["notes"][-1].startswith("Сначала решите по предложенным правкам выше")


def test_the_paid_link_search_starts_on_the_methodologists_words_not_on_the_models_flag(client, model):  # noqa: F811
    run_id = str(uuid4())
    walk(client, run_id, stop="assemble")
    model.revisions = [ContentRevision(reply="Поищу.", find_links=True), ContentRevision(reply="Поищу.", find_links=True)]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "Сделай введение живее"}).json()
    assert not view["busy"]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "Найди ещё ссылки для теории"}).json()
    assert view["busy"]
