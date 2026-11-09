"""Mechanical checks of the draft. Code only; the critics and the methodologist judge the meaning.

block — the README is not assembled until it is fixed; warn — shown, the methodologist decides.
1. no ready solution in materials, tasks or theory;  2. every task has something to submit and points for the peer;
3. every outcome is covered, hours add up, every task has theory and every theory topic has links;
4. no copy-paste between tasks;  5. links come from the search or from a person;
6. the draft is complete, reviewed and its materials parse, so the template never gets holes;
7. in a style: the check-list of a task is short, and a legend says of every task how it is checked.
"""

import csv
import io
import json
import re
from collections.abc import Iterable, Sequence
from typing import Literal, Protocol

import yaml

from ..core.contracts import Contract
from .handoff import STYLE_NAMES, repository_root
from .models import FILED, NOTED, PRESENTATIONS, ContentDraft, TaskContent, Theory, picture_marks

Severity = Literal["block", "warn"]


class Check(Contract):
    code: str
    severity: Severity
    part: str = ""
    message: str


class File(Protocol):
    """A file the learner gets, as the checks see it: where it lies and what is in it."""

    @property
    def path(self) -> str: ...
    @property
    def content(self) -> str: ...


class Written(Protocol):
    """The text of a project, whoever wrote it: a draft of this generator, or a project read from its repository
    (benchmark/). The checks of the text itself ask no more of it than this, so one measure serves both."""

    @property
    def tasks(self) -> Sequence[TaskContent]: ...
    @property
    def materials(self) -> Sequence[File]: ...
    @property
    def theory(self) -> Theory | None: ...


# File names that announce an answer. Paths are latin (models.material_path), so transliterations are listed too.
# A generator's material is named in Latin letters; a file of a repository may be named in Russian. There the words
# for «answer» and «solution» are everyday ones («формат_ответа», «дерево_решений»), so what is looked for is what the
# authors themselves mark the closed part of a project with — «эталон», «не грузим», «не для пиров» — and a file whose
# whole name is the word: «решение.py», «ответы 2.txt».
SOLUTION_PATH = re.compile(r"(solution|answer|otvet|reshen|etalon|correct|(?:^|[_\-./])keys?(?:[_\-./]|$)"
                           r"|эталон|не[\s_]+груз|не[\s_]+для[\s_]+(?:пиров|выдачи)|(?:^|/)(?:решени[ея]|ответы?)(?:[ _\-]?\d+)?(?:\.[^/]*)?$)", re.I)
# Internal wording of plans and prompts that must never reach a learner's file.
INTERNAL_MARKERS = re.compile(
    r"(not_contains|EvidenceSpec|правильн\w* ответ\w*\s*:|эталонн\w* (?:ответ|решени)\w*|(?:студент|участник) должен вывести|ключ ответов)",
    re.I)
# A note to oneself is a word of its own in capitals, or «todo:» in any letters. «xxxxx» in «create view xxxxx as» is a
# name in an example and stopped a tested project of the school (benchmark, 05.10.2026); «TODO-лист» is a thing a
# project may be about. «Модель задачи TODO» is still taken for a note: a project about a to-do list has to be told so.
PLACEHOLDER = re.compile(r"(\b(?:TODO|TBD|XXX)\b(?![-_])|(?i:\b(?:todo|tbd)\s*:|\[вставить|\[заполнить|<\s*(?:вставить|название|имя)|lorem ipsum))")
WORD = re.compile(r"[\wё]+", re.I)
SHINGLE = 8


def learner_text(task: TaskContent) -> str:
    """What the one who solves the task reads of it in the README."""
    return "\n".join([task.title, task.goal, task.context, *task.steps, *task.expected, *task.examples, *task.constraints,
                      task.result, task.check, task.transition])


def task_text(task: TaskContent) -> str:
    """Everything written for the task: the README and the points of the check-list."""
    return "\n".join([learner_text(task), *task.peer_checks])


def prose(task: TaskContent) -> str:
    """The task without its examples: an example is data, and «TODO» may be the input of a program about a to-do list
    — not a note the author left to oneself."""
    return "\n".join([task.title, task.goal, task.context, *task.steps, *task.expected, *task.constraints, task.result, task.check,
                      task.transition, *task.peer_checks])


