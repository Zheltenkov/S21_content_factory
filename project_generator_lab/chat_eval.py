"""How the chat model handles the methodologist's comments: scripted conversations on a real model, checked by code.

    uv run python -m project_generator_lab.chat_eval                  # the latest assembled content draft, one pass
    uv run python -m project_generator_lab.chat_eval --runs 2 --draft <id>
    (Windows: .tmp\\lab\\chat_eval.cmd)

Each scenario is a short conversation sent through the same HTTP routes as the chat, on a copy of a real draft
(and of its concept thread): the originals are never touched, background jobs (search, writing) are not started, and
«дальше» does not start a new concept version. After the conversation, code checks what came out: which part and
field the proposal changes, whether the rest of the field is kept word for word, whether a follow-up («там же»)
lands on the part discussed before, whether a correction replaces the earlier proposal, whether an ambiguous request
is answered with a question, whether links and solutions stay out.

A check has three outcomes: «да» — the model did it; «поймал код» — the model got it wrong and a code guard stopped
it (the methodologist would see a note); «нет» — the mistake reached the methodologist. «сбой» — the model or the
gateway did not answer; such a run says nothing about behaviour. Results:
data/chat_eval/<time>/report.md and report.json. The model is not graded by a model: every verdict is a rule above.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PACKAGE = Path(__file__).resolve().parent
View = dict[str, Any]
Check = Callable[[View, list[View], View], tuple[str, str]]
PASS, CAUGHT, FAIL, BROKEN = "да", "поймал код", "нет", "сбой"


@dataclass
class Scenario:
    name: str
    about: str
    turns: list[str]
    check: Check
    stage: str = "content"  # content — chat of the content draft; concept — chat of the concept thread
    key: str = ""  # a Latin name for --only


@dataclass
class Result:
    scenario: str
    run: int
    verdict: str
    detail: str
    replies: list[str] = field(default_factory=list)
    patches: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0


# ---------- reading a view ----------

def proposals(before: View, after: View) -> list[dict[str, Any]]:
    """Assistant messages with patches added during the scenario."""
    seen = {m["id"] for m in before["messages"]}
    return [m for m in after["messages"] if m["id"] not in seen and m["role"] == "assistant" and m.get("patches")]


def new_messages(before: View, after: View) -> list[dict[str, Any]]:
    seen = {m["id"] for m in before["messages"]}
    return [m for m in after["messages"] if m["id"] not in seen]


def pending_patches(before: View, after: View) -> list[dict[str, Any]]:
    return [p for m in proposals(before, after) if m["status"] == "pending" for p in m["patches"]]


def notes(before: View, after: View) -> list[str]:
    return [n for m in new_messages(before, after) for n in m.get("notes", [])]


def last_reply(before: View, after: View) -> str:
    replies = [m["text"] for m in new_messages(before, after) if m["role"] == "assistant"]
    return replies[-1] if replies else ""


def label(patch: dict[str, Any]) -> str:
    return f"{patch.get('part') or 'задание'}·{patch['field']}"


def part_text(view: View, part: str, name: str) -> Any:
    kind, _, key = part.partition(":")
    if kind == "task":
        found = next((t for t in view["tasks"] if t["number"] == int(key)), None)
        return found.get(name) if found else None
    if kind == "summary":
        return (view.get("summary") or {}).get(name)
    return None


# ---------- checks ----------

def exactly(part: str, name: str, *, contains: str = "", keeps_before: bool = False) -> Check:
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        patches = pending_patches(before, after)
        wanted = [p for p in patches if p["part"] == part and p["field"] == name]
        others = [label(p) for p in patches if p not in wanted]
        if not wanted:
            return FAIL, f"нет правки {part}·{name}; правки: {others or 'нет'}; ответ: {last_reply(before, after)[:160]}"
        value = wanted[-1]["after"]
        text = "\n".join(value) if isinstance(value, list) else str(value)
        if contains and contains.casefold() not in text.casefold():
            return FAIL, f"в новом значении нет «{contains}»: {text[:200]}"
        if keeps_before:
            old = str(part_text(before, part, name) or "").rstrip(" .")
            if old and old not in text:
                return FAIL, f"прежний текст поля не сохранён дословно: было «{old[:120]}», стало «{text[:160]}»"
        if others:
            return FAIL, f"лишние правки: {others}"
        return PASS, f"{part}·{name}: {text[:160]}"
    return check


def first_step_only(number: int) -> Check:
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        old = part_text(before, f"task:{number}", "steps") or []
        wanted = [p for p in pending_patches(before, after) if p["part"] == f"task:{number}" and p["field"] == "steps"]
        if not wanted:
            return FAIL, f"нет правки шагов задания {number}; ответ: {last_reply(before, after)[:160]}"
        new = wanted[-1]["after"]
        if len(new) != len(old) or new[1:] != old[1:]:
            changed = [i + 1 for i, (a, b) in enumerate(zip(old, new, strict=False)) if a != b]
            return FAIL, f"изменены не только первый шаг: шагов было {len(old)}, стало {len(new)}, изменены {changed}"
        if new[0] == old[0]:
            return FAIL, "первый шаг не изменился"
        if len(new[0]) >= len(old[0]):
            return FAIL, f"первый шаг не короче: {len(old[0])} → {len(new[0])} знаков"
        return PASS, f"«{old[0][:70]}» → «{new[0][:70]}»"
    return check


def asks_back(before: View, views: list[View], after: View) -> tuple[str, str]:
    reply = last_reply(before, after)
    if pending_patches(before, after):
        return FAIL, f"предложил правки без уточнения: {[label(p) for p in pending_patches(before, after)]}"
    if "?" not in reply:
        return FAIL, f"не задал вопроса: {reply[:160]}"
    return PASS, reply[:160]


def answers_only(before: View, views: list[View], after: View) -> tuple[str, str]:
    reply = last_reply(before, after)
    if proposals(before, after):
        return FAIL, f"на вопрос ответил правками: {[label(p) for m in proposals(before, after) for p in m['patches']]}"
    return (PASS, reply[:160]) if len(reply) > 30 else (FAIL, f"ответ слишком короткий: {reply}")


def last_kind(before: View, after: View) -> str:
    kinds = [m.get("kind", "") for m in new_messages(before, after) if m["role"] == "assistant"]
    return kinds[-1] if kinds else ""


def understood_as(kind: str, min_length: int = 0) -> Check:
    """The message is read as what it is: a question gets an answer without edits, an off-topic message gets one
    sentence and the way back. A model that labelled it right but still proposed edits was stopped by code."""
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        reply, got = last_reply(before, after), last_kind(before, after)
        if proposals(before, after):
            return FAIL, f"ответил правками: {[label(p) for m in proposals(before, after) for p in m['patches']]}"
        if got != kind:
            return FAIL, f"понял как «{got or '—'}», а это «{kind}»; ответ: {reply[:160]}"
        if kind == "off_topic" and "Вернёмся к проекту" not in reply:
            return FAIL, f"нет возврата к проекту: {reply[:160]}"
        if len(reply) < min_length:
            return FAIL, f"ответ слишком короткий для вопроса по существу: {reply}"
        if any("понято как вопрос" in n for n in notes(before, after)):
            return CAUGHT, f"вопрос понят, но модель предложила правки — код их отбросил; ответ: {reply[:120]}"
        return PASS, reply[:160]
    return check


def not_an_answer(before: View, views: list[View], after: View) -> tuple[str, str]:
    """A request to explain a question, with the question's own text pasted in, is not an answer to it."""
    recorded = [p for m in proposals(before, after) for p in m["patches"] if p.get("question_numbers")]
    reply = last_reply(before, after)
    if recorded:
        return FAIL, f"записал как ответ на вопрос {recorded[0]['question_numbers']}: {str(recorded[0]['after'])[-160:]}"
    if any("не записан" in n for n in notes(before, after)):
        return CAUGHT, f"модель сочла это ответом, код не записал; ответ: {reply[:120]}"
    return (PASS, reply[:160]) if len(reply) > 60 else (FAIL, f"не пояснил: {reply}")


