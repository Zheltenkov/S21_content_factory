"""Methodologist dialogue: every message becomes explicit, reviewable patches of the brief.

The chat never reaches the designer or reviewer. They see only the brief it edits, so a run can be reproduced from its
snapshot. This module holds the HTTP routes and what the dialogue model is given; the thread and its schemas are in
thread.py, edits in patches.py, answers to questions in answers.py, the route in route.py.
"""

import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ..core import chat
from ..core.access import current_owner
from ..core.intents import is_agreement, is_proceed
from ..core.llm import ModelUnavailable
from .answers import ANSWER, recorded_answers, second_look, unanswered
from .collaboration import Resolution, basis
from .input_semantics import input_semantics, program_map, program_position
from .models import Brief, Run
from .patches import apply_patches, build_patches, checked_exclusions, expire_stale, supersede
from .route import agree, changed_fields, next_step, perform, proceed, start_version
from .thread import (
    FIELDS,
    AdvanceRequest,
    DecisionRequest,
    FieldEdit,
    Message,
    MessageRequest,
    Proposal,
    ProposedChange,
    Thread,
    ThreadStart,
    api,
    latest_pending,
    latest_run,
    load_thread,
    lock,
    readable,
    save_thread,
    thread_path,
)

PROMPT = Path(__file__).parent / "prompts" / "dialogue.md"
router = APIRouter()


def view(thread: Thread, latest: Run | None = None) -> dict[str, object]:
    latest = latest or latest_run(thread)
    pending = {p.field for m in thread.messages if m.status == "pending" for p in m.patches}
    return {**thread.model_dump(), "fields": FIELDS,
            "draft_changes": changed_fields(thread, latest),
            "pending_fields": [key for key in FIELDS if key in pending],
            "next_step": next_step(thread, latest), "program_map": program_map(latest.request)}


def by_kind(proposal: Proposal) -> tuple[Proposal, list[str]]:
    """A question is answered, not turned into edits or recorded answers; an off-topic message gets a way back to the
    project and nothing else."""
    return chat.by_kind(proposal, edits=("changes", "answered"), actions=("proceed",), what="задание")


def answers_assistant(thread: Thread) -> bool:
    """A short «да» answers the assistant's own question; after a reply with edits it is agreement (core.chat)."""
    last = next((m for m in reversed(thread.messages[:-1]) if m.role == "assistant"), None)
    return chat.answers_question(last.text if last else None, bool(last and last.patches))


OWNERS = {"previous": "предыдущий проект", "this": "этот проект", "later": "следующий проект", "shared": "сквозная для блока",
          "unclear": "не видно, какой проект"}


def block_goals(run: Run) -> list[dict[str, object]]:
    m = program_map(run.request)
    items = m["block_goal_items"] if m else []
    placed = {g.number: g for g in (run.input_card.block_goals if run.input_card else [])}
    return [{"number": g["number"], "text": g["text"],
             "owner": OWNERS[placed[g["number"]].owner] if g["number"] in placed else "не разложена",
             "project": placed[g["number"]].project if g["number"] in placed else ""}
            for g in (items if isinstance(items, list) else [])]


def off_topic(run: Run) -> list[dict[str, str]]:
    places = {e.id: (e.source, e.location) for e in run.evidence}
    return [{"evidence_id": item.evidence_id, "source": places.get(item.evidence_id, ("", ""))[0],
             "location": places.get(item.evidence_id, ("", ""))[1], "quote": item.quote, "text": item.text or item.quote, "why": item.why}
            for item in (run.input_card.off_topic if run.input_card else [])]