def solution_leaks(draft: Written) -> list[Check]:
    found = []
    for m in draft.materials:
        part = f"material:{m.path}"
        if SOLUTION_PATH.search(m.path):
            found.append(Check(code="solution", severity="block", part=part,
                               message=f"Имя материала «{m.path}» похоже на готовый ответ. Переименуйте или уберите файл."))
        marker = INTERNAL_MARKERS.search(m.content)
        if marker:
            found.append(Check(code="solution", severity="block", part=part,
                               message=f"В материале «{m.path}» есть «{marker.group(0).strip()}» — похоже на ответ или служебную пометку."))
    for t in draft.tasks:
        # The points for the peer are not looked through: a task with one right result may show it to the one who
        # checks, as the tested projects of the school do (decided 05.10.2026). The README may not.
        marker = INTERNAL_MARKERS.search(learner_text(t))
        if marker:
            found.append(Check(code="solution", severity="block", part=f"task:{t.number}",
                               message=f"В задании {t.number} есть «{marker.group(0).strip()}» — ответ или служебная пометка в тексте для участника."))
    for topic in draft.theory.topics if draft.theory else []:
        marker = INTERNAL_MARKERS.search(f"{topic.title}\n{topic.text}\n{topic.notes}\n{topic.question}")
        if marker:
            found.append(Check(code="solution", severity="block", part=f"topic:{topic.number}",
                               message=f"В теме {topic.number} есть «{marker.group(0).strip()}» — теория указывает направление, а не ответ."))
    return found


# The blocks every task has to have, by the field that holds them, as they are named to the methodologist.
BLOCKS = {"steps": "«Что нужно сделать»", "expected": "«Что должно получиться»", "result": "формат сдачи", "peer_checks": "пункты для пира"}


def missing(task: TaskContent) -> list[str]:
    """The blocks of the task that are empty, by field."""
    filled = {"steps": task.steps, "expected": task.expected, "result": task.result.strip(), "peer_checks": task.peer_checks}
    return [name for name in BLOCKS if not filled[name]]


def submissions(draft: Written) -> list[Check]:
    return [Check(code="submission", severity="block", part=f"task:{t.number}",
                  message=f"В задании {t.number} нет: {', '.join(BLOCKS[name] for name in missing(t))}.")
            for t in draft.tasks if missing(t)]


# Points for the peer on one task, as the good run of the intensive has them: about three (decided 06.10.2026).
PEER_POINTS = 4


def style_form(draft: ContentDraft) -> list[Check]:
    """What a style asks of every task and code can see. Advice, never a stop: the methodologist may want it otherwise.
    A draft begun before the styles is not measured by them."""
    if draft.style is None:
        return []
    found = [Check(code="style", severity="warn", part=f"task:{t.number}",
                   message=f"В задании {t.number} пунктов для пира {len(t.peer_checks)} — решено держать 2–{PEER_POINTS}: длинный список проверяют наспех.")
             for t in draft.tasks if len(t.peer_checks) > PEER_POINTS]
    found += [Check(code="style", severity="warn", part=f"task:{t.number}",
                    message=f"В задании {t.number} один пункт для пира — решено держать 2–{PEER_POINTS}: нужен и пункт-действие при проверяющем.")
              for t in draft.tasks if len(t.peer_checks) < 2]
    if draft.style.stale():
        found.append(Check(code="style", severity="warn", part="style",
                           message=f"Сюжет написан для стиля «{STYLE_NAMES.get(draft.style.written_for, draft.style.written_for)}», а проект теперь "
                                   f"в стиле «{STYLE_NAMES[draft.style.profile]}»: поправьте сюжет в чате или скажите, что он подходит и так."))
    # A task written before the style was changed keeps the voice and the form of the other one.
    found += [Check(code="style", severity="warn", part=f"task:{t.number}",
                    message=f"Задание {t.number} написано в стиле «{STYLE_NAMES.get(t.written_in, t.written_in)}», а проект теперь в стиле "
                            f"«{STYLE_NAMES[draft.style.profile]}»: попросите в чате переписать его или скажите, что оно подходит и так.")
              for t in draft.tasks if t.written_in and t.written_in != draft.style.profile]
    if draft.style.profile == "legend":
        found += [Check(code="style", severity="warn", part=f"task:{t.number}",
                        message=f"В задании {t.number} не сказано, как его проверят («Проверка»): участник не знает, что увидит проверяющий.")
                  for t in draft.tasks if not t.check.strip()]
    return found