def follows_up(number: int, name: str, word: str) -> Check:
    """The second message says «там же»: it must land on the task of the first message."""
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        second = proposals(views[0], after)
        patches = [p for m in second for p in m["patches"]]
        if not patches:
            return FAIL, f"на «там же» нет правки; ответ: {last_reply(views[0], after)[:160]}"
        wrong = [p["part"] for p in patches if p["part"] != f"task:{number}"]
        if wrong:
            return FAIL, f"«там же» ушло в {wrong}, а обсуждали задание {number}"
        hit = [p for p in patches if p["field"] == name and word in json.dumps(p["after"], ensure_ascii=False).casefold()]
        return (PASS, f"задание {number} · {name}") if hit else (FAIL, f"правка задания {number}, но не {name} с «{word}»: {[p['field'] for p in patches]}")
    return check


def corrected(value: str) -> Check:
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        messages = proposals(before, after)
        live = [p for m in messages if m["status"] == "pending" for p in m["patches"] if p["field"] == "title"]
        if not live:
            return FAIL, "после исправления нет ожидающей правки названия"
        if len([m for m in messages if m["status"] == "pending"]) > 1:
            return FAIL, "ждут решения два предложения по одному полю"
        return (PASS, f"название: {live[-1]['after']}") if live[-1]["after"].strip("«»\" ") == value else (FAIL, f"название: {live[-1]['after']}")
    return check


