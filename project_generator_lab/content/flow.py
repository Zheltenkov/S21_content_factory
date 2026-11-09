"""The route of the content stage: what each step does, and the background writers.

A step changes the draft in memory and may name a writing job; the API saves the draft first and only
then starts the job, so a job always starts from a stored state. Writers save every part as soon as it is
ready, under the draft lock, and never overwrite a part the methodologist has approved.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from fastapi import HTTPException

from ..core import usage
from ..core.llm import CallMetrics, ModelUnavailable
from ..core.search import pick
from . import corpus, render, research, store
from .checks import blocking
from .edits import apply, build_patches, expire_stale, part_label, supersede
from .handoff import STYLE_NAMES
from .models import (
    PRESENTATIONS,
    TITLE_CHOICES,
    ContentChange,
    ContentDraft,
    ContentMessage,
    Finding,
    Frame,
    Material,
    Review,
    Style,
    Summary,
    TaskContent,
    Theory,
    TheoryPlan,
    TheoryTopic,
    TitledSummaryDraft,
    other,
    sorted_examples,
)
from .stages import TITLE_ACTIONS, latest_pending, next_step, support_missing, task_materials
from .writer import Writer, facts_payload, material_payload, parts

logger = logging.getLogger("project_generator_lab.content")
WRITE_ACTIONS = {"write_style", "write_summary", "write_task", "write_materials", "write_theory_plan", "write_support", "run_review",
                 "find_links", "find_excerpt"}
# The chips that choose how the theory is given: one for each presentation.
PLAN_ACTIONS = {f"plan_{name}": name for name in PRESENTATIONS}
# New files one task may ask for: more usually means guides that repeat the theory. The methodologist can add more in the chat.
MATERIALS_PER_TASK = 2
jobs: dict[str, asyncio.Task[None]] = {}


@dataclass
class Outcome:
    text: str
    job: tuple[str, int | None] | None = None
    # A card to show with the reply: the whole text after approval, the README after assembly.
    part: str = ""
    # Messages that come right after the reply, each with its own card (the check-list after the whole text).
    follow: list[ContentMessage] = field(default_factory=list)


LABELS = {"write_style": "Подбираю стиль проекта", "write_summary": "Пишу название и аннотацию", "write_support": "Готовлю теорию и введение",
          "write_theory_plan": "Думаю, как подать теорию", "find_excerpt": "Ищу выдержку из первоисточника",
          "run_review": "Критики читают текст", "find_links": "Ищу ссылки для теории"}


# Paid writing jobs: two at a time per person, a ceiling for the server (audit 30.09.2026: there was no limit).
JOBS_PER_OWNER = 2
JOBS_ALL = 6
job_owners: dict[str, str] = {}


def room(owner: str) -> None:
    if sum(1 for draft_id in jobs if job_owners.get(draft_id) == owner) >= JOBS_PER_OWNER:
        raise HTTPException(429, "У вас уже пишутся два текста. Дождитесь, пока один из них будет готов.")
    if len(jobs) >= JOBS_ALL:
        raise HTTPException(429, "Сервер сейчас пишет много текстов. Попробуйте через несколько минут.")


def start_job(draft: ContentDraft, action: str, number: int | None) -> Outcome:
    room(draft.owner_id)
    draft.busy = (f"Пишу задание {number}" if action == "write_task" else f"Готовлю материалы к заданию {number}"
                  if action == "write_materials" else LABELS[action])
    return Outcome(draft.busy + "…", (action, number))


def approve(draft: ContentDraft, action: str, number: int | None) -> str:
    if action == "approve_style" and draft.style:
        if draft.style.stale():
            # The story of the other style, taken as it is: the methodologist's from now on. The reasons argued for
            # the other style and are not kept as reasons for this one.
            draft.style.written_for, draft.style.reason = draft.style.profile, ""
        draft.style.status = "approved"
        return f"Стиль согласован: «{STYLE_NAMES[draft.style.profile]}»."
    if action == "approve_summary" and draft.summary:
        draft.summary.status = "approved"
        return "Название и аннотация согласованы."
    if action == "approve_task" and number is not None and (task := draft.task(number)):
        task.status = "approved"
        first = draft.handoff.tasks[0].number == number and len(draft.handoff.tasks) > 1
        return f"Задание {number} согласовано" + (" — это образец для остальных." if first else ".")
    if action == "approve_materials" and number is not None:
        paths = task_materials(draft, number, "draft")
        for material in draft.materials:
            if material.path in paths:
                material.status = "approved"
        return f"Материалы к заданию {number} согласованы: {', '.join(paths)}."
    if action == "approve_theory_plan" and draft.theory_plan:
        if draft.theory_plan.stale():
            # Chosen by the methodologist without the assistant's reasons for it: what was argued and shown was of
            # another presentation and is not kept as said of this one.
            draft.theory_plan.written_for, draft.theory_plan.reason, draft.theory_plan.sample = draft.theory_plan.presentation, "", ""
        draft.theory_plan.status = "approved"
        return f"Подача теории согласована: {PRESENTATIONS[draft.theory_plan.presentation]}."
    if action == "approve_support":
        for found in (draft.theory, draft.frame):
            if found:
                found.status = "approved"
        for material in draft.materials:
            if material.status == "draft":
                material.status = "approved"
        return "Теория и введение согласованы."
    raise HTTPException(409, "Сейчас нечего согласовывать")


def perform(draft: ContentDraft, action: str, number: int | None = None) -> Outcome:
    """Do one step of the route. Approving a part goes straight on to writing the next one, as the chip promised."""
    if action == "apply_pending":
        message = latest_pending(draft)
        if not message:
            raise HTTPException(409, "Правок без решения нет")
        apply(draft, message)
        return Outcome("Применил правки: " + ", ".join(f"«{p.label}»" for p in message.patches) + ".")
    if action == "switch_style":
        if draft.style is None or draft.style.status != "draft":
            raise HTTPException(409, "Стиль уже согласован: смените его словами в чате")
        # The methodologist's choice from now on: the assistant writes the story anew for it and keeps the profile.
        # What stands on the card stays marked as written for the other style (Style.written_for) until it is: a job
        # that fails leaves a style the route knows is not written yet, and offers to write it (stages.next_step).
        draft.style.profile = other(draft.style.profile)
        draft.style.chosen_by = "chat"
        job = start_job(draft, "write_style", None)
        return Outcome(f"Меняю стиль на «{STYLE_NAMES[draft.style.profile]}» и пишу сюжет заново…", job.job)
    if action in PLAN_ACTIONS:
        if draft.theory_plan is None or draft.theory_plan.status != "draft":
            raise HTTPException(409, "Подача теории уже согласована: смените её словами в чате")
        # No model is asked: the choice is the methodologist's, and what the assistant argued and showed stays marked
        # as written for the other presentation (TheoryPlan.written_for). An example for this one is a chip away.
        draft.theory_plan.presentation = PLAN_ACTIONS[action]  # type: ignore[assignment]
        draft.theory_plan.chosen_by = "chat"
        return Outcome(f"Теорию подаём так: {PRESENTATIONS[draft.theory_plan.presentation]}." + chat_hint(next_step(draft)))
    if action in TITLE_ACTIONS:
        summary, place = draft.summary, TITLE_ACTIONS[action]
        if summary is None or summary.status != "draft" or place >= len(summary.title_options):
            raise HTTPException(409, "Название уже согласовано: смените его словами в чате")
        # No model is asked: the title is one the assistant offered, the choice is the methodologist's.
        summary.title = summary.title_options[place]
        return Outcome(f"Название — «{summary.title}»." + chat_hint(next_step(draft)))
    if action in WRITE_ACTIONS:
        return start_job(draft, action, number)
    if action.startswith("approve_"):
        text = approve(draft, action, number)
        if action == "approve_support":
            checklist = render.ensure_checklist(draft)
            note = ("Дисклеймера пока нет: пришлите его текст в чат («дисклеймер чек-листа: …»)." if not checklist.disclaimer.strip()
                    else "Дисклеймер стоит последним пунктом правил проверки.")
            return Outcome(f"{text} Весь материал целиком — ниже. Прочитайте его перед проверкой критиками.", part="full",
                           follow=[ContentMessage(role="assistant", part="checklist",
                                                  text=f"Чек-лист проверки: вопросы — пункты для пира из заданий, вступление и правила — "
                                                       f"стандарт школы. Всё можно поправить в чате. {note}")])
        follow = next_step(draft)
        if follow["action"] in WRITE_ACTIONS and follow["action"] not in {"run_review", "find_links", "find_excerpt"}:
            job = start_job(draft, str(follow["action"]), follow["number"] if isinstance(follow["number"], int) else None)
            return Outcome(f"{text} {job.text}", job.job)
        return Outcome(f"{text} {follow['title']}." if follow["title"] else text)
    if action == "assemble":
        blocks = blocking(draft)
        if blocks:
            raise HTTPException(409, "Перенос остановлен: " + " ".join(c.message for c in blocks))
        draft.assembled_at = datetime.now(timezone.utc).isoformat()
        return Outcome("Перенёс согласованный текст в README.md и check-list.yml по шаблону школы, материалы — в папку materials/.",
                       part="readme")
    raise HTTPException(409, "Сейчас нечего запускать")


def chat_hint(step: dict[str, object]) -> str:
    """What the methodologist may do next, said after a step that starts nothing by itself."""
    return f" Дальше — «{step['button']}»." if step["button"] else ""


def step_matches(draft: ContentDraft, action: str, number: int | None) -> bool:
    """The chip is one of the steps offered now: the main one or an alternative, with the same task number."""
    step = next_step(draft, [c.message for c in blocking(draft)])
    offered = [(step["action"], step["number"])]
    alternatives = step["alternatives"]
    if isinstance(alternatives, list):
        offered += [(a["action"], None) for a in alternatives]
    return any(a == action and (n is None or n == number) for a, n in offered)


def button(draft: ContentDraft, action: str) -> str:
    step = next_step(draft, [c.message for c in blocking(draft)])
    if step["action"] == action:
        return str(step["button"])
    alternatives = step["alternatives"]
    return next((str(a["button"]) for a in alternatives if a["action"] == action), action) if isinstance(alternatives, list) else action


# ---------- background writers ----------

def metrics_fields(metrics: CallMetrics) -> dict[str, object]:
    return {"model": metrics.model, "input_tokens": metrics.input_tokens, "output_tokens": metrics.output_tokens}


def targets(draft: ContentDraft, action: str, number: int | None) -> list[str]:
    if action == "write_style":
        return ["style"] if draft.style is None or draft.style.status == "draft" else []
    if action == "write_summary":
        return ["summary"] if draft.summary is None or draft.summary.status == "draft" else []
    if action == "write_theory_plan":
        return ["theory_plan"] if draft.theory_plan is None or draft.theory_plan.status == "draft" else []
    if action == "write_task" and number is not None:
        task = draft.task(number)
        return [f"task:{number}"] if task is None or task.status == "draft" else []
    if action == "write_materials" and number is not None:
        return [f"material:{p}" for p in task_materials(draft, number, "planned")]
    if action == "write_support":
        return support_missing(draft)
    if action == "find_links":
        return ["links"]
    if action == "find_excerpt":
        return ["excerpt"]
    return ["review"]


def given_style(draft: ContentDraft) -> str:
    """The profile the methodologist named — in the chat, else in the form; "" when the choice is the assistant's."""
    if draft.style and draft.style.chosen_by != "assistant":
        return draft.style.profile
    return draft.handoff.style_profile if draft.handoff.style_profile in STYLE_NAMES else ""