def coverage(draft: ContentDraft) -> list[Check]:
    handoff = draft.handoff
    main = [t for t in handoff.tasks if not t.optional]
    covered = {n for t in main for n in t.outcome_numbers}
    found = [Check(code="coverage", severity="warn", message=f"ОР «{text}» не закрыт ни одним основным заданием.")
             for n, text in enumerate(handoff.outcomes, 1) if n not in covered]
    total = sum(t.hours for t in handoff.tasks)
    if handoff.hours and abs(total - handoff.hours) > 0.5:
        found.append(Check(code="coverage", severity="warn",
                           message=f"Часы заданий ({total:g} ч) не сходятся с трудоёмкостью проекта ({handoff.hours:g} ч)."))
    return found


def theory(draft: ContentDraft) -> list[Check]:
    """Theory is where to dig for each task: a task without a topic, or a topic without a link, is a gap."""
    if draft.theory is None:
        return []
    covered = {n for topic in draft.theory.topics for n in topic.tasks}
    found = [Check(code="theory", severity="warn", part=f"task:{t.number}", message=f"К заданию {t.number} нет ни одной темы в теории.")
             for t in draft.tasks if t.number not in covered]
    # Without the internet a link leads nowhere: none is looked for, and its absence is no gap (the plan of the theory).
    if draft.online():
        found += [Check(code="theory", severity="warn", part=f"topic:{topic.number}", message=f"У темы {topic.number} «{topic.title}» нет ссылок, куда смотреть.")
                  for topic in draft.theory.topics if not topic.links]
    plan = draft.theory_plan
    if plan and plan.english == "yes" and draft.theory.quoted() is None:
        found.append(Check(code="theory", severity="warn", part="theory",
                           message="По плану подачи теории нужна выдержка из первоисточника на английском, а её нет. Найдите её кнопкой "
                                   "«Найти выдержку из первоисточника» — или скажите в чате, что она не нужна."))
    # The presentation shows a summary of every section: one without it shows the short text in its place.
    if draft.presentation() in NOTED:
        found += [Check(code="theory", severity="warn", part=f"topic:{topic.number}",
                        message=f"У темы {topic.number} «{topic.title}» нет конспекта, а теория подаётся так: {PRESENTATIONS[draft.presentation()]}. "
                                "Попросите в чате написать конспект — пока на его месте короткий текст раздела.")
                  for topic in draft.theory.topics if not topic.notes.strip()]
    return found


def pictures(draft: ContentDraft) -> list[Check]:
    """The places of pictures still marked in the text. The generator draws nothing: the marks are for a person to
    replace with pictures or to take out, and the README must not go to learners with them forgotten."""
    if not draft.plans_theory():
        return []
    # Every text a learner is given, piece by piece — a mark is a line of its own within one piece. Of a section of the
    # theory: the text the README shows and the one the file of materials/ holds, by the plan of the theory.
    shown = draft.presentation()
    texts: list[tuple[str, list[str]]] = []
    if draft.summary:
        texts.append(("аннотация", [draft.summary.annotation]))
    if draft.frame:
        texts.append(("введение", [draft.frame.introduction, *draft.frame.instruction, draft.frame.conclusion]))
    texts += [(f"задание {t.number}", [t.context, *t.steps, *t.expected, *t.constraints, t.result, t.check, t.transition])
              for t in sorted(draft.tasks, key=lambda t: t.number)]
    for topic in draft.theory.topics if draft.theory else []:
        summary = topic.notes if topic.notes.strip() else topic.text
        pieces = [] if shown == "materials" else [summary if shown == "notes" else topic.text]
        texts.append((f"раздел теории {topic.number}", [*pieces, *([summary] if shown in FILED else []), topic.question]))
    where = [(name, sum(len(picture_marks(piece)[1]) for piece in pieces)) for name, pieces in texts]
    total = sum(count for _, count in where)
    if not total:
        return []
    return [Check(code="pictures", severity="warn",
                  message=f"В тексте отмечено мест для картинок: {total} ({', '.join(name for name, count in where if count)}). "
                          "Перед выдачей участникам положите картинки в misc/images/ и замените пометки ссылками на них — или уберите пометки.")]


