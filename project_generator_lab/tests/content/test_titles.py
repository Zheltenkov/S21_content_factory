"""Three titles to choose from (content 0.9). The one title the generator gave was «always short and dull» (the
methodologist, 07.10.2026): the prompt asked for 1–3 words «reflecting the essence» and let it lean on the title of
the plan, so the model named the topic. Now the assistant offers three, found three ways; the first is put in, the
others are a chip away, and no model is asked to choose."""

from uuid import uuid4

import pytest
from project_generator_lab import usage as report
from project_generator_lab.content import api as content_api
from project_generator_lab.content import flow, store, writer
from project_generator_lab.content.edits import apply
from project_generator_lab.content.models import ContentChange, Summary, TitledSummaryDraft
from project_generator_lab.content.stages import TITLE_ACTIONS, next_step, title_chips
from project_generator_lab.content.writer import PROMPTS, parts, task_payload
from project_generator_lab.tests.content.fixtures import wait_idle
from project_generator_lab.tests.content.test_parts import propose
from project_generator_lab.tests.content.test_route import (  # noqa: F401 - fixtures
    advance,
    begin,
    client,
    model,
    reviewer,
    search,
    walk,
)
from project_generator_lab.tests.content.test_theory_plan import planned
from pydantic import ValidationError

OFFERED = ["Основы работы продакт-менеджера", "Три недели до фичи", "Кто отвечает за бэклог?"]


def chips(view):
    return [(a["action"], a["button"]) for a in view["next_step"]["alternatives"]]


def test_the_assistant_offers_three_titles_puts_the_first_in_and_says_so(client, model):  # noqa: F811
    run_id = str(uuid4())
    view = begin(client, run_id)
    assert model.schemas[1] is TitledSummaryDraft
    assert view["summary"]["title"] == OFFERED[0] and view["summary"]["title_options"] == OFFERED
    said = view["messages"][-1]
    assert said["part"] == "summary" and said["text"] == (
        "Название и аннотация. Варианты названия: «Основы работы продакт-менеджера», «Три недели до фичи», «Кто отвечает за бэклог?». "
        "Поставил первый; другой — кнопкой под чатом, свой — словами.")
    step = view["next_step"]
    assert step["action"] == "approve_summary" and step["text"].startswith("Название — одно из предложенных: другое ставится кнопкой, своё — словами в чате. ")
    # A chip for each title that is not the one in force.
    assert chips(view) == [("title_2", "Назвать «Три недели до фичи»"), ("title_3", "Назвать «Кто отвечает за бэклог?»")]


def test_a_chip_puts_another_title_in_without_asking_a_model(client, model):  # noqa: F811
    run_id = str(uuid4())
    begin(client, run_id)
    asked = len(model.calls)
    view = advance(client, run_id, "title_3")
    assert len(model.calls) == asked
    assert view["summary"]["title"] == OFFERED[2] and view["summary"]["status"] == "draft" and view["summary"]["title_options"] == OFFERED
    pressed, answer = view["messages"][-2:]
    assert (pressed["role"], pressed["text"]) == ("methodologist", "Назвать «Кто отвечает за бэклог?»")
    assert answer["text"] == "Название — «Кто отвечает за бэклог?». Дальше — «Название и аннотация подходят — писать задание 1»."
    # The first is now one of the others; the route still waits for the agreement.
    assert chips(view) == [("title_1", "Назвать «Основы работы продакт-менеджера»"), ("title_2", "Назвать «Три недели до фичи»")]
    assert view["next_step"]["action"] == "approve_summary"
    # The tasks are written under the chosen title.
    advance(client, run_id, "approve_summary")
    assert model.calls[-1][0] == "TaskDraft" and model.calls[-1][2]["summary"] == {"title": OFFERED[2], "annotation": view["summary"]["annotation"]}