def dialogue_payload(thread: Thread, latest: Run, request: MessageRequest) -> dict[str, object]:
    meanings = input_semantics(latest.request)["fields"]
    assert isinstance(meanings, dict)
    card, concept = latest.input_card, latest.concept
    history = [m for m in thread.messages if m.role != "system"][-9:-1]
    return {
        "message": request.text,
        "target_field": request.target_field,
        "draft": thread.draft.for_concept(),
        "fields": {key: {"label": label, "meaning": meanings.get(key, "")} for key, label in FIELDS.items()},
        "questions": unanswered(thread, latest),
        "resolved_questions": [a.model_dump() for m in thread.messages if m.status != "rejected" for a in m.answers],
        "input_card": {"summary": card.summary,
                       "open_claims": [c.model_dump(include={"area", "text", "kind"}) for c in card.claims if c.kind != "given"]}
        if card else None,
        "concept": {"title": concept.title, "student_result": concept.student_result,
                    "tasks": [{"number": i, "title": t.title, "hours": t.hours, "outcome_numbers": t.outcome_numbers,
                               "deliverable": t.deliverable, "peer_check": t.peer_check} for i, t in enumerate(concept.tasks, 1)]}
        if concept else None,
        "review_findings": [f.message for f in latest.review.findings][:12] if latest.review else [],
        # What the interpreter found unrelated to the project, with the place code knows; the methodologist decides.
        "off_topic": off_topic(latest),
        # The block goals as the card placed them, with their text: what «цель 5 — наша» refers to.
        "block_goals": block_goals(latest),
        "doubtful_skills": [s.model_dump() for s in (card.doubtful_skills if card else [])],
        # The participant's path as the card summarised it, and the plan's facts behind it (collected by code).
        "learner_path": card.learner_path.model_dump() if card and card.learner_path else None,
        "learner_facts": (program_map(latest.request) or {}).get("learner"),
        # Numbered as in the chat, so «учти пункт 2 актуальности» can become a field edit with its link.
        "freshness": [{"number": i, "topic": x.topic, "where": x.where, "status": x.status, "finding": x.finding,
                       "suggestion": x.suggestion, "sources": x.sources[:2]}
                      for i, x in enumerate(latest.freshness.items, 1)] if latest.freshness else [],
        "recent_messages": [{"role": m.role, "text": m.text[:1500], "status": m.status,
                             "changes": [{"field": p.field, "after": p.after} for p in m.patches]} for m in history],
        # Computed by code: where the route stands now, so «что дальше?» is answered from state, not guessed.
        "computed_state": {"program_position": program_position(latest.request), "run_status": latest.status,
                           "next_step": {k: v for k, v in next_step(thread, latest).items() if k in {"title", "text", "button"}}},
    }


@router.post("/api/threads")
async def open_thread(body: ThreadStart) -> dict[str, object]:
    run = api().load_run(body.run_id)
    path = thread_path(run.id)
    if path.is_file():
        return view(load_thread(body.run_id), run)
    thread = Thread(id=run.id, owner_id=current_owner.get(), runs=[run.id], draft=run.request.brief)
    save_thread(thread)
    return view(thread, run)


@router.get("/api/threads/{thread_id}")
async def get_thread(thread_id: UUID) -> dict[str, object]:
    return view(load_thread(thread_id))