# A link written in a text: [name](target) or [name](<target>). The lengths are bounded: a text of brackets alone —
# a model caught in a loop writes such — must not take the check a minute on every look at the draft (review 07.10.2026).
WRITTEN_LINK = re.compile(r"\[([^\]\[\n]{1,300})\]\(<?([^()\[\]\s<>]{1,500})>?\)")
# The address of a file inside a repository of GitLab: «…/-/blob/master/README.md».
REPOSITORY_FILE = re.compile(r"https?://[^\s)>\]]{1,300}?/-/(?:blob|tree|raw)/[^\s)>\]]{0,300}", re.I)
# A path of one computer: a disk of Windows, a file:// address, a network share, a home folder, a system folder. A
# path that only begins with «/» is not one: in a repository it is a path from the root.
LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|file:|\\\\|~[\\/]|/(?:home|Users|mnt|tmp|var|usr|etc|opt|root|private)/)", re.I)
# Code in a text: a fenced block (an unclosed one runs to the end) and a span between backticks. What stands there is
# shown to the letter and is not a link.
CODE = re.compile(r"^ {0,3}(`{3,}(?!`)[^`\n]*|~{3,}(?!~)[^\n]*)\n.*?(?:^ {0,3}(?:`{3,}|~{3,})[ \t]*$|\Z)|`[^`\n]{1,500}`", re.M | re.S)


def names_repository(text: str, root: str) -> bool:
    """Whether a text has the address of this repository, by the web or by ssh — and not of a neighbour whose name
    only begins the same («…/D6P3.ID_157» is not «…/D6P3.ID_1578», nor «…/D6P3.ID_157.v2»)."""
    host, _, path = root.partition("/")
    return bool(re.search(rf"(?<![\w.-]){re.escape(host)}[/:]{re.escape(path)}(?:\.git)?(?![\w-]|\.[\w-])", text, re.I))


def paths(draft: ContentDraft) -> list[Check]:
    """A file of the project is named by its path from the root of the repository — «materials/team.csv», «src/a.py»
    — so that the name holds wherever the repository is copied (decided 07.10.2026). Looked for in what the model and
    the methodologist wrote: the address of the project's own repository, the address of a file in GitLab, a link to
    a path of one computer, a link into materials/ to a file the project does not have. The links the README is
    assembled with are made by code and are such paths already. An address is looked for everywhere, code included —
    «git clone <address>» names the repository too; a link is one only outside code, and only in a text that is
    shown as Markdown."""
    if not draft.since("0.7"):
        return []
    texts: list[tuple[str, str, list[str], bool]] = []   # the part, how to call it, its pieces, whether links are read in it
    if draft.summary:
        texts.append(("summary", "В аннотации", [draft.summary.title, draft.summary.annotation], True))
    if draft.frame:
        texts.append(("frame", "Во введении", [draft.frame.introduction, *draft.frame.instruction, draft.frame.conclusion], True))
    texts += [(f"task:{t.number}", f"В задании {t.number}", [prose(t), *t.examples], True)
              for t in sorted(draft.tasks, key=lambda t: t.number)]
    texts += [(f"topic:{topic.number}", f"В разделе теории {topic.number}", [topic.title, topic.text, topic.notes, topic.question], True)
              for topic in (draft.theory.topics if draft.theory else [])]
    texts += [(f"material:{m.path}", f"В материале «{m.path}»", [m.content], m.path.lower().endswith((".md", ".txt")))
              for m in draft.materials]
    from .render import theory_path

    files = {f"materials/{m.path}" for m in draft.materials} | ({f"materials/{theory_path(draft)}"} if theory_path(draft) else set())
    root = repository_root(draft.handoff.repository)
    rule = "Файлы проекта называйте путём от корня репозитория: materials/…, src/…"
    found: list[Check] = []
    for part, where, pieces, linked in texts:
        text = "\n".join(pieces)
        said: list[str] = []
        if root and names_repository(text, root):
            said.append(f"{where} записан адрес репозитория проекта ({root}). {rule}.")
        elif address := REPOSITORY_FILE.search(text):
            said.append(f"{where} файл назван адресом в GitLab ({address.group(0)[:120]}). Если это файл самого проекта: {rule[0].lower()}{rule[1:]}.")
        for name, target in WRITTEN_LINK.findall(CODE.sub(" ", text)) if linked else []:
            file = target.split("#")[0]
            if LOCAL_PATH.match(target):
                said.append(f"{where} ссылка «{name[:60]}» ведёт на путь одного компьютера ({target[:120]}). {rule}.")
            elif file.startswith("materials/") and file not in files:
                said.append(f"{where} ссылка «{name[:60]}» ведёт на {file[:120]}, а такого файла в материалах проекта нет.")
        found += [Check(code="paths", severity="warn", part=part, message=message) for message in dict.fromkeys(said)]
    return found


