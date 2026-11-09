"""Layer 1 of the measure: the generator's own checks of the text, applied to a project read from its repository.

The checks are those of content/checks.py and no others — a rule that holds a draft of the generator is the rule a
project made by hand is held to, or the two cannot be compared. What a project in a repository cannot be asked
(its inputs are not here, it has no statuses) is not skipped in silence: it is listed in the report as not checked.
"""

import re
from typing import NamedTuple

from ..content import checks
from ..content.checks import Check
from ..content.models import TaskContent, Theory
from . import repository
from .repository import Paired, Project, TextFile

# The blocks of a task in this generator's own README (content/templates/README.md.j2), by the field of the draft.
OWN = {"что нужно сделать": "steps", "что должно получиться": "expected", "ограничения и условия": "constraints",
       "формат сдачи": "result", "переход к следующему заданию": "transition"}
# What a task is asked for depends on how it is written, and a task is not asked for what its template does not have.
# blocks — the generator's own template: everything a draft is asked for. table — the school's task that opens with a
# table: something to do, the files to hand in, a question for the peer; «Что должно получиться» is not a part of it.
# quest — a task given by the line «Получен Quest N.» (the bassin): the line names the file to make, and a line
# «Сдать:» beside it is this generator's own — read when it is there, not asked for when it is not.
# text — none of these: the files to hand in are named somewhere in the text, where code does not find them.
ASKED = {"blocks": ("steps", "expected", "result", "peer_checks"), "table": ("steps", "result", "peer_checks"), "quest": ("steps", "peer_checks"),
         "text": ("steps", "peer_checks")}
NUMBERED, BULLET = re.compile(r"^\d+\.\s+"), re.compile(r"^-\s+")
# What this layer does and what it cannot do for a project that came without its inputs — said in every report.
APPLIED = (
    ("Готовый ответ в файлах и заданиях", "да", "имена всех файлов репозитория, текст файлов до 300 КБ, весь текст каждого задания README"),
    ("У задания есть что делать, что сдать и вопрос пиру", "да", "«что сдать» — строки таблицы в начале задания, блок «Формат сдачи» или строка «Сдать:» "
     "квеста; «что делать» у квеста — строка «Получен Quest N.»; вопрос — из чек-листа. У задания без таблицы, блоков и строки «Сдать:» файлы сдачи "
     "названы в тексте, код их не находит: такие задания перечислены, блокером не считаются"),
    ("Повтор текста между заданиями", "да", "8 слов подряд, от трёх совпадений; формат сдачи и вопросы пиру не сравниваются"),
    ("Заглушки в тексте (TODO, «[вставить…»)", "да", "название, аннотация, разделы, задания, вопросы чек-листа"),
    ("Файлы CSV, JSON, YAML читаются", "да", "файлы до 300 КБ; файл, испорченный намеренно (данные задания «почини»), называется в meta.yaml: broken"),
    ("Вопрос чек-листа без задания", "да", "только здесь: у черновика генератора вопросы строятся из заданий"),
    ("Блок «Что должно получиться»", "по шаблону", "спрашивается с задания, написанного блоками генератора; в задании школы с таблицей его нет"),
    ("Готовый ответ во введении, правилах, аннотации", "нет", "генератор проверяет на это задания, темы теории и материалы; у README темы теории от остальных разделов код не отличает"),
    ("Эталонный ответ в вопросе чек-листа", "нет", "разрешён решением 05.10.2026 и у генератора; считается для сведения («вопросов с блоком кода»)"),
    ("Покрытие ОР, часы заданий", "нет", "нужны входы проекта (строка УП)"),
    ("Тема теории у задания, ссылки тем", "нет", "нужна глава теории, разобранная по темам"),
    ("Согласованность частей, проверка критиками", "нет", "это состояния черновика, у готового проекта их нет"),
)


class Text(NamedTuple):
    """A project as content/checks.Written: what the checks of the text need and no more."""

    tasks: list[TaskContent]
    materials: list[TextFile]
    theory: Theory | None = None


def flat(text: object) -> str:
    """Words of a project inside a line of the report: on one line, so that a name with a line break in it cannot
    begin a heading or a row of the report's own, and with «<» written out, so that a tag in it stays a word."""
    return " ".join(str(text).split()).replace("<", "&lt;")