@router.post("/api/threads/{thread_id}/messages")
async def post_message(thread_id: UUID, body: MessageRequest) -> dict[str, object]:
    async with lock(str(thread_id)):
        thread = load_thread(thread_id)
        latest = latest_run(thread)
        if not api().model_configuration()[0]:
            raise HTTPException(503, "На сервере не настроено подключение к модели")
        # The methodologist's words are saved before the model call so a provider failure loses nothing.
        thread.messages.append(Message(role="methodologist", text=body.text, run_id=latest.id))
        save_thread(thread)
        if is_agreement(body.text) and not answers_assistant(thread):
            thread.messages.append(Message(role="assistant", run_id=latest.id, text=agree(thread, latest)))
            save_thread(thread)
            return view(thread, latest)
        if is_proceed(body.text):
            text = await proceed(thread, latest)
            thread.messages.append(Message(role="assistant", run_id=latest.id, text=text))
            save_thread(thread)
            return view(thread)
        try:
            proposal, metrics = await api().ModelClient().generate(
                Proposal, PROMPT.read_text(encoding="utf-8"), json.dumps(dialogue_payload(thread, latest, body), ensure_ascii=False))
        except ModelUnavailable as error:
            thread.messages.append(Message(role="system", text=f"Сообщение сохранено, но не разобрано: {error}", run_id=latest.id))
            save_thread(thread)
            return view(thread, latest)
        proposal, kind_notes = by_kind(proposal)
        extra, spent = await second_look(thread, latest, body.text, proposal)
        waiting = next((p for m in reversed(thread.messages) if m.status == "pending" for p in m.patches if p.field == "notes"), None)
        changes, answer_notes = recorded_answers(thread.draft, latest, body.text, proposal.changes, [*proposal.answered, *extra], waiting)
        patches, notes = build_patches(thread.draft, changes)
        notes = kind_notes + answer_notes + notes
        patches, notes = checked_exclusions(latest, patches, notes)
        waiting_answers = [a for m in thread.messages if m.status == "pending" for a in m.answers]
        replaced = supersede(thread.messages, {p.field for p in patches})
        if replaced:
            notes.append(f"Предыдущее предложение по тем же полям ({replaced}) заменено этим.")
        reply = proposal.reply
        if proposal.kind == "off_topic":
            reply = chat.way_back(reply, next_step(thread, latest), "подготовка задания")
        # The model may only suggest moving on; code moves on when the methodologist's own words ask for it.
        decision = chat.move(proposal.proceed, proposal.kind, body.text, bool(patches), latest_pending(thread) is not None)
        if decision == "go":
            reply = f"{reply}\n\n{await proceed(thread, latest)}"
        elif note := chat.move_note(decision, next_step(thread, latest),
                                    "Чтобы перейти дальше, примените правки или нажмите «Применить правки и продолжить»."):
            notes.append(note)
        # Store the answer independently of the patch lifecycle, with the exact question and dependency snapshot.
        questions = [*(latest.input_card.questions if latest.input_card else []), *(latest.concept.questions if latest.concept else [])]
        supplied = {a.number: a.answer for a in waiting_answers}
        supplied.update({a.number: a.answer for a in [*proposal.answered, *extra]})
        supplied.update({int(n): text for n, text in ANSWER.findall(body.text)})
        for patch in patches:
            for n in patch.question_numbers:
                supplied.setdefault(n, str(patch.after))
        after = {**thread.draft.model_dump(), **{p.field: p.after for p in patches}}
        answered = [Resolution(number=n, key=questions[n - 1].key, question=questions[n - 1].question, answer=text,
                               basis=basis(after, questions[n - 1].depends_on))
                    for n, text in supplied.items() if 1 <= n <= len(questions) and any(n in p.question_numbers for p in patches)]
        message = Message(role="assistant", text=reply, run_id=latest.id, patches=patches, kind=proposal.kind, answers=answered,
                                       status="pending" if patches else None, notes=notes, model=metrics.model,
                                       input_tokens=(metrics.input_tokens or 0) + spent[0] if metrics.input_tokens is not None else None,
                                       output_tokens=(metrics.output_tokens or 0) + spent[1] if metrics.output_tokens is not None else None)
        thread.messages.append(message)
        for answer in message.answers:
            answer.message_id = message.id
        if (thread.draft.collaboration_mode == "assisted" and patches and proposal.kind in {"request", "answer"}
                and all(p.origin in {"request", "answer"} for p in patches) and not notes):
            # Explicit instructions update only the working brief; approval of the concept is still a separate step.
            apply_patches(thread, message)
            message.text += "\nИзменения внесены в рабочее задание. Их можно отменить."
        save_thread(thread)
        return view(thread)


