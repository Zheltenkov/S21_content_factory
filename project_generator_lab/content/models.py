"""Contracts of the content draft: what the models return, what is stored, what the chat shows.

The draft is built part by part in the chat, in this order: the style the project is told in, title and annotation,
tasks with the reasons behind them, theory with links, introduction and situation, materials. Then the whole text is
read and reviewed, and only at the end code pours it into the README template. Each part is a draft until the
methodologist accepts it; accepted parts are never rewritten by a model.
"""

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from ..core.contracts import STORED_SCHEMA, Contract
from ..core.intents import MessageKind
from .handoff import ConceptHandoff


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


Status = Literal["draft", "approved"]
# File contents keep their whitespace: indentation and the final newline matter in YAML, Python and CSV.
RawText = Annotated[str, StringConstraints(strip_whitespace=False)]
# Text formats only: the peer opens them anywhere, and code can check that they parse.
MATERIAL_FORMATS = {".csv", ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sql"}
SAFE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$")


def material_path(value: str) -> str:
    """A relative path inside materials/: latin letters, digits, _ . - and folders; a text format."""
    path = value.strip().replace("\\", "/").removeprefix("./").removeprefix("materials/")
    parts = PurePosixPath(path).parts
    if not path or not SAFE_PATH.match(path) or ".." in parts or len(parts) > 3:
        raise ValueError("путь в materials/: латиница, цифры, _ . - без пробелов, не выше трёх уровней")
    if PurePosixPath(path).suffix.lower() not in MATERIAL_FORMATS:
        raise ValueError("формат материала: " + ", ".join(sorted(MATERIAL_FORMATS)))
    return path


LIST_MARK = re.compile(r"^\s*(?:[-•*]|\d{1,2}[.)])\s+")


def list_items(values: list[str]) -> list[str]:
    """List items without their own bullets or numbers: the template numbers and bullets them once."""
    return [LIST_MARK.sub("", item).strip() for item in values if LIST_MARK.sub("", item).strip()]


# «ввод → вывод»: one line of the table of examples a task with one right result may show. The arrow of the stored
# line is «→»; where a line has none, the first arrow typed in letters is taken for it («3 5 -> 8»). A line that has «→»
# is cut at it and nowhere else: «p->next → 5» is the input «p->next».
ARROW = "→"
TYPED_ARROW = re.compile(r"⇒|->|=>|—>|–>")
# A new line inside a cell: an item of a list is one line (the chat edits a list line by line).
BREAK = "⏎"
# What stands in the table where the input, or the output, is nothing at all.
NO_INPUT, NO_OUTPUT = "пустой ввод", "пустой вывод"


def example_item(line: str) -> str | None:
    """A line as an example is stored: «ввод → вывод». None when it has no arrow. Said twice, it says the same: the
    stored line is read on every load, and what it reads as must not drift (independent review 06.10.2026: stripping
    list marks from the examples ate a leading «-» or «5.» of an input, again on every load)."""
    text = line.strip()
    if ARROW in text:
        given, shown = text.split(ARROW, 1)
    else:
        sides = TYPED_ARROW.split(text, maxsplit=1)
        if len(sides) != 2:
            return None
        given, shown = sides
    return f"{given.strip() or NO_INPUT} {ARROW} {shown.strip() or NO_OUTPUT}"


def sorted_examples(values: list[str]) -> tuple[list[str], list[str]]:
    """The lines that are examples, as they are stored, and the lines that are not, as they were written."""
    kept: list[str] = []
    dropped: list[str] = []
    for line in values:
        if line.strip():
            found = example_item(line)
            if found is None:
                dropped.append(line.strip())
            else:
                kept.append(found)
    return kept, dropped


def example_items(values: list[str]) -> list[str]:
    """Lines «ввод → вывод» as they are stored; a line with no arrow is refused in words."""
    kept, dropped = sorted_examples(values)
    if dropped:
        raise ValueError(f"пример «{dropped[0][:60]}» пишется как «ввод → вывод»")
    return kept


def example_cells(item: str) -> tuple[str, str]:
    """The two cells of a stored example, as the template puts them into the table."""
    given, _, shown = item.partition(f" {ARROW} ")
    return given, shown


# Where a picture is to stand: a line of its own, «[картинка: что на ней]». The generator draws nothing (decided
# 06.10.2026): the writer marks the place, the README shows the mark, the checks count the marks left, and a person
# puts the pictures in.
PICTURE = re.compile(r"^[ \t]*\[[ \t]*(?:картинка|рисунок|иллюстрация)[ \t]*[:—–][ \t]*([^\[\]]+?)[ \t]*\][ \t]*$", re.I)
FENCE = re.compile(r"^[ \t]*(```|~~~)")


def picture_marks(text: str) -> tuple[list[str], dict[int, str]]:
    """The lines of a text and, by the number of the line, what each mark of a picture says is on it. A mark is a line
    of its own outside a block of code: «[картинка: …]» typed as an example of input stays what was typed. The ends of
    lines are read whichever way they were written (independent review 06.10.2026: a text with CRLF showed no mark)."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    found: dict[int, str] = {}
    fence = ""
    for i, line in enumerate(lines):
        opened = FENCE.match(line)
        if opened and not fence:
            fence = opened.group(1)
        elif opened and opened.group(1) == fence:
            fence = ""
        elif not fence and (mark := PICTURE.match(line)):
            found[i] = mark.group(1).strip()
    return lines, found

Profile = Literal["quest", "legend"]


def other(profile: Profile) -> Profile:
    """The style a project is not told in: there are two."""
    return "legend" if profile == "quest" else "quest"


# ---------- what the models return ----------

class StyleDraft(Contract):
    """How the project is told, proposed by the assistant before anything is written. quest — the text quest of a
    bassin: a scene, a character, quests the story leads to. legend — one working legend at the start and plain tasks.
    What each of them is, is written in styles/: the writer reads the one that was chosen."""

    profile: Profile
    # Why this one, by the concept: the audience, the tools, the story given, the place in the programme.
    reason: str = Field(min_length=10, max_length=1200)
    # What the story is in this project: for a quest — the character, the scene and what came before it; for a legend
    # — the working situation in a few sentences. The writer keeps to it in every part.
    story: str = Field(default="", max_length=3000)
    # What the assistant has to ask before writing: what the story lacks. Empty when nothing does.
    questions: list[str] = Field(default_factory=list, max_length=4)

    @field_validator("questions")
    @classmethod
    def plain_items(cls, values: list[str]) -> list[str]:
        return list_items(values)


class SummaryDraft(Contract):
    title: str = Field(min_length=3, max_length=200)
    annotation: str = Field(min_length=20, max_length=3000)


# How many titles the methodologist is given to choose from (content 0.9).
TITLE_CHOICES = 3
Title = Annotated[str, Field(min_length=3, max_length=200)]


class TitledSummaryDraft(Contract):
    """What the writer of the title answers since 0.9: titles to choose from instead of the one title, which was
    always short and always the name of the topic (07.10.2026). The prompt asks for three, each found another way;
    the schema takes what a model may give instead — one more, one less — and code keeps the first three that differ
    (flow.titles): a step of the route must not fail over a count."""

    titles: list[Title] = Field(min_length=1, max_length=6)
    annotation: str = Field(min_length=20, max_length=3000)


class MaterialNeed(Contract):
    """A file the task needs in materials/. not_contains is internal: it guards against leaking the answer."""

    path: str
    kind: Literal["data", "template", "sample", "reference"]
    purpose: str = Field(min_length=3, max_length=600)
    contains: str = Field(min_length=3, max_length=1500)
    not_contains: str = Field(default="", max_length=800)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return material_path(value)


class SizedMaterialNeed(MaterialNeed):
    target_words: int = Field(default=0, ge=0, le=30000,
                              description="Согласованный объём TXT/MD-корпуса в словах; 0 — обычный материал. Большой корпус готовится частями.")


class Rationale(Contract):
    """Why the task is what it is, for the methodologist only. Written by the same model, so it explains the task;
    it does not prove it: critics compare the task with the concept, not with this text."""

    concept: str = Field(min_length=5, max_length=1200)
    evidence: str = Field(min_length=5, max_length=1200)
    choice: str = Field(min_length=3, max_length=800)
    peer: str = Field(min_length=5, max_length=800)
    links: str = Field(default="", max_length=800)
    assumptions: str = Field(default="", max_length=800)


class TaskOpening(Contract):
    # What every task begins with, in the order the model writes it. The two shapes of a task answer go on from here:
    # the fields of a class stand in its schema in the order they are declared, parents first, so a field cannot be
    # put between those of a parent — and the order is the order the model fills them in.
    title: str = Field(min_length=3, max_length=200)
    goal: str = Field(min_length=5, max_length=600)
    context: str = Field(default="", max_length=5000)
    steps: list[str] = Field(min_length=1, max_length=12)
    # «Что должно получиться»: 2–5 observable signs of a done task.
    expected: list[str] = Field(min_length=1, max_length=6)
    constraints: list[str] = Field(default_factory=list, max_length=6)
    # «Формат сдачи»: what lies in src/ and how the peer opens it.
    result: str = Field(min_length=3, max_length=1500)
    # «Переход к следующему заданию»: how the result feeds the next task; empty for the last one.
    transition: str = Field(default="", max_length=600)

    @field_validator("steps", "expected", "constraints")
    @classmethod
    def plain_items(cls, values: list[str]) -> list[str]:
        return list_items(values)


class PlainTaskDraft(TaskOpening):
    # What the writer of a draft begun before the styles is asked for (content 0.4 and earlier). The client sends the
    # schema of the answer with the prompt (core/llm.py), so this is a part of that draft's request and stays as it
    # was to the letter: the same fields in the same order, the name the model knew it by, and no docstring — that would
    # go into the schema too (independent review 06.10.2026: the two new fields reached the frozen prompt this way).
    model_config = ConfigDict(title="TaskDraft")

    peer_checks: list[str] = Field(min_length=1, max_length=12)
    # What the learner has to figure out to do the task: the theory chapter is built from these.
    needs: list[str] = Field(min_length=1, max_length=8)
    materials: list[MaterialNeed] = Field(default_factory=list, max_length=6)
    rationale: Rationale

    @field_validator("peer_checks", "needs")
    @classmethod
    def plain_points(cls, values: list[str]) -> list[str]:
        return list_items(values)


class TaskDraft(TaskOpening):
    # A task of a draft with a style. The examples and the line of the check stand before the points for the peer: one
    # of those points takes a value «которого нет в примерах», and the model writes the fields in this order.
    # The two fields say in the schema what they hold: a model that answers by the schema alone still writes an
    # example as a line with an arrow. The examples are taken as written (flow.write_task keeps the lines that are
    # examples and tells the methodologist of the others): one wrong line must not cost the whole paid answer, and a
    # line more than the README needs is not a reason to ask the model again.
    examples: list[str] = Field(default_factory=list, max_length=30, description=(
        "Примеры «ввод → вывод», по одному в строке, с одной стрелкой →; ⏎ — новая строка внутри ввода или вывода; "
        "без обратных кавычек вокруг ввода и вывода — шаблон сам покажет их как код"))
    # «Проверка»: what the one who checks will see or do. The legend shows it under the task.
    check: str = Field(default="", max_length=600, description="Что увидит или сделает проверяющий, одной-двумя фразами; пусто, если стиль поле не использует")
    peer_checks: list[str] = Field(min_length=1, max_length=12)
    # What the learner has to figure out to do the task: the theory chapter is built from these.
    needs: list[str] = Field(min_length=1, max_length=8)
    materials: Sequence[MaterialNeed] = Field(default_factory=list, max_length=6)
    rationale: Rationale

    @field_validator("peer_checks", "needs")
    @classmethod
    def plain_points(cls, values: list[str]) -> list[str]:
        return list_items(values)

    @field_validator("examples")
    @classmethod
    def lines(cls, values: list[str]) -> list[str]:
        return [line.strip() for line in values if line.strip()]


class CorpusTaskDraft(TaskDraft):
    materials: list[SizedMaterialNeed] = Field(default_factory=list, max_length=6)

    @field_validator("materials", mode="before")
    @classmethod
    def upgrade_materials(cls, value: object) -> object:
        # Adapters and deterministic writers may return a legacy need; its fields retain the same meaning.
        return [m.model_dump() if isinstance(m, MaterialNeed) else m for m in value] if isinstance(value, list) else value


class TopicDraft(Contract):
    title: str = Field(min_length=3, max_length=200)
    # A short direction, not a lecture: what it is and why the project needs it.
    text: str = Field(min_length=20, max_length=900)
    tasks: list[int] = Field(min_length=1)
    # «Вопрос к практике»: links the topic to what the learner does.
    question: str = Field(default="", max_length=400)
    # For the search only; shown in the README when no link was found.
    keywords: list[str] = Field(default_factory=list, max_length=8)


class TheoryDraft(Contract):
    topics: list[TopicDraft] = Field(min_length=1, max_length=5)


# How the theory is given to the learner (decided 06.10.2026: a chapter in the README by default, and how else it may
# be done is the question the assistant asks before writing it). chapter — short sections with links, as the generator
# always wrote them; notes — each section a short summary one can work from, in the README, as the sheets of a bassin;
# materials — the summaries lie in a file of materials/ and the README has a table «что изучить → где найти», as the
# run of the intensive without internet had it; both — the short chapter in the README and the summaries in the file.
Presentation = Literal["chapter", "notes", "materials", "both"]
PRESENTATIONS: dict[str, str] = {"chapter": "короткая глава в README", "notes": "конспект в README",
                                 "materials": "конспект в материалах", "both": "коротко в README и конспект в материалах"}
YES_NO: dict[str, str] = {"yes": "да", "no": "нет"}
# The presentations that need the summary of a section written, and those that put it into a file.
NOTED = ("notes", "materials", "both")
FILED = ("materials", "both")


class TheoryPlanDraft(Contract):
    """How the theory will be given, proposed by the assistant after the tasks are agreed and before the theory is
    written: the answer decides what is written at all — a pointer, or a summary to work from without the internet."""

    presentation: Presentation
    # Whether the learners have the internet during the project: without it links are of no use and are not looked for.
    internet: Literal["yes", "no"] = "yes"
    # Whether one section gets a short excerpt of a primary source in English, found by the search (never written by a model).
    english: Literal["yes", "no"] = "no"
    reason: str = Field(min_length=10, max_length=1200)
    # How one section will look in this presentation, on a need of this project: a few lines, to see before agreeing.
    sample: str = Field(default="", max_length=1500)
    questions: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("questions")
    @classmethod
    def plain_items(cls, values: list[str]) -> list[str]:
        return list_items(values)


class NotedTopicDraft(TopicDraft):
    # The summary of the section: enough to do the tasks from, without their solution. Asked for only where the
    # presentation shows it; the short text stays what the chapter of the README says.
    notes: str = Field(min_length=100, max_length=4000)


class NotedTheoryDraft(Contract):
    topics: list[NotedTopicDraft] = Field(min_length=1, max_length=5)


class ExcerptPick(Contract):
    """An excerpt of a primary source in English, chosen among pages code has opened. The quote is taken only if it
    stands on the page to the letter: a model does not write «a letter of 1987»."""

    candidate: int = Field(default=0, ge=0)
    topic: int = Field(default=0, ge=0)
    title: str = Field(default="", max_length=200)
    quote: str = Field(default="", max_length=600)
    note: str = Field(default="", max_length=400)


class LinkPick(Contract):
    topic: int = Field(ge=1)
    candidate: int = Field(ge=1)
    title: str = Field(min_length=2, max_length=200)
    why: str = Field(default="", max_length=300)


class LinkSelection(Contract):
    """The writer model chooses among links the code has opened: it never types an address."""

    picks: list[LinkPick] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=600)


class FrameDraft(Contract):
    # «Введение»: the idea of the project, the real context and the situation the learner is in.
    introduction: str = Field(min_length=20, max_length=6000)
    # «Инструкция»: project rules as short points — допускается / запрещено / обязательно, tools, formats.
    instruction: list[str] = Field(default_factory=list, max_length=10)
    # «Заключение»: closes this project, does not announce the next one.
    conclusion: str = Field(default="", max_length=1500)

    @field_validator("instruction")
    @classmethod
    def plain_items(cls, values: list[str]) -> list[str]:
        return list_items(values)


class MaterialDraft(Contract):
    content: RawText = Field(min_length=1, max_length=60000)
    note: str = Field(default="", max_length=600)


class CriticFinding(Contract):
    part: str = Field(default="", max_length=120)
    severity: Literal["important", "advice"]
    message: str = Field(min_length=5, max_length=800)
    suggestion: str = Field(default="", max_length=800)


class CriticReport(Contract):
    summary: str = Field(min_length=1, max_length=1200)
    findings: list[CriticFinding] = Field(default_factory=list, max_length=15)


class FactItem(Contract):
    part: str = Field(default="", max_length=120)
    topic: str = Field(min_length=1, max_length=300)
    status: Literal["current", "outdated", "unclear"]
    finding: str = Field(min_length=1, max_length=1000)
    suggestion: str = Field(default="", max_length=800)
    source_numbers: list[int] = Field(default_factory=list, max_length=8)


class FactReport(Contract):
    summary: str = Field(min_length=1, max_length=1200)
    items: list[FactItem] = Field(default_factory=list, max_length=10)


class ContentChange(Contract):
    part: str = Field(min_length=1, max_length=120)
    field: str = Field(min_length=1, max_length=40)
    value: RawText | list[str]
    reason: str = Field(min_length=1, max_length=600)
    # Only for a new material: the tasks that use it.
    task_numbers: list[int] = Field(default_factory=list)


class ContentRevision(Contract):
    # What the message is, decided first; a question or an off-topic message changes nothing (code holds to it).
    kind: MessageKind = "request"
    reply: str = Field(min_length=1, max_length=2000)
    changes: list[ContentChange] = Field(default_factory=list, max_length=12)
    proceed: bool = False
    # The methodologist asks to find (more) links for the theory: code runs the search, the model does not invent them.
    find_links: bool = False
    # The methodologist asks to see a document: code shows its card in the chat.
    show: Literal["", "full", "readme", "checklist"] = ""


# ---------- what is stored ----------

class Style(StyleDraft):
    # Empty when the methodologist took the style without the assistant's reasons for it.
    reason: str = Field(default="", max_length=1200)
    status: Status = "draft"
    # Who named the profile: the form, the methodologist in the chat, or the assistant's own proposal. A profile the
    # methodologist named is kept when the assistant writes the style anew.
    chosen_by: Literal["form", "chat", "assistant"] = "assistant"
    # The style the reasons and the story were written for. It differs from the profile once that is changed — by the
    # chip (until the assistant has written the style anew), or by words: what stands on the card then argues for and
    # tells of the other style, and is shown as such. An empty reason was this mark at first and was wrong both ways
    # (independent review 06.10.2026: «there and back» lost the reasons; a reason typed with the change hid it).
    written_for: Literal["", "quest", "legend"] = ""

    @model_validator(mode="after")
    def written_for_its_profile(self) -> "Style":
        if not self.written_for:
            self.written_for = self.profile
        return self

    def stale(self) -> bool:
        """The reasons and the story are of the other style."""
        return self.written_for != self.profile


class Summary(SummaryDraft):
    status: Status = "draft"
    # The titles the assistant proposed, the one in `title` among them while it is one of them (content 0.9; none
    # before). They are the methodologist's to choose from until the title is agreed; no text is written by them.
    title_options: list[Title] = Field(default_factory=list, max_length=TITLE_CHOICES)

    def text(self) -> dict[str, object]:
        """What the project is called and promised — as the writers, the critics and the digest of the text read it.
        The titles not chosen are no part of the text."""
        return self.model_dump(include={"title", "annotation"})


class TaskContent(Contract):
    number: int = Field(ge=1)
    title: str
    goal: str
    context: str = ""
    steps: list[str]
    expected: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    result: str
    transition: str = ""
    examples: list[str] = Field(default_factory=list)
    check: str = ""
    # The style the task was written in ("" — before the styles). A task written in the other style is not a model
    # for the next ones, and the methodologist is told it stands apart.
    written_in: Literal["", "quest", "legend"] = ""
    peer_checks: list[str]
    needs: list[str] = Field(default_factory=list)
    rationale: Rationale | None = None
    status: Status = "draft"

    @field_validator("examples")
    @classmethod
    def arrowed(cls, values: list[str]) -> list[str]:
        return example_items(values)


ADDRESS = re.compile(r"https?://\S+")


class Link(Contract):
    title: str
    url: str
    why: str = ""

    def line(self) -> str:
        """«Название — адрес — зачем», the form edits.parse_link reads back into this same link."""
        # A link without its own title (the address is the title) is written once: «url — url» read back would take
        # the second address for the «why». An address inside the title would be read as the link's own: left out.
        title = " ".join(ADDRESS.sub(" ", self.title).split())
        head = self.url if self.title == self.url or not title else f"{title} — {self.url}"
        return head + (f" — {self.why}" if self.why else "")


class TheoryTopic(TopicDraft):
    number: int = Field(ge=1)
    links: list[Link] = Field(default_factory=list)
    # The summary of the section, where the presentation of the theory shows one ("" otherwise, and before 0.6).
    notes: str = Field(default="", max_length=4000)


EXCERPT_LIMIT = 400


class Excerpt(Contract):
    """A short excerpt of a primary source in English with its address: found by the search, opened by code, and
    standing on the page word for word."""

    topic: int = Field(ge=1)
    title: str
    url: str
    # The words of the page, as code found them there. Nothing rewrites them: the chat may only take the excerpt out
    # of the text and put it back (independent review 06.10.2026: an «edited value» of the change that took it out
    # put any typed sentence under the name and the address of the real source).
    quote: str = Field(min_length=1, max_length=EXCERPT_LIMIT)
    removed: bool = False


class TheoryPlan(TheoryPlanDraft):
    # Empty when the methodologist chose the presentation and the assistant gave no reasons for that one.
    reason: str = Field(default="", max_length=1200)
    status: Status = "draft"
    chosen_by: Literal["chat", "assistant"] = "assistant"
    # The presentation the reasons and the sample were written for: after a change by the chip or by words they are
    # of the other one, and are shown as such (as the story of a style is, Style.written_for).
    written_for: Literal["", "chapter", "notes", "materials", "both"] = ""

    @model_validator(mode="after")
    def written_for_its_presentation(self) -> "TheoryPlan":
        if not self.written_for:
            self.written_for = self.presentation
        return self

    def stale(self) -> bool:
        """The reasons and the sample are of another presentation."""
        return self.written_for != self.presentation


class Theory(Contract):
    topics: list[TheoryTopic]
    # Why links are missing, when the search did not run or found nothing.
    note: str = ""
    # None where no excerpt was asked for or none was found (and before 0.6).
    excerpt: Excerpt | None = None
    status: Status = "draft"

    def quoted(self) -> Excerpt | None:
        """The excerpt the text shows: one that was found and not taken out."""
        return self.excerpt if self.excerpt and not self.excerpt.removed else None

    def topic(self, number: int) -> TheoryTopic | None:
        return next((t for t in self.topics if t.number == number), None)


class Frame(FrameDraft):
    status: Status = "draft"


class Checklist(Contract):
    """The texts of check-list.yml around the peer questions. Built by code from school.yaml, edited only by patches;
    the questions themselves are the tasks' peer points. The disclaimer comes from the methodologists."""

    introduction: str
    guidelines: str
    disclaimer: str = ""


class Material(SizedMaterialNeed):
    tasks: list[int] = Field(default_factory=list)
    content: RawText = ""
    note: str = ""
    status: Literal["planned", "draft", "approved"] = "planned"
    # Completed batches survive a failed next call. The key prevents reuse after a specification change.
    generation_key: str = ""
    generation_parts: list[str] = Field(default_factory=list, max_length=50)


class Finding(Contract):
    number: int
    source: Literal["method", "facts"]
    part: str = ""
    severity: Literal["important", "advice"]
    message: str
    suggestion: str = ""
    sources: list[str] = Field(default_factory=list)


class Review(Contract):
    at: str = Field(default_factory=now)
    # The content the critics saw: a later edit makes the review stale, which the chat shows.
    digest: str
    summary: str = ""
    facts_summary: str = ""
    findings: list[Finding] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


Value = RawText | list[str]


class Patch(Contract):
    part: str
    field: str
    label: str
    before: Value
    after: Value
    reason: str = ""
    task_numbers: list[int] = Field(default_factory=list)
    edited: bool = False


class ContentMessage(Contract):
    id: str = Field(default_factory=lambda: uuid4().hex)
    role: Literal["methodologist", "assistant", "system"]
    text: str
    at: str = Field(default_factory=now)
    # A part the assistant has just written (or handoff, full, review, readme): the chat shows its card here.
    part: str = ""
    patches: list[Patch] = Field(default_factory=list)
    status: Literal["pending", "applied", "rejected"] | None = None
    notes: list[str] = Field(default_factory=list)
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    # How the assistant understood the methodologist's message.
    kind: str = ""


# Fields of a task added after drafts were already stored (06.10.2026): see ContentDraft.digest.
LATER = ("examples", "check", "written_in")


class ContentDraft(Contract):
    id: str
    owner_id: str
    # The release shape that wrote the file (core.contracts.STORED_SCHEMA): a rollback reads newer files by it.
    schema_version: int = STORED_SCHEMA
    created_at: str = Field(default_factory=now)
    content_version: str
    handoff: ConceptHandoff
    # Prompts are frozen with the draft: editing a prompt file never changes a draft in progress.
    prompt_snapshots: dict[str, str]
    # None in a draft begun before the styles (content 0.4 and earlier): it goes on by the template it was begun with.
    style: Style | None = None
    # How the theory is given; None until the assistant proposes it, and in a draft begun before 0.6 for good.
    theory_plan: TheoryPlan | None = None
    summary: Summary | None = None
    tasks: list[TaskContent] = Field(default_factory=list)
    theory: Theory | None = None
    frame: Frame | None = None
    materials: list[Material] = Field(default_factory=list)
    # None until code fills it from school.yaml; not part of the digest — the critics do not review it.
    checklist: Checklist | None = None
    # Every link a search call returned: only these may appear as links in the text, unless typed by a person.
    sources: list[str] = Field(default_factory=list)
    review: Review | None = None
    messages: list[ContentMessage] = Field(default_factory=list)
    # What a background job is writing now; empty when idle.
    busy: str = ""
    assembled_at: str | None = None
    batch_prepared: bool = False

    def since(self, version: str) -> bool:
        """The draft was begun by this version of the generator or a later one. For what a version adds without a
        prompt of its own to know it by: a check of the text, a line of the common rules."""
        def numbers(text: str) -> tuple[int, ...]:
            return tuple(int(part) for part in re.findall(r"\d+", text))

        return numbers(self.content_version) >= numbers(version)

    def styled(self) -> bool:
        """The draft was begun with the styles: it holds their texts. An earlier one goes on as it began — by the
        prompts frozen in it and by the template of its time."""
        return "style" in self.prompt_snapshots

    def plans_theory(self) -> bool:
        """The draft was begun with the question of how the theory is given (content 0.6): it holds that prompt. An
        earlier one writes the theory as its frozen prompts ask for it — a short chapter in the README."""
        return "theory_plan" in self.prompt_snapshots and self.styled()

    def presentation(self) -> str:
        """How the theory of this draft is given: by its plan, else the chapter the generator always wrote."""
        return self.theory_plan.presentation if self.theory_plan else "chapter"

    def online(self) -> bool:
        """The learners have the internet, as far as the draft knows: links are looked for and shown."""
        return self.theory_plan is None or self.theory_plan.internet == "yes"

    def task(self, number: int) -> TaskContent | None:
        return next((t for t in self.tasks if t.number == number), None)

    def material(self, path: str) -> Material | None:
        return next((m for m in self.materials if m.path == path), None)

    def digest(self) -> str:
        """Fingerprint of the text itself, statuses excluded: tells whether anything changed since the review.

        What was added to a draft later is counted only where it is filled: a draft written before the styles and the
        examples has the fingerprint it had, so the review it was given is not called stale by a release."""
        def task(found: TaskContent) -> dict[str, object]:
            # In which style a task was written is not its text: a task rewritten to the letter is the same task.
            text = found.model_dump(exclude={"status", "written_in"})
            return {key: value for key, value in text.items() if key not in LATER or value}

        def theory(found: Theory) -> dict[str, object]:
            text = found.model_dump(exclude={"status"})
            text["topics"] = [{key: value for key, value in topic.items() if key != "notes" or value} for topic in text["topics"]]
            if found.quoted():
                return {**text, "excerpt": found.quoted().model_dump(exclude={"removed"})}  # type: ignore[union-attr]
            return {key: value for key, value in text.items() if key != "excerpt"}

        text: dict[str, object] = {
            "summary": self.summary.text() if self.summary else None,
            "tasks": [task(t) for t in self.tasks],
            "theory": theory(self.theory) if self.theory else None,
            "frame": self.frame.model_dump(exclude={"status"}) if self.frame else None,
            "materials": [{k: v for k, v in m.model_dump(exclude={"status", "generation_key", "generation_parts"}).items()
                           if k != "target_words" or v} for m in self.materials]}
        if self.style:
            text["style"] = self.style.profile
        if self.theory_plan:
            # Where the theory lies and whether its links are shown is what the learner reads; why it was chosen is not.
            text["theory_plan"] = [self.theory_plan.presentation, self.theory_plan.internet]
        return hashlib.sha256(json.dumps(text, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
