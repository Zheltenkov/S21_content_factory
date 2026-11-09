"""Answers to the interpretation questions. The next version reads only the brief, never the chat, so every answer —
«Вопрос N: …» or free-form — must leave a line in the brief; a request to explain a question or the card's own text
pasted back is not an answer and is not written down.
"""

import json
import re
from pathlib import Path

from ..core.llm import ModelUnavailable
from .collaboration import received_numbers
from .models import Brief, Run, loose
from .thread import AnswerCheck, Answered, Patch, Proposal, ProposedChange, Thread, api

ANSWERS_PROMPT = Path(__file__).parent / "prompts" / "answers.md"


def numbered_questions(run: Run) -> list[dict[str, object]]:
    """Card questions first, then design questions, numbered continuously as the UI shows them."""
    questions: list[dict[str, object]] = []
    for stage, items in (("входы", run.input_card.questions if run.input_card else []),
                         ("замысел", run.concept.questions if run.concept else [])):
        questions.extend({"number": len(questions) + 1, "stage": stage, "question": q.question, "why": q.why} for q in items)
    return questions


def open_questions(thread: Thread, run: Run) -> int:
    """Interpretation questions without a received answer; they never block going on."""
    answered = {n for m in thread.messages if m.run_id == run.id and m.status in {"applied", "pending"} for p in m.patches for n in p.question_numbers}
    answered |= received_numbers(thread.messages, run.id)
    total = len(run.input_card.questions) if run.input_card else 0
    return len([n for n in range(1, total + 1) if n not in answered])


def unanswered(thread: Thread, run: Run) -> list[dict[str, object]]:
    """Numbered questions without a received answer, including answers awaiting application."""
    done = {n for m in thread.messages if m.run_id == run.id and m.status in {"applied", "pending"} for p in m.patches for n in p.question_numbers}
    done |= received_numbers(thread.messages, run.id)
    return [q for q in numbered_questions(run) if int(str(q["number"])) not in done]


ANSWER = re.compile(r"^\s*вопрос\s*(\d+)\s*[:.)—-]\s*(.+?)\s*$", re.I | re.M)
EXPLAIN = re.compile(r"поясни|объясни|не\s+(?:совсем\s+|очень\s+)?понима|не\s+понял|что\s+(?:значит|имеется\s+в\s+виду|это\s+значит)|в\s+каком\s+смысле|зачем\s+(?:этот|такой)\s+вопрос", re.I)
WORD = re.compile(r"[a-zа-яё0-9]{3,}")


def words(text: str) -> set[str]:
    return set(WORD.findall(text.casefold().replace("ё", "е")))


def card_text(question: dict[str, object]) -> str:
    return f"{question['question']} {question.get('why', '')}"


def quotes_card(answer: str, question: dict[str, object]) -> bool:
    """The «answer» is the card's own wording of the question or of why it is asked: a quote, not an answer."""
    said = words(answer)
    return bool(said) and len(said & words(card_text(question))) >= 0.8 * len(said)


def own_words(text: str, questions: list[dict[str, object]]) -> str:
    """The message without the card texts the methodologist pasted into it."""
    for q in questions:
        for part in (str(q["question"]), str(q.get("why", ""))):
            if len(part.split()) >= 4:
                text = loose(part).sub(" ", text)
    return text


def asks_to_explain(text: str, questions: list[dict[str, object]]) -> bool:
    """A short request to explain a question («поясни этот вопрос, не понимаю»), with or without the question quoted."""
    own = own_words(text, questions)
    return bool(EXPLAIN.search(own)) and len(own.split()) <= 20