# What a material is for is read by the learner: the README of a legend shows it in its table of materials, beside the
# text that speaks to the learner as «ты». Two ways it is written for someone else are looked for; a plain name of the
# file — «Данные о команде для заданий 1–2» — is neither, and is left alone.
# The learner as a third person who does something: «участник формулирует», «студенты сверяют», «заполняется участником».
LEARNERS = {"участник": ("участники", "участником", "участниками"), "студент": ("студенты", "студентом", "студентами"),
            "слушатель": ("слушатели", "слушателем", "слушателями"), "ученик": ("ученики", "учеником", "учениками"),
            "обучающийся": ("обучающиеся", "обучающимся", "обучающимися")}
DOES = re.compile(r"\w{3,}(?:[её]т|[ую]т|[ая]т|ит)(?:ся)?")   # a verb of the third person, by its ending
# The file told of as its designer tells: what it sets, gives, serves as — the first word of the line.
ROLE = re.compile(r"\s*(?:зада[её]т|да[её]т|служит|содержит|нуж(?:ен|на|но|ны)|используется|применяется|позволяет|помогает|описывает|"
                  r"показывает|определяет|предоставляет|обеспечивает|включает|хранит|фиксирует|является|предназначен[аоы]?|требуется|"
                  r"выступает|представляет)(?![\w-])", re.I)
# The line speaks to its reader: «ты», «твой» in any form, or a verb as «ты» does it — «считаешь», «сверишь».
TO_READER = re.compile(r"(?<![\w-])(?:ты|тебя|тебе|тобой|тобою|тво(?:й|я|ё|е|и|его|ей|ему|им|их|ю|ими|ём|ем)|(?!лишь(?![\w-]))\w{2,}(?:ешь|ёшь|ишь)(?:ся)?)(?![\w-])", re.I)


def about_the_learner(text: str, term: str) -> bool:
    """Whether a line tells of the learner as of a third person who does something. A word of the same root that is
    not the learner of the project — «участники встречи», «список участников» — is not that: it is followed by no verb."""
    # The word of the project for its learner, as it is given, and the usual words with their forms.
    acting = {term.strip().lower()} | {form for word, forms in LEARNERS.items() for form in (word, forms[0])}
    acted_by = {form for forms in LEARNERS.values() for form in forms[1:]}
    words = re.findall(r"[\w-]+", text.lower())
    pairs = list(zip(words, words[1:], strict=False))
    return (any(first in acting and DOES.fullmatch(second) for first, second in pairs)
            or any((first.endswith("ся") and second in acted_by) or (first in acted_by and second.endswith("ся")) for first, second in pairs))


