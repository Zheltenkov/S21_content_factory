"""The skills tab of a programme passport («развесовка»): what share of each skill a project gives and how that share
is split between its tasks. Code reads the tab and checks its names and sums; no model is involved.

The other way round, for a project that is being prepared: which task practises which skill is a judgement (a weight
from one to three per task and skill), and the shares are arithmetic — `spread` counts them so that they add up to one
by construction, and `table` writes the block out as the lines of the tab.

The layout (checked against «Паспорт программы_Батон» 05.10.2026): columns «Проект», «Задание», one column per skill,
«check». A project is a block of lines: `all` — the shares of the skills in the project; `01`…`N` — what part of
each skill a main task gets; `Bonus all` and `Bonus 01`… — the same for the bonus part, counted on its own; `check`
— the tab's own sums. An empty cell is zero. The platform refuses a project whose sums do not come to one or whose
skill is not in its catalogue, so what does not add up is reported here, by name, before anyone types it in there.
"""

import csv
import difflib
import io
import re
from collections.abc import Hashable, Iterable, Mapping, Sequence
from typing import NamedTuple, TypeVar

from .models import Concept, SkillBlock, SkillPart, SkillTask, StartRequest

K = TypeVar("K", bound=Hashable)
TOLERANCE = 0.01
PARTS = {"main": "основная часть", "bonus": "бонусная часть"}
# «Bonus 01», «Bonus01», «Bonus_02», «Бонус 1» — but «Bonuses» is a task's name like any other.
BONUS = re.compile(r"\s*(?:bonus|бонус)(?![a-zа-яё])", re.I)
# What a line of the table can be at most: a longer text in these cells is a note someone left on the tab. The same
# for a name: a longer header is not a skill's column, a longer line of the catalogue is not a skill.
MAX_CODE, MAX_LABEL, MAX_NAME = 100, 60, 100
# A share as a table writes it. float() alone also takes «0_1», «١» and «nan».
NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?", re.I | re.A)
MAX_TASKS = 50
# A tab written in percents has a wrong cell in every line: the first few are named, the rest are counted.
MAX_NAMED = 3
FORMULA = tuple("=+-@\t\r")  # what a spreadsheet takes for the start of a formula


def whole(total: float) -> bool:
    """Shares come to one, within a hundredth; 0.99 and 1.01 are within it, whatever a float makes of the difference."""
    return abs(total - 1) <= TOLERANCE + 1e-9


def key(name: str) -> str:
    """A skill's name as it is compared: case and spacing are not a different skill."""
    return " ".join(name.casefold().split())


def code_of(text: str) -> str:
    """A project's code as it is compared between the plan and a skills tab: spacing inside it is not another code."""
    return " ".join(text.split())


def said(value: float) -> str:
    """A sum as it is shown: shares have four decimals, and six significant digits drop what a float adds to them."""
    return f"{value:g}".replace(".", ",")


def is_skills_header(cells: list[str]) -> bool:
    names = {key(cell) for cell in cells}
    return "проект" in names and "задание" in names


def number(cell: str) -> float | None:
    """A share from a cell: empty is zero, a comma is a decimal point; None when it is not a number from 0 to 1.
    Rounded before it is judged: a formula's 1.0000000000000002 is one, and -2.8e-17 is zero."""
    text = cell.strip().replace(",", ".")
    if not text:
        return 0.0
    if not NUMBER.fullmatch(text):
        return None
    value = round(float(text), 4)
    return abs(value) if 0 <= value <= 1 else None


def line_of(label: str) -> tuple[bool, str]:
    """What a line of a block is by its label: (of the bonus part?, "all" | "check" | "task")."""
    bonus = BONUS.match(label)
    rest = key(label[bonus.end():].strip(" _-:.")) if bonus else key(label)
    return bool(bonus), rest if rest in ("all", "check") else "task"