def style_text(draft: ContentDraft) -> tuple[str, str, list[str]] | None:
    """What the methodologist may have changed in the style while the assistant was writing it."""
    return (draft.style.profile, draft.style.story, draft.style.questions) if draft.style else None


async def write_style(draft_id: str, writer: Writer) -> None:
    """The assistant proposes how the project is told and what its story is. A profile the methodologist named is
    kept whatever the model answers: the model gives its reasons and the story for it.

    The chat stays open while the assistant writes. If the style was changed there meanwhile — another profile, an
    answer written into the story — what the model wrote is of a style that is no more: it is asked once again, from
    what stands now (independent review 06.10.2026: a legend was stored under «выбрали вы» with the text of a quest,
    and a story the methodologist had just corrected was overwritten). Changed during the second call too, the style
    is left as the methodologist made it, and they are told so."""
    spent = [0, 0]
    for last in (False, True):
        seen = store.load(draft_id)
        given, before = given_style(seen), style_text(seen)
        written, metrics = await writer.style(seen, given)
        spent = [spent[0] + (metrics.input_tokens or 0), spent[1] + (metrics.output_tokens or 0)]
        async with store.lock(draft_id):
            draft = store.load(draft_id)
            if draft.style is not None and draft.style.status != "draft":
                return
            if style_text(draft) != before:
                if not last:
                    continue
                draft.messages.append(ContentMessage(role="system", text="Стиль меняли, пока я писал сюжет: оставил его таким, каким вы его сделали. "
                                                                         "Если сюжет нужно написать заново — кнопка ниже."))
                store.save(draft)
                return
            chosen_by = draft.style.chosen_by if draft.style and draft.style.chosen_by != "assistant" else "form" if given else "assistant"
            draft.style = Style(**{**written.model_dump(), "profile": given or written.profile}, chosen_by=chosen_by)
            name = STYLE_NAMES[draft.style.profile]
            text = (f"Стиль «{name}» выбрали вы: вот как я вижу в нём этот проект." if given
                    else f"Предлагаю рассказать проект в стиле «{name}» — ниже почему и какой сюжет.")
            if draft.style.questions:
                text += " Есть вопросы по сюжету."
            # Both calls are in the count when the model was asked twice.
            draft.messages.append(ContentMessage(role="assistant", part="style", text=text, model=metrics.model, input_tokens=spent[0], output_tokens=spent[1],
                                                 notes=["Стиль меняли, пока я писал: спросил модель ещё раз, от того, что стало."] if last else []))
            store.save(draft)
            return