@router.post("/api/threads/{thread_id}/advance")
async def advance(thread_id: UUID, body: AdvanceRequest) -> dict[str, object]:
    """A next-step chip: runs exactly the step the methodologist saw, or refuses."""
    async with lock(str(thread_id)):
        thread = load_thread(thread_id)
        run = latest_run(thread)
        step = next_step(thread, run)
        if step["action"] != body.action:
            raise HTTPException(409, "Состояние изменилось. Обновите страницу и посмотрите следующий шаг.")
        # A chip is a reply in the dialogue: it stays in the history as the methodologist's words, answered by the assistant.
        thread.messages.append(Message(role="methodologist", text=str(step["button"]), run_id=run.id))
        text = await perform(thread, run, body.action)
        thread.messages.append(Message(role="assistant", text=text, run_id=thread.runs[-1]))
        save_thread(thread)
        return view(thread)


@router.post("/api/threads/{thread_id}/messages/{message_id}/decision")
async def decide(thread_id: UUID, message_id: str, body: DecisionRequest) -> dict[str, object]:
    async with lock(str(thread_id)):
        thread = load_thread(thread_id)
        message = next((m for m in thread.messages if m.id == message_id and m.patches), None)
        if not message:
            raise HTTPException(404, "Правки не найдены")
        current = thread.draft.model_dump()
        if body.verdict == "apply":
            apply_patches(thread, message, body.values)
        elif body.verdict == "reject":
            if message.status != "pending":
                raise HTTPException(409, "Отклонить можно только ожидающие правки")
            message.status = "rejected"
        else:
            if message.status == "applied":
                if any(current[p.field] != p.after for p in message.patches):
                    raise HTTPException(409, "Поля уже изменены позже: отменить эти правки без конфликта нельзя")
                try:
                    # The old values together may no longer fit (group size 5 back with format «group» kept): refuse, not 500.
                    thread.draft = Brief.model_validate({**current, **{p.field: p.before for p in message.patches}})
                except ValidationError as error:
                    raise HTTPException(409, f"Отменить нельзя: {readable(error)}") from error
            elif message.status == "rejected":
                # Back to «waiting» only if it can still be applied: a stale proposal would block «Применить и продолжить».
                if any(current[p.field] != p.before for p in message.patches):
                    raise HTTPException(409, "Поле изменилось после предложения: попросите правку заново")
                # Two waiting proposals on one field cannot both be applied: the one brought back replaces the other.
                supersede([m for m in thread.messages if m is not message], {p.field for p in message.patches})
            else:
                raise HTTPException(409, "Отменять нечего")
            message.status = "pending"
        expire_stale(thread)
        save_thread(thread)
        return view(thread)


@router.post("/api/threads/{thread_id}/fields")
async def edit_field(thread_id: UUID, body: FieldEdit) -> dict[str, object]:
    """A direct edit goes through the same patch log, so the history shows who changed what."""
    async with lock(str(thread_id)):
        thread = load_thread(thread_id)
        change = ProposedChange(field=body.field, value=body.value, reason="Изменено методологом вручную", origin="request")
        patches, notes = build_patches(thread.draft, [change], origin_override="manual")
        if notes and not patches:
            raise HTTPException(422, notes[0])
        if patches:
            current = thread.draft.model_dump()
            thread.draft = Brief.model_validate({**current, body.field: patches[0].after})
            # A proposal still waiting on this field is now stale: it would sit there and make «Применить и продолжить»
            # fail every time. The methodologist's own edit replaces it.
            replaced = supersede(thread.messages, {body.field})
            thread.messages.append(Message(role="system", text=f"Изменено вручную: «{FIELDS[body.field]}»",
                                           run_id=thread.runs[-1], patches=patches, status="applied",
                                           notes=[f"Ожидавшее предложение ({replaced}) заменено ручной правкой."] if replaced else []))
            save_thread(thread)
        return view(thread)


@router.post("/api/threads/{thread_id}/versions")
async def new_version(thread_id: UUID) -> dict[str, object]:
    async with lock(str(thread_id)):
        thread = load_thread(thread_id)
        await start_version(thread, latest_run(thread))
        save_thread(thread)
        return view(thread)