def parse_blocks(records: list[list[str]]) -> list[SkillBlock]:
    """The tab as blocks, one per project code, in the order of the tab. Only what is not zero is kept. What the tab
    says twice (a skill's column, the line `all`) is taken once, the first time, and the block says so. A code written
    on every line of its block is one block; a code that comes back after another is a second block of that code."""
    header = next((record for record in records if any(cell.strip() for cell in record)), [])
    if not is_skills_header(header):
        return []
    names = [key(cell) for cell in header]
    project, task = names.index("проект"), names.index("задание")
    # The skills stand between the two columns and «check»; what is to the right of «check» is the tab's own notes.
    end = names.index("check") if "check" in names else len(header)
    skills: dict[int, str] = {}
    repeated: dict[int, str] = {}
    for i, cell in enumerate(header[:end]):
        name = " ".join(cell.split())  # a header broken over two lines is one name
        if name and len(name) <= MAX_NAME and i not in (project, task):
            (repeated if key(name) in {key(known) for known in skills.values()} else skills)[i] = name
    blocks: list[SkillBlock] = []
    stated: set[tuple[int, bool]] = set()
    off: dict[int, list[str]] = {}  # the number of a block → its cells that are not shares
    for record in records[records.index(header) + 1:]:
        cells = record + [""] * (len(header) - len(record))
        code, label = code_of(cells[project]), " ".join(cells[task].split())
        if len(code) > MAX_CODE or len(label) > MAX_LABEL:
            continue
        if code and not (blocks and blocks[-1].code == code):
            blocks.append(SkillBlock(code=code))
        if not blocks or not label:
            continue
        block = blocks[-1]
        bonus, kind = line_of(label)
        if kind == "check":
            continue
        shares: dict[str, float] = {}
        for i, skill in skills.items():
            value = number(cells[i])
            if value is None:
                off.setdefault(len(blocks), []).append(f"строка «{label}», «{skill}»: «{cells[i].strip()}» — не доля от 0 до 1")
            elif value:
                shares[skill] = value
        for i, skill in repeated.items():
            again = f"колонка «{skill}» на вкладке повторяется: значения второй не учтены"
            if number(cells[i]) != 0 and again not in block.issues:
                block.issues.append(again)
        part = block.bonus if bonus else block.main
        if kind == "all":
            if (len(blocks), bonus) not in stated:
                part.shares = shares
                stated.add((len(blocks), bonus))
            elif (twice := f"строка «{label}» встречается в блоке дважды: взята первая") not in block.issues:
                block.issues.append(twice)
        elif len(part.tasks) < MAX_TASKS:
            part.tasks.append(SkillTask(label=label, shares=shares))
        elif (over := f"{PARTS['bonus' if bonus else 'main']}: заданий больше {MAX_TASKS} — остальные не прочитаны") not in block.issues:
            block.issues.append(over)
    for at, found in off.items():
        more = len(found) - MAX_NAMED
        counted = [f"и ещё в {more} {'ячейке' if more % 10 == 1 and more % 100 != 11 else 'ячейках'} не доля от 0 до 1"] if more > 0 else []
        blocks[at - 1].issues[:0] = found[:MAX_NAMED] + counted
    return blocks


def nearest(name: str, catalog: list[str]) -> list[str]:
    """The catalogue names closest to this one: three at most, by difflib's own measure of «close»."""
    by_key = {key(known): known for known in catalog}
    return [by_key[found] for found in difflib.get_close_matches(key(name), list(by_key))]


def strangers(names: Iterable[str], catalog: list[str]) -> dict[str, str]:
    """Each name that is not in the platform's catalogue → what to say of it, with the catalogue names closest to it.
    Nothing is replaced on its own: a near name is a hint, the choice is the methodologist's. A name is looked up
    once however many times it is given: a tab repeats its skills in every block, and a lookup goes through the
    whole catalogue."""
    known = {key(name) for name in catalog}
    found = {}
    for name in dict.fromkeys(names):
        if key(name) not in known:
            close = nearest(name, catalog)
            found[name] = f"«{name}» нет в справочнике навыков" + (f" (ближайшие: {', '.join(close)})" if close else "")
    return found