def said(found: list[Check]) -> list[str]:
    """The findings as lines of the report. A repeated passage is found once for every pair of tasks that share it —
    eight tasks with one sentence in common are 28 findings; here they are one line with the number of pairs.
    Nothing else is merged: two tasks with one fault are two lines."""
    lines: list[str] = []
    pairs: dict[str, int] = {}
    for check in found:
        kind = "блокер" if check.severity == "block" else "замечание"
        # Group by the quoted evidence, independent of a legacy/current message prefix or task numbers.
        example = re.search(r"«[^»]+»", check.message) if check.code == "copy_paste" else None
        if example:
            line = (f"{kind} · Значительное текстовое совпадение: {flat(example.group())}. "
                    "Проверьте учебную функцию повтора; совпадение не доказывает дублирование заданий.")
            pairs[line] = pairs.get(line, 0) + 1
            lines += [line] if pairs[line] == 1 else []
        else:
            lines.append(f"{kind} · {flat(check.message)}")
    return [line + (f" — пар заданий: {pairs[line]}" if pairs.get(line, 0) > 1 else "") for line in lines]


class Measured(NamedTuple):
    project: Project
    paired: Paired
    numbers: list[int]  # the number each task goes by in the findings, in the order of the README
    checks: list[Check]
    untabled: list[str]  # the tasks written as plain text, and the quests without a line «Сдать:»: what is handed in is somewhere in the text, unread

    @property
    def blockers(self) -> int:
        return sum(check.severity == "block" for check in self.checks)

    @property
    def warnings(self) -> int:
        """Counted as the report shows them: a passage many pairs of tasks repeat is one remark."""
        return sum(line.startswith("замечание") for line in said(self.checks))


def numbers(project: Project) -> list[int]:
    """The number a task goes by in the findings: its own («Задание 00» is 0) while the numbers tell the tasks apart,
    else its place in the README — main and bonus tasks may both start from one, «1.1» and «1.2» are both one."""
    own = [task.number for task in project.readme.tasks]
    return own if len(set(own)) == len(own) else list(range(1, len(own) + 1))


def shape(task: repository.Task) -> str:
    """How the task is written: blocks, table, quest or text (see ASKED). A task that opens with a table is the
    school's, whatever bold lines its text has; the generator's own of the first template begins with «Что нужно
    сделать»; a quest is given by its line «Получен Quest N.»."""
    return "table" if task.table else "blocks" if any(title.lower() == "что нужно сделать" for title in task.blocks) else "quest" if task.quest else "text"


def items(text: str, mark: re.Pattern[str]) -> list[str]:
    """The items of a list as the template wrote them: a marked line and the indented lines that continue it."""
    found: list[str] = []
    for line in text.splitlines():
        if mark.match(line):
            found.append(mark.sub("", line, count=1).strip())
        elif found and line.startswith(" ") and line.strip():
            found[-1] += "\n" + line.strip()
    return found


class Said(NamedTuple):
    """What a task says, by the fields the checks read."""

    goal: str
    context: str
    steps: list[str]
    expected: list[str]
    constraints: list[str]
    result: str
    transition: str


def said_by(task: repository.Task) -> Said:
    if task.quest and shape(task) == "quest":
        # The story is the situation, the line of the quest is what to do; the list right after the line is what this
        # generator's README says must come out. What is handed in is the quest's line «Сдать:», when it has one.
        after = re.split(r"\n\s*\n", task.quest.rest, maxsplit=1)[0]
        return Said("", task.quest.story, [task.quest.statement] if task.quest.statement else [], items(after, BULLET), [], "; ".join(task.submit), "")
    if shape(task) != "blocks":
        return Said("", "", [part.strip() for part in re.split(r"\n\s*\n", task.text) if part.strip()], [],
                    [*(f"Разрешено — {row}" for row in task.allowed), *(f"Запрещено — {row}" for row in task.forbidden)], "; ".join(task.submit), "")
    block = {OWN[title.lower()]: text for title, text in task.blocks.items() if title.lower() in OWN}
    # «Что нужно сделать» holds the situation, then the line of the goal, then the numbered steps. The goal is the
    # last line that begins with «Цель:» — a situation may have such a line of its own, the steps are indented.
    doing = block.get("steps", "").splitlines()
    aim = next((index for index in reversed(range(len(doing))) if doing[index].startswith("Цель:")), None)
    return Said(doing[aim].removeprefix("Цель:").strip() if aim is not None else "", "\n".join(doing[:aim]).strip() if aim is not None else "",
                items("\n".join(doing[(aim or 0):]), NUMBERED), items(block.get("expected", ""), BULLET), items(block.get("constraints", ""), BULLET),
                block.get("result", "").strip(), block.get("transition", "").strip())