def plan_text(draft: ContentDraft) -> tuple[str, str, str, list[str]] | None:
    """What the methodologist may have changed in the plan of the theory while the assistant was writing it."""
    plan = draft.theory_plan
    return (plan.presentation, plan.internet, plan.english, plan.questions) if plan else None


async def write_theory_plan(draft_id: str, writer: Writer) -> None:
    """The assistant proposes how the theory is given, says why and shows a short sample. A presentation the
    methodologist chose is kept whatever the model answers. If the plan was changed in the chat while the model was
    writing, what the methodologist set stands and the answer is left out: it was written from what is no more."""
    seen = store.load(draft_id)
    given = seen.theory_plan.presentation if seen.theory_plan and seen.theory_plan.chosen_by == "chat" else ""
    before = plan_text(seen)
    written, metrics = await writer.theory_plan(seen, given)
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        if draft.theory_plan is not None and draft.theory_plan.status != "draft":
            return
        if plan_text(draft) != before:
            draft.messages.append(ContentMessage(role="system", text="Подачу теории меняли, пока я писал предложение: оставил её такой, какой вы её "
                                                                     "сделали. Пример для неё — кнопкой ниже."))
            store.save(draft)
            return
        kept = {"internet": draft.theory_plan.internet, "english": draft.theory_plan.english} if draft.theory_plan and given else {}
        draft.theory_plan = TheoryPlan(**{**written.model_dump(), **kept, "presentation": given or written.presentation},
                                       chosen_by="chat" if given else "assistant")
        name = PRESENTATIONS[draft.theory_plan.presentation]
        text = (f"Подачу теории выбрали вы — {name}: вот как это будет выглядеть." if given
                else f"Предлагаю подать теорию так: {name}. Ниже — почему и как это будет выглядеть.")
        if draft.theory_plan.questions:
            text += " Есть вопросы."
        draft.messages.append(ContentMessage(role="assistant", part="theory_plan", text=text, **metrics_fields(metrics)))
        store.save(draft)


