"""HTTP of the content stage. It composes the modules and holds no rules of its own.

The approved concept arrives only through configure(): the application passes a function that returns
a ConceptHandoff for a run id. Model calls that write parts run in the background; the page polls.
"""

import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import Field

from ..core import chat
from ..core.access import current_owner
from ..core.contracts import Contract
from ..core.generation import GenerationSettings
from ..core.intents import is_agreement, is_proceed, wants_to_see
from ..core.llm import ModelClient, ModelUnavailable, model_configuration, reviewer_model
from ..core.search import SearchClient
from . import flow, render, store
from .checks import blocking, run_checks
from .edits import apply, build_patches, expire_stale, revert, supersede
from .handoff import ConceptHandoff, HandoffSource, HandoffUnavailable, card
from .models import PRESENTATIONS, YES_NO, ContentDraft, ContentMessage, ContentRevision, Value
from .stages import latest_pending, next_step, review_stale, task_materials
from .writer import CONTENT_VERSION, EDITABLE, Writer, snapshot

router = APIRouter()


def default_writer() -> Writer:
    # The critic runs on a separate model when one is configured, so the text is not judged by its author.
    reviewer = reviewer_model()
    return Writer(ModelClient(), reviewer=ModelClient(model=reviewer) if reviewer else None, search=SearchClient())


@dataclass
class Settings:
    handoff: HandoffSource | None = None
    writer: Callable[[], Writer] = default_writer


settings = Settings()


def configure(handoff: HandoffSource, writer: Callable[[], Writer] = default_writer) -> None:
    """Called once by the application: where approved concepts come from and how parts are written."""
    settings.handoff, settings.writer = handoff, writer


def concept(run_id: str) -> ConceptHandoff:
    if settings.handoff is None:
        raise HTTPException(503, "Генератор контента не подключён к замыслу")
    try:
        return settings.handoff(run_id)
    except HandoffUnavailable as error:
        raise HTTPException(409, str(error)) from error


class AdvanceRequest(Contract):
    action: Literal["start", "apply_pending", "write_style", "approve_style", "switch_style", "write_summary", "approve_summary", "title_1", "title_2", "title_3", "write_task", "approve_task",
                    "write_materials", "approve_materials", "write_theory_plan", "approve_theory_plan", "plan_chapter", "plan_notes",
                    "plan_materials", "plan_both", "write_support", "approve_support", "run_review", "find_links", "find_excerpt", "assemble"]
    number: int | None = None


class MessageRequest(Contract):
    text: str = Field(min_length=1, max_length=4000)


class DecisionRequest(Contract):
    verdict: Literal["apply", "reject", "revert"]
    values: dict[int, Value] = Field(default_factory=dict)


def view(draft: ContentDraft | None, run_id: str) -> dict[str, object]:
    if draft is None:
        return {"id": run_id, "exists": False, "next_step": next_step(None), "content_version": CONTENT_VERSION}
    blocks = blocking(draft)
    return {**draft.model_dump(exclude={"prompt_snapshots"}), "exists": True,
            "next_step": next_step(draft, [c.message for c in blocks]),
            "checks": [c.model_dump() for c in run_checks(draft)],
            "ready": not blocks, "labels": EDITABLE, "handoff_card": card(draft.handoff, draft.styled()), "review_stale": review_stale(draft),
            # The words a task's fields go by in the README of the draft's style: the card and the patches name them so.
            "task_words": render.field_words(draft),
            # How each task is headed in the README and the check-list, said by the code that heads them.
            "task_headings": {str(t.number): render.heading(draft, t) for t in draft.tasks}, "styled": draft.styled(),
            # The plan of the theory by the names the methodologist reads; the file of the summaries, where there is one.
            "plans_theory": draft.plans_theory(), "presentations": PRESENTATIONS, "yes_no": YES_NO, "theory_file": render.theory_path(draft)}


