"""The only thing the content module knows about the concept: an approved concept as plain data.

Nothing from the concept package is imported here. The application passes a function that turns an
approved run into ConceptHandoff (project_generator_lab/bridge.py), so either module can be moved,
rewritten or replaced by another generator without touching the other one.
"""

import re
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from ..core.contracts import Contract
from ..core.generation import GenerationSettings


class HandoffUnavailable(Exception):
    """The concept exists but cannot be handed over yet, for example it is not approved."""


class TaskHours(Contract):
    reading: float = 0
    setup: float = 0
    doing: float = 0
    debugging: float = 0
    submission: float = 0


class PlannedTask(Contract):
    """One task as the approved concept planned it. The content stage writes it out, never replans it."""

    number: int = Field(ge=1)
    title: str
    action: str
    deliverable: str
    peer_check: str
    independent_choice: str = ""
    support: str = ""
    hours: float = Field(gt=0)
    outcome_numbers: list[int] = Field(default_factory=list)
    optional: bool = False
    workload: TaskHours | None = None
    # The block goals this task closes (their text) and its link to earlier and later projects, from the concept.
    block_goals: list[str] = Field(default_factory=list)
    program_link: str = ""


class SourceText(Contract):
    """A file the methodologist attached to the concept. reviewer files never reach the writers."""

    name: str
    role: Literal["reference", "requirement", "customer", "example", "starter", "reviewer"]
    text: str = ""
    total_chars: int = 0

    @property
    def cropped(self) -> bool:
        return len(self.text) < self.total_chars


# What a block of skills may be told of before the list is cut: the card is for reading, and a block with more wrong
# with it than this is wrong as a whole.
MAX_GAPS = 40


# The two ways a project is told (content/styles/), as the methodologist reads them.
STYLE_NAMES = {"quest": "квест", "legend": "рабочая легенда"}


class ConceptHandoff(Contract):
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    collaboration_mode: Literal["review", "assisted"] = "review"
    run_id: str
    approved_at: str
    concept_version: str
    title: str
    description: str
    # Outcomes by wording, numbered from 1 in this order; tasks refer to them by number.
    outcomes: list[str] = Field(min_length=1)
    program_outcomes: str = ""
    customer_requirements: str = ""
    audience: str = ""
    start_level: str = ""
    start_skills: str = ""
    learner_term: str = "участник"
    hours: float | None = None
    format: str = "individual"
    group_size: int | None = None
    skills: str = ""
    # The skills divided between the tasks of the concept, as the lines of the passport's skills tab (empty for a
    # concept of rules before 1.1 or a project without skills). Not for the writers and not for the learner's
    # repository: it is what the methodologist enters on the platform with the project.
    skill_table: list[list[str]] = Field(default_factory=list, max_length=200)
    # What stands between that block and the passport, said by the stage that made it (the bridge asks the concept's
    # own checks): a skill that reached no task, a part that does not add up. Empty — the block is ready as it is.
    skill_gaps: list[str] = Field(default_factory=list, max_length=MAX_GAPS)
    tools: str = ""
    storytelling_type: str = ""
    storytelling: str = ""
    # How the methodologist asked the text to be told (the form): "" — the assistant proposes; and the story that runs
    # through the projects of the series, in the methodologist's words. The style step of the draft starts from these.
    style_profile: str = ""
    series_story: str = ""
    # The address of the project's repository in GitLab, when the plan gives one: where the README and materials/ will
    # lie. The texts name the files of the project by their paths from its root, never by this address (checks.paths).
    repository: str = ""
    student_result: str = ""
    checking: str = ""
    output_package: str = ""
    constraints: str = ""
    references: str = ""
    notes: str = ""
    # Extra presentation in the learner's text; False does not forbid what an outcome needs.
    diagrams: bool = False
    tables: bool = False
    formulas: bool = False
    # Place in the curriculum, as text counted by code from the plan.
    program_context: str = ""
    program_summary: str = ""
    project_role: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    later_use: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    material_gaps: list[str] = Field(default_factory=list)
    tasks: list[PlannedTask] = Field(min_length=1)
    # Why the concept is as it is, what stayed open, what the reviewer noted and the methodologist accepted.
    decisions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    review_notes: list[str] = Field(default_factory=list)
    sources: list[SourceText] = Field(default_factory=list)
    # Rules written by methodologists (programme, platform), frozen as the concept used them.
    rules: dict[str, str] = Field(default_factory=dict)
    # Web-search findings the methodologist saw; hints for examples, not facts to copy.
    freshness: list[str] = Field(default_factory=list)

    def task(self, number: int) -> PlannedTask:
        return next(t for t in self.tasks if t.number == number)

    def outcome_names(self, numbers: list[int]) -> list[str]:
        return [self.outcomes[n - 1] for n in numbers if 1 <= n <= len(self.outcomes)]

    def writer_sources(self) -> list[SourceText]:
        """Files for the writers. Files «for the reviewer» may hold answers, so only the critic sees them."""
        return [s for s in self.sources if s.role != "reviewer"]