# The quotes a model wraps a title in, by the one that opens them.
WRAPPED = {"«": "»", '"': '"', "'": "'", "“": "”", "„": "“”"}


def bare(title: str) -> str:
    """A title without the quotes around the whole of it and the full stop after it — «Память». or «Память.» — which a
    model adds and a heading does not carry. Quotes inside the title («Проект «Память») and «?», «!», «…» stay."""
    clean = title.strip()
    while True:
        cut = clean.rstrip(". ")
        if len(cut) >= 2 and cut[0] in WRAPPED and cut[-1] in WRAPPED[cut[0]] and cut[0] not in cut[1:-1]:
            cut = cut[1:-1].strip()
        if cut == clean:
            return clean
        clean = cut


def titles(given: list[str]) -> list[str]:
    """The titles to choose from, as the answer gave them: bare, each once, the first three."""
    kept: list[str] = []
    for title in map(bare, given):
        if len(title) >= 3 and title.casefold() not in {one.casefold() for one in kept}:
            kept.append(title)
    return kept[:TITLE_CHOICES]


async def write_summary(draft_id: str, writer: Writer) -> None:
    written, metrics = await writer.summary(store.load(draft_id))
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        if draft.summary is None or draft.summary.status == "draft":
            text = "Название и аннотация."
            if isinstance(written, TitledSummaryDraft):
                # The first is the one the assistant thinks best: it is put in, the rest are a chip or a word away.
                offered = titles(written.titles) or written.titles[:1]
                draft.summary = Summary(title=offered[0], annotation=written.annotation, title_options=offered if len(offered) > 1 else [])
                if len(offered) > 1:
                    text += (" Варианты названия: " + ", ".join(f"«{title}»" for title in offered) +
                             ". Поставил первый; другой — кнопкой под чатом, свой — словами.")
            else:
                draft.summary = Summary(**written.model_dump())
            draft.messages.append(ContentMessage(role="assistant", part="summary", text=text, **metrics_fields(metrics)))
        store.save(draft)


