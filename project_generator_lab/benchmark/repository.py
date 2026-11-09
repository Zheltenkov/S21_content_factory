"""A project as it lies in its repository, read back into what it is made of.

The README of the school is a run of headings: the title with the annotation under it, the school's own blocks, the
introduction, the rules of the project, and the tasks — «Задание 00 — …», «Part 2. …», «Бонусное задание 1*: …».
A task may open with a two-column table: where to put the solution, which files, what is allowed and what is
forbidden. A task of this generator's own README is a run of blocks under bold lines instead — «**Что нужно
сделать**», «**Формат сдачи**». The check-list is YAML with the questions the peer goes through, one or more per task.

A project written the way of the bassin has quests for tasks: «Quest 3. Recursive», «Bonus Quest 5. Func». A quest is
a story, then the line it is given by — «Получен Quest 3. Создать программу src/quest3.c, которая…» — then the
examples. The only quest of a group project has no number: «Quest: Level 1. Hall» is a task when the line «Получен
Quest.» stands under it, and the heading of a group of quests when nothing does. «List 2» is a note of the theory.

Nothing is guessed. A heading that is not of a known kind stays a section under its own name (role «other»), a task
without a question and a question without a task are returned by name. What was read and what was not is for the
report to show.

What the reader does not know, and reads as it stands: a heading written as HTML or underlined with «===» is text;
«Часть 1. Введение» is a task, as «Part 1. Создание базы данных» is; a heading indented by two spaces or more is a
line of a list item, as the steps of the generator's own README are; the row above the dashes of a table is its
header, though somebody may have put «Директория | ex00» there; «Задание 3. Бонус: расчёт премии» is a bonus task,
as «Задание 6. Бонусное. Дамп памяти» is.

A repository kept in a working folder rather than in Git has its files under other names — «README.md.docx», a
check-list with the day in its name — and drafts beside them. Which file is the README is then said by whoever
keeps the case (`readme`, `checklist`, `skip` of its meta.yaml), never picked by its looks: a folder with three
READMEs has one that was given to the learners, and code does not know which.
"""

import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import yaml

from . import word

# A README or a check-list larger than this is not a text somebody wrote for a learner.
MAX_TEXT = 2_000_000
# The text of any other file is read up to this size; a larger file is known by its name and size only.
MAX_FILE = 300_000
MAX_FILES = 5000
MAX_ALIKE = 6
TEXT_FORMATS = {".md", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".py", ".sql", ".sh"}
# The names are looked for whatever their case: «README_rus.md» is the Russian README too.
READMES = ("README_RUS.md", "README.md")
CHECKLISTS = ("check-list_RUS.yml", "check-list.yml")

