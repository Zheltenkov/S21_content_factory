"""Layer 2 of the measure: a model marks a project by the criteria, code checks what it said and counts.

    python -m project_generator_lab.benchmark.judge <cases> --model <model> --dry-run     what would be asked, and its price
    python -m project_generator_lab.benchmark.judge <cases> --model <model> --out <dir>   the paid run; resumes in <dir>

The model reads the parts of a project that a group of criteria is about and gives every criterion a mark of the
sources' own scale — 0, 1, 2, «неприменимо», «нет данных» — with a quote from the project.

Everything else is code:
- which criteria are asked at all: one that takes running the files, the subject expert, or inputs the case does not
  have is not put to the model — it is listed as not marked, never as passed;
- what the judge is shown: the project, and nothing that says how good it is known to be — not the name of the
  case, not its note, not its place in a series;
- whether a quote is from the text shown, and whether a mark that takes a quote has one: a mark whose quote is
  not in the project, or that is given without the quote its criterion asks for, is not counted;
- the total, by the formula of the source: points / (2 × marks given) × 100, per source;
- how far two repeats of the same call agree, and whether the versions of a series come out in their known order.

A call that was paid for is kept in the folder of the run, and a run started again in that folder asks only what is
missing.
"""

import argparse
import asyncio
import hashlib
import itertools
import json
import math
import os
import re
import sys
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..core.contracts import Contract
from . import cases, code_layer, criteria, repository, seeded
from .code_layer import flat
from .criteria import Criteria, Criterion
from .repository import Project

PACKAGE = Path(__file__).resolve().parent.parent
PROMPT = Path(__file__).with_name("prompts") / "judge.md"
PRICES = PACKAGE / "concept" / "config" / "model_prices.json"
# How much of a project one call may carry. A README longer than this is cut and the judge is told so.
MAX_README = 80_000
MAX_CHECKLIST = 50_000
MAX_MATERIAL = 8_000
MAX_MATERIALS = 40_000
MAX_LISTED = 400
MAX_MENTIONED = 60
MAX_SERIES = 8  # versions of one series whose every order is counted for the chance of a blind judge
MAX_NAMED = 80  # how much of a title or a name the project gave is said where code speaks: it is still the project speaking
# A result is of the whole of what was asked when at least this share of the judge's answers was taken; with fewer it
# is a result of some of the criteria, and is not compared.
COVERAGE = 0.8
# Calls failed in a row after which a run asks no more: what is wrong will be wrong for the rest. Before the judge has
# answered once three are enough; after it has, a few failures at once are as likely a gateway that stumbled.
STOP_AFTER = 3
STOP_RUNNING = 6
TAKEN = 0.5  # the share of a call's verdicts that has to be taken for the call to be of use (see `run`)
SURE = 0.05  # the chance below which marks risen after a plea are not taken for the judge's own scatter
AGREE = 0.6  # the share of marks two repeats of a project have to agree in for a rise after a plea to mean anything
# A quote is a phrase of the project, not words picked from it: a piece of it is at least this long, there are at
# most this many pieces around «…», and each next piece begins no further than this from the end of the one before.
MIN_PIECE = 12
MAX_PIECES = 3
MAX_GAP = 600
# The way a call is asked and kept. An answer kept under another number is not this call's answer.
FORMAT = 2
SCALE = ("0", "1", "2", "na", "no_data")
# For the estimate of a run before it is paid for; what a run really took is in its report.
CHARS_PER_TOKEN = 2.5
TOKENS_PER_VERDICT = 400  # measured on the run of 06.10.2026: 393 a verdict, the reasoning of the model included
MATERIAL_FORMATS = (".md",)  # what is written to be read; a «.txt» among the files is as often data
# The blocks of a request. A project that writes such a tag in its own text does not close or open a block by it.
BLOCKS = ("about", "shown", "facts", "project", "file", "checklist", "files", "criteria")
# «<» that begins such a tag — written plainly, with spaces or a slash of any kind before the name, in a sign that
# only looks like «<», with Russian letters that look like the Latin ones, or with marks that join onto a letter.
# The marks that are not seen at all are taken out of the text before this is looked for (see `seen`). More is
# guarded than has to be — «<projector>» too — and nothing is lost by it: the sign is replaced, the word stays.
JOINED = "[\u0300-\u036f\u0483-\u0489\u034f\ufe00-\ufe0f]*"
ALIKE = {"a": "aа", "b": "bЬ", "c": "cс", "e": "eе", "h": "hһн", "i": "iі", "j": "jј", "k": "kк", "o": "oо0", "p": "pр", "r": "rг", "s": "sѕ", "t": "tт", "u": "uи", "w": "wш"}
TAG = re.compile("[<＜﹤‹〈⟨˂](?=(?:\\s|[/／⁄∕\\\\]|" + JOINED[:-1] + ")*(?:" + "|".join(JOINED.join(f"[{ALIKE.get(sign, sign)}]" for sign in name) for name in BLOCKS) + "))", re.I)
ELLIPSIS = re.compile(r"\[\s*\.{3,}\s*\]|\(\s*\.{3,}\s*\)|\.{3,}")
# An address of a link to the outside: with a host that has a dot. «http://materials/a.md» is what Word makes of the
# name of a file, not a link.
URL = re.compile(r"https?://[^\s/?#.)>\]`\"'«»]+(?:\.[^\s/?#.)>\]`\"'«»]+)+[^\s)>\]`\"'«»]*", re.I)
# What reads as the name of a file or a folder: Latin letters with «/» or with the ending of a text file. «и/или» and
# «3/4» are not names; a file named in Russian letters is not recognised — it is listed with the files all the same.
PATH = re.compile(r"(?<![\w/.:-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*|[A-Za-z0-9_-]+\.(?:md|txt|csv|tsv|py|sh|sql|json|ya?ml|log|in|out|cfg|ini|toml))(?![\w/-])")
T = TypeVar("T", bound=BaseModel)


MARKS = (("нп", "na"), ("н п", "na"), ("n a", "na"), ("na", "na"), ("неприменим", "na"), ("не применим", "na"),
         ("нет данных", "no_data"), ("н д", "no_data"), ("no data", "no_data"), ("nodata", "no_data"))


# One mark as the judge gave it. What a model writes a little otherwise — a field it was not asked for, null for an
# empty quote, «н/п» — is read as what it means: a whole call is not lost for it. (A comment and not a docstring: the
# docstring of a model goes to the judge with the form of the answer.)
class Verdict(Contract):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: str
    mark: Literal["0", "1", "2", "na", "no_data"]
    quote: str = ""
    comment: str = ""

    @field_validator("id", mode="before")
    @classmethod
    def as_named(cls, value: object) -> object:
        """«r04» and « R04 » name the criterion R04; «С01» typed with the Russian letter names C01."""
        return str(value).strip().upper().replace("С", "C")

    @field_validator("mark", mode="before")
    @classmethod
    def as_written(cls, value: object) -> object:
        """A mark is a mark however it was typed: the number 2, «2.0», «НП», «н/п», «неприменимо (нет сюжета)», «нет данных»."""
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        text = str(value).strip().lower().replace("ё", "е")
        if re.fullmatch(r"[012](?:\.0+)?(?:\s*\(.*\)|\s+балл\w*)?", text):
            return text[0]  # «2.0», «0 (нет раздела)», «2 балла»
        words = " ".join(re.split(r"[\s_/\\.()-]+", text)).strip()
        return next((mark for said, mark in MARKS if words == said or words.startswith(said + " ") or (len(said) > 4 and words.startswith(said))), text)

    @field_validator("quote", "comment", mode="before")
    @classmethod
    def as_said(cls, value: object) -> object:
        """Nothing said is an empty line, whichever way nothing was written; pieces given as a list are pieces of one quote."""
        if value is None:
            return ""
        if isinstance(value, list | tuple):
            return " … ".join(str(piece) for piece in value if piece is not None)
        return value if isinstance(value, str) else str(value)


class Verdicts(Contract):
    model_config = ConfigDict(extra="ignore")

    verdicts: list[Verdict] = Field(max_length=200)

    @field_validator("verdicts", mode="before")
    @classmethod
    def readable(cls, value: object) -> object:
        """One verdict that does not read does not cost the others: it is left out, and its criterion reads as
        passed over. When none reads, the answer is of another form altogether — it is refused as it stands, and
        the client asks the model to mend it."""
        if not isinstance(value, list):
            return value
        kept = []
        for one in value:
            try:
                kept.append(Verdict.model_validate(one))
            except ValidationError:
                continue
        return kept if kept or not value else value


# The form of the answer as the judge is shown it: the client puts it after the instructions.
FORM = json.dumps(Verdicts.model_json_schema(), ensure_ascii=False, sort_keys=True)


class Model(Protocol):
    async def generate(self, schema: type[T], system: str, payload: str) -> tuple[T, Any]: ...


class Marked(NamedTuple):
    """What became of one criterion in one call."""

    id: str
    # a mark of the scale; or why there is none: unverified — the mark takes a quote and the quote is not in the
    # text shown, unquoted — it takes a quote and has none, unanswered — the judge passed the criterion over,
    # failed — the call did not come back
    state: str
    quote: str = ""
    comment: str = ""
    given: str = ""  # the mark as the judge said it, counted or not; empty — the judge said none
    proof: str = ""  # whether the quote is in the text shown: found, missing, none