async def write_task(draft_id: str, number: int, writer: Writer) -> None:
    seen = store.load(draft_id)
    # The style the writer was told to write in: the one in force when it was asked, whatever is changed meanwhile.
    asked_in = seen.style.profile if seen.style else ""
    written, metrics = await writer.task(seen, number)
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        existing = draft.task(number)
        if existing is None or existing.status == "draft":
            if existing:
                draft.tasks.remove(existing)
            # A line of the examples that is no example («ввод → вывод») is left out and named: one wrong line does not
            # cost the answer, and nothing reaches the README that the table could not show.
            examples, not_examples = sorted_examples(getattr(written, "examples", []))
            draft.tasks.append(TaskContent(number=number, **{**written.model_dump(exclude={"materials"}), "examples": examples},
                                           written_in=asked_in))
            draft.tasks.sort(key=lambda t: t.number)
            added: list[str] = []
            dropped: list[str] = []
            for need in written.materials:
                material = draft.material(need.path)
                if material is None and len(added) >= MATERIALS_PER_TASK:
                    dropped.append(need.path)
                elif material is None:
                    draft.materials.append(Material(**need.model_dump(), tasks=[number]))
                    added.append(need.path)
                elif number not in material.tasks:
                    material.tasks.append(number)
            text = f"Задание {number} «{written.title}» и почему оно такое."
            if added:
                text += " Понадобятся материалы: " + ", ".join(added) + " — подготовлю сразу после согласования задания."
            if dropped:
                text += (f" Модель просила ещё {', '.join(dropped)} — не добавил: больше {MATERIALS_PER_TASK} новых файлов на задание "
                         "обычно значит справки, которые повторяют теорию. Нужен файл — попросите в чате.")
            if not_examples:
                text += (" В примеры не взял строки, написанные не как «ввод → вывод»: " + "; ".join(f"«{line[:80]}»" for line in not_examples)
                         + ". Нужны — пришлите их в чат в этом виде.")
            draft.messages.append(ContentMessage(role="assistant", part=f"task:{number}", text=text, **metrics_fields(metrics)))
        store.save(draft)


async def write_theory(draft_id: str, writer: Writer) -> None:
    """Short sections by the writer, then links by the research agent: a link enters the text only if code opened it."""
    seen = store.load(draft_id)
    written, metrics = await writer.theory(seen)
    valid = {t.number for t in seen.handoff.tasks}
    theory = Theory(topics=[
        TheoryTopic(**{**t.model_dump(), "tasks": [n for n in t.tasks if n in valid] or sorted(valid)[:1]}, number=i)
        for i, t in enumerate(written.topics, 1)])
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        if draft.theory is not None and draft.theory.status == "approved":
            return
        draft.theory = theory
        store.save(draft)
    # Without the internet the learners cannot follow a link: none is looked for (the plan of the theory, content 0.6).
    found = await search_links(draft_id, theory.topics, writer) if seen.online() else research.Found()
    wanted = seen.theory_plan is not None and seen.theory_plan.english == "yes"
    quoted = await search_excerpt(draft_id, theory.topics, writer) if wanted else research.FoundExcerpt()
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        if draft.theory is None or draft.theory.status == "approved":
            return
        # Links go into the stored topics: an edit made while the search was running is kept.
        for topic in draft.theory.topics:
            if not topic.links:
                topic.links = found.links.get(topic.number, [])
        draft.theory.note = found.note
        if quoted.excerpt and draft.theory.topic(quoted.excerpt.topic):
            draft.theory.excerpt = quoted.excerpt
        draft.sources = list(dict.fromkeys([*draft.sources, *found.live, *quoted.live]))
        count = sum(len(t.links) for t in draft.theory.topics)
        text = f"Теоретический блок: разделов — {len(draft.theory.topics)}, ссылок — {count}, все из страниц, которые открылись."
        if not seen.online():
            text = f"Теоретический блок: разделов — {len(draft.theory.topics)}. Ссылки не искал: у участников нет интернета."
        if seen.presentation() != "chapter":
            text += f" Подача — {PRESENTATIONS[seen.presentation()]}."
        if draft.theory.excerpt:
            text += f" Выдержка из первоисточника — в разделе {draft.theory.excerpt.topic}: она стоит на странице дословно."
        models = list(dict.fromkeys([*found.models, *quoted.models]))
        draft.messages.append(ContentMessage(role="assistant", part="theory", text=" ".join(x for x in (text, found.note, quoted.note) if x),
                                             **metrics_fields(metrics), notes=[f"Поиск: {', '.join(models)}"] if models else []))
        store.save(draft)


async def search_links(draft_id: str, topics: list[TheoryTopic], writer: Writer) -> research.Found:
    """The research agent; a failure of the search is a note, not a failure of the theory."""
    try:
        return await research.find_links(writer, store.load(draft_id), topics, date.today())
    except ModelUnavailable as error:
        return research.Found(note=f"Ссылки не найдены: {error}")


async def search_excerpt(draft_id: str, topics: list[TheoryTopic], writer: Writer) -> research.FoundExcerpt:
    """The excerpt of a primary source; a failure of the search is a note, not a failure of the theory."""
    try:
        return await research.find_excerpt(writer, store.load(draft_id), topics, date.today())
    except ModelUnavailable as error:
        return research.FoundExcerpt(note=f"Выдержку из первоисточника не взял: {error}")