def unknown(names: list[str], catalog: list[str]) -> list[str]:
    return list(strangers(names, catalog).values())


def part_issues(part: SkillPart, title: str) -> list[str]:
    issues: list[str] = []
    total = sum(part.shares.values())
    if part.tasks and not part.shares:
        return [f"{title}: заданий {len(part.tasks)}, а долей навыков нет"]
    if part.shares and not whole(total):
        issues.append(f"{title}: сумма долей навыков {said(total)} вместо 1")
    if not part.tasks:
        return issues  # a project not broken into tasks yet has nothing to split
    for skill, share in part.shares.items():
        split = sum(task.shares.get(skill, 0) for task in part.tasks)
        if not whole(split):
            issues.append(f"{title}: «{skill}» — доля {said(share)}, по заданиям сумма {said(split)} вместо 1")
    stray = dict.fromkeys(skill for task in part.tasks for skill in task.shares if skill not in part.shares)
    for skill in stray:
        split = sum(task.shares.get(skill, 0) for task in part.tasks)
        issues.append(f"{title}: «{skill}» — доли в проекте нет, а задания дают {said(split)}")
    idle = [task.label for task in part.tasks if not any(skill in part.shares for skill in task.shares)]
    if idle:
        issues.append(f"{title}: без навыков проекта остаются задания {', '.join(idle)}")
    return issues


def block_issues(block: SkillBlock, strange: Mapping[str, str]) -> list[str]:
    """Everything in a block the platform would refuse or that contradicts the block itself. strange — what
    `strangers` says of the tab's names: found once for the tab, not again for every block."""
    issues = list(block.issues)
    if not block.main.shares and not block.main.tasks:  # with tasks, the main part says it itself
        issues.append("в строке all нет ни одного навыка с долей")
    issues.extend(strange[name] for name in skill_names(block) if name in strange)
    issues.extend(part_issues(block.main, PARTS["main"]))
    issues.extend(part_issues(block.bonus, PARTS["bonus"]))
    return issues


def skill_names(block: SkillBlock) -> list[str]:
    """The skills a project develops: those with a share in its main part, then those only its bonus part has."""
    return list(dict.fromkeys([*block.main.shares, *block.bonus.shares]))


def exact(parts: Mapping[K, float]) -> dict[K, float]:
    """Parts of a whole as shares in hundredths that add up to exactly one. Each part gets its hundredths rounded
    down; what is left over goes, a hundredth at a time, to the parts that lost most by the rounding — the earlier
    of two that lost the same. A part that is not above zero has no share."""
    positive = {name: value for name, value in parts.items() if value > 0}
    total = sum(positive.values())
    exactly = {name: round(value * 100 / total, 9) for name, value in positive.items()}
    hundredths = {name: int(value) for name, value in exactly.items()}
    lost = sorted(exactly, key=lambda name: hundredths[name] - exactly[name])  # stable: the order given breaks a tie
    for name in lost[:100 - sum(hundredths.values())]:
        hundredths[name] += 1
    return {name: value / 100 for name, value in hundredths.items() if value}


def spread(project: Mapping[str, float], tasks: Sequence[tuple[str, Mapping[str, float]]]) -> SkillPart:
    """A part of a project — its main tasks or its bonus ones — from what each task practises: (label, {skill: weight}).
    project — the shares of the skills in the project where the plan states them (the line `all`); they are taken as
    they are. Where it does not, a skill's share is its part of all the weight the tasks give. Every skill is then
    divided between the tasks by their weights. Nothing is made to fit: a skill of the plan that no task practises,
    or a task's skill the plan does not have, stays as it is for `part_issues` to name."""
    given = [{skill: weight for skill, weight in weights.items() if weight > 0} for _, weights in tasks]
    totals: dict[str, float] = {}
    for weights in given:
        for skill, weight in weights.items():
            totals[skill] = totals.get(skill, 0) + weight
    split = {skill: exact({at: weights.get(skill, 0) for at, weights in enumerate(given)}) for skill in totals}
    return SkillPart(shares=dict(project) or exact(totals), tasks=[
        SkillTask(label=label, shares={skill: split[skill][at] for skill in given[at] if at in split[skill]})
        for at, (label, _) in enumerate(tasks)])