def loaded(key: str) -> ContentDraft:
    """A stored draft with its check-list texts: drafts started before the check-list part get them from school.yaml.

    A proposal whose text changed under it is withdrawn here too, not only after a decision or a job: one saved by an
    earlier version (a link line printed differently) could never be applied and kept «Применить правки» failing
    (independent review 01.10.2026). The routes that change the draft save it; a read only shows it."""
    draft = store.load(key)
    render.ensure_checklist(draft)
    expire_stale(draft)
    return draft


def say(draft: ContentDraft, outcome: flow.Outcome) -> None:
    draft.messages.append(ContentMessage(role="assistant", text=outcome.text, part=outcome.part))
    draft.messages.extend(outcome.follow)


def launch_after_save(draft: ContentDraft, outcome: flow.Outcome) -> None:
    if outcome.job:
        flow.launch(draft.id, outcome.job, settings.writer, draft.owner_id)


def focus(draft: ContentDraft) -> str:
    """The part the methodologist is looking at: the one the next step is about, else the last one written."""
    step = next_step(draft)
    action, number = step["action"], step["number"]
    if action == "apply_pending" and (pending := latest_pending(draft)):
        # The methodologist is answering the proposal: «сделай мягче» is about the fields it changes.
        return ", ".join(dict.fromkeys(p.part for p in pending.patches))
    if action == "approve_style":
        return "style"
    if action == "approve_summary":
        return "summary"
    if action == "approve_task":
        return f"task:{number}"
    if action == "approve_materials":
        return ", ".join(f"material:{p}" for p in task_materials(draft, int(str(number)), "draft"))
    if action == "approve_theory_plan":
        return "theory_plan"
    if action == "approve_support":
        return "theory, frame"
    if draft.review is not None:
        return "review"
    return next((m.part for m in reversed(draft.messages) if m.part), "")


@router.get("/api/content/{run_id}")
async def get_draft(run_id: UUID) -> dict[str, object]:
    key = str(run_id)
    if store.exists(key) and store.readable(key):
        return view(loaded(key), key)
    concept(key)
    return view(None, key)


@router.post("/api/content/{run_id}/start")
async def start(run_id: UUID) -> dict[str, object]:
    key = str(run_id)
    async with store.lock(key):
        if store.exists(key):
            if store.readable(key):
                return view(loaded(key), key)
            # Only the owner's own unreadable draft is set aside: knowing the id is not enough (audit 30.09.2026).
            if store.owner(key) not in {"", current_owner.get()}:
                raise HTTPException(404, "Черновик контента не найден")
        # The concept is checked for its owner before any file is moved: an ownerless old draft follows its concept.
        handoff = concept(key)
        if store.exists(key):
            store.set_aside(key)
        if not model_configuration()[0]:
            raise HTTPException(503, "На сервере не настроено подключение к модели")
        draft = ContentDraft(id=key, owner_id=current_owner.get(), content_version=CONTENT_VERSION,
                             handoff=handoff, prompt_snapshots=snapshot())
        render.ensure_checklist(draft)
        draft.messages.append(ContentMessage(role="methodologist", text=str(next_step(None)["button"])))
        draft.messages.append(ContentMessage(role="assistant", part="handoff",
                                             text="Вот что я получил из согласованного замысла. Если чего-то не хватает — "
                                                  "поправьте замысел до того, как по нему написан текст."))
        # The style the project is told in comes before any of its text.
        outcome = flow.perform(draft, "write_style")
        draft.messages.append(ContentMessage(role="assistant", text=outcome.text))
        store.save(draft)
    launch_after_save(draft, outcome)
    return view(draft, key)


@router.post("/api/content/{run_id}/advance")
async def advance(run_id: UUID, body: AdvanceRequest) -> dict[str, object]:
    """A next-step chip: runs exactly the step the methodologist saw, or refuses."""
    key = str(run_id)
    if body.action == "start":
        return await start(run_id)
    async with store.lock(key):
        draft = loaded(key)
        if not flow.step_matches(draft, body.action, body.number):
            raise HTTPException(409, "Состояние изменилось. Обновите страницу и посмотрите следующий шаг.")
        draft.messages.append(ContentMessage(role="methodologist", text=flow.button(draft, body.action)))
        outcome = flow.perform(draft, body.action, body.number)
        say(draft, outcome)
        store.save(draft)
    launch_after_save(draft, outcome)
    return view(draft, key)