def skills_row(skills: str, skill_table: list[list[str]], skill_gaps: list[str]) -> dict[str, object]:
    """The skills of the project and whether the block the concept made of them is ready for the passport. Whether it
    is, is not judged here: the stage that made the block says what is wrong with it (`skill_gaps`), and this row is
    green only when it said nothing. Only the shares shown under a ready block are read from the table, by its layout."""
    names: dict[str, str] = {}
    for name in re.split(r"[\n;,]", skills):
        if name.strip():
            names.setdefault(" ".join(name.split()).casefold(), " ".join(name.split()))  # a name said twice is one, as first spelt
    count = len(names)
    if not count:
        return {"label": "Навыки", "value": "не заданы", "ok": False, "items": []}
    if not skill_table:
        # Also a draft begun before the block was handed over: it says what this handoff holds, not what the run has.
        return {"label": "Навыки", "value": f"{count}, блок развесовки по заданиям не передан", "ok": False, "items": list(names.values())}
    if skill_gaps:
        return {"label": "Навыки", "value": f"{count}, блок развесовки требует правки", "ok": False, "items": skill_gaps}
    head = skill_table[0]
    shares = [("бонус: " if line[1] != "all" else "") + f"{name} — {share}"
              for line in skill_table[1:] if len(line) == len(head) and line[1:2] in (["all"], ["Bonus all"])
              for name, share in zip(head[2:-1], line[2:-1], strict=True) if share]
    return {"label": "Навыки", "value": f"{count}, разложены по заданиям: блок развесовки для паспорта готов", "ok": True, "items": shares}


def repository_root(address: str) -> str:
    """The repository an address of the plan names, as «host/group/project»: without the scheme, «.git», the part
    after «/-/», what follows «?» and the full stop a cell may end with; an ssh address — «git@host:group/project.git»
    — names the same. Empty — the address is not one of a project: no host with a dot, or less than a group and a
    project after it (a host or a group alone would name every repository there)."""
    text = address.strip().rstrip(".,;:!?)»\"'")
    web = re.match(r"https?://(?:[^\s/@]+@)?([^\s?#]+)", text, re.I)
    ssh = re.match(r"(?:ssh://)?git@([^\s:/]+)[:/]([^\s?#]+)", text, re.I)
    if not web and not ssh:
        return ""
    whole = web[1] if web else f"{ssh[1]}/{ssh[2]}"  # type: ignore[index]
    root = re.sub(r"\.git$", "", whole.split("/-/")[0].rstrip("/"), flags=re.I).lower()
    host, _, path = root.partition("/")
    return root if "." in host and "/" in path.strip("/") else ""