def test_an_agreed_title_has_no_chips_and_a_chip_kept_from_before_is_refused(client):  # noqa: F811
    run_id = str(uuid4())
    begin(client, run_id)
    view = advance(client, run_id, "approve_summary")
    assert all(not action.startswith("title_") for action, _ in chips(view))
    refused = client.post(f"/api/content/{run_id}/advance", json={"action": "title_2", "number": None})
    assert refused.status_code == 409
    assert wait_idle(client, run_id)["summary"]["title"] == OFFERED[0]
    draft = store.load(run_id)
    with pytest.raises(Exception) as error:
        flow.perform(draft, "title_2")
    assert "Название уже согласовано: смените его словами в чате" in str(error.value.detail)  # type: ignore[attr-defined]


def test_a_chip_for_a_title_that_was_not_offered_is_refused(client, model):  # noqa: F811
    model.titles = ["Память", "Сбой"]
    run_id = str(uuid4())
    view = begin(client, run_id)
    assert chips(view) == [("title_2", "Назвать «Сбой»")]
    assert client.post(f"/api/content/{run_id}/advance", json={"action": "title_3", "number": None}).status_code == 409
    assert client.post(f"/api/content/{run_id}/advance", json={"action": "title_4", "number": None}).status_code == 422


@pytest.mark.parametrize("given, kept", [
    (["«Битая память».", "битая память", "  Сбой  ", "Что сломалось?", "Пятое"], ["Битая память", "Сбой", "Что сломалось?"]),
    (['"Тишина"', "Тишина.", "ТИШИНА"], ["Тишина"]),
    (["Кто отвечает за бэклог?", "Три недели…"], ["Кто отвечает за бэклог?", "Три недели…"]),
    (["«C»", "Go.", "Память"], ["Память"]),
    # Quotes around a word of the title are the title's own; so is a quote that only opens it.
    (["Проект «Память»", "«Память» и «Сбой»", "„Допуск“."], ["Проект «Память»", "«Память» и «Сбой»", "Допуск"]),
    (["«Память.»", "'Сбой'...", "“Тишина”"], ["Память", "Сбой", "Тишина"]),
])
def test_the_titles_are_taken_without_quotes_and_full_stops_each_once_the_first_three(given, kept):
    assert flow.titles(given) == kept


def test_one_title_left_is_put_in_without_a_choice(client, model):  # noqa: F811
    model.titles = ["«Тишина».", "тишина"]
    view = begin(client, str(uuid4()))
    assert view["summary"]["title"] == "Тишина" and view["summary"]["title_options"] == []
    assert view["messages"][-1]["text"] == "Название и аннотация." and chips(view) == []
    assert not view["next_step"]["text"].startswith("Название — одно из")


def test_titles_that_clean_to_nothing_leave_the_first_as_it_was_given(client, model):  # noqa: F811
    model.titles = ["«C»."]
    view = begin(client, str(uuid4()))
    assert view["summary"]["title"] == "«C»." and view["summary"]["title_options"] == []


def test_the_answer_may_hold_one_title_more_or_less_but_not_none():
    note = "В этом проекте ты запустишь фичу."
    assert len(TitledSummaryDraft(titles=["Раз-два", "Три-четыре", "Пять-шесть", "Семь"], annotation=note).titles) == 4
    for wrong in ([], ["Аб"], ["Название"] * 7):
        with pytest.raises(ValidationError):
            TitledSummaryDraft(titles=wrong, annotation=note)
    with pytest.raises(ValidationError):
        Summary(title="Название", annotation=note, title_options=["Раз-два", "Три-четыре", "Пять-шесть", "Семь"])


def test_a_draft_begun_before_is_asked_for_the_one_title_its_prompt_speaks_of(client, model, monkeypatch):  # noqa: F811
    # The version a draft is begun with is the one in force where it is made; its prompts are frozen with it, so the
    # schema of the answer must be the one those prompts describe.
    monkeypatch.setattr(content_api, "CONTENT_VERSION", "0.8")
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    assert store.load(run_id).content_version == "0.8"
    view = advance(client, run_id, "approve_style")
    assert model.schemas[1].__name__ == "SummaryDraft"
    assert view["summary"]["title_options"] == [] and view["messages"][-1]["text"] == "Название и аннотация." and chips(view) == []