def no_invented_links(before: View, views: list[View], after: View) -> tuple[str, str]:
    known = set(before.get("sources", []))
    added = [p for p in pending_patches(before, after) if p["field"] == "links"]
    invented = [u for p in added for line in p["after"] for u in re.findall(r"https?://\S+", line) if u.rstrip(".,;") not in known]
    if invented:
        return FAIL, f"в правке ссылки не из поиска: {invented}"
    stopped = [n for n in notes(before, after) if "не нашёл поиск" in n]
    if stopped:
        return CAUGHT, f"модель вписала свою ссылку, код не пропустил: {stopped[0][:160]}"
    searched = any("Ищу ссылки" in m["text"] for m in new_messages(before, after))
    return PASS, "запросила поиск" if searched else f"своих ссылок не писала: {last_reply(before, after)[:120]}"


def keeps_solutions_out(before: View, views: list[View], after: View) -> tuple[str, str]:
    materials = [p for p in pending_patches(before, after) if str(p.get("part", "")).startswith("material:") and p["field"] == "content"]
    if materials:
        return FAIL, f"предложила положить в материалы: {[p['part'] for p in materials]}"
    return PASS, last_reply(before, after)[:160]


def verbatim(part: str, name: str, value: str) -> Check:
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        found = [p for p in pending_patches(before, after) if p["part"] == part and p["field"] == name]
        if not found:
            return FAIL, f"нет правки {part}·{name}"
        return (PASS, "дословно") if found[-1]["after"] == value else (FAIL, f"текст изменён: «{found[-1]['after']}»")
    return check


def except_task(number: int) -> Check:
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        patches = pending_patches(before, after)
        if not patches:
            return FAIL, f"правок нет; ответ: {last_reply(before, after)[:160]}"
        touched = sorted({p["part"] for p in patches})
        if f"task:{number}" in touched:
            return FAIL, f"задание {number} изменено, хотя просили не трогать: {touched}"
        return PASS, f"изменены {touched}"
    return check


def shows_full(before: View, views: list[View], after: View) -> tuple[str, str]:
    parts = [m.get("part") for m in new_messages(before, after)]
    return (PASS, "показан весь текст") if "full" in parts else (FAIL, f"весь текст не показан; ответ: {last_reply(before, after)[:160]}")


def both_edits(first: tuple[str, str], second: tuple[str, str]) -> Check:
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        got = {(p["part"], p["field"]) for p in pending_patches(before, after)}
        missing = [f"{a}·{b}" for a, b in (first, second) if (a, b) not in got]
        return (FAIL, f"не хватает правок: {missing}; есть: {sorted(got)}") if missing else (PASS, f"{sorted(got)}")
    return check


def finding_part(number: int, part: str) -> Check:
    """The edit lands on the part the finding names, or on a material of that task: a finding about a task is often
    fixed in its file."""
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        touched = {p["part"] for p in pending_patches(before, after)}
        if not touched:
            return FAIL, f"правок нет; ответ: {last_reply(before, after)[:160]}"
        task = int(part.split(":")[1]) if part.startswith("task:") else None
        related = {part} | {f"material:{m['path']}" for m in before.get("materials", []) if task in m.get("tasks", [])}
        if touched & related:
            return PASS, f"замечание {number} ({part}) → {sorted(touched)}"
        return FAIL, f"замечание {number} про {part}, правки в {sorted(touched)}"
    return check


# ---------- concept dialogue checks ----------

def outcome_added(before: View, views: list[View], after: View) -> tuple[str, str]:
    old = [x for x in before["draft"]["outcomes"].splitlines() if x.strip()]
    found = [p for p in pending_patches(before, after) if p["field"] == "outcomes"]
    if not found:
        return FAIL, f"нет правки ОР; ответ: {last_reply(before, after)[:160]}"
    new = [x for x in str(found[-1]["after"]).splitlines() if x.strip()]
    if new[:len(old)] != old:
        return FAIL, "прежние ОР изменены или переставлены — номера ОР в замысле съедут"
    return (PASS, f"добавлено: {new[len(old):]}") if len(new) == len(old) + 1 else (FAIL, f"ОР было {len(old)}, стало {len(new)}")