def voice(draft: ContentDraft) -> list[Check]:
    """A line of the table of materials that is not written to the learner (decided 07.10.2026 on the first project
    made by a real model: «Задаёт вариант помощника, для которого участник формулирует промпт»). Only where the README
    shows the line — in a legend — and only for a draft whose writer was asked to write it so."""
    if not draft.since("0.8") or draft.style is None or draft.style.profile != "legend":
        return []
    term = draft.handoff.learner_term
    found = []
    for material in draft.materials:
        line = material.purpose.strip()
        said = ""
        if about_the_learner(line, term):
            said = "говорит об участнике в третьем лице"
        elif ROLE.match(line) and not TO_READER.search(line):
            said = "описывает роль файла в проекте, а не то, что с ним делает участник"
        if said:
            found.append(Check(code="voice", severity="warn", part=f"material:{material.path}",
                               message=f"Пояснение к материалу «{material.path}» в таблице README {said}: «{line[:160]}». Эту строку читает сам "
                                       "участник — напишите, что он делает с файлом, обращаясь к нему: «По этому файлу ты…»."))
    return found


def links(draft: ContentDraft) -> list[Check]:
    """A link the search did not return was typed by a person: fine, but worth a second look before publishing."""
    known = set(draft.sources)
    return [Check(code="links", severity="warn", part=f"topic:{topic.number}",
                  message=f"Ссылку {link.url} в теме {topic.number} поиск не находил — её добавили вручную, проверьте, что она открывается.")
            for topic in (draft.theory.topics if draft.theory else []) for link in topic.links if link.url not in known]