SHOWN = {"full": ("Весь текст — ниже, его же можно открыть отдельной страницей.", "full"),
         "readme": ("README по текущему черновику — ниже. Пока текст не согласован, в нём видны пропуски.", "readme"),
         "checklist": ("Чек-лист проверки — ниже.", "checklist")}


def show(draft: ContentDraft, name: str) -> ContentMessage:
    text, part = SHOWN[name]
    if name == "readme" and not draft.summary:
        return ContentMessage(role="assistant", text="README собирается из текста, а текста пока нет: начнём с названия и аннотации.")
    return ContentMessage(role="assistant", text=text, part=part)


def go_on(draft: ContentDraft) -> flow.Outcome:
    """«Дальше» from the chat: the step the chip would run. Errors are told, not raised."""
    step = next_step(draft, [c.message for c in blocking(draft)])
    if not step["action"]:
        return flow.Outcome(f"{step['title']}. {step['text']}")
    try:
        return flow.perform(draft, str(step["action"]), step["number"] if isinstance(step["number"], int) else None)
    except HTTPException as error:
        return flow.Outcome(f"Не получилось перейти дальше: {error.detail}")


# Words of the methodologist that ask for links: the paid search starts on them, not on the model's flag alone.
# «Первоисточник» is not «источник»: an excerpt is asked for by its own words, below.
LINKS = re.compile(r"ссылк|(?<!перво)источник|литератур|почитать|документац|статьи|статью", re.I)
# Words that ask to search for an excerpt of a primary source (again): the search is code's, the chat model has no
# such action and would answer that it cannot — or start the search for links instead.
FIND = r"(?:найди|найти|поищи|поискать|подбери|подобрать|замени|заменить|друг\w+|заново|ещё раз)"
QUOTED = r"(?:выдержк\w*|первоисточник\w*)"
EXCERPT = re.compile(rf"{FIND}\b.{{0,60}}{QUOTED}|{QUOTED}.{{0,60}}{FIND}", re.I | re.S)
# «Убери выдержку и найди ссылки» is no request for an excerpt: a message that takes it out is left to the chat model.
NO_EXCERPT = re.compile(r"убер|убрат|удал|не\s+нужн|без\s+выдержк", re.I)


def wants_excerpt(text: str) -> bool:
    return bool(EXCERPT.search(text)) and not NO_EXCERPT.search(text)


def answers_assistant(draft: ContentDraft) -> bool:
    """«Да» after the assistant's own question («сократить цель или шаги?») answers it; it must not approve the part
    under discussion. A reply that showed a part or came with edits asked about them: «ок» after it approves."""
    last = next((m for m in reversed(draft.messages[:-1]) if m.role == "assistant"), None)
    return chat.answers_question(last.text if last else None, bool(last and (last.patches or last.part)))


def story_asked(draft: ContentDraft) -> bool:
    """The proposed style waits with questions about the story. A bare «да» then may answer one of them («Уместен ли
    юмор?») as well as agree to the style: it is neither taken for an agreement nor sent to the model to guess — the
    methodologist is asked which it was (independent review 06.10.2026: «да» to such a question agreed to the style,
    started the paid call for the title, and the answer was lost)."""
    return bool(draft.style and draft.style.status == "draft" and draft.style.questions and next_step(draft)["action"] == "approve_style")


def plan_asked(draft: ContentDraft) -> bool:
    """The same for the plan of the theory: «да» under «Есть ли у участников интернет?» is an answer as likely as an
    agreement, and an agreement starts the paid writing of the theory."""
    plan = draft.theory_plan
    return bool(plan and plan.status == "draft" and plan.questions and next_step(draft)["action"] == "approve_theory_plan")