async def find_new_excerpt(draft_id: str, writer: Writer) -> None:
    """On request — the chip or the words of the methodologist: search for an excerpt of a primary source again. The
    one found takes the place of the one that stood; nothing found leaves the text as it was, and says why."""
    seen = store.load(draft_id)
    if seen.theory is None:
        return
    quoted = await search_excerpt(draft_id, seen.theory.topics, writer)
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        if draft.theory is None:
            return
        draft.sources = list(dict.fromkeys([*draft.sources, *quoted.live]))
        notes = [f"Поиск: {', '.join(dict.fromkeys(quoted.models))}"] if quoted.models else []
        if quoted.excerpt and draft.theory.topic(quoted.excerpt.topic):
            draft.theory.excerpt = quoted.excerpt
            draft.messages.append(ContentMessage(
                role="assistant", part="theory", notes=notes,
                text=f"Нашёл выдержку из первоисточника — в разделе {quoted.excerpt.topic}: она стоит на странице дословно."))
        else:
            draft.messages.append(ContentMessage(role="assistant", notes=notes,
                                                 text=quoted.note or "Выдержку из первоисточника не взял: подходящей страницы не нашлось."))
        store.save(draft)


async def find_more_links(draft_id: str, writer: Writer) -> None:
    """On request from the chat: search again and propose the links as patches — the methodologist applies them."""
    draft = store.load(draft_id)
    if draft.theory is None or not draft.online():
        return
    wanted = [t for t in draft.theory.topics if len(t.links) < 2] or draft.theory.topics
    found = await search_links(draft_id, wanted, writer)
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        draft.sources = list(dict.fromkeys([*draft.sources, *found.live]))
        changes = []
        for topic in draft.theory.topics if draft.theory else []:
            new = [link for link in found.links.get(topic.number, []) if link.url not in {x.url for x in topic.links}]
            if new:
                changes.append(ContentChange(part=f"topic:{topic.number}", field="links", value=[x.line() for x in topic.links + new],
                                             reason=f"Найдено поиском и открыто кодом: {len(new)}"))
        patches, notes = build_patches(draft, changes)
        # The search's proposal replaces a chat proposal still waiting on the same links: both cannot be applied.
        replaced = supersede(draft.messages, {(p.part, p.field) for p in patches})
        if replaced:
            notes.append(f"Предыдущее предложение по тем же ссылкам ({replaced}) заменено этим.")
        text = (f"Нашёл ссылки для разделов теории: {len(patches)}. Примените или отклоните." if patches
                else "Новых подходящих ссылок не нашёл.") + (f" {found.note}" if found.note else "")
        draft.messages.append(ContentMessage(role="assistant", text=text, patches=patches, status="pending" if patches else None,
                                             notes=[*notes, *([f"Поиск: {', '.join(found.models)}"] if found.models else [])]))
        store.save(draft)


async def write_frame(draft_id: str, writer: Writer) -> None:
    written, metrics = await writer.frame(store.load(draft_id))
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        if draft.frame is None or draft.frame.status == "draft":
            draft.frame = Frame(**written.model_dump())
            draft.messages.append(ContentMessage(role="assistant", part="frame", text="Введение, ситуация и вступление к практике.",
                                                 **metrics_fields(metrics)))
        store.save(draft)


async def write_material(draft_id: str, path: str, writer: Writer) -> None:
    material = store.load(draft_id).material(path)
    if material and material.target_words:
        await write_corpus(draft_id, path, writer)
        return
    written, metrics = await writer.material(store.load(draft_id), path)
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        material = draft.material(path)
        if material and material.status == "planned":
            material.content, material.note, material.status = written.content, written.note, "draft"
            draft.messages.append(ContentMessage(role="assistant", part=f"material:{path}", text=f"Материал {path}. {written.note}".strip(),
                                                 **metrics_fields(metrics)))
        store.save(draft)