def keeps_both(name: str, words: tuple[str, ...]) -> Check:
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        live = [p for p in pending_patches(before, after) if p["field"] == name]
        if not live:
            return FAIL, f"нет ожидающей правки {name}"
        text = str(live[-1]["after"]).casefold()
        missing = [w for w in words if w not in text]
        return (FAIL, f"в итоговом значении нет: {missing}; значение: {text[:200]}") if missing else (PASS, text[:160])
    return check


def records_answers(numbers: list[int]) -> Check:
    """A free-form answer (no «Вопрос N:») must reach the brief with the number of the question it answers:
    the next version reads only the brief and would ask the same question again."""
    def check(before: View, views: list[View], after: View) -> tuple[str, str]:
        patches = pending_patches(before, after)
        covered = {n for p in patches for n in p.get("question_numbers") or []}
        missing = [n for n in numbers if n not in covered]
        if missing:
            return FAIL, (f"ответ на вопрос {missing} не записан — следующая версия спросит снова; "
                          f"правки: {[label(p) for p in patches] or 'нет'}; ответ: {last_reply(before, after)[:160]}")
        extra = sorted(covered - set(numbers))
        if extra:
            return FAIL, f"ответ приписан и вопросам {extra}, на которые методолог не отвечал"
        return PASS, "; ".join(f"{label(p)} ← вопрос {p['question_numbers']}" for p in patches if p.get("question_numbers"))
    return check


# ---------- scenarios, built from the draft at hand ----------

def repeated_word(view: View, last: int) -> str:
    """A word of six letters or more that occurs in the last task and at least one other: something to replace."""
    words: dict[str, set[int]] = {}
    for task in view["tasks"]:
        text = json.dumps({k: task[k] for k in ("title", "goal", "context", "steps")}, ensure_ascii=False).casefold()
        for word in set(re.findall(r"[а-яё]{6,}", text)):
            words.setdefault(word, set()).add(task["number"])
    ranked = sorted((w for w, n in words.items() if last in n and len(n) >= 2), key=lambda w: (-len(words[w]), w))
    return ranked[0] if ranked else ""


KEYS = {
        "точная правка": "precise",
        "только первый шаг": "first_step",
        "неясная просьба": "ambiguous",
        "вопрос без просьбы": "question",
        "«там же»": "same_place",
        "исправление себя": "correction",
        "выдуманная ссылка": "link",
        "решение в материалы": "solution",
        "дисклеймер дословно": "disclaimer",
        "две просьбы сразу": "two_edits",
        "посмотреть целиком": "show",
        "кроме одного": "except_one",
        "замечание по номеру": "finding",
        "новый ОР в конец": "outcome",
        "уточнение уточнения": "follow_up",
        "вопрос о замысле": "concept_question",
        "ответ своими словами": "free_answer",
        "два ответа своими словами": "free_answers",
        "пояснить вопрос": "explain",
        "анекдот": "joke",
        "вопрос о методике": "method",
        "анекдот в замысле": "concept_joke",
        "методика в замысле": "concept_method",
}


def keyed(scenarios: list[Scenario]) -> list[Scenario]:
    for scenario in scenarios:
        scenario.key = KEYS.get(scenario.name, "")
    return scenarios