def table(block: SkillBlock) -> list[list[str]]:
    """A block as the lines of a skills tab, with the tab's own sums — what `parse_blocks` reads back as the same
    block, and what is copied into the passport as it is."""
    parts = ((block.main, ""), (block.bonus, "Bonus "))
    names = list(dict.fromkeys(skill for part, _ in parts for shares in (part.shares, *(task.shares for task in part.tasks)) for skill in shares))

    def line(code: str, label: str, shares: Mapping[str, float], check: str) -> list[str]:
        return [code, label, *[said(shares[name]) if name in shares else "" for name in names], check]

    lines = [["Проект", "Задание", *names, "check"]]
    for part, bonus in parts:
        if bonus and not part.shares and not part.tasks:
            continue
        lines.append(line("" if bonus else block.code, bonus + "all", part.shares, said(sum(part.shares.values()))))
        lines.extend(line("", task.label, task.shares, "") for task in part.tasks)
        if part.tasks:
            sums = {name: sum(task.shares.get(name, 0) for task in part.tasks) for name in names if any(name in task.shares for task in part.tasks)}
            lines.append(line("", "check", sums, ""))  # as the tab writes it, in the bonus part too
    return lines


def sheet(block: SkillBlock) -> str:
    """The block as a file a spreadsheet opens: the lines of the tab, «;» between the cells, a comma in the numbers —
    as a Russian Excel and a Google sheet read it — so that the block is pasted into the passport as it is. A name
    comes from a passport somebody else may have made, and a spreadsheet runs a cell that begins with «=», «+», «-»
    or «@» as a formula: such a cell is written as text, with an apostrophe before it — and read back, it keeps the
    apostrophe: for such a name the file is not the block."""
    out = io.StringIO()
    csv.writer(out, delimiter=";", lineterminator="\r\n").writerows(
        [("'" + cell if cell[:1] in FORMULA else cell) for cell in line] for line in table(block))
    return out.getvalue()


def gaps(block: SkillBlock, names: Sequence[str]) -> list[str]:
    """What stands between the block of a concept and the passport. The block is held to what the passport's own
    blocks are held to when a tab is read — a block this module would complain of on the way in is not one to hand
    out as ready — and to the project: a skill of the form that reached no task is not in the block at all."""
    earned = {key(skill) for part in (block.main, block.bonus) for task in part.tasks for skill in task.shares}
    # A skill with a share in a part that has tasks, and no task of its own, is named by the check of the block
    # («доля 0,4, по заданиям сумма 0 вместо 1»): not said twice.
    named = {key(skill) for part in (block.main, block.bonus) if part.tasks for skill in part.shares}
    lost = [name for name in names if key(name) not in earned | named]
    return [*([f"ни одному заданию не достались: {', '.join(f'«{name}»' for name in lost)}"] if lost else []), *block_issues(block, {})]


def names_of(text: str) -> list[str]:
    """The skills named in a field of the form: one a line, or separated by «;» or «,». A name said twice is one."""
    names: dict[str, str] = {}
    for part in re.split(r"[\n;,]", text):
        if part.strip():
            names.setdefault(key(part), " ".join(part.split()))
    return list(names.values())