def as_tasks(project: Project, paired: Paired) -> list[TaskContent]:
    """The tasks of the README in the shape the checks read. Built without validation: a task read from a README is
    what it is (number 0, no goal), not a draft to be refused."""
    found = []
    for index, (number, task) in enumerate(zip(numbers(project), project.readme.tasks, strict=True)):
        said = said_by(task)
        found.append(TaskContent.model_construct(
            number=number, title=task.title or task.label, goal=said.goal, context=said.context, steps=said.steps, expected=said.expected,
            constraints=said.constraints, result=said.result, transition=said.transition,
            peer_checks=[q.text for q in paired.questions.get(index, []) if q.text], needs=[], rationale=None, status="approved"))
    return found


def measure(project: Project) -> Measured:
    """The findings of the generator's checks for the project. A README in which no task was recognised is not a
    project without faults — it is not measured: ValueError."""
    readme = project.readme
    if not readme.tasks:
        raise ValueError(f"в {project.readme_file} проекта «{project.name}» не найдено ни одного задания: заголовков «Задание N», «Part N», «Quest N» нет"
                         + "".join(f"; {note}" for note in project.notes))
    paired = repository.pair(readme, project.checklist)
    tasks = as_tasks(project, paired)
    # What the learner reads of a task, whole — every line under its heading as it stands, the table too — for the
    # checks that look for words: nothing a parse of the task into rows and blocks left out can hide from them. The questions of the check-list
    # are the peer's: an answer shown there is not shown to the one who solves the task, and whether it may be shown
    # at all is not for this layer to decide.
    whole = [TaskContent.model_construct(
        number=parsed.number, title=parsed.title, goal="", context=task.raw,
        steps=[], expected=[], constraints=[], result="", transition="", peer_checks=[], needs=[], rationale=None, status="approved")
        for parsed, task in zip(tasks, readme.tasks, strict=True)]
    found = checks.solution_leaks(Text(whole, project.files))
    # Where the solution goes is read from the table a task opens with or from its block. A task with neither says
    # it in its text — «напиши скрипт part1.sql» — and code cannot tell that from a task that says nothing: not
    # asked, and named.
    shapes = [shape(task) for task in readme.tasks]
    for task, written in zip(tasks, shapes, strict=True):
        gaps = [checks.BLOCKS[name] for name in checks.missing(task) if name in ASKED[written]]
        if gaps:
            found.append(Check(code="submission", severity="block", part=f"task:{task.number}", message=f"В задании {task.number} нет: {', '.join(gaps)}."))
    found += checks.copy_paste(Text(tasks, project.files))
    questions = project.checklist.questions if project.checklist else []
    found += checks.stubs([("summary", f"{readme.title}\n{readme.annotation}"), *((f"section:{s.title}", s.text) for s in readme.sections),
                           *((f"task:{task.number}", checks.task_text(task)) for task in whole), *((f"question:{q.name}", q.text) for q in questions)])
    for file in project.files:
        if file.content and file.path not in project.broken and (problem := checks.parses(file)):
            found.append(Check(code="complete", severity="block", part=f"material:{file.path}",
                               message=f"Файл «{file.path}» не читается как {file.path.rsplit('.', 1)[-1]}: {problem}."))
    # By number, a question left over names no task there is. In order, it is one more than the tasks of its part:
    # it may well be a second question about the last of them, and its name does not say so.
    left = ("не относится ни к одному заданию README: номера задания в названии нет или задания с таким номером нет" if paired.by == "номеру" else
            "не сопоставлен с заданием: вопросы не называют номера заданий и взяты по порядку, а этот в своей части лишний по счёту")
    found += [Check(code="checklist", severity="warn", part="checklist", message=f"Вопрос чек-листа «{name}» {left}.") for name in paired.questions_without]
    # Taken in order, a question that does name a number may stand against a task of another number: the pairs are
    # then a guess, and it is said to be one.
    numbered = sum(bool(repository.numbering(q.name)) for q in questions)
    if paired.by == "порядку" and numbered:
        found.append(Check(code="checklist", severity="warn", part="checklist", message=(
            f"Номер задания называют {numbered} из {len(questions)} вопросов чек-листа — не большинство (или номера заданий в README повторяются), "
            "поэтому вопросы сопоставлены с заданиями по порядку. Проверьте пары: вопрос с номером мог встать не к своему заданию.")))
    # What is handed in was not read: the task is plain text, or a quest without a line «Сдать:» — its file is named in the line of the quest.
    unread = [task.label for task, written in zip(readme.tasks, shapes, strict=True) if written == "text" or (written == "quest" and not task.submit)]
    return Measured(project, paired, numbers(project), found, unread)