async def write_corpus(draft_id: str, path: str, writer: Writer) -> None:
    """Checkpoint every bounded call; retry resumes it without regenerating completed batches."""
    if not path.lower().endswith((".txt", ".md")):
        raise ModelUnavailable("Объём в словах поддерживается для TXT/Markdown. Для таблиц задайте число строк в требованиях.")
    seen = store.load(draft_id)
    initial = seen.material(path)
    assert initial is not None
    key = corpus.fingerprint(material_payload(seen, path))
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        material = draft.material(path)
        assert material is not None
        if material.generation_key != key:
            material.generation_key, material.generation_parts = key, []
        store.save(draft)
    for _ in range(50):
        seen = store.load(draft_id)
        material = seen.material(path)
        if material is None or material.status != "planned":
            return
        if corpus.fingerprint(material_payload(seen, path)) != key:
            raise ModelUnavailable("Требования к корпусу изменились во время подготовки. Повторите с текущими требованиями.")
        text = corpus.merge(material.generation_parts)
        count = corpus.word_count(text)
        if count >= material.target_words * 0.95:
            if count > material.target_words * 1.15:
                raise ModelUnavailable("Корпус вышел за согласованный диапазон объёма. Сохранённые части доступны для пересмотра.")
            async with store.lock(draft_id):
                draft = store.load(draft_id)
                current = draft.material(path)
                if current is None or current.status != "planned" or corpus.fingerprint(material_payload(draft, path)) != key:
                    return
                current.content, current.status = text, "draft"
                current.note = f"Синтетический корпус: {count} слов, цель {current.target_words}; частей {len(current.generation_parts)}. Требуется содержательная проверка."
                draft.messages.append(ContentMessage(role="assistant", part=f"material:{path}", text=current.note))
                store.save(draft)
            return
        size = min(800, material.target_words - count)
        if len(material.generation_parts) >= 50:
            raise ModelUnavailable("Достигнут предел 50 частей корпуса. Подготовленные части сохранены.")
        written, metrics = await writer.material_batch(seen, path, size, len(material.generation_parts) + 1)
        if corpus.word_count(written.content) < size * 0.5 or corpus.duplicate_blocks(corpus.merge([*material.generation_parts, written.content])):
            raise ModelUnavailable("Часть корпуса слишком короткая или повторяет целые фрагменты. Готовые части сохранены; повторите подготовку.")
        async with store.lock(draft_id):
            draft = store.load(draft_id)
            current = draft.material(path)
            if current is None or current.status != "planned" or corpus.fingerprint(material_payload(draft, path)) != key:
                return
            current.generation_parts.append(written.content)
            draft.messages.append(ContentMessage(role="assistant", text=f"Корпус {path}: сохранена часть {len(current.generation_parts)}; "
                                                 f"слов {corpus.word_count(corpus.merge(current.generation_parts))} из примерно {current.target_words}.",
                                                 **metrics_fields(metrics)))
            store.save(draft)
    raise ModelUnavailable("Достигнут предел 50 частей корпуса. Подготовленные части сохранены.")


async def run_review(draft_id: str, writer: Writer) -> None:
    """Two critics, no scores: the methodical one on the reviewer model, the factual one on the search model."""
    seen = store.load(draft_id)
    ids = {str(p["id"]) for p in parts(seen)}
    report, metrics = await writer.critic(seen)
    findings = [Finding(number=0, source="method", part=f.part if f.part in ids else "", severity=f.severity,
                        message=f.message, suggestion=f.suggestion) for f in report.findings]
    models, notes, facts_summary = [metrics.model], [], ""
    urls: list[str] = []
    try:
        facts = await research.check_facts(writer, seen, facts_payload(seen, date.today()))
        models += facts.models
        urls = [page.url for page in facts.pages]
        report_facts = facts.report
        facts_summary = report_facts.summary if report_facts else ""
        for item in report_facts.items if report_facts else []:
            if item.status == "current":
                continue
            kept, _ = pick(urls, item.source_numbers)
            # A verdict without an opened source is advice at most.
            findings.append(Finding(number=0, source="facts", part=item.part if item.part in ids else "",
                                    severity="important" if item.status == "outdated" and kept else "advice",
                                    message=f"{item.topic}: {item.finding}", suggestion=item.suggestion, sources=kept))
    except ModelUnavailable as error:
        notes.append(f"Фактическая проверка не выполнена: {error}")
    for i, finding in enumerate(findings, 1):
        finding.number = i
    review = Review(digest=seen.digest(), summary=report.summary, facts_summary=facts_summary, findings=findings, models=models, notes=notes)
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        draft.review = review
        draft.sources = list(dict.fromkeys([*draft.sources, *urls]))
        important = sum(1 for f in findings if f.severity == "important")
        draft.messages.append(ContentMessage(role="assistant", part="review", **metrics_fields(metrics),
                                             text=f"Проверка критиков: важных замечаний {important}, советов {len(findings) - important}."))
        store.save(draft)


async def write_part(draft_id: str, target: str, writer: Writer) -> None:
    kind, _, key = target.partition(":")
    if kind == "style":
        await write_style(draft_id, writer)
    elif kind == "summary":
        await write_summary(draft_id, writer)
    elif kind == "theory_plan":
        await write_theory_plan(draft_id, writer)
    elif kind == "task":
        await write_task(draft_id, int(key), writer)
    elif kind == "theory":
        await write_theory(draft_id, writer)
    elif kind == "frame":
        await write_frame(draft_id, writer)
    elif kind == "material":
        await write_material(draft_id, key, writer)
    elif kind == "links":
        await find_more_links(draft_id, writer)
    elif kind == "excerpt":
        await find_new_excerpt(draft_id, writer)
    else:
        await run_review(draft_id, writer)