WHICH = ("Не понял, это ответ на вопрос о сюжете или согласие со стилем. Ответ напишите словами — я впишу его в сюжет. "
         "Если стиль подходит и вопросы можно оставить без ответа — кнопка «{button}» или «дальше».")
WHICH_PLAN = ("Не понял, это ответ на вопрос о подаче теории или согласие с ней. Ответ напишите словами — я учту его. "
              "Если вариант подходит и вопросы можно оставить без ответа — кнопка «{button}» или «дальше».")


def agree(draft: ContentDraft) -> flow.Outcome:
    """«Пойдёт»: accept the proposal it answers, or approve the part under discussion. Never anything else.

    The proposal it answers is the assistant's last reply. An earlier one still waiting is not decided by an «ок» to
    something said after it: the hint names its button instead (independent review 01.10.2026)."""
    last = next((m for m in reversed(draft.messages) if m.role == "assistant"), None)
    if last is not None and last.status == "pending":
        return go_on(draft)
    step = next_step(draft)
    if str(step["action"] or "").startswith("approve_"):
        return go_on(draft)
    return flow.Outcome("Понял, ничего не меняю." + chat.next_hint(step))


def by_kind(revision: ContentRevision) -> tuple[ContentRevision, list[str]]:
    """A question is answered, not turned into edits; an off-topic message changes, starts and shows nothing."""
    return chat.by_kind(revision, edits=("changes",), actions=("proceed", "find_links", "show"), what="текст")


@router.post("/api/content/{run_id}/messages")
async def post_message(run_id: UUID, body: MessageRequest) -> dict[str, object]:
    key = str(run_id)
    async with store.lock(key):
        draft = loaded(key)
        # The methodologist's words are saved before any model call, so a provider failure loses nothing.
        draft.messages.append(ContentMessage(role="methodologist", text=body.text))
        store.save(draft)
        outcome: flow.Outcome | None = None
        if wants_to_see(body.text):
            draft.messages.append(show(draft, wants_to_see(body.text)))
        elif wants_excerpt(body.text) and draft.plans_theory() and draft.theory is not None and draft.theory_plan is not None and not draft.busy:
            # Asked for in words: the plan says «да» from now on, and the search is started as the chip would start it.
            try:
                outcome = flow.start_job(draft, "find_excerpt", None)
                draft.theory_plan.english = "yes"
                say(draft, outcome)
            except HTTPException as error:
                outcome = None
                draft.messages.append(ContentMessage(role="assistant", text=str(error.detail)))
        elif is_agreement(body.text) and (story_asked(draft) or plan_asked(draft)) and not answers_assistant(draft):
            which = WHICH if story_asked(draft) else WHICH_PLAN
            draft.messages.append(ContentMessage(role="assistant", text=which.format(button=next_step(draft)["button"])))
        elif is_proceed(body.text) or (is_agreement(body.text) and not answers_assistant(draft)):
            outcome = agree(draft) if is_agreement(body.text) else go_on(draft)
            say(draft, outcome)
        else:
            try:
                revision, metrics = await settings.writer().revise(draft, body.text, focus(draft))
            except ModelUnavailable as error:
                draft.messages.append(ContentMessage(role="system", text=f"Сообщение сохранено, но не разобрано: {error}"))
                store.save(draft)
                return view(draft, key)
            revision, notes = by_kind(revision)
            patches, more = build_patches(draft, revision.changes, typed=body.text)
            notes += more
            reply, part = revision.reply, ""
            if revision.kind == "off_topic":
                reply = chat.way_back(reply, next_step(draft), "учебный контент")
            if revision.show and not patches:
                reply, part = f"{reply}\n\n{SHOWN[revision.show][0]}", SHOWN[revision.show][1]
            elif revision.find_links and not patches and draft.theory is not None and not draft.online() and LINKS.search(body.text):
                # Links the learners cannot open are not searched for: the search is paid, and its result would be shown
                # nowhere. Said in place of the model's reply, which may have promised the search.
                reply = "Ссылки не ищу: по плану подачи теории у участников нет интернета. Если он есть — скажите, я поправлю план."
            elif revision.find_links and not patches and draft.theory is not None and not draft.busy and LINKS.search(body.text):
                # Links come from the research agent, never from the chat model: it only says a search is wanted, and
                # the paid search starts only when the methodologist's own words ask for links.
                try:
                    outcome = flow.start_job(draft, "find_links", None)
                    reply = f"{reply}\n\n{outcome.text}"
                except HTTPException as error:
                    notes.append(str(error.detail))
            elif (decision := chat.move(revision.proceed, revision.kind, body.text, bool(patches),
                                        latest_pending(draft) is not None)) == "go":
                # Approving a part and starting a paid job follow the methodologist's words, not the model's flag alone.
                outcome = go_on(draft)
                reply, part = f"{reply}\n\n{outcome.text}", outcome.part
            elif note := chat.move_note(decision, next_step(draft), "Чтобы перейти дальше, примените правки."):
                notes.append(note)
            superseded = supersede(draft.messages, {(p.part, p.field) for p in patches})
            if superseded:
                notes.append(f"Предыдущее предложение по тем же полям ({superseded}) заменено этим.")
            draft.messages.append(ContentMessage(role="assistant", text=reply, part=part, patches=patches, kind=revision.kind,
                                                 status="pending" if patches else None, notes=notes, model=metrics.model,
                                                 input_tokens=metrics.input_tokens, output_tokens=metrics.output_tokens))
            if draft.handoff.collaboration_mode == "assisted" and revision.kind in {"request", "answer"} and patches and not notes:
                apply(draft, draft.messages[-1])
                draft.messages[-1].text += "\nИзменения внесены в рабочий контент. Их можно отменить."
            if outcome and part:
                draft.messages.extend(outcome.follow)
        store.save(draft)
    if outcome:
        launch_after_save(draft, outcome)
    return view(draft, key)