def recorded_answers(draft: Brief, run: Run, text: str, changes: list[ProposedChange],
                     answered: list[Answered] | None = None,
                     waiting: Patch | None = None) -> tuple[list[ProposedChange], list[str]]:
    """An answer must leave a trace in the brief even when it changes no field: the next version reads only the
    brief, never the chat, and would ask the same question again. Answers come from «Вопрос N: …» lines (read by
    code) and from the model, which says which questions a free-form message answers. An answer not turned into an
    edit goes to «Уточнения методолога» with the question it answers.

    What the model names is checked: a request to explain a question and the card's own text pasted into the chat
    are not answers, and are not written down.

    `waiting` is a notes proposal still waiting for a decision. A new notes proposal replaces it (patches.supersede),
    so the answers recorded there are carried over: otherwise two answers in a row without «Применить» between them
    kept only the second one, and the next version asked the first question again (complaint 29.10.2026)."""
    numbered = {int(str(q["number"])): q for q in numbered_questions(run)}
    covered = {n for c in changes for n in c.question_numbers}
    found = {int(n): answer for n, answer in ANSWER.findall(text)}
    notes: list[str] = []
    explaining = not found and asks_to_explain(text, list(numbered.values()))
    for item in answered or []:
        if item.number in found or item.number not in numbered:
            continue
        if explaining or quotes_card(item.answer, numbered[item.number]):
            notes.append(f"Ответ на вопрос {item.number} не записан: в сообщении просьба пояснить или текст самой карточки, а не ответ.")
            continue
        found[item.number] = item.answer
    missing = [(n, answer) for n, answer in sorted(found.items()) if n in numbered and n not in covered]
    notes_change = next((c for c in changes if c.field == "notes"), None)
    if not missing and not (notes_change and waiting):
        return changes, notes
    base = str(notes_change.value) if notes_change else draft.notes
    # One line per answer: a line break inside an answer would leave an orphan line when the answer is replaced.
    lines = [f"Ответ на вопрос «{numbered[n]['question']}»: {' '.join(answer.split())}" for n, answer in missing]
    # A new answer to the same question replaces the old line: two answers to one question contradict each other in
    # the brief the next version reads (audit 30.09.2026).
    heads = tuple(f"Ответ на вопрос «{numbered[n]['question']}»:" for n, _ in missing)
    kept = [x for x in base.rstrip().splitlines() if not x.startswith(heads)]
    # Answers from the waiting proposal that this one would drop; a newer answer to the same question wins.
    carried = [x for x in str(waiting.after or "").splitlines()
               if x.startswith("Ответ на вопрос «") and x not in kept and not x.startswith(heads)] if waiting else []
    value = "\n".join(x for x in [*kept, *carried, *lines] if x)
    if value == draft.notes.rstrip():
        return changes, notes  # the same answer is already in the brief: nothing new to write
    numbers = sorted({*(notes_change.question_numbers if notes_change else []), *(n for n, _ in missing),
                      *(waiting.question_numbers if carried and waiting else [])})
    record = ProposedChange(field="notes", value=value, reason="Ответ методолога на вопрос разбора: следующая версия читает только задание",
                            origin="answer", question_numbers=numbers)
    return [c for c in changes if c is not notes_change] + [record], notes


async def second_look(thread: Thread, run: Run, text: str, proposal: "Proposal") -> tuple[list[Answered], tuple[int, int]]:
    """When questions are still open after the first call and the message named none of them, one narrow call asks
    only which questions it answers: a single simple task is where a small model is reliable. A failure changes
    nothing: the first call's result stands."""
    taken = {n for c in proposal.changes for n in c.question_numbers} | {a.number for a in proposal.answered}
    taken |= {int(n) for n, _ in ANSWER.findall(text)}
    open_ = [q for q in unanswered(thread, run) if int(str(q["number"])) not in taken]
    if not open_ or proposal.kind in {"question", "off_topic"} or asks_to_explain(text, numbered_questions(run)):
        return [], (0, 0)
    payload = {"message": text, "questions": open_,
               "assistant_before": next((m.text[:1500] for m in reversed(thread.messages[:-1]) if m.role == "assistant"), "")}
    try:
        check, metrics = await api().ModelClient().generate(AnswerCheck, ANSWERS_PROMPT.read_text(encoding="utf-8"),
                                                            json.dumps(payload, ensure_ascii=False))
    except ModelUnavailable:
        return [], (0, 0)
    known = {int(str(q["number"])) for q in open_}
    return [a for a in check.answered if a.number in known], (metrics.input_tokens or 0, metrics.output_tokens or 0)
