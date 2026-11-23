"""What both chats of the lab do with a message in the same way: what its kind allows, the way back after an
off-topic message, when a bare «да» answers the assistant, and when the chat may move on.

These are guardrails on the execution layer (Li Bojie, 1.2.5): code holds the model to the kind it declared and moves
on only on the methodologist's own words. Each chat used to keep its own copy of these rules, and every fix of the
audit 30.09.2026 had to be made twice; written once, the two chats cannot drift apart. What differs between the chats
(which fields an answer has, what a button is called) is passed in.
"""

from collections.abc import Mapping
from typing import Literal, TypeVar

from pydantic import BaseModel

from .intents import asked, mentions_proceed

A = TypeVar("A", bound=BaseModel)

# Kinds that never change anything: a question gets an answer, an off-topic message a way back to the project.
SILENT = {"question", "off_topic"}
AS_QUESTION = "Сообщение понято как вопрос: правок не предлагаю. Если нужно изменить {what}, напишите, что поменять."
DECIDE_FIRST = "Сначала решите по предложенным правкам выше: применить или отклонить. Потом — дальше."
ASK_WORD = "Перехожу дальше только по вашему слову: кнопка «{button}» или напишите «дальше»."

Move = Literal["go", "stay", "apply_first", "decide_first", "ask_word"]


def cleared(answer: A, names: tuple[str, ...]) -> A:
    """The answer with these fields back at their schema defaults (no edits, no flags)."""
    fields = type(answer).model_fields
    return answer.model_copy(update={name: fields[name].get_default(call_default_factory=True) for name in names})


def by_kind(answer: A, edits: tuple[str, ...], actions: tuple[str, ...], what: str) -> tuple[A, list[str]]:
    """Hold the model to the kind it declared first. An off-topic message changes, starts and shows nothing. A question
    is answered, not turned into edits: a model that called the message a question and still proposed edits contradicts
    itself, and the label wins, because the methodologist asked something and did not ask for a change.

    edits — the answer's fields that change the brief or the draft; actions — those that start or show something."""
    kind = getattr(answer, "kind", "request")  # every chat answer schema has it; the bound type cannot say so
    if kind == "off_topic":
        return cleared(answer, edits + actions), []
    if kind == "question" and any(getattr(answer, name) for name in edits):
        return cleared(answer, edits), [AS_QUESTION.format(what=what)]
    return answer, []


def way_back(reply: str, step: Mapping[str, object], default: str) -> str:
    """After an off-topic message the way back is written by code from the route, so it is there whatever the model
    wrote."""
    title = str(step.get("title") or "").lower() or default
    return f"{reply}\n\nВернёмся к проекту: {title}." + (f" Дальше — «{step['button']}»." if step.get("button") else "")


def next_hint(step: Mapping[str, object]) -> str:
    """The step a «пойдёт» did not take, offered rather than taken."""
    return f" Дальше — «{step['button']}»: кнопка ниже или напишите «дальше»." if step.get("button") else ""


def answers_question(last_reply: str | None, came_with_something: bool) -> bool:
    """«Да» answers what was said last. When the assistant's last reply asked something and brought no edits or part,
    «да» answers that question, and the model reads it (audit 30.09.2026) — even if an earlier proposal still waits:
    applying that one would answer a question nobody asked (independent review 01.10.2026: «без Excel» landed in
    the constraints in reply to «Добавить это в ограничения?»). A reply with edits or a written part asked about them
    («Подходит?»): «да» after it is agreement, handled by code."""
    return last_reply is not None and not came_with_something and asked(last_reply)


def move(flag: bool, kind: str, text: str, proposed: bool, waiting: bool) -> Move:
    """Where the model's «move on» flag may lead. Moving on has consequences — edits applied, a paid run started — so it
    follows the methodologist's own words (intents.mentions_proceed), never the flag alone: a model can be steered by
    text from uploaded files or found pages, the methodologist's message cannot. A mixed message («нет, это не нужно,
    давай дальше») never decides a proposal still waiting: the chip or a bare «дальше» does.

    proposed — this answer brings edits of its own; waiting — an earlier proposal waits for a decision."""
    if not flag or kind in SILENT:
        return "stay"
    if proposed:
        return "apply_first"
    if not mentions_proceed(text):
        return "ask_word"
    return "decide_first" if waiting else "go"


def move_note(decision: Move, step: Mapping[str, object], apply_first: str) -> str:
    """What the chat tells the methodologist instead of moving on; "" when there is nothing to tell."""
    if decision == "apply_first":
        return apply_first
    if decision == "decide_first":
        return DECIDE_FIRST
    if decision == "ask_word" and step.get("button"):
        return ASK_WORD.format(button=step["button"])
    return ""