# One space before «#» is still a heading (an etalon has such a task); more is the indent of a list item, and the
# «#» there is a line of a step. «#» at the end close the heading only after a space: «Основы C#» keeps its «#».
HEADING = re.compile(r"^ ?(#{1,6})[ \t]+(.*)$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
TAG = re.compile(r"<[^>]+>")
# «Задание 05 — Аудит», «Part 2. Изменение данных», «Задание 1.2 Б», «Дополнительно. Part 4. Метаданные», «Бонусное задание 1*: …»,
# «Задание 7 (дополнительное). Один правильный вариант», «Задание 6. Бонусное. Дамп памяти» — the word of a bonus task stands
# before the heading, after the number, or as a sentence of its own before the title.
# «Quest 3. Recursive», «Bonus Quest 5. Func» are the tasks of a project written the way of the bassin.
TASK = re.compile(r"^(?P<label>(?P<bonus>(?:дополнительно|бонусн\w*|бонус|bonus)\W*)?(?:задание|упражнение|exercise|task|part|часть|quest|квест)\s+"
                  r"(?P<number>\d{1,6}(?:\.\d{1,6})*)(?!\d)\*?)\s*(?P<extra>\((?:дополнительн\w*|бонусн\w*|бонус|bonus|optional)\))?\s*"
                  r"(?:[.:—–-]\s*)?(?P<after>(?:дополнительн\w*|бонусн\w*|бонус|bonus)(?:\s+задание)?(?:\s*[.:]\s*|\s+[—–-]\s+|\s*$))?(?P<title>(?s:.*))$", re.I)
# (The title takes whatever is left, a break of a line too: a name of two lines then matches at once instead of
# being tried in every way its spaces can be shared out.)
# «Задания 1—2. Разведка и модули», «Задания 3, 4 и 6» — a question of the check-list asked about several tasks.
# The row is taken whole or not at all: «Задания 1—2 и 3.1» is not a row «1—2» with something after it.
JOINT = r"\s*(?:[—–-]|,|\bи\b|\band\b|&)\s*"
SEVERAL = re.compile(rf"^(?:задания|упражнения|части|exercises|tasks|parts|quests|квесты)\s+(?P<row>\d{{1,6}}(?:{JOINT}\d{{1,6}})+)(?!\d|\.\d|{JOINT}\d)", re.I)
MAX_SEVERAL = 20  # tasks a question may be about: no check-list has a question about more
# «Получен Quest 3. Создать программу…» — the line a quest is given by. A document of Word has it as a heading of the
# fourth level, this generator's README as a bold phrase the sentence goes on after; the quest of a group project
# has no number. «Получен Questionnaire» is not such a line, nor is one with a number no quest has.
STATEMENT = re.compile(r"^ ?(?P<level>#{1,6})?[ \t]*[*_]{0,3}получен[ \t]+(?:bonus[ \t]+)?quest\b(?:[ \t]+\d{1,6}\b)?(?![ \t]*\d)[ \t]*[.:!]?[*_]{0,3}[ \t]*(?P<said>.*)$", re.I)
# «Quest: Level 1. Hall», «Bonus Quest: Level 1. Hall» — a heading that numbers no quest (see parse_readme).
LONE = re.compile(r"^(?P<label>(?P<bonus>bonus\s+)?quest)s?\s*:\s*(?P<title>.*)$", re.I)
# «**Сдать:** src/task1.md» — what a quest of this generator's README is handed in by.
HANDED = re.compile(r"^[*_]{0,3}сдать[ \t]*:[*_]{0,3}[ \t]*(?P<what>\S.*)$", re.I)


def is_bonus(found: re.Match[str]) -> bool:
    return bool(found["bonus"] or found["extra"] or found["after"])
BONUS = re.compile(r"bonus|бонус|дополнительн", re.I)
# The first that fits. A chapter is known by its number and is «theory» only when its name says so: «Глава 2.
# Теоретический блок» is the theory, «Глава 1. Введение и инструкция» a chapter, not the rules. «List 2» is a note of
# the theory in a project written the way of the bassin, under «Chapter II» that does not say so.
ROLES = (("contents", r"^(содержание|оглавление|contents)$"), ("school", r"как учиться"), ("introduction", r"^(введение|introduction|преамбула)$"),
         ("rules", r"рекомендаци|правила|rules|инструкци"), ("theory", r"теор|theor|^list\s+\d"), ("conclusion", r"^(заключение|conclusion)$"))
CHAPTER = re.compile(r"^(chapter|глава)\s+[\divxlc]+\b", re.I)
GROUPS = (("allowed", r"разреш|allowed"), ("forbidden", r"запрещ|denied|forbidden"))
SEPARATOR = re.compile(r"^:?-+:?$")
# A block of code: in fences, or indented by four after an empty line and not an item of a nested list.
CODE = re.compile(r"^ {0,3}(```|~~~)|(?:\A|\n[ \t]*\n)(?: {4,}|\t)(?![-*+] |\d+[.)] )\S", re.M)
BLOCK = re.compile(r"^\*\*(?P<title>[^*]+)\*\*\s*$")  # a line that is one bold phrase opens a block of the task


class Section(NamedTuple):
    title: str
    role: str  # title, contents, school, chapter, introduction, rules, theory, conclusion — or other
    text: str


class Quest(NamedTuple):
    """A task given the way of the bassin, by its three parts."""

    story: str  # what stands before the line «Получен Quest N.»: how the learner comes to need what is asked
    statement: str  # the line itself without those words, as far as it goes: what to make
    rest: str  # what stands after it: the examples, the notes, the way to hand the work in


class Named(NamedTuple):
    """What the heading of a task says of it."""

    label: str
    number: str  # as the heading has it: «05», «1.2»; empty — the heading numbers nothing
    title: str
    bonus: bool


class Task(NamedTuple):
    label: str  # the heading as far as the number: «Задание 05», «Part 4»
    number: int  # the number of the label; of «Задание 1.2» — its first part; of a quest without a number — 1, the only one of its part
    ref: str  # the number as the label has it, without leading zeros: «5», «1.2» — what a question refers to; empty — the label has none
    title: str
    bonus: bool
    text: str  # what the task says, without its heading table
    raw: str  # every line under the heading as it stands, the table too: what the checks of words look through
    table: bool  # the task opens with a table
    submit: list[str]  # the rows of the table: where the solution goes and in which files
    allowed: list[str]
    forbidden: list[str]
    other: list[str]  # the rows under a bold row that is neither «Разрешено» nor «Запрещено»
    blocks: dict[str, str]  # the text under each bold line of the task, by the line; empty — the task has none
    quest: Quest | None = None  # the task is given by a line «Получен Quest N.»: its parts


class Readme(NamedTuple):
    title: str
    annotation: str
    sections: list[Section]  # everything that is not a task, in the order of the file
    tasks: list[Task]
    images: int
    unclosed: bool  # a block of code is opened and never closed: what follows it was read as code


class Question(NamedTuple):
    name: str
    text: str
    rating: str
    bonus: bool  # of a section the check-list itself calls a bonus one

    @property
    def code(self) -> bool:
        """The question shows a block of code. Code inside a line («`create index …`») is not counted."""
        return bool(CODE.search(self.text))


class Checklist(NamedTuple):
    introduction: str
    guidelines: str
    questions: list[Question]


class TextFile(NamedTuple):
    path: str  # from the root of the repository, with «/»
    size: int
    content: str  # empty for a file that is not text or is larger than MAX_FILE


class Project(NamedTuple):
    name: str
    readme_file: str
    readme: Readme
    checklist_file: str  # empty — the repository has none
    checklist: Checklist | None
    files: list[TextFile]  # everything else the repository holds
    notes: list[str]  # what was not read, and why
    broken: tuple[str, ...] = ()  # the files damaged on purpose — the data of a task about mending them
    text: str = ""  # the README as it was read, whole: what a reader of the project sees


class Paired(NamedTuple):
    """The tasks of the README against the questions of the check-list."""

    by: str  # «номеру» — questions name the tasks' numbers; «порядку» — none does, they are taken in order
    questions: dict[int, list[Question]]  # the index of a task in Readme.tasks → its questions
    tasks_without: list[str]  # the labels of the tasks no question asks about
    questions_without: list[str]  # the names of the questions about no task


class Fence:
    """Whether a line is code. A fence is three or more «`» or «~»; it is closed by as many of the same, alone on a
    line — a shorter run or the other mark inside it is code."""

    def __init__(self) -> None:
        self.mark = ""

    def __call__(self, line: str) -> bool:
        found = FENCE.match(line)
        if not self.mark:
            # «```select 1;``` — проверь» is code inside a line, not the opening of a block.
            self.mark = found[1] if found and not (found[1][0] == "`" and "`" in found[2]) else ""
            return bool(self.mark)
        if found and found[1][0] == self.mark[0] and len(found[1]) >= len(self.mark) and not found[2].strip():
            self.mark = ""
        return True


def plain(text: str) -> str:
    """A heading as it reads: no tags, no emphasis marks around it."""
    return TAG.sub("", text).strip().strip("_*").strip()


def body_of(lines: list[str]) -> str:
    """The lines under a heading as text: no empty lines around it, the indent of its first line kept — four spaces
    there are a block of code."""
    return "\n".join(lines).strip("\n").rstrip()


def role(title: str) -> str:
    if CHAPTER.match(title):
        return "theory" if re.search(r"теор|theor", title, re.I) else "chapter"
    return next((name for name, pattern in ROLES if re.search(pattern, title, re.I)), "other")


def heading_of(line: str) -> tuple[int, str] | None:
    """The level and the words of a heading; None — the line is not one. «#» at the end close it only after a space."""
    found = HEADING.match(line.rstrip())
    if not found:
        return None
    words = found[2]
    closed = words.rstrip("#")
    return len(found[1]), plain(closed if closed != words and closed[-1:] in ("", " ", "\t") else words)


def ref(number: str) -> str:
    return ".".join(str(int(part)) for part in number.split("."))


def cells(line: str) -> list[str]:
    """The cells of a row. «\\|» is a bar inside a cell — «cat a \\| wc» is one cell, not two."""
    row = line.strip().lstrip("|")
    bare = row.rstrip("|")
    row = bare + ("|" if bare.endswith("\\") and len(bare) < len(row) else "")  # the bar after «\» is the cell's own
    return [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", row)]


def heading_table(lines: list[str]) -> tuple[list[list[str]], list[str]]:
    """The table a task opens with, as rows of cells, and the lines after it. No table — no rows."""
    start = next((i for i, line in enumerate(lines) if line.strip()), len(lines))
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    rows = [cells(line) for line in lines[start:end]]
    # The table of a task has a column of names and a column of values. One column is a frame somebody drew around
    # a sample — a document from Google Docs keeps code so — and stays the text of the task.
    return (rows, lines[end:]) if rows and len(rows[0]) > 1 else ([], lines)


def blocks_of(lines: list[str]) -> dict[str, str]:
    found: dict[str, list[str]] = {}
    title, code = "", Fence()
    for line in lines:
        opened = None if code(line) else BLOCK.match(line)
        if opened:
            title = opened["title"].strip()
            found.setdefault(title, [])
        elif title:
            found[title].append(line)
    return {name: body_of(body) for name, body in found.items()}


def quest_of(lines: list[str]) -> Quest | None:
    """The parts of a task given by a line «Получен Quest N.»; None — it has no such line. The statement goes as far
    as its paragraph does. Where a document of Word made it a heading, a statement of several paragraphs is several
    headings of that level one after another: they are taken while they last."""
    code = Fence()
    start = next((index for index, line in enumerate(lines) if not code(line) and STATEMENT.match(line)), None)
    if start is None:
        return None
    opened = STATEMENT.match(lines[start])
    assert opened is not None
    level, said, end = opened["level"], [plain(opened["said"])], start + 1
    while end < len(lines):
        line = lines[end]
        heading = HEADING.match(line.rstrip())
        if level and not line.strip():
            # Between the headings of one statement the document has empty lines; after the last one it ends.
            after = next((later for later in lines[end:] if later.strip()), "")
            if not ((ahead := HEADING.match(after.rstrip())) and ahead[1] == level and not STATEMENT.match(after)):
                break
        elif level and heading and heading[1] == level and not STATEMENT.match(line):
            said.append(plain(heading[2]))
        elif level or not line.strip() or heading or line.lstrip().startswith("|") or FENCE.match(line):
            break
        else:
            said.append(line.strip())
        end += 1
    return Quest(body_of(lines[:start]), "\n".join(part for part in said if part), body_of(lines[end:]))


def handed_in(lines: list[str]) -> list[str]:
    """What a quest says to hand in, by its lines «Сдать: …» — outside blocks of code."""
    code = Fence()
    return [found["what"].strip() for line in lines if not code(line) and (found := HANDED.match(line.strip()))]


def task_of(named: Named, lines: list[str]) -> Task:
    rows, rest = heading_table(lines)
    title = named.title
    parts: dict[str, list[str]] = {"submit": [], "allowed": [], "forbidden": [], "other": []}
    group = "submit"
    # The row above the line of dashes is the header of the table, not a row of it: «Задание 00: Погружение в мир
    # SQL» there is the title of a task whose heading has none, anything else says nothing of the task.
    headed = len(rows) > 1 and all(SEPARATOR.match(cell) for cell in rows[1])
    if headed and (in_table := TASK.match(plain(rows[0][0]))):
        title = title or plain(in_table["title"])
    for row in rows[2:] if headed else rows:
        key = plain(row[0])
        value = re.sub(r"(?:\s*;)+\s*", "; ", TAG.sub(";", " | ".join(row[1:]))).strip("; ")  # «<br>» between two files
        if not value and row[0].strip().startswith("**"):
            # A bold row opens a group: «**Разрешено**», «**Запрещено**»; under another name the rows are kept apart.
            group = next((name for name, pattern in GROUPS if re.search(pattern, key, re.I)), "other")
            parts["other"] += [key] if group == "other" else []
        elif key or value:
            parts[group].append(f"{key}: {value}" if key and value else key or value)
    quest = quest_of(rest)
    if quest and not rows:
        # A quest has no table to say where its solution goes: this generator's README says it by a line of its own.
        parts["submit"] = handed_in(quest.rest.splitlines())
    return Task(named.label, int(named.number.split(".")[0]) if named.number else 1, ref(named.number) if named.number else "", title, named.bonus,
                body_of(rest), body_of(lines), bool(rows), **parts, blocks=blocks_of(rest), quest=quest)


def parse_readme(text: str) -> Readme:
    sections: list[Section] = []
    tasks: list[Task] = []
    title, level = "", 0  # the heading being read and its level
    body: list[str] = []  # the lines under it
    named: re.Match[str] | None = None  # the heading is a task's
    first = True
    above = ""  # the role of the last heading of the first or second level: a topic under «Теоретический блок» is theory
    code = Fence()

    def close() -> None:
        nonlocal first, above
        lone = None if named else LONE.match(title)
        if named:
            tasks.append(task_of(Named(named["label"], named["number"], plain(named["title"]), is_bonus(named)), body))
            above = ""
        elif lone and quest_of(body):
            # «Quest: Level 1. Hall» with the line «Получен Quest.» under it is the quest of a group project; with
            # nothing under it, it is the heading the quests of a room stand under.
            tasks.append(task_of(Named(plain(lone["label"]), "", plain(lone["title"]), bool(lone["bonus"])), body))
            above = ""
        elif title or "".join(body).strip():
            own = "title" if first and title else role(title)
            sections.append(Section(title, "theory" if own == "other" and above == "theory" and level > 2 else own, body_of(body)))
            above = own if level <= 2 else above
        first = first and not title

    for line in text.splitlines():
        heading = None if code(line) else heading_of(line)
        task = TASK.match(heading[1]) if heading else None
        # Inside a task only a task or a heading of the task's own level or above ends it: its «###» are its own.
        # Outside tasks, headings below the third level are the inner structure of a section.
        opens = heading and (task or heading[0] <= (level if named else 3))
        if heading and opens:
            close()
            title, level, body, named = heading[1], heading[0], [], task
        else:
            body.append(line)
    close()
    head = next((s for s in sections if s.role == "title"), Section("", "title", ""))
    return Readme(head.title, head.text, [s for s in sections if s is not head], tasks, text.count("!["), bool(code.mark))


def words(value: object) -> str:
    """A value of the check-list as text. A list or a mapping where words are expected is no words: written out, a
    few lines of YAML that refer to one another are gigabytes."""
    return str(value).strip() if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def parse_checklist(text: str) -> Checklist:
    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as error:  # brackets nested by the thousand are not a check-list either
        raise ValueError(f"чек-лист не читается как YAML ({type(error).__name__})") from error
    if not isinstance(data, dict) or not isinstance(data.get("sections"), list):
        raise ValueError("в чек-листе нет списка sections")
    questions = []
    for section in data["sections"]:
        asked = section.get("questions") if isinstance(section, dict) else None
        for q in asked if isinstance(asked, list) else []:
            if isinstance(q, dict):
                name = " ".join(words(q.get("name")).split())  # a name is one line, whatever YAML folded into it
                # A bonus question is one of a bonus section, or one whose own name says so.
                bonus = bool(BONUS.search(words(section.get("name"))) or ((numbered := TASK.match(name)) and is_bonus(numbered)))
                questions.append(Question(name, words(q.get("description")), words(q.get("rating")), bonus))
    return Checklist(words(data.get("introduction")), words(data.get("guidelines")), questions)


def numbers_of(row: str) -> list[int]:
    """The numbers a row of tasks names: «1—3» is 1, 2, 3; «3, 4 и 6» is 3, 4, 6. A row that runs backwards, or
    names more tasks than a question is ever about, names nothing."""
    found: list[int] = []
    for part in re.split(r"\s*(?:,|\bи\b|\band\b|&)\s*", row, flags=re.I):
        ends = [int(number) for number in re.split(r"\s*[—–-]\s*", part)]
        if len(ends) > 2 or ends[-1] < ends[0] or ends[-1] - ends[0] >= MAX_SEVERAL:
            return []
        found += range(ends[0], ends[-1] + 1)
    found = list(dict.fromkeys(found))
    return found if 1 < len(found) <= MAX_SEVERAL else []


def numbering(name: str) -> re.Match[str] | None:
    """What in the name of a question numbers tasks: a row of them, or one; None — nothing does."""
    row = SEVERAL.match(name)
    return row if row and numbers_of(row["row"]) else TASK.match(name)


def asked_about(name: re.Match[str] | None, bonus: bool, places: dict[tuple[bool, str], int]) -> list[int]:
    """The tasks a numbered name of a question is about, by their places; none — the name numbers a task there is
    not. «Задания 1—2» is about each of the tasks it names, and about none when the README lacks one of them: a
    question that names a task that is not there is not quietly taken for a question about the rest."""
    if name is None:
        return []
    if "row" in name.groupdict():
        named = [(bonus, str(number)) for number in numbers_of(name["row"])]
        return [places[key] for key in named] if named and all(key in places for key in named) else []
    parts = ref(name["number"]).split(".")
    return next(([places[bonus, ".".join(parts[:size])]] for size in range(len(parts), 0, -1) if (bonus, ".".join(parts[:size])) in places), [])


def pair(readme: Readme, checklist: Checklist | None) -> Paired:
    questions = checklist.questions if checklist else []
    found: dict[int, list[Question]] = {}
    stray: list[str] = []
    numbered = [numbering(q.name) for q in questions]
    keys = [(task.bonus, task.ref) for task in readme.tasks]
    if 2 * sum(bool(name) for name in numbered) > len(questions) and len(set(keys)) == len(keys):
        # Most questions name a task's number (one name that happens to hold a number is not a numbering): a question
        # is about the task with its number, of its part — «Задание 1.2» about «Задание 1» when there is no «1.2».
        # One that names none is about no task: taking it in order would move every pair after it by one.
        places = {key: index for index, key in enumerate(keys)}
        for question, name in zip(questions, numbered, strict=True):
            about = asked_about(name, question.bonus, places)
            stray += [] if about else [question.name]
            for place in about:
                found.setdefault(place, []).append(question)
        by = "номеру"
    else:
        # The names do not number the tasks, or the numbers do not tell the tasks apart: the questions of a part are
        # taken against its tasks in order, one for one.
        for bonus in (False, True):
            places_in_part = [index for index, task in enumerate(readme.tasks) if task.bonus == bonus]
            of_part = [q for q in questions if q.bonus == bonus]
            for place, question in zip(places_in_part, of_part, strict=False):
                found[place] = [question]
            stray.extend(q.name for q in of_part[len(places_in_part):])
        by = "порядку"
    return Paired(by, found, [task.label for index, task in enumerate(readme.tasks) if index not in found], stray)


def named_as(folder: Path, names: tuple[str, ...], passed: Sequence[Path] = ()) -> Path | None:
    """The file of the folder under the first of the names it has, whatever the case of its letters — a file, not a
    link to one, and not one of those said to be passed over."""
    files = {child.name.lower(): child for child in sorted(folder.iterdir())
             if child.is_file() and not child.is_symlink() and child not in passed} if folder.is_dir() else {}
    return next((files[name.lower()] for name in names if name.lower() in files), None)


def find(folder: Path) -> Path | None:
    """Where the repository is: the folder itself, or its only subfolder when an archive wrapped it in one more."""
    if named_as(folder, READMES):
        return folder
    inner = [child for child in sorted(folder.iterdir()) if child.is_dir() and not child.is_symlink() and not child.name.startswith(".")] if folder.is_dir() else []
    return inner[0] if len(inner) == 1 and named_as(inner[0], READMES) else None


def text_of(path: Path, limit: int) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.stat().st_size <= limit else ""


def same(name: str) -> str:
    """A name as it compares: «й» typed as one letter and «й» as two (an archive made on a Mac) are one name."""
    return unicodedata.normalize("NFC", name)


def located(root: Path, named: str, field: str) -> Path:
    """The file or folder of the repository a meta.yaml names, by its path from the root. Each part of the path is a
    name that is there: «..», a path from the root of the disk, a link out of the folder name nothing. ValueError
    says which field names what is not there — a name with a slip in it must not pass for «nothing to skip»."""
    place = root
    for part in [part for part in named.replace("\\", "/").split("/") if part]:
        inside = [child for child in sorted(place.iterdir()) if same(child.name) == same(part) and not child.is_symlink()] if place.is_dir() else []
        if not inside:
            raise ValueError(f"«{root.name}»: в meta.yaml поле {field} называет «{named}», а «{part}» в «{place.name}» нет")
        exact = [child for child in inside if child.name == part]
        if len(exact) != 1 and len(inside) > 1:
            raise ValueError(f"«{root.name}»: в meta.yaml поле {field} называет «{named}», а имён «{part}» в «{place.name}» несколько: буквы набраны по-разному")
        place = (exact or inside)[0]
    if place == root:
        raise ValueError(f"«{root.name}»: в meta.yaml поле {field} пустое")
    return place


def alike(root: Path, word: str, formats: tuple[str, ...], passed: Sequence[Path] = ()) -> str:
    """The files of the repository that look like the one not found, for the message that asks to name it — not
    those said to be passed over."""
    found = [p.relative_to(root).as_posix() for p in sorted(root.rglob("*"))
             if p.is_file() and not p.is_symlink() and word in p.name.lower().replace("-", "").replace("_", "") and p.name.lower().endswith(formats)
             and not any(p == place or place in p.parents for place in passed)][:MAX_ALIKE]
    return f"; похожие файлы: {', '.join(f'«{name}»' for name in found)}" if found else ""


def read(folder: Path, readme: str = "", checklist: str = "", skip: Sequence[str] = (), broken: Sequence[str] = ()) -> Project:
    """The project in the folder. No README — ValueError: without it there is nothing to call a project.

    `readme`, `checklist` — the files to read when they are not under the school's names, by their paths from the
    folder; `skip` — the files and folders of it that are not the project (drafts, what is kept from the learners);
    `broken` — its files damaged on purpose, which are not asked to read as their format.
    A README named with «.docx» is a Word document and is read as one."""
    # Paths of a meta.yaml are from the folder itself: with any of them given, a repository wrapped one folder
    # deeper is not looked for — «inner/README.md» and «README.md» would both be right, and one of them wrong.
    root = folder if readme or checklist or skip or broken else find(folder)
    readme_path = None if root is None or not root.is_dir() else located(root, readme, "readme") if readme else named_as(root, READMES)
    if root is None or readme_path is None:
        others = alike(folder, "readme", (".md", ".docx")) if folder.is_dir() else ""
        raise ValueError(f"в каталоге «{folder.name}» нет {' или '.join(READMES)}" + (f"{others} — назовите нужный в meta.yaml: readme" if others else ""))
    if not readme_path.is_file():
        raise ValueError(f"«{folder.name}»: «{readme}» из meta.yaml — не файл")
    passed = [located(root, name, "skip") for name in skip]

    def skipped(path: Path) -> bool:
        return any(path == place or place in path.parents for place in passed)

    # Under the school's names the check-list is the first of them that was not said to be passed over.
    checklist_path = located(root, checklist, "checklist") if checklist else named_as(root, CHECKLISTS, passed)
    if checklist_path is not None and not checklist_path.is_file():
        raise ValueError(f"«{folder.name}»: «{checklist}» из meta.yaml — не файл")
    # What is to be read cannot be what is to be passed over: one of the two lines of the meta.yaml is a slip.
    if both := [name for name, path in (("readme", readme_path), ("checklist", checklist_path)) if path and skipped(path)]:
        raise ValueError(f"«{folder.name}»: в meta.yaml поле skip называет то же, что {' и '.join(both)}")
    notes: list[str] = []
    worded = readme_path.suffix.lower() == ".docx"
    readme_text = word.markdown(readme_path) if worded else text_of(readme_path, MAX_TEXT)
    if not readme_text.strip() or len(readme_text) > MAX_TEXT:
        raise ValueError(f"{readme_path.name} в «{folder.name}» пустой или больше {MAX_TEXT // 1_000_000} МБ")
    if worded:
        notes.append(f"{readme_path.name} — документ Word: прочитан как текст с заголовками, списками и таблицами; блоки кода в нём не отличаются от абзацев")
    if "�" in readme_text:
        notes.append(f"{readme_path.name} не в UTF-8: часть букв не прочитана")
    parsed = parse_readme(readme_text)
    if parsed.unclosed:
        notes.append(f"в {readme_path.name} блок кода открыт и не закрыт: текст после него прочитан как код")
    questions = None
    if checklist_path is None:
        others = alike(root, "checklist", (".yml", ".yaml"), passed)
        notes.append("чек-листа в репозитории нет" + (f"{others} — назовите нужный в meta.yaml: checklist" if others else ""))
    elif checklist_path.stat().st_size > MAX_TEXT:
        notes.append(f"{checklist_path.name} больше {MAX_TEXT // 1_000_000} МБ, не прочитан")
    else:
        try:
            questions = parse_checklist(text_of(checklist_path, MAX_TEXT))
        except ValueError as error:
            notes.append(f"{checklist_path.name}: {error}")
        if questions and not questions.questions:
            notes.append(f"в {checklist_path.name} нет ни одного вопроса")
        if questions and any("\ufffd" in q.name + q.text for q in questions.questions):
            notes.append(f"{checklist_path.name} не в UTF-8: часть букв не прочитана")
        # Files named by hand are a pair by the word of whoever named them; under the school's names the pair is checked.
        if not (readme or checklist) and ("_rus." in readme_path.name.lower()) != ("_rus." in checklist_path.name.lower()):
            notes.append(f"{readme_path.name} и {checklist_path.name} на разных языках: русской пары в репозитории нет")
    # Under the school's names a README has its pair in the other language beside it, and that pair is the README
    # too, not a file of the project. A README named by hand has no such pair: a «README.md» beside it is a file like
    # any other — a draft, most likely — and is read as one unless it is said to be skipped.
    own = {name.lower() for name in (*(() if readme else READMES), *(() if checklist else CHECKLISTS))}
    # A link is not a file of the project: what it points at may lie anywhere on the disk.
    # The order is that of Windows, where the paid runs are made: there paths compare with no regard to case, and
    # elsewhere «README» stands before «datasets». The same project is the same list — and the same question — anywhere.
    every = sorted((p for p in root.rglob("*") if p.is_file() and not p.is_symlink() and ".git" not in p.relative_to(root).parts and p.name != ".gitkeep"),
                   key=lambda p: ([part.lower() for part in p.relative_to(root).parts], p.relative_to(root).as_posix()))
    kept = [p for p in every if not skipped(p) and not (p.parent == root and p.name.lower() in own) and p not in (readme_path, checklist_path)
            and not (p.parent == root and p.name.lower() == "meta.yaml")]  # what is known of the case is not the project's
    if passed:
        notes.append(f"по meta.yaml не читались ({sum(skipped(p) for p in every)} файлов): {', '.join(f'«{name}»' for name in skip)}")
    damaged = [located(root, name, "broken") for name in broken]
    if strangers := [name for name, place in zip(broken, damaged, strict=True) if place not in kept]:
        raise ValueError(f"«{folder.name}»: в meta.yaml поле broken называет не файл проекта: {', '.join(f'«{name}»' for name in strangers)}")
    if damaged:
        notes.append(f"по meta.yaml испорчены намеренно, на читаемость не проверялись: {', '.join(f'«{name}»' for name in broken)}")
    if len(kept) > MAX_FILES:
        notes.append(f"файлов {len(kept)}, прочитаны первые {MAX_FILES}")
    files = [TextFile(p.relative_to(root).as_posix(), p.stat().st_size, text_of(p, MAX_FILE) if p.suffix.lower() in TEXT_FORMATS else "")
             for p in kept[:MAX_FILES]]
    return Project(folder.name, readme_path.relative_to(root).as_posix(), parsed, checklist_path.relative_to(root).as_posix() if checklist_path else "",
                   questions, files, notes, tuple(place.relative_to(root).as_posix() for place in damaged), readme_text)