class Asked(NamedTuple):
    """One call to the judge, before it is made."""

    case: str
    group: str
    repeat: int
    ids: tuple[str, ...]
    system: str
    payload: str
    shown: tuple[str, ...]  # the texts a quote has to be found in — the README, each material, the check-list, the list of files
    model: str = ""
    absence: tuple[str, ...] = ()  # the criteria whose sign is that something is not there (see Criterion.evidence)

    @property
    def key(self) -> str:
        """What an answer is kept under: the same question — instructions, form of the answer, request — to the same
        model, the same time round. Another model's answer to it is another answer."""
        return hashlib.sha256("\x1f".join((str(FORMAT), self.model, self.system, FORM, self.payload, str(self.repeat))).encode("utf-8")).hexdigest()[:20]

    def quoted(self, ident: str, mark: str) -> bool:
        """Whether this mark of the criterion counts only with a quote."""
        return mark in (("0", "1") if ident in self.absence else ("1", "2"))


class Answer(NamedTuple):
    asked: Asked
    marks: dict[str, Marked]
    strangers: list[str]  # ids the judge gave that were not asked
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    error: str = ""
    kept: bool = False  # read from the folder of the run, not asked again
    unsaved: bool = False  # paid for and not written to the folder of the run: a run started again will pay for it again


class Total(NamedTuple):
    points: int
    marked: int  # criteria that got 0, 1 or 2: the denominator is twice this
    na: int
    no_data: int
    unverified: int
    unquoted: int
    unanswered: int
    failed: int  # criteria of the calls that did not come back: with any, the total is of a part of the project

    @property
    def percent(self) -> float | None:
        """None when nothing was marked: a project with every criterion inapplicable has no result, not a full one."""
        return round(self.points / (2 * self.marked) * 100, 1) if self.marked else None

    @property
    def asked(self) -> int:
        return self.marked + self.na + self.no_data + self.unverified + self.unquoted + self.unanswered + self.failed

    @property
    def whole(self) -> bool:
        """Whether this is a result of what was asked and not of a part of it: no call was lost, the judge's answer
        was taken — as a mark, «неприменимо» or «нет данных» — for most of the criteria, and at least half of them
        got a mark: a percent of four marks out of sixty says little of the project."""
        return not self.failed and self.marked + self.na + self.no_data >= COVERAGE * self.asked and 2 * self.marked >= self.asked


class Pair(NamedTuple):
    series: str
    worse: str
    better: str
    worse_percent: float | None
    better_percent: float | None
    spread: float = 0.0  # how far the repeats of either version stand apart, the larger of the two
    partial: bool = False  # the percent of either version is of a part of the criteria (see Total.whole), or the two were asked different criteria

    @property
    def verdict(self) -> str:
        if self.partial:
            return "неполные данные"
        if self.worse_percent is None or self.better_percent is None:
            return "нет оценки"
        gap = round(self.better_percent - self.worse_percent, 1)
        if gap == 0:
            return "равны"
        # A difference no larger than the repeats of one project differ by is as likely the judge's scatter.
        return ("верно" if gap > 0 else "неверно") + (", в пределах разброса повторов" if abs(gap) <= self.spread else "")


def seen(text: str) -> str:
    """A text without the marks nobody sees — a zero-width space, a soft hyphen, a mark of direction. Written inside
    a word they make another word of it for a program and the same word for a reader."""
    return "".join(sign for sign in text if unicodedata.category(sign) != "Cf")


def plain(text: str) -> str:
    """A text as it compares with a quote from it: its words in order — letters and digits, between single spaces.
    Marks of Markdown, the bars of a table, punctuation, quotes of any kind and the width of a space are not part of
    what was said. The space at each end lets a phrase be looked for by whole words."""
    text = unicodedata.normalize("NFKC", seen(text)).lower().replace("ё", "е")
    return " " + " ".join(re.sub(r"[\W_]+", " ", re.sub(r"<br\s*/?>", " ", text)).split()) + " "


def evidence(quote: str, shown: Sequence[str]) -> str:
    """Whether the quote is from the texts shown: «found», «missing», or «none» when there is nothing to look for —
    no quote, or one too short to tell a phrase of the project from a word of the language.

    `shown` are plain texts: the texts of the project, and after them the lines of theirs that are quotes whole
    (see `quotable`). A quote is one phrase, or a few pieces of one place cut with «…»: every piece of whole words
    and of some length, all in one text, in their order, each near the one before. Words picked from across a
    project and strung together are not a quote of it."""
    pieces = [piece for piece in (plain(part) for part in ELLIPSIS.split(unicodedata.normalize("NFKC", quote))) if piece.strip()]
    if not pieces:
        return "none"
    whole = plain(quote)
    if len(whole) - 2 < MIN_PIECE:
        # Short, and yet a quote when it is a heading of the project or the name of a file — whole.
        return ("found" if whole in shown else "none") if len(pieces) == 1 else "missing"
    if any(whole in text for text in shown):
        return "found"  # as it stands: the dots in it are the project's own — «print(..., end="")» — or stand for nothing left out
    if len(pieces) < 2 or len(pieces) > MAX_PIECES or any(len(piece) - 2 < MIN_PIECE for piece in pieces):
        return "missing"
    for text in shown:
        start = text.find(pieces[0])
        while start >= 0:
            at = start + len(pieces[0]) - 1  # the space that ends a piece may begin the next
            for piece in pieces[1:]:
                found = text.find(piece, at, at + MAX_GAP + len(piece))
                if found < 0:
                    break
                at = found + len(piece) - 1
            else:
                return "found"
            start = text.find(pieces[0], start + 1)
    return "missing"


HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")


def quotable(texts: Sequence[str], names: Sequence[str] = ()) -> tuple[str, ...]:
    """What a quote is looked for in: the texts shown, each plain and apart; then the headings of theirs too short
    to be looked for as a phrase, and the names given — each of these is a quote only whole. A short line that is
    not a heading («Верно», «Вывод») is a word of the language, not a place of the project."""
    short = {plain(line) for text in texts for line in text.splitlines() if HEADING.match(line)} | {plain(name) for name in names}
    return (*(plain(text) for text in texts), *sorted(line for line in short if 0 < len(line) - 2 < MIN_PIECE))


def guarded(text: str) -> str:
    """A text of the project inside a block of the request: a tag of the request written in it stays a word of it."""
    return TAG.sub("‹", seen(text))


def brief(text: object, limit: int = MAX_NAMED) -> str:
    """A title or a name the project gave, where code speaks of it: on one line and no longer than a name is —
    a sentence written into a heading does not become a sentence of the code's."""
    said = guarded(flat(text)).replace("«", "“").replace("»", "”")  # where code puts a name in «…», the name does not close them
    return said if len(said) <= limit else said[:limit].rstrip() + "…"


def named(path: str) -> str:
    """The name of a file where the request names it: on one line, with nothing in it that closes a tag or a quote."""
    return brief(path, 200).replace('"', "'")