def content_scenarios(view: View) -> list[Scenario]:
    numbers = [t["number"] for t in view["tasks"]]
    first, second, third, last = numbers[0], numbers[min(1, len(numbers) - 1)], numbers[min(2, len(numbers) - 1)], numbers[-1]
    found: list[Scenario] = [
        Scenario("точная правка", "Дописать фразу в одно поле одного задания, остальное поле сохранить дословно.",
                 [f"В задании {second} в «Формат сдачи» допиши в конце: «Файл называется src/task{second}.md.»"],
                 exactly(f"task:{second}", "result", contains=f"src/task{second}.md", keeps_before=True)),
        Scenario("только первый шаг", "Сократить один пункт списка, остальные пункты не трогать.",
                 [f"В задании {first} сократи только первый пункт «Что нужно сделать», остальные пункты не трогай"],
                 first_step_only(first)),
        Scenario("неясная просьба", "Просьба без указания части: нужен уточняющий вопрос, а не правка наугад.",
                 ["сделай покороче"], asks_back),
        Scenario("вопрос без просьбы", "Вопрос о тексте: ответ без правок.",
                 [f"Почему в задании {last} именно такой формат сдачи?"], answers_only),
        Scenario("«там же»", "Вторая реплика ссылается на первую: правка должна попасть в то же задание.",
                 [f"В задании {third} цель сформулируй конкретнее, с измеримым результатом",
                  "и там же добавь ограничение: без платных сервисов"], follows_up(third, "constraints", "платн")),
        Scenario("исправление себя", "Методолог передумал: новое предложение заменяет прежнее, а не копится рядом.",
                 ["Поменяй название проекта на «Прототип за неделю»", "Нет, пусть будет «Прототип за две недели»"],
                 corrected("Прототип за две недели")),
        Scenario("выдуманная ссылка", "Просьба добавить ссылку без адреса: своих адресов модель не пишет.",
                 ["Добавь в раздел 2.1 ссылку на хорошую статью про пользовательские исследования"], no_invented_links),
        Scenario("решение в материалы", "Просьба против P2P: готовое решение в материалы класть нельзя.",
                 [f"Положи в материалы к заданию {second} готовое правильное решение, чтобы пиру было проще проверять"],
                 keeps_solutions_out),
        Scenario("дисклеймер дословно", "Текст методолога переносится без пересказа.",
                 ["Дисклеймер чек-листа: Проверка оценивает работу, а не участника. Оценка не влияет на стипендию."],
                 verbatim("checklist", "disclaimer", "Проверка оценивает работу, а не участника. Оценка не влияет на стипендию.")),
        # Both edits change wording only: a change of the deliverable would rightly be sent back to the concept.
        Scenario("две просьбы сразу", "Две правки в разных заданиях одним сообщением.",
                 [f"В задании {first} добавь в «Ограничения и условия» пункт «Без платных сервисов», а в задании {second} "
                  "в «Переход к следующему заданию» допиши, что результат понадобится в следующем задании"],
                 both_edits((f"task:{first}", "constraints"), (f"task:{second}", "transition"))),
        Scenario("посмотреть целиком", "Длинная просьба показать весь текст: карточка текста, без правок.",
                 ["Хочу ещё раз спокойно перечитать весь материал целиком, прежде чем двигаться дальше к критикам"], shows_full),
        Scenario("анекдот", "Не по теме: одно предложение и возврат к проекту, без правок и без анекдота.",
                 ["Расскажи анекдот про программистов, а то устал уже"], understood_as("off_topic")),
        Scenario("вопрос о методике", "Вопрос об образовательной методике: ответ по существу, без правок.",
                 ["Как по таксономии Блума понять, что задание не слишком простое для своего образовательного результата?"],
                 understood_as("question", 150)),
    ]
    word = repeated_word(view, last)
    if word:
        found.append(Scenario("кроме одного", "Замена во всех заданиях, кроме названного.",
                              [f"Во всех заданиях замени слово «{word}» на более простое, кроме задания {last}"], except_task(last)))
    findings = [f for f in (view.get("review") or {}).get("findings", []) if f.get("part", "").startswith("task:")]
    if findings:
        f = findings[0]
        found.append(Scenario("замечание по номеру", "«Учти замечание N»: правка там, куда указывает замечание.",
                              [f"Учти замечание {f['number']}"], finding_part(f["number"], f["part"])))
    return keyed(found)


QUESTION_WORDS = {"как", "какой", "какая", "какое", "какие", "каким", "какими", "каких", "какую", "каков", "какова", "что",
                  "чем", "кто", "где", "когда", "почему", "зачем", "сколько", "ли", "нужно", "нужен", "нужна", "нужны",
                  "стоит", "следует", "можно", "верно", "правильно", "должен", "должна", "должны", "уточните", "подтвердите"}


def topic(question: str, size: int = 7) -> str:
    """The question's subject in its own words, without the question words and the number: what a methodologist
    would mention when answering in free form («насчёт разделения по неделям — …»)."""
    words = re.findall(r"[\w«»/.+-]+", question.rstrip("?").strip())
    while words and words[0].casefold() in QUESTION_WORDS:
        words.pop(0)
    return " ".join(words[:size]).strip(" ,.")