class Planned(NamedTuple):
    """The skills of the project that is being prepared."""

    names: list[str]  # as the form names them, in its order
    code: str  # the project's code in the plan, or its title where the plan has no code
    # The shares the plan states for these skills in the project (the lines `all` and `Bonus all`), under the form's
    # names. None — the plan has no block for the project, or the form names another set of skills: a share is a part
    # of a whole, and with another whole the plan's shares say nothing.
    stated: SkillBlock | None
    # The plan has a block for the project, and the form names another set of skills than the block.
    differs: bool = False


def planned(request: StartRequest) -> Planned:
    from .source_policy import effective_request

    request = effective_request(request)
    names = names_of(request.brief.skills)
    by_key = {key(name): name for name in names}
    plan = request.curriculum
    row = next((r for r in plan.rows if r.id == request.selected_row_id), None) if plan else None
    code = code_of(row.code) if row else ""
    block = next((b for b in plan.skill_blocks if b.code == code), None) if plan and code and request.brief.curriculum_policy == "authoritative" else None
    if block is None or not names or {key(skill) for skill in skill_names(block)} != set(by_key):
        return Planned(names, (code or code_of(request.brief.title))[:MAX_CODE], None, bool(block and names and skill_names(block)))

    def own(shares: Mapping[str, float]) -> dict[str, float]:
        return {by_key[key(skill)]: share for skill, share in shares.items()}

    return Planned(names, code, SkillBlock(code=code, main=SkillPart(shares=own(block.main.shares)), bonus=SkillPart(shares=own(block.bonus.shares))))


class Divided(NamedTuple):
    """The skills of a project divided between the tasks of its concept."""

    block: SkillBlock  # as a block of the skills tab: main tasks «01»…, bonus ones «Bonus 01»…
    strangers: dict[int, list[str]]  # the number of a task → the skills it names that the project does not have
    twice: dict[int, list[str]]  # … → the skills it names a second time under another spelling
    idle: list[int]  # the numbers of the tasks that practise no skill of the project
    # Of the main part, what the concept itself says, whatever the plan states: the highest weight a main task gives
    # each skill, and the part of all the work of the main tasks (weight × hours) that falls to each skill.
    peak: dict[str, int]
    weighed: dict[str, float]


def divide(concept: Concept, project: Planned) -> Divided:
    """Which task practises what, and how much of the task is about it, is the concept's: a weight. How big the task
    is, is known: its hours. A skill is divided between the tasks by weight × hours — an hour-long task that is all
    about a skill does not outweigh a day-long one that is half about it. A name the project does not have, and a
    second weight for one skill, are not counted and not dropped in silence: they are returned to be said."""
    by_key = {key(name): name for name in project.names}
    parts: dict[bool, list[tuple[str, dict[str, float]]]] = {False: [], True: []}
    strangers: dict[int, list[str]] = {}
    twice: dict[int, list[str]] = {}
    idle: list[int] = []
    peak: dict[str, int] = {}
    work: dict[str, float] = {}
    for number, task in enumerate(concept.tasks, 1):
        own: dict[str, float] = {}
        for skill, weight in task.skills.items():
            name = by_key.get(key(skill))
            if name is None:
                strangers.setdefault(number, []).append(skill)
            elif name in own:
                twice.setdefault(number, []).append(skill)
            else:
                own[name] = weight * task.hours
                if not task.optional:
                    peak[name] = max(peak.get(name, 0), weight)
                    work[name] = work.get(name, 0) + own[name]
        parts[task.optional].append((f"{'Bonus ' if task.optional else ''}{len(parts[task.optional]) + 1:02}", own))
        if not own:
            idle.append(number)
    stated = project.stated or SkillBlock(code=project.code)
    # Without bonus tasks the plan's line for the bonus part stays as the plan has it: it is the plan's, not ours to drop.
    block = SkillBlock(code=project.code, main=spread(stated.main.shares, parts[False]), bonus=spread(stated.bonus.shares, parts[True]))
    return Divided(block, strangers, twice, idle, peak, exact(work))