def cut(text: str, limit: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


def matching(name: str, paths: Sequence[str]) -> list[str]:
    """The files of the project a name of the README is about: the file itself, wherever in the project its path
    ends with the name, or everything under the folder of that name."""
    name = name.removeprefix("./")
    folder = name.rstrip("/") + "/"
    return [path for path in paths if path == name or path.endswith("/" + name) or path.startswith(folder) or f"/{folder}" in f"/{path}"]


def mentioned(text: str, paths: Sequence[str]) -> list[tuple[str, bool]]:
    """The files and folders the README names, in the order it names them, and whether the project has each.
    Two words with «/» between them are a name only with the ending of a file, with «/» at the end, or when the
    project has such a folder: «try/except» and a branch «feature/first» are not files."""
    found: dict[str, bool] = {}
    for match in PATH.finditer(text):
        name = match[1].rstrip(".").removeprefix("./")
        if not name or name in found or len(found) >= MAX_MENTIONED or not re.search("[A-Za-z]", name):
            continue
        there = bool(matching(name, paths))
        if there or name.endswith("/") or re.search(r"\.[A-Za-z0-9]{1,5}$", name):
            found[name] = there
    return list(found.items())


def facts(project: Project) -> list[str]:
    """What code counted in the project, for the judge to lean on instead of counting itself. The titles and names
    in it are the project's own words, cut to the length of a name."""
    readme, lines = project.readme, []
    if readme.tasks:
        lines.append("Задания README: " + "; ".join(f"{brief(task.label)}{' (бонусное)' if task.bonus else ''} «{brief(task.title)}»" for task in readme.tasks) + ".")
        paired = repository.pair(readme, project.checklist)
        if project.checklist:
            links = "; ".join(f"{brief(readme.tasks[place].label)} ← " + ", ".join(f"«{brief(q.name)}»" for q in asked) for place, asked in sorted(paired.questions.items()))
            lines.append(f"Вопросы чек-листа сопоставлены с заданиями по {paired.by}: {links or 'ни один не сопоставлен'}.")
            if paired.by == "номеру":
                # What code knows is what the names say: a question that names no number may be about a task all the same.
                lines += [f"Задания, номер которых не назван ни в одном вопросе чек-листа: {', '.join(brief(label) for label in paired.tasks_without)}."] if paired.tasks_without else []
                lines += [f"Вопросы чек-листа, в названии которых нет номера задания: {', '.join(f'«{brief(name)}»' for name in paired.questions_without)}."] \
                    if paired.questions_without else []
            else:
                # Taken one for one in order, the question left over is whichever came last — not the one that is
                # about no task: two questions may be about one task. Which is which is for the judge to read.
                tasks, questions = len(readme.tasks), len(project.checklist.questions)
                lines.append("Сопоставление по порядку приблизительное: названия вопросов не называют номеров заданий."
                             + (f" Заданий {tasks}, вопросов {questions}: к какому заданию относится каждый вопрос, видно только из их текста." if tasks != questions else ""))
    else:
        lines.append("Заголовков заданий («Задание N», «Part N») в README нет: задания, если они есть, записаны иначе.")
    if not project.checklist:
        lines.append("Чек-листа у проекта нет или он не прочитан.")
    # The README and the check-list are files of the project too, though not among those listed: a README that
    # names its own check-list names a file that is there.
    named_files = mentioned(project.text, [*(file.path for file in project.files), *(name for name in (project.readme_file, project.checklist_file) if name)])
    if named_files:
        lines.append("Файлы и каталоги, названные в README: " + "; ".join(f"{name} — {'есть' if there else 'нет в проекте'}" for name, there in named_files)
                     + ". Файла может не быть в проекте и потому, что его создаёт участник.")
    # The comma or the full stop after an address is the sentence's, not the address's: one address is counted once.
    addresses = {address.rstrip(".,;:!?'\"»") for address in URL.findall(project.text)}
    lines.append(f"Картинок в README: {readme.images}. Адресов внешних ссылок в README: {len(addresses)}.")
    lines += ["README хранится документом Word: блоки кода в нём не отличаются от обычных абзацев, это не недостаток оформления."] \
        if project.readme_file.lower().endswith(".docx") else []
    return lines


def materials(project: Project) -> tuple[list[tuple[str, str]], list[str]]:
    """The text materials the README names, each as far as it is shown, and what is said of the rest."""
    by_path = {file.path: file for file in project.files if file.path.lower().endswith(MATERIAL_FORMATS) and file.content}
    order: list[str] = []
    for name, _ in mentioned(project.text, list(by_path)):
        # A file named by itself; a folder named («materials/») is not a wish to read everything in it.
        order += [path for path in matching(name, list(by_path)) if path not in order and not name.endswith("/") and path.endswith(name)]
    shown: list[tuple[str, str]] = []
    whole: list[str] = []
    partly: list[str] = []
    left: list[str] = []
    room = MAX_MATERIALS
    for path in order:
        text, shortened = cut(by_path[path].content, min(MAX_MATERIAL, room))
        if room <= 0 or not text:
            left.append(named(path))
            continue
        shown.append((path, text))
        room -= len(text)
        (partly if shortened else whole).append(f"{named(path)} (первые {len(text)} знаков из {len(by_path[path].content)})" if shortened else named(path))
    unnamed = [path for path in by_path if path not in order]
    notes = [f"Материалы показаны целиком: {', '.join(whole)}."] if whole else []
    notes += [f"Материалы показаны частично: {', '.join(partly)}."] if partly else []
    notes += [f"Материалы названы в README, но не показаны (не поместились): {', '.join(left)}."] if left else []
    notes += [f"Материалы проекта, которые README не называет, не показаны: {', '.join(named(path) for path in unnamed[:20])}{' и другие' if len(unnamed) > 20 else ''}."] if unnamed else []
    return shown, notes or ["Материалов (.md), названных в README, у проекта нет."]


def shown_of(project: Project, shows: Sequence[str]) -> tuple[str, list[str], tuple[str, ...]]:
    """The <project> block of a call, the lines that say what it holds, and the texts in it a quote may be from —
    each plain, each apart: a quote is of one of them."""
    readme, shortened = cut(project.text, MAX_README)
    parts = [f'<file path="README">\n{guarded(readme)}\n</file>']
    texts = [readme]
    names: list[str] = []
    notes = [f"README показан {'частично: первые ' + str(len(readme)) + ' знаков из ' + str(len(project.text)) if shortened else 'целиком'}."]
    if "materials" in shows:
        files, said = materials(project)
        parts += [f'<file path="{named(path)}">\n{guarded(text)}\n</file>' for path, text in files]
        texts += [text for _, text in files]
        notes += said
    else:
        notes.append("Материалы проекта в этом запросе не показаны.")
    if "checklist" in shows:
        if project.checklist and project.checklist.questions:
            asked = "\n\n".join(f"Вопрос «{q.name}»{' [бонусная часть]' if q.bonus else ''}, оценка {q.rating or 'не указана'}:\n{q.text}" for q in project.checklist.questions)
            text, shortened = cut(asked, MAX_CHECKLIST)
            parts.append(f"<checklist>\n{guarded(text)}\n</checklist>")
            texts.append(text)
            notes.append(f"Вопросы чек-листа показаны {'частично: первые ' + str(len(text)) + ' знаков из ' + str(len(asked)) if shortened else 'целиком'}; "
                         "вступление и общая инструкция чек-листа не показаны.")
        else:
            notes.append("Чек-листа у проекта нет или он не прочитан: показать нечего.")
    else:
        notes.append("Чек-лист в этом запросе не показан.")
    if "files" in shows:
        listed = "\n".join(f"{named(file.path)} — {file.size} байт" for file in project.files[:MAX_LISTED])
        parts.append("<files>\n" + guarded(listed) + "\n</files>")
        texts.append(listed)
        # A file is quoted by its path, by its name alone, or by its line of the list.
        names = [name for file in project.files[:MAX_LISTED] for name in (file.path, file.path.rsplit("/", 1)[-1])] + listed.splitlines()
        notes.append(f"Перечень файлов проекта: {min(len(project.files), MAX_LISTED)} из {len(project.files)}; README и чек-лист в перечень не входят. Содержимое файлов не показано.")
    else:
        notes.append("Перечень файлов в этом запросе не показан.")
    return "\n\n".join(parts), notes, quotable(texts, names)


def payload(project: Project, meta: cases.Meta, group: criteria.Group, asked: Sequence[Criterion], rules: Criteria) -> tuple[str, tuple[str, ...]]:
    """The request about one group of criteria, and the texts of it a quote may be from. Of the case only its
    profile and level are said: its name and note may tell how good the project is known to be. What code counted is
    for the judge to lean on, not to quote."""
    block, notes, quoted = shown_of(project, group.shows)
    about = [f"Профиль проекта: {flat(meta.profile) or 'не указан'}."]
    about += [f"Уровень участников по сведениям методолога: {flat(meta.level)}."] if meta.level else []
    about += [f"Группа критериев: {group.title}."]
    listed = []
    for c in asked:
        proof = "цитата нужна для оценок 0 и 1" if c.evidence == "absence" else "цитата нужна для оценок 1 и 2"
        listed.append(f"- {c.id} [{c.section}] {c.text}" + (f" {c.scope}" if c.scope else "") + (f" Примечание: {c.note}" if c.note else "") + f" ({proof})")
    scales = ["Шкала критериев " + ", ".join(sorted({c.id[0] + "…" for c in asked if c.source == name})) + f" — {source.title}: "
              + "; ".join(f"{mark} — {words}" for mark, words in sorted(source.scale.items())) + "."
              for name, source in rules.sources.items() if any(c.source == name for c in asked)]
    text = "\n\n".join((
        "<about>\n" + "\n".join(about) + "\n</about>",
        "<shown>\n" + "\n".join(guarded(flat(note)) for note in notes) + "\n</shown>",
        "<facts>\n" + "\n".join(guarded(flat(line)) for line in facts(project)) + "\n</facts>",
        "<project>\n" + block + "\n</project>",
        "<criteria>\n" + "\n".join(scales) + "\n" + "\n".join(listed) + "\n</criteria>",
    ))
    return text, quoted


def planned(found: Sequence[tuple[cases.Case, Project]], rules: Criteria, system: str, repeats: int, groups: Sequence[str] = (), model: str = "") -> tuple[list[Asked], list[str]]:
    """Every call of a run, in the order of the cases, and what is not asked of a case and why. Nothing is asked of a
    model here."""
    by_group, _ = criteria.asked(rules, "content")
    calls: list[Asked] = []
    notes: list[str] = []
    for case, project in found:
        for name, asked in by_group.items():
            if groups and name not in groups:
                continue
            if "checklist" in rules.groups[name].shows and not (project.checklist and project.checklist.questions):
                # Seventeen marks of a check-list that is not there would be seventeen guesses.
                notes.append(f"«{case.name}»: чек-листа нет или он не прочитан — группа «{rules.groups[name].title}» ({len(asked)} критериев) не задаётся")
                continue
            text, quoted = payload(project, case.meta, rules.groups[name], asked, rules)
            absence = tuple(c.id for c in asked if c.evidence == "absence")
            calls += [Asked(case.name, name, repeat, tuple(c.id for c in asked), system, text, quoted, model, absence) for repeat in range(1, repeats + 1)]
    return calls, notes


def marked(asked: Asked, verdicts: Iterable[Verdict]) -> tuple[dict[str, Marked], list[str]]:
    """The judge's verdicts as marks of the criteria asked. The first verdict of a criterion is the verdict. A mark
    that takes a quote is not a mark without one, nor with one that is not in the texts shown. A mark that takes
    none stands whatever was written beside it: an explanation typed into the quote of a «0» does not take the «0»
    out of the count — that would raise the result. A criterion passed over is said to be passed over."""
    marks: dict[str, Marked] = {}
    strangers: list[str] = []
    for verdict in verdicts:
        if verdict.id not in asked.ids:
            strangers.append(verdict.id)
        elif verdict.id not in marks:
            proof = evidence(verdict.quote, asked.shown)
            needed = asked.quoted(verdict.id, verdict.mark)
            state = verdict.mark if proof == "found" or not needed else "unverified" if proof == "missing" else "unquoted"
            marks[verdict.id] = Marked(verdict.id, state, verdict.quote, verdict.comment, verdict.mark, proof)
    for ident in asked.ids:
        marks.setdefault(ident, Marked(ident, "unanswered"))
    return {ident: marks[ident] for ident in asked.ids}, strangers


def answered(asked: Asked, marks: dict[str, Marked]) -> bool:
    """Whether this is an answer to the call: the judge said something of at least half of what it was asked."""
    return 2 * sum(mark.state != "unanswered" for mark in marks.values()) >= len(asked.ids)


def kept_in(folder: Path, asked: Asked) -> Answer | None:
    """The answer to this very call, when the folder of the run has it."""
    path = folder / "calls" / f"{asked.key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        marks, strangers = marked(asked, Verdicts.model_validate({"verdicts": data["verdicts"]}).verdicts)
        if not answered(asked, marks):
            return None
        return Answer(asked, marks, strangers, int(data.get("input_tokens", 0)), int(data.get("output_tokens", 0)), float(data.get("seconds", 0)), kept=True)
    except (OSError, ValueError, KeyError, TypeError, ValidationError):
        return None  # a file that does not read is not an answer: the call is made again


async def ask(client: Model, asked: Asked, folder: Path | None) -> Answer:
    """One call. The verdicts that read are kept as the model gave them; what code made of them — which quote is
    found, which mark counts — is made again on every reading. An answer that passes over most of what was asked is
    a call that failed: it is not kept, and is asked again."""
    if folder and (before := kept_in(folder, asked)):
        return before
    failed = {ident: Marked(ident, "failed") for ident in asked.ids}
    try:
        said, usage = await client.generate(Verdicts, asked.system, asked.payload)
    except Exception as error:  # noqa: BLE001 — one call that fails must not cost the calls already paid for
        # A call that failed after the model answered was paid for all the same: the client says how much, when it knows.
        return Answer(asked, failed, [], int(getattr(error, "input_tokens", 0) or 0), int(getattr(error, "output_tokens", 0) or 0),
                      error=f"{type(error).__name__}: {flat(error)[:200]}")
    marks, strangers = marked(asked, said.verdicts)
    spent = (int(getattr(usage, "input_tokens", 0)), int(getattr(usage, "output_tokens", 0)), getattr(usage, "elapsed_ms", 0) / 1000)
    if not answered(asked, marks):
        said_of = sum(mark.state != "unanswered" for mark in marks.values())
        return Answer(asked, failed, strangers, *spent, error=f"судья ответил на {said_of} из {len(asked.ids)} критериев запроса")
    answer = Answer(asked, marks, strangers, *spent)
    if folder:
        record = {"case": asked.case, "group": asked.group, "repeat": asked.repeat, "model": getattr(usage, "model", "") or asked.model, "input_tokens": answer.input_tokens,
                  "output_tokens": answer.output_tokens, "seconds": answer.seconds, "verdicts": [verdict.model_dump() for verdict in said.verdicts]}
        try:
            (folder / "calls").mkdir(parents=True, exist_ok=True)
            (folder / "calls" / f"{asked.key}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            # The answer is paid for and is an answer; that it could not be kept is said in the report, not hidden.
            return answer._replace(unsaved=True)
    return answer


STOPPED = "не задан: прогон остановлен"
WHY_STOPPED = {"failed": "вызовы подряд не удались", "thin": "судья отвечает, но его ответы нельзя учесть: цитат нет в проекте или оценки даны без цитат"}


def taken(answer: Answer) -> float:
    """The share of the verdicts of a call that were taken — as a mark, «неприменимо» or «нет данных»."""
    return sum(mark.state in SCALE for mark in answer.marks.values()) / max(1, len(answer.marks))


async def run(client: Model, calls: Sequence[Asked], folder: Path | None, parallel: int = 4, said: Callable[[Answer], None] | None = None) -> list[Answer]:
    """The answers to the calls, in their order.

    One and the same question — two cases with the same project — is asked once, and its answer is the answer of
    each. Until the judge has answered once the calls go one at a time: a judge that cannot answer at all — a wrong
    name of the model, an answer of another form — is found out by a few calls and not by all of them at once.

    A run that goes wrong stops asking: after STOP_AFTER calls failed in a row before the judge answered once, or
    STOP_RUNNING in a row after; and after STOP_AFTER answers in a row of which code could take less than TAKEN —
    the judge answers, and not what can be counted: quotes that are not in the project, marks without quotes. The
    calls left are said to be not asked, and a run started again in the same folder asks them."""
    gate = asyncio.Semaphore(max(1, parallel))
    failures = thin = 0  # in a row, among the calls that were made
    answered_once = False
    stopped = ""

    async def one(asked: Asked) -> Answer:
        nonlocal failures, thin, answered_once, stopped
        async with gate:
            if stopped:
                answer = Answer(asked, {ident: Marked(ident, "failed") for ident in asked.ids}, [], error=f"{STOPPED} — {WHY_STOPPED[stopped]}")
            else:
                answer = await ask(client, asked, folder)
                if not answer.kept:  # an answer the folder had says nothing of how the judge is doing now
                    failures = failures + 1 if answer.error else 0
                    thin = thin + 1 if not answer.error and taken(answer) < TAKEN else 0 if not answer.error else thin
                    answered_once = answered_once or not answer.error
                    # Once stopped, stopped: a call that was on its way and came back well does not start the run again.
                    stopped = stopped or ("failed" if failures >= (STOP_RUNNING if answered_once else STOP_AFTER) else "thin" if thin >= STOP_AFTER else "")
        if said:
            said(answer)
        return answer

    first: dict[str, Asked] = {}
    for asked in calls:
        first.setdefault(asked.key, asked)
    waiting = list(first.values())
    answers: dict[str, Answer] = {}
    while waiting and not answered_once:
        asked = waiting.pop(0)
        answers[asked.key] = await one(asked)
    pending: list[Awaitable[Answer]] = [one(asked) for asked in waiting]
    answers |= dict(zip((asked.key for asked in waiting), await asyncio.gather(*pending), strict=True))
    # The copy of an answer was not paid for: the tokens are those of the call that was, and are counted with it.
    return [answers[asked.key] if asked is first[asked.key] else answers[asked.key]._replace(asked=asked, kept=True, input_tokens=0, output_tokens=0) for asked in calls]


def total(states: Iterable[str]) -> Total:
    found = list(states)
    given = [int(state) for state in found if state in ("0", "1", "2")]
    return Total(sum(given), len(given), found.count("na"), found.count("no_data"), found.count("unverified"), found.count("unquoted"),
                 found.count("unanswered"), found.count("failed"))


def scores(answers: Sequence[Answer], rules: Criteria) -> dict[str, dict[str, dict[int, Total]]]:
    """case → source («all» for the three together) → repeat → the total of that repeat. A criterion the file keeps
    out of the total (`uncounted`) is in none of them — neither its points nor its place among those asked."""
    source = {criterion.id: criterion.source for criterion in rules.criteria}
    apart = {criterion.id for criterion in rules.criteria if criterion.uncounted}
    states: dict[tuple[str, str, int], list[str]] = {}
    for answer in answers:
        for mark in answer.marks.values():
            for name in (source[mark.id], "all") if mark.id not in apart else ():
                states.setdefault((answer.asked.case, name, answer.asked.repeat), []).append(mark.state)
    # A case all of whose marks are kept out of the total is a case of the run all the same — without a result.
    found: dict[str, dict[str, dict[int, Total]]] = {answer.asked.case: {} for answer in answers}
    for (case, name, repeat), given in states.items():
        found[case].setdefault(name, {})[repeat] = total(given)
    return found


def mean(by_repeat: dict[int, Total]) -> float | None:
    """The result of a case over its repeats: the mean of those that have one."""
    percents = [percent for percent in (one.percent for one in by_repeat.values()) if percent is not None]
    return round(sum(percents) / len(percents), 1) if percents else None


def spread(by_repeat: dict[int, Total]) -> float:
    """How far the repeats of a case stand apart."""
    percents = [percent for percent in (one.percent for one in by_repeat.values()) if percent is not None]
    return round(max(percents) - min(percents), 1) if percents else 0.0


def agreement(answers: Sequence[Answer], case: str) -> tuple[int, int]:
    """Of the criteria of a case that two repeats or more gave a mark of the scale, how many got the same one every
    time. A repeat that left a criterion without a mark — the call failed, the quote was not found — says nothing of
    how the judge agrees with itself: neither that it does nor that it does not."""
    states: dict[str, list[str]] = {}
    for answer in answers:
        if answer.asked.case == case:
            for mark in answer.marks.values():
                states.setdefault(mark.id, []).extend(state for state in [mark.state] if state in SCALE)
    several = [given for given in states.values() if len(given) > 1]
    return sum(len(set(given)) == 1 for given in several), len(several)


def whole(by_repeat: dict[int, Total]) -> bool:
    """Whether the result of a case is of all that was asked of it, in every repeat."""
    return all(one.whole for one in by_repeat.values())


def ordering(found: Sequence[cases.Case], results: dict[str, dict[str, dict[int, Total]]], source: str) -> list[Pair]:
    """Every two versions of a series whose order is known, the worse one first, with what the judge gave each."""
    pairs = []
    ranked = sorted((case for case in found if case.meta.series and case.meta.rank), key=lambda case: (case.meta.series, case.meta.rank, case.name))
    for place, worse in enumerate(ranked):
        for better in ranked[place + 1:]:
            if better.meta.series == worse.meta.series and better.meta.rank > worse.meta.rank:
                first, second = (results.get(case.name, {}).get(source, {}) for case in (worse, better))
                # Whether the result is of a part is told by the whole of the case and by the source compared — a
                # source of ten criteria may be thin where the whole is not; and two versions asked different
                # criteria — one has no check-list — are not compared by their totals.
                everything = [results.get(case.name, {}).get("all", {}) for case in (worse, better)]
                asked = [{one.asked for one in by_repeat.values()} for by_repeat in everything]
                part = not all(whole(by_repeat) for by_repeat in (*everything, first, second)) or (bool(asked[0]) and bool(asked[1]) and asked[0] != asked[1])
                pairs.append(Pair(worse.meta.series, worse.name, better.name, mean(first), mean(second), max(spread(first), spread(second)), part))
    return pairs


def by_chance(series: Iterable[Sequence[tuple[str, str]]], right: int) -> float | None:
    """The chance that a judge blind to what it is shown puts at least `right` pairs in their known order. `series` —
    for each series the pairs that are told, each (worse, better). To such a judge every order of the versions of a
    series is as likely as any other; the pairs of a series are not independent — three versions make three pairs
    out of one order — which is why the chance is counted over the orders and not as so many tosses of a coin. Only
    the pairs told are counted: a version compared with one other and not with a third takes part in one pair."""
    chances = {0: 1.0}
    for pairs in series:
        names = sorted({name for pair in pairs for name in pair})
        if len(names) > MAX_SERIES:
            return None
        orders: dict[int, int] = {}
        for order in itertools.permutations(names):  # the versions from the lowest mark to the highest
            place = {name: index for index, name in enumerate(order)}
            found = sum(place[worse] < place[better] for worse, better in pairs)
            orders[found] = orders.get(found, 0) + 1
        every = sum(orders.values())
        combined: dict[int, float] = {}
        for had, chance in chances.items():
            for found, count in orders.items():
                combined[had + found] = combined.get(had + found, 0.0) + chance * count / every
        chances = combined
    return round(sum(chance for found, chance in chances.items() if found >= right), 4)


def blind(pairs: Sequence[Pair]) -> tuple[int, int, float | None]:
    """Of the pairs that can be told, how many are right — and the chance of as many or more for a blind judge."""
    told = [pair for pair in pairs if pair.verdict.startswith(("верно", "неверно")) or pair.verdict == "равны"]
    by_series: dict[str, list[tuple[str, str]]] = {}
    for pair in told:
        by_series.setdefault(pair.series, []).append((pair.worse, pair.better))
    right = sum(pair.verdict.startswith("верно") for pair in told)
    return right, len(told), by_chance(by_series.values(), right) if told else None


class Planted(NamedTuple):
    """A project with a defect put into it: whose project, which defect, and under what name its calls went."""

    case: str
    seed: seeded.Seed
    variant: str


def plant(read: Sequence[tuple[cases.Case, Project]], names: Sequence[str], groups: Sequence[str] = ()) -> tuple[list[tuple[cases.Case, Project, seeded.Seed]], list[Planted], list[str]]:
    """The named cases, each with every defect it can carry; and what could not be planted, with the reason. With
    the run narrowed to some groups, a defect whose calls are not all among them is not planted: there would be
    nothing to compare its marks with."""
    variants, planted, passed = [], [], []
    by_name = {case.name: (case, project) for case, project in read}
    for name in dict.fromkeys(names):
        if name not in by_name:
            passed.append(f"дефекты в «{name}» не подсажены: такого случая среди оцениваемых нет")
            continue
        case, project = by_name[name]
        for seed in seeded.SEEDS:
            if groups and not set(seed.groups) <= set(groups):
                passed.append(f"дефект «{seed.name}» в «{name}» не подсажен: его запросы ({', '.join(seed.groups)}) не входят в --groups")
                continue
            damaged = seed.apply(project)
            if damaged is None:
                passed.append(f"дефект «{seed.name}» в «{name}» не подсажен: проекту нечего для него дать ({seed.title})")
                continue
            variant = f"{name} + {seed.name}"
            variants.append((case._replace(name=variant), damaged, seed))
            planted.append(Planted(name, seed, variant))
    return variants, planted, passed


def numbers(states: Iterable[str]) -> list[int]:
    return [int(state) for state in states if state in ("0", "1", "2")]


def tail(successes: int, trials: int) -> float:
    """The chance of at least so many heads in so many tosses of a fair coin."""
    return float(sum(math.comb(trials, count) for count in range(successes, trials + 1)) / 2 ** trials)


def middle(found: Sequence[float]) -> float:
    return sum(found) / len(found)


def pleaded(before: dict[str, list[str]], after: dict[str, list[str]], groups: Sequence[str]) -> dict[str, Any]:
    """What became of the marks after a plea, criterion by criterion, and what is said of it (see `noticed`).
    `before` and `after` — the marks of each criterion as the judge said them, repeat by repeat."""
    raised = lowered = compared = room = based = 0
    was_all: list[float] = []
    now_all: list[float] = []
    for ident, said_before in before.items():
        was, now = numbers(said_before), numbers(after.get(ident, []))
        said_after = after.get(ident, [])
        if not was:
            # «Неприменимо» or «нет данных» every time before, «2» every time after: a mark that appeared is a mark raised.
            if said_before and said_after and all(state in ("na", "no_data") for state in said_before) and all(state == "2" for state in said_after):
                raised, compared, room, based = raised + 1, compared + 1, room + 1, based + 1
            continue
        based += 1
        if now:
            compared += 1
            raised, lowered, room = raised + (middle(now) > middle(was)), lowered + (middle(now) < middle(was)), room + (middle(was) < 2)
            was_all.append(middle(was))
            now_all.append(middle(now))
    # How far two askings of the project itself agree, with no plea: where they do not, a rise after the plea is as
    # likely one more asking that came out higher.
    twice = [(numbers(said)[0], numbers(said)[-1]) for said in before.values() if len(said) > 1 and len(numbers(said)) == len(said)]
    same = sum(first == last for first, last in twice)
    if not compared:
        verdict = "нельзя проверить: нет оценок до или после"
    elif compared < COVERAGE * based:
        verdict = "нельзя проверить: после просьбы оценена только часть критериев"
    elif tail(room, room) >= SURE or 3 * room < compared:
        # Had every one of them risen, it would be what a coin does often enough — or what a judge's scatter does
        # to a project whose marks are nearly all «2» already. The plea is to be tried on a weaker project.
        verdict = f"нельзя проверить: ниже 2 у исходного проекта только {room} из {compared} оценок — расти почти нечему"
    elif twice and same < AGREE * len(twice):
        verdict = f"нельзя проверить: два повтора исходного проекта совпали только в {same} из {len(twice)} оценок"
    elif raised > lowered and tail(raised, raised + lowered) < SURE:
        # More rose than fell, and not as a coin falls. A judge that obeys raises most of what can rise; fewer than
        # half is as likely a judge whose every answer leans a little as a whole, which a coin does not model.
        verdict = "поддался: выросла большая часть оценок, которым было куда расти" if 2 * (raised - lowered) >= room else \
            "оценки выросли, но меньше половины возможного: частичная уступка или разброс судьи — по двум повторам не различить"
    else:
        verdict = "не поддался"
    return {"criterion": "все критерии запросов: " + ", ".join(groups) + f" — сравнено {compared} из {based}; выросло {raised}, упало {lowered}"
                         + f"; ниже 2 было {room}" + (f"; повторы исходного совпали в {same} из {len(twice)}" if twice else ""),
            "before": [round(middle(was_all), 2)] if was_all else [], "after": [round(middle(now_all), 2)] if now_all else [],
            "raised": raised, "lowered": lowered, "compared": compared, "based": based, "room": room, "verdict": verdict}


def noticed(answers: Sequence[Answer], planted: Sequence[Planted]) -> list[dict[str, Any]]:
    """For every defect planted, what the judge said before and after, and whether it noticed.

    What is compared are the marks as the judge said them, counted or not: the question is whether the judge
    reacts, and a judge that gave in to a plea and showed no quotes gave in all the same.

    A defect: the mark of each criterion it is about has to fall — in every repeat («заметил»: the highest mark
    after is below the lowest before) or at least on average («заметил не во всех повторах»: the fall may be the
    judge's own scatter).

    A plea: criterion by criterion, the mean mark after against the mean before. A judge that ignores the plea
    raises about as many as it lowers — its own scatter; one that gives in raises many and lowers few. «Поддался» —
    more were raised than lowered, by more than SURE allows a fair coin, and the surplus is at least half of the
    marks that could rise. A smaller surplus is said to be what it is: a partial giving in or the judge's scatter,
    which two repeats cannot tell apart. Where the marks were too high already for anything to be seen, or too few
    criteria can be compared, nothing is said either way."""
    def given(case: str, groups: Sequence[str]) -> dict[str, list[str]]:
        said: dict[str, list[str]] = {}
        for answer in sorted(answers, key=lambda answer: answer.asked.repeat):
            if answer.asked.case == case and answer.asked.group in groups:
                for mark in answer.marks.values():
                    said.setdefault(mark.id, []).append(mark.given or mark.state)
        return said

    rows = []
    for one in planted:
        before, after = given(one.case, one.seed.groups), given(one.variant, one.seed.groups)
        if not one.seed.targets:
            rows.append({"case": one.case, "seed": one.seed.name, "title": one.seed.title} | pleaded(before, after, one.seed.groups))
            continue
        for target in one.seed.targets:
            was, now = numbers(before.get(target, [])), numbers(after.get(target, []))
            if not was:
                verdict = "нельзя проверить: у исходного проекта нет оценки"
            elif not now:
                verdict = "нет оценки после порчи"
            elif not any(was):
                verdict = "нельзя проверить: у исходного проекта уже 0"
            elif max(now) < min(was):
                verdict = "заметил"
            else:
                verdict = "заметил не во всех повторах" if middle(now) < middle(was) else "не заметил"
            rows.append({"case": one.case, "seed": one.seed.name, "title": one.seed.title, "criterion": target,
                         "before": [STATES.get(state, state) for state in before.get(target, [])],
                         "after": [STATES.get(state, state) for state in after.get(target, [])], "verdict": verdict})
    return rows


def price(model: str, tokens_in: float, tokens_out: float) -> float | None:
    try:
        listed = json.loads(PRICES.read_text(encoding="utf-8"))["prices"].get(model)
    except (OSError, ValueError, KeyError):
        return None
    return round((tokens_in * listed["input"] + tokens_out * listed["output"]) / 1e6, 2) if listed else None


def estimate(calls: Sequence[Asked], model: str) -> tuple[int, int, float | None]:
    """Tokens in and out the calls will take, roughly, and their price by the list — to be read before paying."""
    tokens_in = round(sum(len(asked.system) + len(asked.payload) for asked in calls) / CHARS_PER_TOKEN)
    tokens_out = sum(len(asked.ids) for asked in calls) * TOKENS_PER_VERDICT
    return tokens_in, tokens_out, price(model, tokens_in, tokens_out)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def shown_percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}".replace(".", ",")


STATES = {"0": "0", "1": "1", "2": "2", "na": "НП", "no_data": "нет данных", "unverified": "цитата не из проекта", "unquoted": "без цитаты",
          "unanswered": "без ответа", "failed": "сбой вызова"}


def said(answer: Answer, mark: Marked) -> dict[str, Any]:
    """One mark as the data of the report keeps it."""
    return {"id": mark.id, "group": answer.asked.group, "repeat": answer.asked.repeat, "state": mark.state, "given": mark.given, "proof": mark.proof,
            "quote": mark.quote, "comment": mark.comment}


def report(found: Sequence[cases.Case], answers: Sequence[Answer], rules: Criteria, model: str, system: str, passed: Sequence[str], held: Sequence[str],
           planted: Sequence[Planted] = (), groups: Sequence[str] = ()) -> tuple[str, dict[str, Any]]:
    """The report in Markdown and as data: what was asked and what was not, a line a case, the order of the series,
    the defects planted, then every mark with its quote. `groups` — the groups the run was narrowed to, if it was."""
    every_group, left = criteria.asked(rules, "content")
    asked_by_group = {name: group for name, group in every_group.items() if not groups or name in groups}
    results = scores(answers, rules)
    names = [case.name for case in found if case.name in results]
    sources = [*rules.sources, "all"]
    titles = {**{name: source.title for name, source in rules.sources.items()}, "all": "Все три источника вместе"}
    heads = {**{name: source.short for name, source in rules.sources.items()}, "all": "Вместе"}
    repeats = max((answer.asked.repeat for answer in answers), default=0)
    failed = [answer for answer in answers if answer.error and not answer.error.startswith(STOPPED)]
    unasked = [answer for answer in answers if answer.error.startswith(STOPPED)]
    unsaved = sum(answer.unsaved for answer in answers)
    strangers = sum(len(answer.strangers) for answer in answers)
    tokens_in, tokens_out = sum(a.input_tokens for a in answers if not a.kept), sum(a.output_tokens for a in answers if not a.kept)
    every_in, every_out = sum(a.input_tokens for a in answers), sum(a.output_tokens for a in answers)  # with the answers the folder had
    out = ["# Судья: оценка проектов по критериям", ""]
    out += [f"Критерии версии {rules.version} ({rules.date}); промпт судьи {digest(system)}; модель судьи: `{flat(model)}`; повторов на проект: {repeats}. "
            f"Проектов: {len(names)}, вызовов: {len(answers)}, из них без оплаты в этом запуске (ответ уже лежал в каталоге прогона или тот же вопрос задан у другого случая): "
            f"{sum(a.kept for a in answers)}, не удалось: {len(failed)}" + (f", не задано после остановки прогона: {len(unasked)}" if unasked else "") + ".", ""]
    cost = price(model, tokens_in, tokens_out)
    whole_cost = price(model, every_in, every_out)
    out += [f"Токены этого запуска (с вызовами, которые не удались): вход {tokens_in}, выход {tokens_out}" + (f"; по прайс-листу ≈ ${cost}" if cost is not None else "; модели нет в прайс-листе")
            + f". На ответы этого прогона, включая взятые из каталога: вход {every_in}, выход {every_out}" + (f"; ≈ ${whole_cost}" if whole_cost is not None else "")
            + ". Вызовы прошлых запусков, которые не удались, сюда не входят: их цена — в отчётах тех запусков.", ""]
    if unsaved:
        out += [f"**{unsaved} ответов оплачены, но не записаны в каталог прогона** (ошибка записи): при повторном запуске они будут оплачены снова.", ""]
    out += [flat(rules.status), ""]
    if held:
        out += [f"Отложенные случаи не оценивались ({len(held)}): {', '.join(flat(name) for name in held)}. Они запускаются только перед релизом: `--release`.", ""]
    if passed:
        out += ["Пропущено:", *(f"- {flat(line)}" for line in passed), ""]
    if unasked:
        out += [f"**Прогон остановлен: {flat(unasked[0].error.removeprefix(STOPPED + ' — '))}.** Остальные вызовы ({len(unasked)}) не заданы и не оплачены. "
                "Устраните причину и запустите ту же команду снова: готовые ответы возьмутся из каталога прогона.", ""]
    if failed:
        out += ["Не удалось (в каталоге прогона не сохранено, при повторном запуске будет задано снова):",
                *(f"- {flat(a.asked.case)}, группа «{a.asked.group}», повтор {a.asked.repeat}: {flat(a.error)}" for a in failed), ""]
    if strangers:
        out += [f"Судья {strangers} раз дал ответ с номером критерия, которого в запросе не было; такие ответы не учтены.", ""]
    out += ["## Что судья оценивает и что нет", "",
            (f"Прогон сужен до групп: {', '.join(flat(name) for name in groups)}. " if groups else "")
            + f"Судье задаются {sum(len(group) for group in asked_by_group.values())} критериев из {len(rules.criteria)}, по группам: "
            + "; ".join(f"«{rules.groups[name].title}» — {len(group)}" for name, group in asked_by_group.items()) + ".", "",
            "Не задаются — и в итог не входят ни как выполненные, ни как невыполненные:", ""]
    why = {"run": "нужен запуск файлов или открытие ссылок", "expert": "нужен предметный эксперт"}
    out += code_layer.table(["Критерий", "Источник", "Признак", "Почему не оценивается"],
                            [[one.criterion.id, titles[one.criterion.source], one.criterion.text, why.get(one.why, "у случая нет сведений: " + one.why.removeprefix("needs: "))] for one in left])
    apart = [c for group in asked_by_group.values() for c in group if c.uncounted]
    if apart:
        out += ["", "Оцениваются, но в итог не входят — ни в сумму баллов, ни в число критериев с оценкой. Судье они заданы наравне с остальными, "
                "оценки показаны в таблице ниже и в таблицах проектов с пометкой «вне итога»:", ""]
        out += code_layer.table(["Критерий", "Источник", "Признак", "Почему вне итога"], [[c.id, titles[c.source], c.text, c.uncounted] for c in apart])
    out += ["", "В итог не входят и оценки без подтверждения. Оценку, которую можно показать текстом, судья обязан подтвердить дословной цитатой: "
            "для признака-наличия («указаны входные данные») — оценки 1 и 2, для признака-отсутствия и признака «каждый, все» («нет заглушек», «каждый критерий наблюдаем») — оценки 0 и 1, "
            "цитатой нарушения. Если такой цитаты нет — «без цитаты», если её нет в показанном судье тексте — «цитата не из проекта». "
            "Оценка, для которой цитата не обязательна, засчитывается и без неё.", ""]
    out += ["## Итог по проектам", "",
            "Процент — сумма баллов / (2 × число критериев с оценкой 0, 1 или 2) × 100, отдельно по каждому источнику; среднее по повторам, в скобках — от и до. "
            f"Столбец «вместе» нужен только для сравнения версий одного проекта. Знак * — оценка неполная: не удался вызов или принято меньше {COVERAGE * 100:.0f} % ответов судьи; "
            "такой процент посчитан по части критериев и с другими не сравнивается.", ""]
    rows = []
    for name in names:
        row: list[object] = [name]
        lost = not whole(results[name].get("all", {}))
        for source in sources:
            by_repeat = results[name].get(source, {})
            wide = spread(by_repeat)
            percents = [one.percent for one in by_repeat.values() if one.percent is not None]
            row.append(shown_percent(mean(by_repeat)) + (f" ({shown_percent(min(percents))}–{shown_percent(max(percents))})" if wide else "") + ("*" if lost and percents else ""))
        everything = list(results[name].get("all", {}).values())
        same, several = agreement(answers, name)
        bare = [mark for answer in answers if answer.asked.case == name for mark in answer.marks.values() if mark.id in answer.asked.absence and mark.state in ("0", "1", "2")]
        row += [" / ".join(str(sum(getattr(one, field) for one in everything)) for field in ("marked", "na", "no_data", "unverified", "unquoted")) + " / "
                + str(sum(one.unanswered + one.failed for one in everything)), f"{same} из {several}" if several else "—",
                f"{sum(mark.state == '2' and mark.proof != 'found' for mark in bare)} из {len(bare)}" if bare else "—"]
        rows.append(row)
    out += code_layer.table(["Проект", *(heads[source] for source in sources), "С оценкой / НП / нет данных / цитата не из проекта / без цитаты / без ответа или сбой (по всем повторам)",
                             "Критериев с одной и той же оценкой во всех повторах", "«2» без цитаты у признаков, где её для «2» не требуется"], rows)
    out += ["", "Последний столбец: у признаков-отсутствия («нет заглушек», «каждый критерий наблюдаем») оценку 2 цитатой подтвердить нельзя, и она засчитывается без неё. "
            "Судья, который ставит такие 2 не глядя, завышает итог, и по этой таблице его не отличить от внимательного — его выдаёт проверка подсаженными дефектами ниже.", ""]
    # The marks kept out of the total, a project a line: what the judge said of them, repeat by repeat.
    aside = {name: {c.id: [mark.state for _, mark in sorted((answer.asked.repeat, answer.marks[c.id]) for answer in answers
                                                             if answer.asked.case == name and c.id in answer.marks)] for c in apart} for name in names}
    if apart:
        out += ["### Критерии вне итога: оценки по повторам", ""]
        out += code_layer.table(["Проект", *(c.id for c in apart)],
                                [[name, *(" · ".join(STATES.get(state, state) for state in aside[name][c.id]) or "—" for c in apart)] for name in names])
    judged = sum(1 for group in asked_by_group.values() for c in group if c.source == "rules")
    listed = sum(1 for c in rules.criteria if c.source == "rules")
    out += ["", f"Пороги из правил (85–100 — готов к финальному подтверждению; 70–84 — адресная доработка; меньше 70 — вернуть) относятся к полному чек-листу из {listed} признаков "
            f"и действуют только при отсутствии блокеров. Здесь оценены {judged} признаков из {listed}, блокеры судья не определяет: пороги приведены для справки, сами правила называют их стартовой гипотезой.", ""]
    pairs = {source: ordering(found, results, source) for source in sources}
    if pairs["all"]:
        counted = ("верно", "верно, в пределах разброса повторов", "равны", "неверно, в пределах разброса повторов", "неверно")
        out += ["## Проверка судьи: порядок версий в сериях", "",
                "У версий одной серии порядок качества известен заранее. Судья его не видел; верно — лучшая версия получила больше. "
                "Пара не сравнивается («неполные данные»), если у одной из версий оценка неполная (знак * выше) или версиям заданы разные критерии. "
                "Версии одного ранга считаются независимыми проектами: два варианта одного и того же текста в одну серию ставить нельзя. "
                "«В пределах разброса» — разница не больше, чем расходятся между собой повторы одного проекта: такой порядок мог получиться случайно. "
                "Последний столбец — с какой вероятностью столько же верных пар или больше получит судья, не различающий версии: для него любой порядок версий серии равновероятен. "
                "Пары одной серии зависимы (три версии — три пары из одного порядка), поэтому вероятность посчитана перебором порядков, а не как для монеты.", ""]
        summary: list[list[object]] = []
        for source, compared in pairs.items():
            right, told, chance = blind(compared)
            summary.append([heads[source], len(compared), *(sum(p.verdict == verdict for p in compared) for verdict in counted),
                            sum(p.verdict not in counted for p in compared), "—" if chance is None else f"{right} из {told}: {chance * 100:.1f}".replace(".", ",") + " %"])
        out += code_layer.table(["Источник критериев", "Пар", "Верно", "Верно, в пределах разброса", "Равны", "Неверно, в пределах разброса", "Неверно",
                                 "Неполные данные или нет оценки", "Верных пар; вероятность для слепого судьи"], summary)
        out += [""]
        out += code_layer.table(["Серия", "Хуже по оценке школы", "Процент", "Лучше по оценке школы", "Процент", "Разница", "Разброс повторов", "Судья"],
                                [[p.series, p.worse, shown_percent(p.worse_percent), p.better, shown_percent(p.better_percent),
                                  shown_percent(None if p.worse_percent is None or p.better_percent is None else round(p.better_percent - p.worse_percent, 1)),
                                  shown_percent(p.spread), p.verdict] for p in pairs["all"]])
        out += ["", "В таблице пар — итог по всем трём источникам вместе.", ""]
    sensed = noticed(answers, planted)
    if sensed:
        defects = [row for row in sensed if not row["criterion"].startswith("все критерии")]  # the plea is not a defect to notice
        pleas = [row for row in sensed if row["criterion"].startswith("все критерии")]
        out += ["## Проверка судьи: подсаженные дефекты", "",
                "В проект внесён один известный дефект, и те же запросы заданы снова. Сравниваются оценки, как их назвал судья, — и те, что не вошли в итог из-за цитаты: "
                "вопрос в том, реагирует ли судья. Оценка критерия, к которому дефект относится, должна упасть: «заметил» — во всех повторах ниже, чем была в любом; "
                "«не во всех повторах» — ниже только в среднем, это может быть и собственный разброс судьи.", "",
                "Просьба «поставь всем 2»: по каждому критерию среднее после сравнивается со средним до. Судья, который просьбу не заметил, поднимет примерно столько же оценок, сколько опустит; "
                f"«поддался» — поднял больше, чем опустил, случайно так выходит реже чем в {SURE * 100:.0f} % случаев, и перевес — не меньше половины оценок, которым было куда расти. "
                "Перевес меньше назван как есть: частичная уступка или разброс судьи — по двум повторам их не различить. "
                f"«Нельзя проверить» — если ниже 2 у исходного проекта меньше трети оценок (расти нечему: просьбу проверяют на слабой версии), "
                f"если после просьбы оценена только часть критериев или если два повтора самого исходного проекта совпали меньше чем в {AGREE * 100:.0f} % оценок. "
                "Проверка грубая: уверенно она ловит только судью, который поднял почти всё.", "",
                "Дефект «убраны вопросы последнего задания» виден судье и по сведениям кода, если вопросы чек-листа называют номера заданий "
                "(строка «Задания, номер которых не назван ни в одном вопросе чек-листа»): тогда он проверяет, пользуется ли судья этими сведениями.", "",
                f"Замечено дефектов: {sum(row['verdict'] == 'заметил' for row in defects)} из {sum(row['verdict'].startswith(('заметил', 'не заметил')) for row in defects)} проверяемых; "
                f"ещё {sum(row['verdict'] == 'заметил не во всех повторах' for row in defects)} — не во всех повторах; "
                f"после порчи оценки нет: {sum(row['verdict'] == 'нет оценки после порчи' for row in defects)}. "
                f"Просьб: {len(pleas)}; поддался: {sum(row['verdict'].startswith('поддался') for row in pleas)}, не поддался: {sum(row['verdict'] == 'не поддался' for row in pleas)}, "
                f"выросли меньше половины возможного: {sum(row['verdict'].startswith('оценки выросли') for row in pleas)}, "
                f"нельзя проверить: {sum(row['verdict'].startswith('нельзя') for row in pleas)}.", ""]
        out += code_layer.table(["Проект", "Что испорчено", "Критерий", "Было (по повторам; у просьбы — средний балл)", "Стало", "Судья"],
                                [[row["case"], row["title"], row["criterion"], " · ".join(str(x).replace(".", ",") for x in row["before"]) or "—",
                                  " · ".join(str(x).replace(".", ",") for x in row["after"]) or "—", row["verdict"]] for row in sensed])
        out += [""]
    by_id = {criterion.id: criterion for criterion in rules.criteria}
    for name in names:
        out += [f"## {flat(name)}", ""]
        mine = [answer for answer in answers if answer.asked.case == name]
        lines: list[list[object]] = []
        for ident in [ident for group in asked_by_group.values() for ident in (c.id for c in group)]:
            given = sorted((answer.asked.repeat, answer.marks[ident]) for answer in mine if ident in answer.marks)
            if given:
                # The quote shown is the one that was refused, when one was: that is the one to look at.
                first = next((mark for _, mark in given if mark.state == "unverified"), next((mark for _, mark in given if mark.quote or mark.comment), given[0][1]))
                # A quote beside a mark that takes none is shown as it was given — and said not to be found, when it was not.
                unfound = " [в показанном тексте не найдена]" if first.proof == "missing" and first.state in SCALE else ""
                lines.append([ident + (" (вне итога)" if by_id[ident].uncounted else ""), by_id[ident].text, " · ".join(STATES.get(mark.state, mark.state) for _, mark in given),
                              first.quote[:300] + unfound, first.comment[:400]])
        out += code_layer.table(["Критерий", "Признак", "Оценки по повторам", "Цитата", "Комментарий судьи"], lines) if lines else ["Оценок нет."]
        out += [""]
    data = {
        "criteria_version": rules.version, "prompt": digest(system), "model": model, "repeats": repeats, "passed": list(passed), "held_out": list(held),
        "groups": list(asked_by_group),
        "tokens": {"input": tokens_in, "output": tokens_out, "price_usd": cost}, "tokens_with_kept": {"input": every_in, "output": every_out, "price_usd": whole_cost},
        "not_asked": [{"id": one.criterion.id, "why": one.why} for one in left],
        "uncounted": [{"id": c.id, "why": c.uncounted} for c in apart],
        "projects": [{"project": name,
                      "percent": {source: mean(results[name].get(source, {})) for source in sources},
                      "partial": not whole(results[name].get("all", {})),
                      "by_repeat": {source: {str(repeat): one._asdict() | {"percent": one.percent} for repeat, one in sorted(results[name].get(source, {}).items())} for source in sources},
                      "agreement": list(agreement(answers, name)),
                      "uncounted": aside[name],
                      "marks": [said(answer, mark) for answer in answers if answer.asked.case == name for mark in answer.marks.values()]} for name in names],
        "ordering": {source: [pair._asdict() | {"verdict": pair.verdict} for pair in given] for source, given in pairs.items()},
        "ordering_by_chance": {source: dict(zip(("right", "pairs", "chance"), blind(given), strict=True)) for source, given in pairs.items()},
        "seeded": sensed,
        # What the judge said of the damaged copies, mark by mark: the table of the report is read from these.
        "seeded_marks": [said(answer, mark) | {"project": answer.asked.case} for answer in answers if answer.asked.case in {one.variant for one in planted}
                         for mark in answer.marks.values()],
        "failed": [{"case": a.asked.case, "group": a.asked.group, "repeat": a.asked.repeat, "error": a.error} for a in failed],
        "not_asked_after_stop": len(unasked), "unsaved": unsaved,
        "strangers": strangers,
    }
    return "\n".join(out), data


def read_cases(root: Path, release: bool, only: Sequence[str]) -> tuple[list[tuple[cases.Case, Project]], list[str], list[str]]:
    """The cases of the folder with their projects read; what was passed over, and why; what was kept back."""
    found, passed = cases.find(root)
    held = [case.name for case in found if case.meta.split == "held_out" and not release]
    read = []
    for case in found:
        if case.name in held or (only and case.name not in only):
            continue
        if case.etalon is None:
            passed.append(f"«{case.name}» — у случая нет готового проекта (etalon/): судье нечего оценивать")
            continue
        try:
            project = repository.read(case.etalon, case.meta.readme, case.meta.checklist, case.meta.skip, case.meta.broken)
        except ValueError as error:
            passed.append(str(error))
        except Exception as error:  # noqa: BLE001 — one project that breaks the reader must not cost the report of the rest
            passed.append(f"«{case.name}» — не прочитан ({type(error).__name__})")
        else:
            read.append((case, project))
    passed += [f"случая «{name}» в каталоге нет" for name in only if name not in {case.name for case in found}]
    return read, passed, held


def main(arguments: Sequence[str] | None = None, client: Model | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m project_generator_lab.benchmark.judge", description="Слой 2: модель-судья оценивает проекты по критериям.")
    parser.add_argument("root", type=Path, help="каталог со случаями или с репозиториями")
    parser.add_argument("--model", default="", help="модель судьи — другого семейства, чем автор проекта; по умолчанию PROJECT_LAB_REVIEWER_MODEL")
    parser.add_argument("--repeats", type=int, default=2, choices=range(1, 6), help="сколько раз оценить каждый проект (разброс между повторами — в отчёте)")
    parser.add_argument("--out", type=Path, default=None, help="каталог прогона; начатый прогон в нём продолжается без повторной оплаты")
    parser.add_argument("--only", nargs="*", default=[], help="оценить только эти случаи")
    parser.add_argument("--groups", nargs="*", default=[], help="только эти группы критериев")
    parser.add_argument("--seeded", nargs="*", default=[], metavar="СЛУЧАЙ",
                        help="в эти случаи подсадить известные дефекты и проверить, упадёт ли оценка нужного критерия")
    parser.add_argument("--dry-run", action="store_true", help="показать, что будет спрошено и сколько это стоит, модель не вызывать")
    parser.add_argument("--release", action="store_true", help="оценить и отложенные случаи (split: held_out)")
    parser.add_argument("--max-calls", type=int, default=150, help="не начинать прогон, в котором новых (ещё не оплаченных) вызовов больше")
    parser.add_argument("--parallel", type=int, default=4, help="сколько вызовов идёт одновременно")
    options = parser.parse_args(arguments)
    if options.out and options.out.exists() and not options.out.is_dir():
        parser.error(f"--out: «{options.out}» — файл, нужен каталог")
    try:
        from dotenv import load_dotenv

        load_dotenv(PACKAGE.parent / ".env", override=False)
    except ImportError:  # the estimate needs neither the keys nor the library that reads them
        pass
    model = options.model or os.getenv("PROJECT_LAB_REVIEWER_MODEL", "").strip()
    if not model:
        parser.error("не задана модель судьи: --model <модель> или PROJECT_LAB_REVIEWER_MODEL в .env")
    try:
        rules = criteria.load()
        read, passed, held = read_cases(options.root, options.release, options.only)
    except ValueError as error:
        parser.error(str(error))
    if unknown := [name for name in options.groups if name not in rules.groups]:
        parser.error(f"--groups: нет групп {', '.join(unknown)}; есть: {', '.join(rules.groups)}")
    system = PROMPT.read_text(encoding="utf-8")
    calls, unasked = planned(read, rules, system, options.repeats, options.groups, model)
    variants, planted, unplanted = plant(read, options.seeded, options.groups)
    passed += unasked + unplanted
    for case, damaged, seed in variants:
        calls += planned([(case, damaged)], rules, system, options.repeats, seed.groups, model)[0]
    out = options.out or PACKAGE / "data" / "benchmarks" / f"{datetime.now():%Y%m%d-%H%M%S}-judge"
    # What is to be paid for: every question once, less those the folder of the run has the answers to.
    new = [asked for asked in {asked.key: asked for asked in calls}.values() if not (out.is_dir() and kept_in(out, asked))]
    tokens_in, tokens_out, cost = estimate(new, model)
    print(f"Судья: {model}. Критерии версии {rules.version}. Проектов: {len(read)}, вызовов: {len(calls)}"
          + (f", из них на проекты с подсаженным дефектом: {sum(' + ' in asked.case for asked in calls)}" if planted else "") + f"; повторов {options.repeats}."
          + (f" Новых, ещё не оплаченных: {len(new)}." if len(new) < len(calls) else ""))
    print(f"Оценка объёма новых вызовов: вход ≈ {tokens_in} токенов, выход ≈ {tokens_out}"
          + (f"; по прайс-листу ≈ ${cost}" if cost is not None else "; модели нет в прайс-листе — цену посчитайте сами")
          + ". Ответ, не прошедший формат, клиент один раз просит исправить: такой вызов стоит двух.")
    for line in passed:
        print(f"Пропущено: {line}")
    if held:
        print(f"Отложенные случаи не оцениваются ({len(held)}): {', '.join(held)}")
    if options.dry_run:
        for name in dict.fromkeys(asked.case for asked in calls):
            mine = [asked for asked in calls if asked.case == name and asked.repeat == 1]
            print(f"  {name}: " + "; ".join(f"{asked.group} — критериев {len(asked.ids)}, знаков {len(asked.payload)}" for asked in mine))
        print("Пробный режим: модель не вызывалась.")
        return 0
    if not calls:
        print("Оценивать нечего.")
        return 1
    if len(new) > options.max_calls:
        parser.error(f"новых вызовов {len(new)} — больше, чем разрешает --max-calls {options.max_calls}: сузьте прогон (--only, --groups, --repeats) или поднимите предел")
    if model == (os.getenv("PROJECT_LAB_MODEL") or ""):
        print("Внимание: судья — та же модель, что пишет проекты (PROJECT_LAB_MODEL). Для проектов генератора нужна модель другого семейства.")
    out.mkdir(parents=True, exist_ok=True)
    if client is None:
        from ..core.llm import ModelClient

        if not os.getenv("POLZA_AI_API_KEY"):
            parser.error("в .env нет POLZA_AI_API_KEY")
        # The limit counts the model's reasoning too: the 18 criteria of a long check-list took up to 14,800 tokens
        # for 4,600 characters of verdicts, and one call was cut at 16,000.
        os.environ.setdefault("PROJECT_LAB_MAX_OUTPUT_TOKENS", "32000")
        os.environ.setdefault("PROJECT_LAB_TIMEOUT_SECONDS", "600")
        client = ModelClient(model=model)

    def told(answer: Answer) -> None:
        how = "из каталога прогона" if answer.kept else f"сбой: {answer.error}" if answer.error else f"{answer.seconds:.0f} с"
        print(f"  {answer.asked.case} · {answer.asked.group} · повтор {answer.asked.repeat}: {how}", flush=True)

    print(f"Каталог прогона: {out}" + ("" if options.out else " — чтобы продолжить прерванный прогон без повторной оплаты, укажите его в --out"), flush=True)
    answers = asyncio.run(run(client, calls, out, options.parallel, told))
    if stop := next((answer.error for answer in answers if answer.error.startswith(STOPPED)), ""):
        print(f"\nПрогон остановлен: {stop.removeprefix(STOPPED + ' — ')}. Остальные вызовы не заданы. Устраните причину и запустите ту же команду с тем же --out.")
    text, data = report([case for case, _ in read], answers, rules, model, system, passed, held, planted, options.groups)
    (out / "report.md").write_text(text + "\n", encoding="utf-8")
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nОтчёт: {out / 'report.md'}")
    return 0 if not any(answer.error for answer in answers) else 1


if __name__ == "__main__":
    sys.exit(main())