@router.post("/api/content/{run_id}/prepare-draft", status_code=202)
async def prepare_draft(run_id: UUID) -> dict[str, object]:
    key = str(run_id)
    async with store.lock(key):
        draft = loaded(key)
        if not draft.since("0.11"):
            raise HTTPException(409, "Пакетная подготовка доступна в новых проектах; этот черновик сохраняет прежние правила")
        if draft.busy or latest_pending(draft):
            raise HTTPException(409, "Дождитесь работы или примените ожидающие предложения")
        flow.room(draft.owner_id)
        draft.busy = "Готовлю весь черновик"
        draft.batch_prepared = False
        store.save(draft)
        flow.launch(key, ("prepare_draft", None), settings.writer, draft.owner_id)
    return view(draft, key)


@router.post("/api/content/{run_id}/generation")
async def generation_settings(run_id: UUID, body: GenerationSettings) -> dict[str, object]:
    key = str(run_id)
    async with store.lock(key):
        draft = loaded(key)
        if draft.busy:
            raise HTTPException(409, "Дождитесь завершения текущей генерации")
        if not draft.since("0.11"):
            raise HTTPException(409, "Этот черновик сохраняет прежние правила. Новые настройки доступны в новом проекте.")
        draft.handoff.generation = body
        draft.messages.append(ContentMessage(role="system", text="Настройки подачи обновлены для следующих генераций. Готовые тексты сохранены."))
        store.save(draft)
    return view(draft, key)


@router.post("/api/content/{run_id}/approve-bundle")
async def approve_bundle(run_id: UUID) -> dict[str, object]:
    key = str(run_id)
    async with store.lock(key):
        draft = loaded(key)
        if draft.busy or not draft.batch_prepared or latest_pending(draft) or not draft.review or review_stale(draft):
            raise HTTPException(409, "Завершите подготовку и актуальную проверку комплекта")
        # This endpoint is an explicit human decision; background generation never assigns approval.
        for part in [draft.style, draft.summary, draft.theory_plan, draft.theory, draft.frame, *draft.tasks, *draft.materials]:
            if part is not None:
                part.status = "approved"
        draft.batch_prepared = False
        draft.messages.append(ContentMessage(role="system", text="Методолог согласовал комплект целиком."))
        store.save(draft)
    return view(draft, key)