def test_the_titles_not_chosen_are_no_part_of_the_text():
    draft = planned("legend", "chapter")
    assert draft.summary is not None
    before, digest = draft.summary.text(), draft.digest()
    draft.summary.title_options = [draft.summary.title, "Другое название", "Третье название"]
    # Neither the writers nor the digest of the text the critics judged read them…
    assert draft.summary.text() == before == {"title": draft.summary.title, "annotation": draft.summary.annotation}
    assert task_payload(draft, 1)["summary"] == before and draft.digest() == digest
    # …the chat does, to know what «возьми второй» means; without any, it reads what it always read.
    shown = next(part for part in parts(draft) if part["id"] == "summary")["fields"]
    assert shown == {**before, "title_options": draft.summary.title_options}
    draft.summary.title_options = []
    assert next(part for part in parts(draft) if part["id"] == "summary")["fields"] == before


def test_a_title_said_in_the_chat_is_an_edit_like_any_other_and_the_list_is_not_edited():
    draft = planned("legend", "chapter")
    assert draft.summary is not None
    draft.summary.status, draft.summary.title_options = "draft", [draft.summary.title, "Три недели до фичи", "Кто отвечает за бэклог?"]
    message = propose(draft, ContentChange(part="summary", field="title", value="Три недели до фичи", reason="второй вариант"),
                      ContentChange(part="summary", field="title_options", value=["Своё"], reason="другие варианты"))
    assert [(p.field, p.after) for p in message.patches] == [("title", "Три недели до фичи")] and len(message.notes) == 1
    apply(draft, message)
    assert draft.summary.title == "Три недели до фичи" and len(draft.summary.title_options) == 3
    # A title of the methodologist's own is none of the offered: all three stay a chip away.
    draft.summary.title = "Свой заголовок"
    assert [chip["action"] for chip in title_chips(draft.summary)] == list(TITLE_ACTIONS)
    assert next_step(draft)["action"] == "approve_summary"


def test_a_long_title_is_cut_on_its_chip_only():
    long = "Почему запрос, который вчера работал секунду, сегодня работает минуту и что с этим делать?"
    summary = Summary(title="Короткое", annotation="В этом проекте ты разберёшь медленный запрос.", title_options=["Короткое", long])
    assert title_chips(summary) == [{"action": "title_2", "button": f"Назвать «{long[:59]}…»"}] and summary.title_options[1] == long


def test_the_prompts_ask_for_titles_found_three_ways_and_not_for_the_name_of_the_topic():
    asked = PROMPTS.text("summary")
    assert "titles — три варианта названия на выбор методологу" in asked and "title —" not in asked
    for way in ("образ из ситуации проекта", "дело участника", "вопрос или вызов"):
        assert way in asked
    assert "не повторяет название из замысла (project.title) и не называет тему" in asked
    assert "сами эти названия не бери" in asked
    # The styles no longer ask for «1–3 слова о теме»: the line of each says where its title comes from.
    for style, line in (("quest", "Название — из сцены"), ("legend", "Название — из рабочей ситуации легенды")):
        text = writer.STYLES.text(style)
        assert line in text and "1–3 слова" not in text
    assert "summary · title" in PROMPTS.text("revise") and "title_options" in PROMPTS.text("revise")
    assert writer.CONTENT_VERSION == "0.11"

def test_every_schema_the_route_of_a_content_draft_asks_for_has_a_stage(client, model, reviewer):  # noqa: F811
    """The other way round: a schema added to the route (the three titles of content 0.9 came as «TitledSummaryDraft»)
    would be written into the account under a name the report has no row for — and was, until this test."""
    view, _ = walk(client, str(uuid4()))
    assert view["assembled_at"]
    asked = {schema.__name__ for fake in (model, reviewer) for schema in fake.schemas}
    assert len(asked) >= 9 and asked <= set(report.STAGES), asked - set(report.STAGES)
