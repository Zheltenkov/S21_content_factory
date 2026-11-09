"""Replies that carry no content: agreement («пойдёт») and asking to move on («дальше»).

They are recognised by code in every chat of the lab, so a model never turns them into an edit.
"""

import re
from typing import Literal

# What a message is, decided by the chat model before it writes anything (the first field of its answer). Code then
# holds it to it: a question or an off-topic message changes nothing, whatever else the model put in its answer.
# The page names each kind (KIND_LABEL in static/chat.js; tests/test_js.py keeps the two lists equal).
MessageKind = Literal["request", "answer", "suggestion", "question", "off_topic"]

# Words that carry agreement only. A message made of them changes nothing, so the model is not asked:
# a model tends to turn "пойдёт" into an edit of whatever it proposed last.
AGREEMENT = {
    "да", "ок", "ok", "окей", "okay", "ага", "угу", "хорошо", "отлично", "супер", "норм", "нормально", "пойдет",
    "пойдёт", "годится", "подходит", "устраивает", "согласен", "согласна", "согласны", "принято", "верно", "спасибо",
    "всё", "все", "так", "и", "как", "есть", "оставь", "оставляем", "оставить", "меня", "вполне", "в", "целом",
}
FILLER = {"всё", "все", "так", "и", "как", "есть", "меня", "в", "целом", "вполне"}


# A message made only of these asks to move on. «Генерируй задания», «продолжаем» need no model call;
# a model tends to turn them into an edit of the output package instead.
PROCEED = {
    "дальше", "далее", "продолжаем", "продолжай", "продолжайте", "продолжим", "продолжить", "поехали", "генерируй",
    "генерируйте", "сгенерируй", "сгенерируйте", "запускай", "запусти", "запустить", "запускаем", "переходим", "перейдем",
    "перейдём", "переходи", "вперед", "вперёд", "next", "go", "готовь", "подготовь", "подготовьте", "делай", "собирай", "собери",
}
PROCEED_FILLER = {
    "давай", "давайте", "ну", "то", "тогда", "уже", "можно", "пойдем", "пойдём", "идем", "идём", "к", "на", "следующий",
    "следующему", "шаг", "шагу", "этап", "этапу", "замысел", "замыслу", "задания", "заданий", "проект", "пожалуйста", "теперь", "а",
}


SHOW_VERBS = r"(покаж|показ|открой|открыть|выведи|вывести|дай|посмотр|увидеть|где)"
SHOW = {
    "full": re.compile(SHOW_VERBS + r".{0,20}(весь|полный|целиком|целый)\s*(текст|материал)?|^\W*(весь|полный)\s+(текст|материал)\W*$", re.I),
    "readme": re.compile(SHOW_VERBS + r".{0,20}(readme|ридми)", re.I),
    "checklist": re.compile(SHOW_VERBS + r".{0,20}(чек-?лист|check-?list)", re.I),
}


def wants_to_see(text: str) -> str:
    """«Покажи весь текст», «где README?»: a short request to see a document, answered by code with its card.
    A longer message may ask for more than that; it goes to the model, which can still ask to show a document."""
    words = re.findall(r"[a-zа-яё-]+", text.casefold())
    if len(words) > 8:
        return ""
    return next((name for name, pattern in SHOW.items() if pattern.search(text)), "")


def is_proceed(text: str) -> bool:
    words = re.findall(r"[a-zа-яё]+", text.casefold())
    rest = re.sub(r"[\w\s]", "", text, flags=re.UNICODE)
    return (bool(words) and len(words) <= 7 and not rest.strip(".,!?…) ") and bool(set(words) & PROCEED)
            and set(words) <= PROCEED | PROCEED_FILLER | AGREEMENT)


CLAUSES = re.compile(r"[.,;:!?\n—–]+|\s+(?:и|а|но|тогда|потом)\s+", re.I)
# «и так далее», «и т. д.» are not a request to move on; «сначала …» puts something before moving on.
IDIOMS = re.compile(r"\bи\s+так\s+далее\b|\bи\s+далее\b|\bи\s+т\.?\s*д\.?|\bи\s+т\.?\s*п\.?", re.I)
FIRST = re.compile(r"\b(сначала|сперва|прежде|перед\s+тем|пока\s+не)\b", re.I)


def mentions_proceed(text: str) -> bool:
    """The methodologist's own words end the message by asking to move on («материалов нет, идём дальше»).

    A transition has consequences — edits applied, a paid run started — so it follows only what code reads in the
    methodologist's text, never the model's flag alone: a model can be steered by text from uploaded files or found
    pages, the methodologist's message cannot (execution-layer guardrail, audit 30.09.2026). Read narrowly: the last
    clause must be the request, the message is not a question, and nothing is put before moving on («сначала …»)."""
    if "?" in text or FIRST.search(text):
        return False
    clauses = [part for part in CLAUSES.split(IDIOMS.sub(" ", text)) if part and part.strip()]
    return bool(clauses) and is_proceed(clauses[-1])


def is_agreement(text: str) -> bool:
    if text.strip() in {"+", "+1", "👍"}:
        return True
    words = re.findall(r"[a-zа-яё]+", text.casefold())
    rest = re.sub(r"[\w\s]", "", text, flags=re.UNICODE).replace("👍", "").replace("+", "")
    return bool(words) and len(words) <= 6 and not rest.strip(".,!?…) ") and set(words) <= AGREEMENT and not set(words) <= FILLER


QUOTED = re.compile(r"«[^»]*»|\"[^\"]*\"|“[^”]*”")


def asked(reply: str) -> bool:
    """The assistant's last reply ends by asking the methodologist something («…это про X или про Y?»). Then a bare
    «да» answers that question: it goes to the model, not to «accept the proposal» (audit 30.09.2026). A «?» inside a
    quoted question of the card («на вопрос «Нужен ли SQL?»») is not the assistant asking."""
    return "?" in QUOTED.sub("", reply).strip()[-200:]