@router.post("/api/content/{run_id}/messages/{message_id}/decision")
async def decide(run_id: UUID, message_id: str, body: DecisionRequest) -> dict[str, object]:
    key = str(run_id)
    async with store.lock(key):
        draft = loaded(key)
        message = next((m for m in draft.messages if m.id == message_id and m.patches), None)
        if not message:
            raise HTTPException(404, "Правки не найдены")
        if body.verdict == "apply":
            apply(draft, message, body.values)
        elif body.verdict == "reject":
            if message.status != "pending":
                raise HTTPException(409, "Отклонить можно только ожидающие правки")
            message.status = "rejected"
        else:
            revert(draft, message)
        # A decision may leave another waiting proposal stale (same field changed): withdraw it rather than block the route.
        expire_stale(draft)
        store.save(draft)
        return view(draft, key)


def assembled(run_id: UUID) -> ContentDraft:
    draft = loaded(str(run_id))
    blocks = blocking(draft)
    if blocks:
        raise HTTPException(409, "Комплект ещё не готов: " + " ".join(c.message for c in blocks))
    return draft


def filename(draft: ContentDraft, suffix: str) -> str:
    return f"content-{draft.id[:8]}{suffix}"


@router.get("/api/content/{run_id}/readme")
async def readme(run_id: UUID) -> PlainTextResponse:
    draft = assembled(run_id)
    return PlainTextResponse(render.readme(draft), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="README.md"'})


@router.get("/api/content/{run_id}/check-list")
async def checklist(run_id: UUID) -> PlainTextResponse:
    draft = assembled(run_id)
    return PlainTextResponse(render.checklist(draft), media_type="application/yaml; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="check-list.yml"'})


@router.get("/api/content/{run_id}/check-list-preview")
async def checklist_preview(run_id: UUID) -> PlainTextResponse:
    """check-list.yml of the current draft, to look at and edit in the chat before the transfer."""
    return PlainTextResponse(render.checklist(loaded(str(run_id))), media_type="text/plain; charset=utf-8")


@router.get("/api/content/{run_id}/preview")
async def preview(run_id: UUID) -> PlainTextResponse:
    """README of the current draft, holes included: to look at, never to hand out."""
    # text/plain: a browser shows it; text/markdown would be downloaded silently.
    return PlainTextResponse(render.readme(loaded(str(run_id))), media_type="text/plain; charset=utf-8")


@router.get("/api/content/{run_id}/file")
async def project_file(run_id: UUID, path: str = "") -> PlainTextResponse:
    """A file of the project by its path from the root of the repository — «materials/theory.md» — so that the links
    of the README open in the preview as they will in the repository."""
    text = render.project_file(loaded(str(run_id)), path)
    if text is None:
        raise HTTPException(404, f"Файла «{path[:200]}» в проекте нет. Файлы из src/ создаёт участник: в комплекте их нет.")
    return PlainTextResponse(text, media_type="text/plain; charset=utf-8")


@router.get("/api/content/{run_id}/text")
async def full_text(run_id: UUID) -> PlainTextResponse:
    """The whole material as one text in reading order, reasons included: what the methodologist reviews before the README."""
    return PlainTextResponse(render.full_text(loaded(str(run_id))), media_type="text/plain; charset=utf-8")


@router.get("/api/content/{run_id}/bundle")
async def bundle(run_id: UUID) -> Response:
    draft = assembled(run_id)
    data = io.BytesIO(render.bundle(draft))
    return Response(data.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{filename(draft, ".zip")}"'})


def recover() -> None:
    flow.recover()


async def stop_all() -> None:
    await flow.stop_all()