def row(measured: Measured) -> dict[str, object]:
    """What the report says of one project, as plain data."""
    project, readme, questions = measured.project, measured.project.readme, measured.project.checklist.questions if measured.project.checklist else []
    return {
        "project": project.name, "readme": project.readme_file, "checklist": project.checklist_file,
        "tasks": sum(not task.bonus for task in readme.tasks), "bonus_tasks": sum(task.bonus for task in readme.tasks),
        "questions": len(questions), "paired_by": measured.paired.by if questions else "",
        "tasks_without_question": measured.paired.tasks_without, "questions_without_task": measured.paired.questions_without,
        "tasks_without_table": measured.untabled, "questions_with_code": sum(q.code for q in questions),
        "numbers": [[number, task.label] for number, task in zip(measured.numbers, readme.tasks, strict=True)],
        "images": readme.images, "files": len(project.files), "files_read": sum(bool(file.content) for file in project.files),
        "sections": [{"title": s.title, "role": s.role, "chars": len(s.text)} for s in readme.sections],
        "blockers": measured.blockers, "warnings": measured.warnings, "notes": project.notes, "findings": said(measured.checks),
        "checks": [check.model_dump() for check in measured.checks],
    }


def table(header: list[str], lines: list[list[object]]) -> list[str]:
    def cell(value: object) -> str:
        return flat(value).replace("|", "\\|")

    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header), *("| " + " | ".join(cell(v) for v in line) + " |" for line in lines)]


def report(measured: list[Measured], passed: list[str], held: list[str]) -> str:
    """The report in Markdown: what was applied, a line a project, then every finding of every project."""
    rows = [row(m) for m in measured]
    clean = sum(not m.blockers for m in measured)
    tasks, unread = sum(len(m.project.readme.tasks) for m in measured), sum(len(m.untabled) for m in measured)
    out = ["# Слой кода: проекты из репозиториев", ""]
    out += [f"Проектов: {len(rows)}. Без блокеров: {clean} из {len(rows)}. Блокеров всего: {sum(m.blockers for m in measured)}, "
            f"замечаний: {sum(m.warnings for m in measured)}. Заданий: {tasks}, из них формат сдачи кодом не прочитан у {unread}.", ""]
    if held:
        out += [f"Отложенные случаи не измерялись ({len(held)}): {', '.join(flat(name) for name in held)}. Они запускаются только перед релизом: `--release`.", ""]
    if passed:
        out += ["Пропущено:", *(f"- {flat(line)}" for line in passed), ""]
    out += ["## Что проверено", "", *table(["Проверка", "Применена", "Чем и почему"], [list(line) for line in APPLIED]), ""]
    out += ["## Проекты", "", *table(
        ["Проект", "Заданий", "Бонусных", "Вопросов чек-листа", "Сопоставлены по", "Заданий, где сдача читается кодом", "Вопросов с блоком кода",
         "Картинок", "Файлов (прочитано)", "Блокеры", "Замечания"],
        [[r["project"], r["tasks"], r["bonus_tasks"], r["questions"], r["paired_by"] or "—", len(m.project.readme.tasks) - len(m.untabled),
          r["questions_with_code"], r["images"], f"{r['files']} ({r['files_read']})", m.blockers, m.warnings]
         for r, m in zip(rows, measured, strict=True)]), ""]
    out += ["Замечание о повторе текста выдаётся на каждую пару заданий; в отчёте пары с одним повтором собраны в одну строку.", ""]
    for m in measured:
        project = m.project
        out += [f"## {flat(project.name)}", "", f"README: `{flat(project.readme_file)}`, чек-лист: `{flat(project.checklist_file) or 'нет'}`."]
        if m.numbers != [task.number for task in project.readme.tasks]:  # numbered by place: say which task is which
            out += ["Номера заданий в замечаниях: " + ", ".join(f"{n} — «{flat(task.label)}»" for n, task in zip(m.numbers, project.readme.tasks, strict=True)) + "."]
        outside = [flat(s.title) for s in project.readme.sections if s.role == "other" and s.title]
        out += [f"Разделы вне шаблона: {', '.join(f'«{title}»' for title in outside)}."] if outside else []
        out += [f"- {flat(note)}" for note in project.notes]
        if m.untabled:
            out += [f"- формат сдачи записан не таблицей и не блоком, кодом не проверен: {', '.join(flat(label) for label in m.untabled)}"]
        out += [f"- {line}" for line in said(m.checks)] or ["Замечаний кода нет."]
        out += [""]
    return "\n".join(out)
