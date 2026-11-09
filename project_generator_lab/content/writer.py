"""Model calls of the content stage: one part per call, a compact JSON payload, prompts frozen in the draft.

Three kinds of model: the writer (every text), the reviewer (the methodical critic; another model when
PROJECT_LAB_REVIEWER_MODEL is set, so the text is not judged by its author) and the search model (links for
the theory and the factual critic). The writer never decides the route and never saves: flow.py does.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ..core.generation import GenerationSettings, writing
from ..core.llm import CallMetrics, ModelClient
from ..core.prompts import PromptStore
from ..core.search import SearchClient
from ..core.web import Page, open_pages
from .handoff import STYLE_NAMES
from .models import (
    LATER,
    NOTED,
    PRESENTATIONS,
    ContentDraft,
    ContentRevision,
    CorpusTaskDraft,
    CriticReport,
    ExcerptPick,
    FrameDraft,
    MaterialDraft,
    NotedTheoryDraft,
    PlainTaskDraft,
    StyleDraft,
    SummaryDraft,
    TaskDraft,
    TheoryDraft,
    TheoryPlanDraft,
    TitledSummaryDraft,
)
from .stages import next_step

T = TypeVar("T", bound=BaseModel)

# 0.4 (05.10.2026): a point for the peer may show the one right result of a task (prompts/task.md).
# 0.5 (06.10.2026): the project is told in a style the assistant proposes first — the quest of a bassin or a working
# legend (styles/, prompts/style.md); a task may show examples «ввод → вывод» and asks for the result of the work, not
# for a report on it; the points for the peer are two to four, with a command, an action before the one who checks and
# a value of their own. Decided by the methodologist on the templates of the bassin and the three runs of the intensive.
# 0.6 (06.10.2026): how the theory is given is a question the assistant asks before writing it (prompts/theory_plan.md):
# a short chapter in the README by default, or a summary to work from — in the README or in a file of materials/ — and
# whether the learners have the internet; a section may get a short excerpt of a primary source in English, found by
# the search and checked by code to stand on the page word for word (prompts/primary.md, excerpt.md); a quest marks
# the places of its pictures. A draft begun before holds none of these prompts and goes on as it began.
# 0.7 (07.10.2026): the files of the project are named by their paths from the root of the repository — a line of the
# common rules (prompts/system.md) and a check of what was written (checks.paths); the address of the repository comes
# from the plan (column «GitLab») and is not shown to the writers. A draft begun before is not checked for it.
# 0.8 (07.10.2026): what a material is for is written to the learner — «по этой таблице ты считаешь…» — because the
# README of a legend shows it in its table of materials (prompts/task.md); a check tells of a line written about the
# learner or about the role of the file instead (checks.voice). A draft begun before is not checked for it.
# 0.9 (07.10.2026): three titles to choose from instead of one — an image of the project's situation, what the learner
# does, a question the project answers — because the one title was always short and always the name of the topic: the
# prompt asked for «1–3 слова, отражающее суть» and let it lean on the title of the plan (prompts/summary.md, the
# line of the title in both styles). The first is taken, the others are a chip away (flow.TITLE_ACTIONS). A draft
# begun before is asked for one title, as its frozen prompt says.
# 0.10 (29.10.2026): a task is as large as its hours. A project of 6 h and one of 60 h were written alike: the writer
# of a task was given its hours and told nothing of them, and was told to keep the first task's volume. Now the prompt
# of a task measures the work by the hours (prompts/task.md), the other tasks come with theirs, a material's data are
# as large as the hours of its tasks (prompts/material.md), and the critic checks it (prompts/critic.md). A draft
# begun before is written as its frozen prompts say.
CONTENT_VERSION = "0.11"
PROMPTS = PromptStore(Path(__file__).parent / "prompts")
PROMPT_NAMES = ("system", "style", "summary", "task", "theory_plan", "theory", "sources", "links", "primary", "excerpt", "frame", "material",
                "critic", "facts", "facts_select", "revise")
# An excerpt is looked for in the first pages the search names, in this much of each.
READ_PAGES, READ_CHARS = 6, 20000
# What a style is, for the writer: the voice, the frame of the README and how each field of a task is written in it.
# Knowledge, not a task: it stands between the common rules and the prompt of the part being written.
STYLES = PromptStore(Path(__file__).parent / "styles")
# The calls that write or judge the learner's text: they read the style the project is told in.
STYLED = {"summary", "task", "theory_plan", "theory", "frame", "material", "critic", "revise"}

# What the chat may change in each kind of part, with the names the methodologist sees.
EDITABLE: dict[str, dict[str, str]] = {
    "style": {"profile": "Стиль подачи", "story": "Сюжет", "questions": "Вопросы по сюжету", "written_for": "Для какого стиля написан сюжет"},
    "summary": {"title": "Название", "annotation": "Аннотация"},
    "task": {"title": "Название", "context": "Ситуация и данные", "goal": "Цель", "steps": "Что нужно сделать",
             "expected": "Что должно получиться", "examples": "Примеры «ввод → вывод»", "constraints": "Ограничения и условия",
             "result": "Формат сдачи", "check": "Проверка", "transition": "Переход к следующему заданию",
             "peer_checks": "Пункты для пира", "written_in": "В каком стиле написано"},
    "theory_plan": {"presentation": "Как подаётся теория", "internet": "Интернет у участников", "english": "Выдержка из первоисточника на английском",
                    "questions": "Вопросы о подаче теории"},
    "theory": {"excerpt": "Выдержка из первоисточника"},
    "topic": {"title": "Название раздела", "text": "Текст", "notes": "Конспект", "question": "Вопрос к практике", "links": "Где изучить"},
    "frame": {"introduction": "Введение", "instruction": "Инструкция", "conclusion": "Заключение"},
    "material": {"content": "Содержимое", "purpose": "Зачем участнику", "contains": "Требования к материалу", "target_words": "Объём корпуса в словах"},
    "checklist": {"disclaimer": "Дисклеймер", "introduction": "Вступление к проверке", "guidelines": "Правила проверки"},
}
LIST_FIELDS = {"steps", "expected", "examples", "constraints", "peer_checks", "links", "instruction", "questions"}


def style_key(profile: str) -> str:
    return f"style_{profile}"


def snapshot() -> dict[str, str]:
    """The prompts and both styles, frozen with the draft: the style may be changed in the chat, and the other one has
    to be the text this draft began with too."""
    return PROMPTS.snapshot(*PROMPT_NAMES) | {style_key(name): STYLES.text(name) for name in STYLE_NAMES}


def layout_of(draft: ContentDraft) -> str:
    """The style the draft is told in now; "" before the styles."""
    return draft.style.profile if draft.style else ""


# What came with the question of how the theory is given (content 0.6): the part of the plan, the summary of a
# section and the excerpt. A draft begun before is not offered them — its frozen prompts say nothing of them.
PLANNED = {"theory_plan": None, "theory": None, "topic": {"notes"}}


def editable(draft: ContentDraft) -> dict[str, dict[str, str]]:
    """What the chat may change in this draft. A draft begun before the styles has no style to change and no examples
    or line of the check in its tasks: its template shows neither, and its prompts say nothing of them. One begun
    before the plan of the theory has no plan, no summaries of the sections and no excerpt."""
    found = {kind: dict(fields) for kind, fields in EDITABLE.items()}
    if not draft.since("0.11"):
        found["material"] = {key: value for key, value in found["material"].items() if key not in {"target_words", "contains"}}
    if not draft.plans_theory():
        found = {kind: {name: label for name, label in fields.items() if name not in (PLANNED.get(kind) or ())}
                 for kind, fields in found.items() if kind not in PLANNED or PLANNED[kind] is not None}
    if not draft.styled():
        found = {kind: {name: label for name, label in fields.items() if name not in LATER} for kind, fields in found.items() if kind != "style"}
    return found


def told(draft: ContentDraft) -> dict[str, object]:
    """The style of the project for a call, under the name the prompts know it by. Nothing for a draft without one: a
    draft begun before the styles sends what it always sent."""
    if draft.style is None:
        return {}
    style: dict[str, object] = {"profile": draft.style.profile, "name": STYLE_NAMES[draft.style.profile], "story": draft.style.story}
    if draft.style.stale():
        # The profile was changed and the story has not been written for it: the writer is told what it is reading.
        style["story_note"] = (f"Сюжет написан для стиля «{STYLE_NAMES.get(draft.style.written_for, draft.style.written_for)}»: возьми из него "
                               "факты и персонажей, а голос и вид текста — по выбранному стилю.")
    return {"style": style}


def dumps(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def project(draft: ContentDraft, reviewer: bool = False) -> dict[str, object]:
    """The approved concept as the calls see it. Files «for the reviewer» go to the critic only."""
    h = draft.handoff
    files = h.sources if reviewer else h.writer_sources()
    return {
        "title": h.title, "description": h.description, "learner_term": h.learner_term,
        "outcomes": [{"number": i, "text": text} for i, text in enumerate(h.outcomes, 1)],
        "program_outcomes": h.program_outcomes, "customer_requirements": h.customer_requirements,
        "audience": h.audience, "start_level": h.start_level, "start_skills": h.start_skills,
        "format": h.format, "group_size": h.group_size, "hours": h.hours, "tools": h.tools, "skills": h.skills,
        "storytelling_type": h.storytelling_type, "storytelling": h.storytelling, **told(draft),
        "student_result": h.student_result,
        "checking": h.checking, "constraints": h.constraints, "references": h.references, "notes": h.notes,
        "output_package": h.output_package, "presentation": {"diagrams": h.diagrams, "tables": h.tables, "formulas": h.formulas},
        "program_context": h.program_context, "program_summary": h.program_summary, "project_role": h.project_role,
        "prerequisites": h.prerequisites, "later_use": h.later_use, "out_of_scope": h.out_of_scope,
        "material_gaps": h.material_gaps, "decisions": h.decisions, "open_questions": h.open_questions,
        "review_notes": h.review_notes, "fixed_rules": h.rules, "freshness": h.freshness,
        "attached_files": [{"name": f.name, "role": f.role, "text": f.text, "cropped": f.cropped} for f in files],
    }


def brief(draft: ContentDraft) -> dict[str, object]:
    """A short project card for calls that do not need the whole concept."""
    h = draft.handoff
    return {"title": draft.summary.title if draft.summary else h.title, "learner_term": h.learner_term,
            "audience": h.audience, "start_level": h.start_level, "tools": h.tools, **told(draft),
            "outcomes": [{"number": i, "text": text} for i, text in enumerate(h.outcomes, 1)], "fixed_rules": h.rules}


def plan(draft: ContentDraft, number: int) -> dict[str, object]:
    task = draft.handoff.task(number)
    return {**task.model_dump(), "outcomes": draft.handoff.outcome_names(task.outcome_numbers)}


def task_text(draft: ContentDraft, number: int, rationale: bool = False) -> dict[str, object]:
    task = draft.task(number)
    assert task is not None
    text = task.model_dump(exclude={"status"} if rationale else {"status", "rationale"})
    # A draft begun before the styles sends what it always sent: it has none of the fields added with them.
    return text if draft.styled() else {key: value for key, value in text.items() if key not in LATER}


def style_payload(draft: ContentDraft, given: str) -> dict[str, object]:
    """What the assistant proposes a style from: the concept, the story of the series and the style already named."""
    return {"project": project(draft),
            "tasks": [{"number": t.number, "title": t.title, "action": t.action, "deliverable": t.deliverable, "optional": t.optional}
                      for t in draft.handoff.tasks],
            "profile_given": given, "series_story": draft.handoff.series_story,
            # What was proposed before, and for which style it was written: after a change of the profile that is the other one.
            "current": draft.style.model_dump(include={"profile", "reason", "story", "questions", "written_for"}) if draft.style else None,
            "profiles": {name: draft.prompt_snapshots[style_key(name)] for name in STYLE_NAMES}}


def plan_payload(draft: ContentDraft, given: str) -> dict[str, object]:
    """What the assistant proposes the presentation of the theory from: the concept, what each agreed task needs the
    learner to understand, and the presentation the methodologist has already named."""
    return {"project": project(draft),
            "tasks": [{"number": t.number, "title": t.title, "goal": t.goal, "needs": t.needs} for t in sorted(draft.tasks, key=lambda t: t.number)],
            "presentation_given": given,
            "current": draft.theory_plan.model_dump(include={"presentation", "internet", "english", "reason", "sample", "questions", "written_for"})
            if draft.theory_plan else None,
            "presentations": PRESENTATIONS}


def summary_payload(draft: ContentDraft) -> dict[str, object]:
    return {"project": project(draft),
            "tasks": [{"number": t.number, "title": t.title, "action": t.action, "deliverable": t.deliverable, "optional": t.optional}
                      for t in draft.handoff.tasks]}


def task_payload(draft: ContentDraft, number: int) -> dict[str, object]:
    first = draft.handoff.tasks[0].number
    exemplar = draft.task(first)
    assert draft.summary is not None
    return {
        "project": project(draft),
        "summary": draft.summary.text(),
        "planned_task": plan(draft, number),
        # With their hours since 0.10: a task twice as long is twice the work, and the writer sees the others' measure.
        "other_tasks": [{"number": t.number, "title": t.title, "action": t.action, "deliverable": t.deliverable,
                         **({"hours": t.hours} if draft.since("0.10") else {})}
                        for t in draft.handoff.tasks if t.number != number],
        # The accepted first task sets style and volume; it is shown only once approved, and never to itself. A task
        # written in the other style (the style was changed after it) is no model: «держись его стиля» would argue
        # with the style the writer is told to write in.
        "exemplar": task_text(draft, first, rationale=True)
        if exemplar and exemplar.status == "approved" and number != first and exemplar.written_in == layout_of(draft) else None,
        "written": [{"number": t.number, "title": t.title, "result": t.result} for t in draft.tasks if t.number != number],
        "materials": [m.model_dump(include={"path", "kind", "purpose", "contains", "tasks"}) for m in draft.materials],
    }


def theory_payload(draft: ContentDraft) -> dict[str, object]:
    payload: dict[str, object] = {"project": project(draft),
                                  "tasks": [{"number": t.number, "title": t.title, "goal": t.goal, "steps": t.steps, "needs": t.needs}
                                            for t in sorted(draft.tasks, key=lambda t: t.number)]}
    if draft.plans_theory():
        # Agreed or not yet proposed (the chapter by default): the writer is told which text it is writing.
        payload["plan"] = {"presentation": draft.presentation(), "internet": "yes" if draft.online() else "no"}
    return payload


def frame_payload(draft: ContentDraft) -> dict[str, object]:
    return {"project": project(draft),
            "summary": draft.summary.text() if draft.summary else None,
            "tasks": [{"number": t.number, "title": t.title, "goal": t.goal, "context": t.context, "result": t.result}
                      for t in sorted(draft.tasks, key=lambda t: t.number)],
            "theory_topics": [t.title for t in draft.theory.topics] if draft.theory else [],
            "materials": [m.model_dump(include={"path", "purpose"}) for m in draft.materials],
            "school_instruction": "Код сам добавит в «Инструкцию» правила школы: клонировать проект с GitLab, всё сдавать в src/, "
                                  "вести работу в ветке develop, не класть лишних файлов. Их не повторяй."}


def material_payload(draft: ContentDraft, path: str) -> dict[str, object]:
    material = draft.material(path)
    assert material is not None
    return {
        "project": brief(draft),
        "attached_files": project(draft)["attached_files"],
        "introduction": draft.frame.introduction if draft.frame else "",
        "material": material.model_dump(include={"path", "kind", "purpose", "contains", "not_contains"}
                                        | ({"target_words"} if material.target_words else set())),
        # The hours of each, since 0.10: the data are as large as the work they are for.
        "tasks": [{**task_text(draft, n), **({"hours": draft.handoff.task(n).hours} if draft.since("0.10") else {})}
                  for n in material.tasks if draft.task(n)],
        # Files already written, with their beginning: names, numbers and people must stay the same across files.
        "written_materials": [{"path": m.path, "purpose": m.purpose, "start": m.content[:1500]}
                              for m in draft.materials if m.path != path and m.content],
        "other_materials": [m.path for m in draft.materials if m.path != path and not m.content],
    }


def parts(draft: ContentDraft, rationale: bool = False) -> list[dict[str, object]]:
    """Every part as a model sees it: its id, status and fields. Ids are what patches and findings refer to."""
    found: list[dict[str, object]] = []
    if draft.style:
        # The reasons are shown only while they argue for the style in force.
        found.append({"id": "style", "status": draft.style.status,
                      "fields": draft.style.model_dump(include={"profile", "story", "questions", "written_for"} | (set() if draft.style.stale() else {"reason"}))})
    if draft.summary:
        # The titles proposed with the one in force, while there are any: «возьми второй» in the chat means one of them.
        offered = {"title_options": draft.summary.title_options} if draft.summary.title_options else {}
        found.append({"id": "summary", "status": draft.summary.status, "fields": {**draft.summary.text(), **offered}})
    for t in sorted(draft.tasks, key=lambda t: t.number):
        found.append({"id": f"task:{t.number}", "status": t.status, "fields": task_text(draft, t.number, rationale)})
    if draft.theory_plan:
        found.append({"id": "theory_plan", "status": draft.theory_plan.status,
                      "fields": draft.theory_plan.model_dump(include={"presentation", "internet", "english", "questions", "written_for"}
                                                             | (set() if draft.theory_plan.stale() else {"reason", "sample"}))})
    if draft.theory:
        # The summary of a section is shown where the draft may have one; the excerpt is a quotation, shown as it stands.
        noted = draft.plans_theory()
        found += [{"id": f"topic:{t.number}", "status": draft.theory.status, "tasks": t.tasks,
                   "fields": {"title": t.title, "text": t.text, **({"notes": t.notes} if noted else {}), "question": t.question,
                              "links": [link.line() for link in t.links]}}
                  for t in draft.theory.topics]
        if quoted := draft.theory.quoted():
            found.append({"id": "theory", "status": draft.theory.status,
                          "note": "Выдержка из первоисточника: дословная цитата страницы. Её можно убрать (пустое значение), но не переписать.",
                          "fields": {"excerpt": quoted.quote, "source": quoted.url}})
    if draft.frame:
        found.append({"id": "frame", "status": draft.frame.status, "fields": draft.frame.model_dump(exclude={"status"})})
    for m in draft.materials:
        found.append({"id": f"material:{m.path}", "status": m.status, "tasks": m.tasks, "kind": m.kind,
                      "fields": m.model_dump(include=({"content", "purpose", "not_contains"} if rationale else {"content", "purpose"})
                                              | ({"contains", "target_words"} if draft.since("0.11") else set()))})
    if draft.checklist and not rationale:
        # The critic does not review the check-list texts: they are the school's, and the disclaimer is the methodologists'.
        found.append({"id": "checklist", "status": "code", "note": "Вопросы чек-листа — пункты для пира из заданий (task:N · peer_checks).",
                      "fields": draft.checklist.model_dump()})
    return found


def critic_payload(draft: ContentDraft) -> dict[str, object]:
    return {"project": project(draft, reviewer=True),
            "planned_tasks": [plan(draft, t.number) for t in draft.handoff.tasks],
            "content": parts(draft, rationale=True)}


def facts_payload(draft: ContentDraft, today: date) -> dict[str, object]:
    return {"today": today.isoformat(), "tools": draft.handoff.tools,
            "topics": [{"part": f"topic:{t.number}", "title": t.title, "links": [link.url for link in t.links]}
                       for t in (draft.theory.topics if draft.theory else [])],
            "tasks": [{"part": f"task:{t.number}", "title": t.title, "steps": t.steps, "result": t.result} for t in draft.tasks],
            "materials": [{"part": f"material:{m.path}", "purpose": m.purpose} for m in draft.materials]}


def revise_payload(draft: ContentDraft, text: str, focus: str) -> dict[str, object]:
    history = [m for m in draft.messages if m.role != "system"][-9:-1]
    step = next_step(draft)
    return {
        "message": text,
        "focus": focus,
        "parts": parts(draft),
        "editable": editable(draft),
        "planned_tasks": [plan(draft, t.number) for t in draft.handoff.tasks],
        "project": brief(draft),
        "review": [f.model_dump(include={"number", "part", "message", "suggestion", "sources"}) for f in draft.review.findings] if draft.review else [],
        "known_links": draft.sources,
        "recent_messages": [{"role": m.role, "text": m.text[:1500], "status": m.status,
                             "changes": [{"part": p.part, "field": p.field} for p in m.patches]} for m in history],
        "computed_state": {"next_step": {k: step[k] for k in ("title", "text", "button")}},
    }


class Writer:
    def __init__(self, client: ModelClient, reviewer: ModelClient | None = None, search: SearchClient | None = None,
                 opener: Callable[[list[str]], Awaitable[list[Page]]] = open_pages,
                 reader: Callable[[list[str]], Awaitable[list[Page]]] | None = None) -> None:
        self.client, self.reviewer, self.search, self.opener = client, reviewer or client, search or SearchClient(), opener
        self.reader = reader

    async def open_pages(self, urls: list[str]) -> list[Page]:
        return await self.opener(urls)

    async def compose(self, draft: ContentDraft, schema: type[T], name: str, payload: dict[str, object]) -> tuple[T, CallMetrics]:
        """Only writing uses project sampling. Critics and extraction retain the provider defaults."""
        modern = draft.since("0.11")
        with writing(draft.handoff.generation if modern else GenerationSettings()):
            system = self.system(draft, name)
            if modern:
                system += "\n" + draft.handoff.generation.instruction()
            return await self.client.generate(schema, system, dumps(payload))

    async def read_pages(self, urls: list[str]) -> list[Page]:
        """Pages opened to be read, not only looked at: with enough of their text to take an excerpt from."""
        if self.reader is not None:
            return await self.reader(urls)
        return await open_pages(urls[:READ_PAGES], text_limit=READ_CHARS, prose=True)

    @staticmethod
    def system(draft: ContentDraft, name: str) -> str:
        """The common rules, the style the project is told in (for the calls that write or judge its text), the task."""
        style = [draft.prompt_snapshots[style_key(draft.style.profile)]] if draft.style and name in STYLED else []
        return "\n\n".join([draft.prompt_snapshots["system"], *style, draft.prompt_snapshots[name]])

    async def style(self, draft: ContentDraft, given: str) -> tuple[StyleDraft, CallMetrics]:
        return await self.compose(draft, StyleDraft, "style", style_payload(draft, given))

    async def summary(self, draft: ContentDraft) -> tuple[SummaryDraft | TitledSummaryDraft, CallMetrics]:
        # The schema goes to the model with the prompt: a draft begun before 0.9 is asked for the one title its
        # frozen prompt speaks of.
        schema: type[SummaryDraft] | type[TitledSummaryDraft] = TitledSummaryDraft if draft.since("0.9") else SummaryDraft
        return await self.compose(draft, schema, "summary", summary_payload(draft))

    async def task(self, draft: ContentDraft, number: int) -> tuple[TaskDraft | PlainTaskDraft, CallMetrics]:
        # The schema of the answer goes to the model with the prompt: a draft begun before the styles is asked for
        # the task it was always asked for, without the fields its frozen prompt says nothing of.
        schema: type[TaskDraft] | type[PlainTaskDraft] = TaskDraft if draft.styled() else PlainTaskDraft
        if draft.since("0.11"):
            schema = CorpusTaskDraft
        return await self.compose(draft, schema, "task", task_payload(draft, number))

    async def theory_plan(self, draft: ContentDraft, given: str) -> tuple[TheoryPlanDraft, CallMetrics]:
        return await self.client.generate(TheoryPlanDraft, self.system(draft, "theory_plan"), dumps(plan_payload(draft, given)))

    async def theory(self, draft: ContentDraft) -> tuple[TheoryDraft | NotedTheoryDraft, CallMetrics]:
        # The summaries are asked for only where the presentation shows them: the answer of a chapter is the answer
        # the writer was always asked for.
        schema: type[TheoryDraft] | type[NotedTheoryDraft] = NotedTheoryDraft if draft.presentation() in NOTED else TheoryDraft
        return await self.compose(draft, schema, "theory", theory_payload(draft))

    async def excerpt(self, draft: ContentDraft, payload: dict[str, object]) -> tuple[ExcerptPick, CallMetrics]:
        return await self.client.generate(ExcerptPick, self.system(draft, "excerpt"), dumps(payload))

    async def frame(self, draft: ContentDraft) -> tuple[FrameDraft, CallMetrics]:
        return await self.compose(draft, FrameDraft, "frame", frame_payload(draft))

    async def material(self, draft: ContentDraft, path: str) -> tuple[MaterialDraft, CallMetrics]:
        return await self.compose(draft, MaterialDraft, "material", material_payload(draft, path))

    async def material_batch(self, draft: ContentDraft, path: str, words: int, part: int) -> tuple[MaterialDraft, CallMetrics]:
        material = draft.material(path)
        assert material is not None
        payload = material_payload(draft, path)
        payload["batch"] = {"number": part, "target_words": words, "total_target_words": material.target_words,
                            "previous_ending": material.generation_parts[-1][-1500:] if material.generation_parts else ""}
        instruction = (self.system(draft, "material") + "\nСейчас напиши только следующую часть текстового корпуса. "
                       "Ориентируйся на batch.target_words, не меньше 80% этого объёма. Не повторяй прежние диалоги; "
                       "меняй темы и ситуации в рамках требований. Не пиши заголовок файла, итоги и служебные пояснения в content. "
                       "Каждый диалог заверши целиком. Это синтетические данные, не записи реальных людей.")
        with writing(draft.handoff.generation):
            return await self.client.generate(MaterialDraft, instruction, dumps(payload))

    async def critic(self, draft: ContentDraft) -> tuple[CriticReport, CallMetrics]:
        return await self.reviewer.generate(CriticReport, self.system(draft, "critic"), dumps(critic_payload(draft)))

    async def revise(self, draft: ContentDraft, text: str, focus: str) -> tuple[ContentRevision, CallMetrics]:
        return await self.client.generate(ContentRevision, self.system(draft, "revise"), dumps(revise_payload(draft, text, focus)))