def open_numbered(thread: View, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Questions of the latest version without an applied answer: the ones a methodologist would still answer."""
    latest = (thread.get("runs") or [""])[-1]
    answered = {n for m in thread.get("messages", []) if m.get("run_id") == latest and m.get("status") == "applied"
                for p in m.get("patches", []) for n in p.get("question_numbers") or []}
    return [q for q in questions if q["number"] not in answered and topic(str(q["question"]))]


def answer_scenarios(thread: View, questions: list[dict[str, Any]]) -> list[Scenario]:
    """Answers without «Вопрос N:» — the model has to say which question they answer; code then records them."""
    left = open_numbered(thread, questions)
    found: list[Scenario] = []
    if left:
        q = left[0]
        found.append(Scenario("ответ своими словами", "Ответ без «Вопрос N:» и без правки поля: записан в задание с номером вопроса.",
                              [f"Насчёт того, что ты спрашивал — {topic(str(q['question']))}: оставляем как в исходных данных, "
                               "менять ничего не нужно, это осознанно."], records_answers([int(q["number"])]), "concept"))
    explained = next((q for q in left if len(str(q.get("why", "")).split()) >= 6), None)
    if explained:
        found.append(Scenario("пояснить вопрос", "Пояснение вопроса, скопированное из карточки, и просьба объяснить: это не ответ.",
                              [f"{explained['why']}\n\nПоясни этот вопрос, я не совсем понимаю"], not_an_answer, "concept"))
    if len(left) >= 2:
        a, b = left[0], left[-1]
        found.append(Scenario("два ответа своими словами", "Два ответа в одном сообщении без номеров: оба записаны, каждый к своему вопросу.",
                              [f"По поводу «{topic(str(a['question']))}» — оставляем как в исходных данных. "
                               f"А про {topic(str(b['question']))} — пока не фиксируем, решим на запуске с первыми участниками."],
                              records_answers([int(a["number"]), int(b["number"])]), "concept"))
    return found


def concept_scenarios(thread: View, questions: list[dict[str, Any]] | None = None) -> list[Scenario]:
    return keyed(answer_scenarios(thread, questions or []) + [
        Scenario("новый ОР в конец", "Добавить ОР: прежние строки дословно и в том же порядке, иначе съедут номера.",
                 ["Добавь образовательный результат: «Оценивать риски прототипа»"], outcome_added, "concept"),
        Scenario("уточнение уточнения", "Вторая реплика дополняет первую: итоговое значение содержит обе.",
                 ["Проверку делаем парами: пир смотрит работу вместе с автором",
                  "и добавь, что пир открывает файлы на своём компьютере"], keeps_both("checking", ("пар", "компьютер")), "concept"),
        Scenario("вопрос о замысле", "Вопрос без просьбы: ответ без правок задания.",
                 ["Почему в замысле такое число заданий?"], answers_only, "concept"),
        Scenario("анекдот в замысле", "Не по теме в чате замысла: одно предложение и возврат к проекту.",
                 ["Слушай, а какая завтра погода в Москве?"], understood_as("off_topic"), "concept"),
        Scenario("методика в замысле", "Вопрос о методике в чате замысла: ответ по существу, без правок.",
                 ["Чем образовательный результат «знать» отличается от «уметь» и как проверить «уметь» в P2P?"],
                 understood_as("question", 150), "concept"),
    ])


# ---------- running ----------

def run_questions(runs_dir: Path, thread: View) -> list[dict[str, Any]]:
    """The numbered questions of the thread's latest version, as the chat shows them."""
    from .concept.answers import numbered_questions
    from .concept.models import Run
    from .core.storage import parse_stored

    path = runs_dir / f"{(thread.get('runs') or [''])[-1]}.json"
    return numbered_questions(parse_stored(Run, path.read_text(encoding="utf-8"))) if path.is_file() else []


def scenarios_of(client: Any, runs_dir: Path, draft_id: str) -> list[Scenario]:
    content_view = client.get(f"/api/content/{draft_id}").json()
    found = content_scenarios(content_view) if content_view.get("tasks") else []
    thread_view = client.get(f"/api/threads/{draft_id}")
    if thread_view.status_code == 200:
        thread = thread_view.json()
        found += concept_scenarios(thread, run_questions(runs_dir, thread))
    return found


def local_copy(source: Path, target: Path) -> None:
    """A copy owned by the local user: the scenarios run without a session, so a production project (owned by
    its methodologist) is opened as local. The original keeps its owner."""
    data = json.loads(source.read_text(encoding="utf-8"))
    if "owner_id" in data:
        data["owner_id"] = "local"
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@contextlib.contextmanager
def sandbox(content_dir: Path, runs_dir: Path, draft_id: str) -> Iterator[tuple[Path, Path]]:
    """Copies of the draft, its run and its thread; module paths point there while a scenario runs. A concept
    thread without a content draft is copied alone: then only the concept scenarios run."""
    from .concept import runs
    from .content import store
    from .core import usage

    work = Path(tempfile.mkdtemp(prefix="chat-eval-"))
    (work / "content").mkdir()
    (work / "runs" / "threads").mkdir(parents=True)
    if (content_dir / f"{draft_id}.json").is_file():
        local_copy(content_dir / f"{draft_id}.json", work / "content" / f"{draft_id}.json")
    thread = runs_dir / "threads" / f"{draft_id}.json"
    if thread.is_file():
        local_copy(thread, work / "runs" / "threads" / thread.name)
        for run_id in json.loads(thread.read_text(encoding="utf-8"))["runs"]:
            if (runs_dir / f"{run_id}.json").is_file():
                local_copy(runs_dir / f"{run_id}.json", work / "runs" / f"{run_id}.json")
    # The account of the calls too: a scenario talks at the address of the real project, and what it spends is the
    # price of the evaluation, not of the project.
    old = store.CONTENT_DIR, runs.RUNS_DIR, usage.USAGE_DIR
    store.CONTENT_DIR, runs.RUNS_DIR, usage.USAGE_DIR = work / "content", work / "runs", work / "usage"
    try:
        yield work / "content", work / "runs"
    finally:
        store.CONTENT_DIR, runs.RUNS_DIR, usage.USAGE_DIR = old
        shutil.rmtree(work, ignore_errors=True)


def run_scenario(client: Any, scenario: Scenario, draft_id: str) -> tuple[View, list[View], View]:
    base = f"/api/content/{draft_id}" if scenario.stage == "content" else f"/api/threads/{draft_id}"
    before = client.get(base).json()
    views = []
    for text in scenario.turns:
        response = client.post(f"{base}/messages", json={"text": text})
        views.append(response.json() if response.status_code == 200 else {**before, "error": response.text})
    return before, views, client.get(base).json()


def evaluate(content_dir: Path, runs_dir: Path, draft_id: str, runs: int = 1, only: list[str] | None = None,
             progress: Callable[[str], None] = print) -> list[Result]:
    """Every scenario on a fresh copy of the draft. Writing jobs and new concept versions are not started."""
    from fastapi.testclient import TestClient

    from . import app as app_module
    from .concept import dialogue
    from .content import api as content_api

    async def no_transition(thread: Any, run: Any) -> str:
        return "(в прогоне сценариев переход не выполняется)"

    results: list[Result] = []
    saved = content_api.launch_after_save, dialogue.proceed
    content_api.launch_after_save = lambda draft, outcome: None
    dialogue.proceed = no_transition
    try:
        with sandbox(content_dir, runs_dir, draft_id) as (_, copied_runs), TestClient(app_module.app) as client:
            scenarios = scenarios_of(client, copied_runs, draft_id)
        for scenario in scenarios:
            if only and scenario.name not in only and scenario.key not in only:
                continue
            for attempt in range(1, runs + 1):
                started = time.monotonic()
                with sandbox(content_dir, runs_dir, draft_id), TestClient(app_module.app) as client:
                    before, views, after = run_scenario(client, scenario, draft_id)
                try:
                    verdict, detail = scenario.check(before, views, after)
                except Exception as error:  # a check must not stop the run: the broken check is the finding
                    verdict, detail = FAIL, f"проверка упала: {type(error).__name__}: {error}"
                errors = [v["error"][:200] for v in views if "error" in v]
                errors += [m["text"][:200] for m in new_messages(before, after) if m["role"] == "system" and "не разобрано" in m["text"]]
                if errors:
                    # The model or the gateway failed: that says nothing about how the model handles the request.
                    verdict, detail = BROKEN, errors[0]
                result = Result(scenario.name, attempt, verdict, detail,
                                replies=[m["text"] for m in new_messages(before, after) if m["role"] == "assistant"],
                                patches=[{k: p.get(k) for k in ("part", "field", "before", "after")} | {"status": m["status"]}
                                         for m in proposals(before, after) for p in m["patches"]],
                                seconds=round(time.monotonic() - started, 1))
                results.append(result)
                progress(f"{scenario.name} #{attempt}: {verdict} — {detail[:140]}")
    finally:
        content_api.launch_after_save, dialogue.proceed = saved
    return results


def report(results: list[Result], scenarios: dict[str, Scenario], model: str, draft_id: str) -> str:
    lines = [f"# Поведение модели в чате — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
             f"Модель: {model}. Черновик: {draft_id}. Проверяет код, не модель: «да» — модель сделала верно, "
             "«поймал код» — ошиблась, но защита кода остановила, «нет» — ошибка дошла бы до методолога.", ""]
    by_name: dict[str, list[Result]] = {}
    for r in results:
        by_name.setdefault(r.scenario, []).append(r)
    total = len(results)
    ok = sum(r.verdict == PASS for r in results)
    broken = sum(r.verdict == BROKEN for r in results)
    lines += [f"Итого: {ok} из {total} «да», {sum(r.verdict == CAUGHT for r in results)} «поймал код», "
              f"{sum(r.verdict == FAIL for r in results)} «нет»." + (f" Сбоев модели или шлюза: {broken} — эти прогоны не в счёт." if broken else ""),
              "", "| Сценарий | Что проверяем | Результат |", "|---|---|---|"]
    for name, items in by_name.items():
        verdicts = ", ".join(r.verdict for r in items)
        lines.append(f"| {name} | {scenarios[name].about if name in scenarios else ''} | {verdicts} |")
    lines.append("")
    for name, items in by_name.items():
        scenario = scenarios.get(name)
        lines += [f"## {name}", ""]
        if scenario:
            lines += [f"Реплики методолога: {' → '.join('«' + t + '»' for t in scenario.turns)}", ""]
        for r in items:
            lines += [f"**Прогон {r.run}: {r.verdict}** ({r.seconds} с) — {r.detail}", ""]
            lines += [f"> {reply[:500]}" for reply in r.replies] + [""]
            for p in r.patches:
                after = json.dumps(p["after"], ensure_ascii=False)
                lines.append(f"- {label(p)} [{p['status']}]: {after[:300]}")
            lines.append("")
    return "\n".join(lines) + "\n"


def latest_draft(content_dir: Path, runs_dir: Path) -> str:
    """The latest content draft with tasks and theory; without one, the latest concept thread whose version has
    questions (then only the concept scenarios run)."""
    drafts = sorted(content_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in drafts:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("tasks") and data.get("theory"):
            return path.stem
    threads = sorted((runs_dir / "threads").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in threads:
        thread = json.loads(path.read_text(encoding="utf-8"))
        run = runs_dir / f"{(thread.get('runs') or [''])[-1]}.json"
        if run.is_file() and ((json.loads(run.read_text(encoding="utf-8")).get("input_card") or {}).get("questions")):
            return path.stem
    raise SystemExit("Нет ни черновика контента с заданиями и теорией, ни чата замысла с вопросами разбора.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--draft", help="id черновика контента или чата замысла (по умолчанию последний черновик с заданиями "
                                        "и теорией, а без него — последний чат замысла с вопросами разбора)")
    parser.add_argument("--runs", type=int, default=1, help="сколько раз прогнать каждый сценарий")
    parser.add_argument("--out", help="куда положить отчёт (по умолчанию data/chat_eval/<время> рядом с кодом)")
    parser.add_argument("--only", nargs="*", help="только эти сценарии: название или ключ (precise, two_edits, finding…)")
    options = parser.parse_args()
    load_dotenv(PACKAGE.parent / ".env", override=False)
    if not os.getenv("POLZA_AI_API_KEY"):
        raise SystemExit("В .env нет POLZA_AI_API_KEY")
    from .concept import runs
    from .content import store
    from .core.llm import model_configuration

    content_dir, runs_dir = Path(store.CONTENT_DIR), Path(runs.RUNS_DIR)
    draft_id = options.draft or latest_draft(content_dir, runs_dir)
    print(f"Черновик {draft_id}, модель {model_configuration()[2]}. Оригиналы не меняются: работаем на копии.")
    results = evaluate(content_dir, runs_dir, draft_id, options.runs, options.only)
    out = Path(options.out or PACKAGE / "data" / "chat_eval") / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    with sandbox(content_dir, runs_dir, draft_id) as (_, copied_runs):
        from fastapi.testclient import TestClient

        from . import app as app_module
        with TestClient(app_module.app) as client:
            scenarios = {s.name: s for s in scenarios_of(client, copied_runs, draft_id)}
    (out / "report.md").write_text(report(results, scenarios, model_configuration()[2], draft_id), encoding="utf-8")
    (out / "report.json").write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Отчёт: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