async def prepare_bundle(draft_id: str, writer: Writer) -> None:
    """Build the whole draft without pretending a human approved its individual parts."""
    draft = store.load(draft_id)
    if draft.style is None:
        await write_style(draft_id, writer)
    if store.load(draft_id).summary is None:
        await write_summary(draft_id, writer)
    for task in draft.handoff.tasks:
        if store.load(draft_id).task(task.number) is None:
            await write_task(draft_id, task.number, writer)
        for material in list(store.load(draft_id).materials):
            if material.status == "planned" and task.number in material.tasks:
                await write_material(draft_id, material.path, writer)
    draft = store.load(draft_id)
    if draft.theory_plan is None and draft.plans_theory():
        await write_theory_plan(draft_id, writer)
    if store.load(draft_id).theory is None:
        await write_theory(draft_id, writer)
    if store.load(draft_id).frame is None:
        await write_frame(draft_id, writer)
    for material in list(store.load(draft_id).materials):
        if material.status == "planned":
            await write_material(draft_id, material.path, writer)
    # Reusing an unchanged bundle must not pay for another identical review.
    draft = store.load(draft_id)
    if draft.review is None or draft.review.digest != draft.digest():
        await run_review(draft_id, writer)
    async with store.lock(draft_id):
        draft = store.load(draft_id)
        draft.batch_prepared = True
        draft.messages.append(ContentMessage(role="assistant", part="full", text="Весь черновик подготовлен и проверен. "
                                             "Прочитайте комплект, внесите правки или согласуйте его целиком. Части пока не согласованы."))
        store.save(draft)


async def run_job(draft_id: str, action: str, number: int | None, writer: Writer) -> None:
    done = False
    try:
        if action == "prepare_draft":
            await prepare_bundle(draft_id, writer)
            done = True
            return
        for target in targets(store.load(draft_id), action, number):
            async with store.lock(draft_id):
                draft = store.load(draft_id)
                draft.busy = {"review": "Критики читают текст", "links": "Ищу ссылки: поиск, проверка страниц, отбор",
                              "theory_plan": LABELS["write_theory_plan"], "excerpt": LABELS["find_excerpt"]}.get(
                                  target, f"Пишу: {part_label(target).lower()}")
                store.save(draft)
            await write_part(draft_id, target, writer)
        done = True
    except ModelUnavailable as error:
        done = True  # set first: if telling about the error fails too, busy must still be cleared below
        async with store.lock(draft_id):
            draft = store.load(draft_id)
            draft.messages.append(ContentMessage(role="system", text=f"Не получилось: {error} Готовые части сохранены — повторите кнопкой ниже."))
            store.save(draft)
    except asyncio.CancelledError:
        raise
    except Exception:
        done = True
        logger.exception("content job failed: draft=%s action=%s", draft_id, action)
        async with store.lock(draft_id):
            draft = store.load(draft_id)
            draft.messages.append(ContentMessage(role="system", text="Внутренняя ошибка при подготовке. Готовые части сохранены — повторите кнопкой ниже."))
            store.save(draft)
    finally:
        # A stopped server leaves busy for recover() to clear with a note; any other exit clears it here.
        if done:
            async with store.lock(draft_id):
                draft = store.load(draft_id)
                draft.busy = ""
                # What the job rewrote may have made a waiting chat proposal stale: withdraw it rather than leave a dead end.
                expire_stale(draft)
                store.save(draft)


def launch(draft_id: str, job: tuple[str, int | None], writer_factory: Callable[[], Writer], owner: str = "") -> None:
    action, number = job
    # The calls of the job are written into the account of its draft (core/usage.py), whichever request started it.
    task = asyncio.create_task(run_job(draft_id, action, number, writer_factory()), context=usage.under(draft_id))
    jobs[draft_id], job_owners[draft_id] = task, owner

    def done(_: object) -> None:
        jobs.pop(draft_id, None)
        job_owners.pop(draft_id, None)

    task.add_done_callback(done)


def recover() -> None:
    """After a restart no job is alive: busy drafts get a note and their step is offered again."""
    for draft in store.drafts():
        if draft.busy and draft.id not in jobs:
            draft.messages.append(ContentMessage(role="system", text=f"Сервер перезапущен: «{draft.busy}» не завершено. Готовые части сохранены — продолжите кнопкой ниже."))
            draft.busy = ""
            store.save(draft)


async def stop_all() -> None:
    for task in jobs.values():
        task.cancel()
    if jobs:
        await asyncio.gather(*jobs.values(), return_exceptions=True)
