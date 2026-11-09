"""The rules both chats share (core.chat), checked once here; the routes of each chat are checked in their own tests."""

import pytest
from project_generator_lab.core import chat
from project_generator_lab.core.contracts import Contract
from pydantic import Field


class Answer(Contract):
    kind: str = "request"
    reply: str = "Ответ"
    changes: list[str] = Field(default_factory=list)
    proceed: bool = False
    show: str = ""


@pytest.mark.parametrize("flag, kind, text, proposed, waiting, expected", [
    (False, "request", "дальше", False, False, "stay"),           # no flag: code does not move, whatever the words
    (True, "question", "а дальше?", False, False, "stay"),        # a question moves nothing on
    (True, "off_topic", "дальше", False, False, "stay"),
    (True, "request", "сократи цель, дальше", True, False, "apply_first"),  # its own edits come first
    (True, "request", "цель короче", False, False, "ask_word"),   # the flag alone is not the methodologist's word
    (True, "answer", "нет, это не нужно, давай дальше", False, True, "decide_first"),  # never decides a waiting proposal
    (True, "answer", "материалов нет, идём дальше", False, False, "go"),
    (True, "suggestion", "и так далее", False, False, "ask_word"),
])
def test_moving_on_follows_the_methodologists_words_not_the_flag(flag, kind, text, proposed, waiting, expected):
    assert chat.move(flag, kind, text, proposed, waiting) == expected


def test_what_the_chat_says_instead_of_moving_on():
    step = {"button": "Подготовить замысел"}
    assert chat.move_note("apply_first", step, "Примените правки.") == "Примените правки."
    assert chat.move_note("decide_first", step, "x") == chat.DECIDE_FIRST
    assert chat.move_note("ask_word", step, "x") == "Перехожу дальше только по вашему слову: кнопка «Подготовить замысел» или напишите «дальше»."
    assert chat.move_note("ask_word", {"button": ""}, "x") == ""  # nothing to press: nothing to say
    assert chat.move_note("go", step, "x") == chat.move_note("stay", step, "x") == ""


def test_a_question_keeps_no_edits_and_an_off_topic_message_keeps_nothing():
    asked = Answer(kind="question", changes=["правка"], proceed=True, show="full")
    kept, notes = chat.by_kind(asked, edits=("changes",), actions=("proceed", "show"), what="текст")
    # A question may still show a document or ask to move on (code decides); its edits are dropped with a note.
    assert (kept.changes, kept.proceed, kept.show) == ([], True, "full")
    assert notes == ["Сообщение понято как вопрос: правок не предлагаю. Если нужно изменить текст, напишите, что поменять."]
    off, notes = chat.by_kind(asked.model_copy(update={"kind": "off_topic"}), edits=("changes",), actions=("proceed", "show"), what="текст")
    assert (off.changes, off.proceed, off.show, off.reply, notes) == ([], False, "", "Ответ", [])
    # Retention: a plain question and a request pass as they are, without a note.
    for answer in (Answer(kind="question", proceed=True), Answer(kind="request", changes=["правка"], proceed=True)):
        assert chat.by_kind(answer, edits=("changes",), actions=("proceed",), what="текст") == (answer, [])


def test_the_way_back_and_the_hint_name_the_step_from_the_route():
    assert chat.way_back("Я помогаю с проектом.", {"title": "Входы разобраны", "button": "Подготовить замысел"}, "x") == (
        "Я помогаю с проектом.\n\nВернёмся к проекту: входы разобраны. Дальше — «Подготовить замысел».")
    assert chat.way_back("Только проект.", {"title": "", "button": ""}, "учебный контент") == "Только проект.\n\nВернёмся к проекту: учебный контент."
    assert chat.next_hint({"button": "Написать задание 2"}) == " Дальше — «Написать задание 2»: кнопка ниже или напишите «дальше»."
    assert chat.next_hint({}) == ""


@pytest.mark.parametrize("last, came_with_something, expected", [
    ("Это про сдачу или про комплект?", False, True),
    ("Сократил цель. Подходит?", True, False),            # «Подходит?» after edits: «ок» is agreement
    ("Записал ответ на вопрос «Нужен ли SQL?».", False, False),  # the «?» of a quoted card question
    ("Записал.", False, False),
    (None, False, False),
])
def test_a_bare_yes_answers_the_assistant_only_after_its_own_question(last, came_with_something, expected):
    assert chat.answers_question(last, came_with_something) is expected