def shingles(texts: list[str]) -> dict[tuple[str, ...], str]:
    """8-word windows inside each piece of text; a window never spans two fields or two list items."""
    found: dict[tuple[str, ...], str] = {}
    for text in texts:
        words = WORD.findall(text.lower())
        found.update({tuple(words[i:i + SHINGLE]): " ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)})
    return found


def copy_paste(draft: Written) -> list[Check]:
    """Flag substantial overlap in task work as a warning requiring review. Peer points follow one pattern by design
    («Попроси рассказать…») and so does the way the work is handed in — the same folder, the same kind of file, the
    same meeting with a peer in every task (decided 05.10.2026: nine tested projects of fifteen repeat it) — so
    neither is compared; short common phrases are fine."""
    tasks = sorted(draft.tasks, key=lambda t: t.number)
    # Shared goals and story are deliberate; compare the work, not its repeated educational label.
    grams = {t.number: shingles([*t.steps, *t.expected]) for t in tasks}
    found = []
    for i, a in enumerate(tasks):
        for b in tasks[i + 1:]:
            shared = grams[a.number].keys() & grams[b.number].keys()
            denominator = min(len(grams[a.number]), len(grams[b.number]))
            if len(shared) >= 12 and denominator and len(shared) / denominator >= 0.45:
                example = grams[a.number][min(shared)]
                found.append(Check(code="copy_paste", severity="warn", part=f"task:{b.number}",
                                   message=f"В условиях заданий {a.number} и {b.number} есть значительное текстовое совпадение: «{example}…». "
                                           "Проверьте учебную функцию повтора; совпадение не доказывает дублирование заданий."))
    return found


def widths(text: str, mark: str) -> set[int]:
    return {len(row) for row in csv.reader(io.StringIO(text), delimiter=mark) if row}


def evenly_cut(text: str, mark: str) -> bool:
    """The mark cuts every row of the text into the same number of columns, two or more."""
    try:
        found = widths(text, mark)
    except csv.Error:  # a quote that opens a cell under the comma is a letter under another mark, and never closes
        return False
    return len(found) == 1 and min(found) >= 2


def parses(material: File) -> str:
    """Why a material's text does not open as its format; empty when it does."""
    suffix = material.path.rsplit(".", 1)[-1].lower()
    try:
        if suffix == "json":
            json.loads(material.content)
        elif suffix in {"yaml", "yml"}:
            yaml.safe_load(material.content)
        elif suffix == "csv":
            # A table reads when its rows are of one width. Commas are tried first, as they always were; a table whose
            # rows differ by commas still reads when «;» or a tab cuts every row into the same two columns or more —
            # a table saved by a Russian Excel has «;» between its columns and commas inside its sentences.
            if len(widths(material.content, ",")) > 1 and not any(evenly_cut(material.content, mark) for mark in (";", "\t")):
                return "в строках CSV разное число колонок"
    except (ValueError, yaml.YAMLError, csv.Error, RecursionError) as error:  # brackets nested by the thousand
        return type(error).__name__
    return ""


def completeness(draft: ContentDraft) -> list[Check]:
    found = []

    def block(part: str, message: str) -> None:
        found.append(Check(code="complete", severity="block", part=part, message=message))

    if not draft.summary or draft.summary.status != "approved":
        block("summary", "Название и аннотация не согласованы.")
    for n in [t.number for t in draft.handoff.tasks]:
        task = draft.task(n)
        if not task or task.status != "approved":
            block(f"task:{n}", f"Задание {n} не {'написано' if not task else 'согласовано'}.")
    for part, label, found_part in (("theory", "Теория", draft.theory), ("frame", "Введение и ситуация", draft.frame)):
        if found_part is None or found_part.status != "approved":
            block(part, f"{label} — {'не написано' if found_part is None else 'не согласовано'}.")
    for m in draft.materials:
        if m.status != "approved":
            block(f"material:{m.path}", f"Материал «{m.path}» не {'подготовлен' if m.status == 'planned' else 'согласован'}.")
        elif not m.content.strip():
            block(f"material:{m.path}", f"Материал «{m.path}» пустой.")
        elif problem := parses(m):
            block(f"material:{m.path}", f"Материал «{m.path}» не читается как {m.path.rsplit('.', 1)[-1]}: {problem}.")
    if any(msg.status == "pending" for msg in draft.messages):
        block("", "Есть правки без решения.")
    if draft.review is None:
        block("", "Критики ещё не проверяли текст.")
    if draft.styled() and (draft.style is None or draft.style.status != "approved"):
        block("style", "Стиль проекта не " + ("предложен." if draft.style is None else "согласован."))
    texts = [("summary", f"{draft.summary.title}\n{draft.summary.annotation}")] if draft.summary else []
    texts += [("frame", "\n".join([draft.frame.introduction, *draft.frame.instruction, draft.frame.conclusion]))] if draft.frame else []
    texts += [(f"topic:{t.number}", f"{t.title}\n{t.text}\n{t.notes}\n{t.question}") for t in (draft.theory.topics if draft.theory else [])]
    texts += [(f"task:{t.number}", prose(t)) for t in draft.tasks] + [(f"material:{m.path}", m.content) for m in draft.materials]
    return found + stubs(texts)


def stubs(texts: Iterable[tuple[str, str]]) -> list[Check]:
    """Text left unfinished, in (part, text) pairs: a note to oneself the learner would read."""
    return [Check(code="complete", severity="block", part=part, message=f"Осталась заглушка «{placeholder.group(0)}» ({part}).")
            for part, text in texts if (placeholder := PLACEHOLDER.search(text))]


def open_items(draft: ContentDraft) -> list[Check]:
    """What the methodologists still owe, not a defect of the text: shown once, never raised by the critic."""
    rules = " ".join(draft.handoff.rules.values()).lower()
    disclaimer = draft.checklist.disclaimer if draft.checklist else str(school_settings().get("checklist_disclaimer") or "")
    if "дисклеймер" in rules and not disclaimer.strip():
        return [Check(code="open", severity="warn", part="checklist",
                      message="Дисклеймер к чек-листу по правилам программы обязателен, а его текста нет. "
                              "Пришлите текст в чат («дисклеймер чек-листа: …») — он войдёт в check-list.yml.")]
    return []


def school_settings() -> dict[str, object]:
    from .render import school

    return school()


def run_checks(draft: ContentDraft, final: bool = False) -> list[Check]:
    """final adds completeness: it is noise while parts are still being written, but guards the assembly."""
    found = (solution_leaks(draft) + submissions(draft) + style_form(draft) + coverage(draft) + theory(draft) + copy_paste(draft)
             + links(draft) + paths(draft) + voice(draft) + pictures(draft) + open_items(draft))
    return found + completeness(draft) if final else found


def blocking(draft: ContentDraft) -> list[Check]:
    return [c for c in run_checks(draft, final=True) if c.severity == "block"]