def card(handoff: ConceptHandoff, styled: bool = True) -> list[dict[str, object]]:
    """What the generator received, counted by code: the methodologist sees a gap before anything is written on it.
    A draft begun before the styles received neither a style nor a story: its card does not say the assistant will
    propose one."""
    main = [t for t in handoff.tasks if not t.optional]
    hours = sum(t.hours for t in main)  # the project hours are filled by the main tasks; a bonus is extra, as in the concept checks
    story = handoff.series_story.strip()
    told: list[dict[str, object]] = [
        {"label": "Стиль подачи", "value": STYLE_NAMES.get(handoff.style_profile, "предложит ассистент"), "ok": True},
        {"label": "Сквозной сюжет серии", "value": "задан" if story else "нет", "ok": True, "items": [story] if story else []}] if styled else []
    # Said only where the plan gave an address: a draft that received none shows the card it always showed.
    # A value that is not the address of a project — the name of the repository, «будет позже» — is shown as such: the
    # check of the texts has nothing to look for by it.
    rule = "Файлы проекта в тексте называются путём от корня репозитория: materials/…, src/…"
    named = bool(repository_root(handoff.repository))
    told += [{"label": "Репозиторий проекта", "value": handoff.repository, "ok": named,
              "items": [rule] if named else ["Это не адрес репозитория (ждём вид https://…/группа/проект): проверка не сможет найти его в текстах.", rule]}
             ] if handoff.repository else []
    rows: list[dict[str, object]] = [
        {"label": "Задания", "value": f"{len(main)} основных" + (f" + {len(handoff.tasks) - len(main)} бонус" if len(main) < len(handoff.tasks) else "")
         + f" · {hours:g} ч" + (f" из {handoff.hours:g} ч" if handoff.hours else ""), "ok": True},
        {"label": "Образовательные результаты", "value": f"{len(handoff.outcomes)}", "items": handoff.outcomes, "ok": True},
        {"label": "Требования заказчика", "value": "есть" if handoff.customer_requirements else "не заданы", "ok": bool(handoff.customer_requirements)},
        {"label": "ОР программы", "value": "есть" if handoff.program_outcomes else "не заданы", "ok": bool(handoff.program_outcomes)},
        {"label": "Аудитория и уровень", "value": ", ".join(x for x in (handoff.audience, handoff.start_level) if x) or "не заданы",
         "ok": bool(handoff.audience)},
        {"label": "Инструменты", "value": handoff.tools or "не заданы", "ok": bool(handoff.tools)},
        skills_row(handoff.skills, handoff.skill_table, handoff.skill_gaps),
        {"label": "Сторителлинг", "value": handoff.storytelling_type or ("задан" if handoff.storytelling else "нет"), "ok": True},
        *told,
        {"label": "Место в учебном плане", "value": handoff.program_context.splitlines()[0] if handoff.program_context else "нет", "ok": bool(handoff.program_context)},
        {"label": "Приложенные файлы", "value": f"{len(handoff.sources)}" if handoff.sources else "нет", "ok": True,
         "items": [f"{s.name} · " + ("только проверяющему" if s.role == "reviewer" else
                                     f"передано {len(s.text):,} из {s.total_chars:,} знаков".replace(",", " ") if s.cropped else "целиком")
                   for s in handoff.sources]},
        {"label": "Обоснования замысла", "value": f"{len(handoff.decisions)}", "ok": True},
        {"label": "Вопросы замысла", "value": f"{len(handoff.open_questions)}", "items": handoff.open_questions, "ok": not handoff.open_questions},
        {"label": "Замечания проверяющего", "value": f"{len(handoff.review_notes)}", "items": handoff.review_notes, "ok": not handoff.review_notes},
        {"label": "Правила методистов", "value": ", ".join({"program_rules": "программа", "platform": "платформа"}.get(k, k) for k in handoff.rules) or "нет",
         "ok": bool(handoff.rules)},
        {"label": "Проверка актуальности", "value": f"{len(handoff.freshness)} находок" if handoff.freshness else "не запускалась", "ok": True},
    ]
    return rows


# Given an approved run id, returns its handoff; raises HandoffUnavailable or an HTTP 404 of the owner module.
HandoffSource = Callable[[str], ConceptHandoff]
