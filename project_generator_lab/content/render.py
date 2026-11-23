"""Assembly by code: the agreed draft is poured into the school templates. No model takes part here.

Templates and the fixed school texts live in templates/: methodologists edit them without touching code.
"""

import io
import json
import re
import zipfile
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .handoff import STYLE_NAMES
from .models import (
    BREAK,
    FILED,
    NO_INPUT,
    NO_OUTPUT,
    PRESENTATIONS,
    YES_NO,
    Checklist,
    ContentDraft,
    TaskContent,
    example_cells,
    picture_marks,
)

TEMPLATES = Path(__file__).parent / "templates"


class RenderError(RuntimeError):
    """The template produced something that does not read back as the data: a template bug, never shipped."""


def continued(text: str, indent: int) -> str:
    """Keep a multi-line list item inside its list item in Markdown."""
    return text.replace("\n", "\n" + " " * indent)


def cell(text: object) -> str:
    """A value as one cell of a Markdown table: a bar would end the cell, a new line the row."""
    return lines(str(text)).replace("|", "\\|").replace("\n", "<br>").replace(BREAK, "<br>").strip()


def lines(text: str) -> str:
    """One way to end a line: a return of the carriage alone would break a row of a table as a new line does."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


WRAPPED = re.compile(r"^(`+)(?!`)(.+?)(?<!`)\1$", re.S)


def code(text: str) -> str:
    """A line as a code span: what was typed is what is read. In a table a bar inside the span is still escaped."""
    if not text:
        return ""
    barred = text.replace("|", "\\|")
    if len(text) > 2 and text[0] == "`" == text[-1] and "`" not in text[1:-1]:
        return barred  # written as code already
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    # A span that begins and ends with a space loses one on each side, and a backtick at an edge would join the fence.
    pad = " " if text[0] == "`" or text[-1] == "`" or (text[0] == " " == text[-1] and text.strip()) else ""
    return f"{fence}{pad}{barred}{pad}{fence}"


def example(text: str) -> str:
    """A side of an example as a cell of the table. It is shown to the letter, as code: «2*x+3*x» written as text
    comes out as «2x+3x» in italics, «<N>» is not shown at all, and three spaces between two numbers are one
    (independent review 06.10.2026) — and an example is there to show exactly what is typed and what is printed.
    The words that stand for nothing typed or nothing printed are words, not code."""
    if text in (NO_INPUT, NO_OUTPUT):
        return text
    # A side the model wrapped in backticks as a whole is the same side without them: split by lines as it stood, it
    # would leave a backtick at the head of the first line and at the foot of the last.
    whole = WRAPPED.match(text)
    if whole and whole.group(1) not in whole.group(2):
        text = whole.group(2).strip() if len(whole.group(1)) > 1 else whole.group(2)
    return "<br>".join(code(line) for line in lines(text).replace("\n", BREAK).split(BREAK))


def statement(steps: list[str]) -> str:
    """The statement of a quest, to stand after «Получен Quest N.». Items of one line each are its sentences, in one
    paragraph. Where an item runs over lines — a list, a block of code — nothing is joined: each item is a paragraph
    as it was written, or a fence at the end of one would swallow what follows."""
    items = [item.strip() for item in steps if item.strip()]
    if any("\n" in item for item in items):
        return "\n\n".join(items)
    return sentences(items)


def sentences(items: list[str]) -> str:
    """The items as one paragraph: each ends as a sentence does, so two of them never run together."""
    return " ".join(item.strip() if item.strip().endswith((".", "!", "?", "…", ":", ";")) else item.strip() + "." for item in items if item.strip())


SPECIAL = re.compile(r"([\\`*_\[\]<>|~&])")
LEADING = re.compile(r"^(\s*)(?:([#+\-=])|(\d+)([.)]))")


def literal(text: str) -> str:
    """A line of someone else's words as Markdown shows it to the letter. An excerpt is a quotation: «git rebase
    [<options>]» poured in as it is came out as «git rebase []», «__init__» as a bold «init», and a line that begins
    with «#» or «-» as a heading or a list (independent review 06.10.2026). The marks Markdown reads are escaped; a
    mark that means something only at the head of a line — there only."""
    line = SPECIAL.sub(r"\\\1", " ".join(text.split()))
    return LEADING.sub(lambda head: head.group(1) + (f"\\{head.group(2)}" if head.group(2) else f"{head.group(3)}\\{head.group(4)}"), line)


def address(url: str) -> str:
    """An address as the target of a Markdown link, in angle brackets: brackets and spaces inside it do not end the link."""
    return "<" + url.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20") + ">"


@cache
def environment() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), trim_blocks=True, lstrip_blocks=True,
                      keep_trailing_newline=True, undefined=StrictUndefined, autoescape=False)
    env.filters["continued"] = continued
    env.filters["cell"] = cell
    env.filters["example"] = example
    env.filters["statement"] = statement
    env.filters["literal"] = literal
    env.filters["address"] = address
    env.filters["yaml_string"] = lambda value: json.dumps(str(value), ensure_ascii=False)
    return env


def school() -> dict[str, object]:
    data: dict[str, object] = yaml.safe_load((TEMPLATES / "school.yaml").read_text(encoding="utf-8"))
    return data


def anchor(title: str, seen: dict[str, int]) -> str:
    """GitLab heading ids: lowercase, punctuation removed, spaces to hyphens, repeated hyphens merged, duplicates numbered."""
    slug = re.sub(r"[^\w\- ]", "", title.lower().replace("*", ""))
    slug = re.sub(r"-{2,}", "-", slug.replace(" ", "-")).strip("-")
    count = seen.get(slug, 0)
    seen[slug] = count + 1
    return f"{slug}-{count}" if count else slug


def layout(draft: ContentDraft) -> str:
    """The frame of the README: the style the project is told in, or the template of the time before the styles."""
    return draft.style.profile if draft.style else "classic"


def words(profile: str) -> dict[str, object]:
    """The fixed words of a style, as the methodologists keep them in school.yaml."""
    styles = school()["styles"]
    assert isinstance(styles, dict)
    found: dict[str, object] = styles[profile]
    return found


# What the fields of a task are called where the methodologist reads them — the card in the chat, a proposed edit, the
# whole text — by the frame of the README: the same field is «Формулировка» of a quest and «Шаги» of a legend, and an
# edit named «Что нужно сделать» would point at nothing the card shows.
FIELD_WORDS: dict[str, dict[str, str]] = {
    "quest": {"context": "Сюжет квеста", "steps": "Формулировка", "expected": "Требования к результату", "examples": "Примеры «ввод → вывод»",
              "constraints": "Внимание", "result": "Сдать", "check": "Проверка", "transition": "Чем закончилась сцена"},
    "legend": {"context": "Что создаёшь и с чем работаешь", "steps": "Шаги", "expected": "Должно получиться", "examples": "Примеры «ввод → вывод»",
               "constraints": "Условия", "result": "Результат", "check": "Проверка", "transition": "Зачем"},
    "classic": {"context": "Ситуация и данные", "steps": "Что нужно сделать", "expected": "Что должно получиться",
                "examples": "Примеры «ввод → вывод»", "constraints": "Ограничения и условия", "result": "Формат сдачи", "check": "Проверка",
                "transition": "Переход к следующему заданию"},
}


def field_words(draft: ContentDraft) -> dict[str, str]:
    return FIELD_WORDS[layout(draft)]


def topic_name(draft: ContentDraft, number: int | str) -> str:
    """A section of the theory as the methodologist is told of it: «List 2» of a quest, «Раздел 2» of a legend,
    «Раздел 2.2» of the first template."""
    return {"quest": f"List {number}", "legend": f"Раздел {number}"}.get(layout(draft), f"Раздел 2.{number}")


def heading(draft: ContentDraft, task: TaskContent) -> str:
    """How the task is headed in the README and named in the check-list — one line for both. In a style: «Quest N.
    Название» or «Задание N. Название», a bonus one marked as the style marks it and numbered with the rest. Without
    one: «Задание N. Название», bonus tasks numbered on their own, with a star, as in the generator's template."""
    planned = draft.handoff.task(task.number)
    if draft.style:
        form = str(words(draft.style.profile)["bonus" if planned.optional else "task"])
        return form.replace("{n}", str(task.number)).replace("{title}", task.title)
    if not planned.optional:
        return f"Задание {task.number}. {task.title}"
    bonus = [t.number for t in draft.handoff.tasks if t.optional].index(task.number) + 1
    return str(school().get("bonus_title", "Бонусное задание {n}*")).format(n=bonus) + f": {task.title}"


def ordered_tasks(draft: ContentDraft) -> list[TaskContent]:
    return sorted(draft.tasks, key=lambda t: t.number)


GAP = "_Ещё не написано._"
Entry = dict[str, object]


class Contents:
    """The table of contents of a README, filled in the order its headings stand: GitLab numbers the anchors of
    headings that read alike in that order."""

    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.seen: dict[str, int] = {}

    def add(self, level: int, title: object) -> Entry:
        entry: Entry = {"level": level, "title": str(title), "anchor": anchor(str(title), self.seen)}
        self.entries.append(entry)
        return entry


def written(draft: ContentDraft) -> tuple[dict[str, Any], dict[str, Any]]:
    """The title with the annotation and the frame, or visible gaps: a preview never pretends to be complete."""
    summary = draft.summary.model_dump() if draft.summary else {"title": draft.handoff.title, "annotation": GAP}
    frame = draft.frame.model_dump() if draft.frame else {"introduction": GAP, "instruction": [], "conclusion": GAP}
    return summary, frame


def pictured(text: str, word: object) -> str:
    """The marks of pictures as the README shows them: a quoted line that cannot be taken for the text itself, with an
    empty line on each side — the line after a quotation would be read as its continuation, and two marks in a row as one."""
    lines, found = picture_marks(text)
    if not found:
        return text
    shown: list[str] = []
    for i, line in enumerate(lines):
        if i not in found:
            shown.append(line)
            continue
        if shown and shown[-1].strip():
            shown.append("")
        shown.append(f"> **{word}** {found[i]}")
        if i + 1 < len(lines) and lines[i + 1].strip():
            shown.append("")
    return "\n".join(shown)


THEORY_FILE = "theory.md"


def theory_path(draft: ContentDraft) -> str:
    """The file of materials/ that holds the summaries of the theory; "" where the presentation puts none there. A
    file of a task is never written over: the first name no material has is taken, whatever the case of its letters."""
    if draft.presentation() not in FILED or not (draft.theory and draft.theory.topics):
        return ""
    taken = {material.path.lower() for material in draft.materials}
    names = (THEORY_FILE, "theory_notes.md", *(f"theory_notes_{n}.md" for n in range(2, len(taken) + 3)))
    return next(name for name in names if name not in taken)


def file_headings(draft: ContentDraft) -> tuple[str, dict[int, tuple[str, str]]]:
    """The title of the file of the theory and, for each section, its heading there with the anchor GitLab gives it."""
    seen: dict[str, int] = {}
    title = str(school()["theory_file"]).replace("{title}", draft.summary.title if draft.summary else draft.handoff.title)
    anchor(title, seen)
    found = {}
    for topic in draft.theory.topics if draft.theory else []:
        name = f"{topic.number}. {topic.title}"
        found[topic.number] = (name, anchor(name, seen))
    return title, found


def topic_view(draft: ContentDraft, topic: Any) -> dict[str, object]:
    """A section of the theory as a template shows it, by the plan of the theory: which text stands in the README,
    whether links are of use, the excerpt that belongs to it and where its summary lies."""
    shown = draft.presentation()
    online = draft.online()
    excerpt = draft.theory.quoted() if draft.theory else None
    path = theory_path(draft)
    return {**topic.model_dump(),
            # The summary where the README is to be worked from; the short text otherwise.
            "body": topic.notes if shown == "notes" and topic.notes.strip() else topic.text,
            "links": [link.model_dump() for link in topic.links] if online else [],
            "keywords": topic.keywords if online else [],
            "excerpt": excerpt.model_dump() if excerpt and excerpt.topic == topic.number else None,
            "more": f"materials/{path}#{file_headings(draft)[1][topic.number][1]}" if path else ""}


def topics_of(draft: ContentDraft, contents: Contents, level: int, form: str) -> list[dict[str, object]]:
    """The sections of the theory with their headings in the README. Where the theory lies in a file of materials/
    only, the README has no such headings: a section is named as the file names it and is found there."""
    found = []
    filed = draft.presentation() == "materials" and theory_path(draft)
    for topic in draft.theory.topics if draft.theory else []:
        view = topic_view(draft, topic)
        if filed:
            name, _ = file_headings(draft)[1][topic.number]
            found.append({**view, "heading": name, "anchor": "", "href": view["more"]})
        else:
            item = contents.add(level, form.replace("{n}", str(topic.number)).replace("{title}", topic.title))
            found.append({**view, "heading": item["title"], "anchor": item["anchor"], "href": f"#{item['anchor']}"})
    return found


def tasks_of(draft: ContentDraft, contents: Contents, level: int, topics: list[dict[str, object]]) -> list[dict[str, object]]:
    found = []
    for t in ordered_tasks(draft):
        item = contents.add(level, heading(draft, t))
        found.append({**t.model_dump(), "heading": item["title"], "example_rows": [example_cells(line) for line in t.examples],
                      "materials": [m.path for m in draft.materials if t.number in m.tasks],
                      "theory": [{"title": x["heading"], "anchor": x["anchor"], "href": x["href"]} for x in topics if t.number in x["tasks"]]})  # type: ignore[operator]
    return found


def marked(draft: ContentDraft, frame: dict[str, Any], tasks: list[dict[str, object]], topics: list[dict[str, object]], word: object) -> None:
    """The places of pictures, shown as such in every text of the README that may hold one. A draft begun before the
    marks (content 0.5 and earlier) is shown as it was written: a line in brackets there is the author's own text."""
    if not draft.plans_theory():
        return
    for name in ("introduction", "conclusion"):
        frame[name] = pictured(str(frame[name]), word)
    for task in tasks:
        for name in ("context", "transition"):
            task[name] = pictured(str(task[name]), word)
    for topic in topics:
        topic["body"] = pictured(str(topic["body"]), word)


def theory_file(draft: ContentDraft) -> str:
    """The summaries of the theory as a file of materials/: each section with what the README would have said of it —
    where to read on, the excerpt, the question — so that the file can be read without the README and without the internet."""
    title, headings = file_headings(draft)
    said = school()["theory_words"]
    assert isinstance(said, dict)
    topics = [{**topic_view(draft, topic), "heading": headings[topic.number][0],
               "body": pictured(topic.notes if topic.notes.strip() else topic.text, said["picture"])}
              for topic in (draft.theory.topics if draft.theory else [])]
    return tidy(environment().get_template("theory.md.j2").render(title=title, topics=topics, words=said))


def tidy(text: str) -> str:
    return re.sub(r"\n{4,}", "\n\n\n", text).rstrip() + "\n"


def readme(draft: ContentDraft) -> str:
    """README of the draft in the frame of its style; of a draft begun before the styles — by the template of that time."""
    return {"quest": readme_quest, "legend": readme_legend}.get(layout(draft), readme_classic)(draft)


def readme_classic(draft: ContentDraft) -> str:
    """The generator's first template: Глава 1 (Введение, Инструкция), Глава 2 (теория 2.N), Глава 3 (задания, бонус),
    Заключение."""
    fixed = school()
    headings = fixed["headings"]
    assert isinstance(headings, dict)
    summary, frame = written(draft)
    learn = bool(fixed.get("how_to_learn_in_readme"))
    contents = Contents()
    contents.add(0, headings["chapter1"])
    if learn:
        contents.add(1, "Как учиться в «Школе 21»")
    contents.add(1, headings["introduction"])
    contents.add(1, headings["instruction"])
    contents.add(0, headings["theory"])
    topics = topics_of(draft, contents, 1, str(fixed.get("topic_prefix", "")) + "{n}. {title}")
    contents.add(0, headings["practice"])
    tasks = tasks_of(draft, contents, 1, topics)
    if frame["conclusion"]:
        contents.add(0, headings["conclusion"])
    return tidy(environment().get_template("README.md.j2").render(
        summary=summary, frame=frame, topics=topics, tasks=tasks, school=fixed, headings=headings, toc=contents.entries, learn=learn))


def readme_quest(draft: ContentDraft) -> str:
    """The text quest of a bassin: Введение with the school's recommendations, Chapter I — the scene, Chapter II — the
    theory as found sheets, Chapter III — the quests, Chapter IV — how it ends."""
    said = words("quest")
    summary, frame = written(draft)
    contents = Contents()
    contents.add(0, said["introduction"])
    contents.add(1, said["recommendations"])
    contents.add(0, said["scene"])
    contents.add(0, said["theory"])
    topics = topics_of(draft, contents, 1, str(said["topic"]))
    contents.add(0, said["practice"])
    tasks = tasks_of(draft, contents, 1, topics)
    if frame["conclusion"]:
        contents.add(0, said["conclusion"])
    marked(draft, frame, tasks, topics, said["picture"])
    return tidy(environment().get_template("README.quest.md.j2").render(
        summary=summary, frame=frame, topics=topics, tasks=tasks, school=school(), words=said, toc=contents.entries,
        learn=bool(school().get("how_to_learn_in_readme")), theory_file=theory_path(draft),
        filed=draft.presentation() == "materials" and bool(theory_path(draft))))


def readme_legend(draft: ContentDraft) -> str:
    """One working legend and plain tasks: the legend, «Старт работы» with the rules and the table of materials, the
    theory, the tasks each with what came of it, how it is checked and what it is for, and what is handed in."""
    said = words("legend")
    summary, frame = written(draft)
    contents = Contents()
    contents.add(0, said["start"])
    topics: list[dict[str, object]] = []
    # Where the theory lies in the file of materials/ alone, the README has no chapter of it: the table of «Старт
    # работы» says where each section is.
    filed = draft.presentation() == "materials" and bool(theory_path(draft))
    if draft.theory and draft.theory.topics:
        if not filed:
            contents.add(0, said["theory"])
        topics = topics_of(draft, contents, 1, str(said["topic"]))
    tasks = tasks_of(draft, contents, 0, topics)
    for task in tasks:
        planned = draft.handoff.task(int(str(task["number"])))
        task["label"] = str(said["handed_bonus"]).replace("{n}", str(task["number"])) if planned.optional else str(task["number"])
    if frame["conclusion"]:
        contents.add(0, said["conclusion"])
    if tasks:
        contents.add(0, said["handed"])
    marked(draft, frame, tasks, topics, said["picture"])
    return tidy(environment().get_template("README.legend.md.j2").render(
        summary=summary, frame=frame, topics=topics, tasks=tasks, school=school(), words=said, toc=contents.entries,
        learn=bool(school().get("how_to_learn_in_readme")), materials=[m.model_dump(include={"path", "purpose"}) for m in draft.materials],
        theory_file=theory_path(draft), filed=filed))


def fence(text: str) -> str:
    """A code fence longer than any backtick run inside the text, so a material never breaks the page."""
    longest = max((len(run) for run in re.findall(r"`{3,}", text)), default=2)
    return "`" * (longest + 1)


def full_text(draft: ContentDraft) -> str:
    """The whole material in reading order, reasons included: what the methodologist and the critics read."""
    style = {**draft.style.model_dump(), "name": STYLE_NAMES[draft.style.profile], "stale": draft.style.stale(),
             "written_for_name": STYLE_NAMES.get(draft.style.written_for, draft.style.written_for)} if draft.style else None
    plan = draft.theory_plan
    given = {"name": PRESENTATIONS[plan.presentation], "internet": YES_NO[plan.internet], "english": YES_NO[plan.english],
             "file": theory_path(draft)} if plan else None
    text = environment().get_template("full_text.md.j2").render(
        summary=draft.summary, frame=draft.frame, theory=draft.theory, checklist=ensure_checklist(draft), style=style, plan=given,
        tasks=[{**t.model_dump(), "heading": heading(draft, t)} for t in ordered_tasks(draft)],
        materials=[{**m.model_dump(), "fence": fence(m.content)} for m in draft.materials],
        topic_prefix=str(school().get("topic_prefix", "")), words=field_words(draft),
        topic_names={t.number: topic_name(draft, t.number) for t in (draft.theory.topics if draft.theory else [])})
    return re.sub(r"\n{4,}", "\n\n\n", text).rstrip() + "\n"


def ensure_checklist(draft: ContentDraft) -> Checklist:
    """The check-list texts of this draft; filled from school.yaml the first time, then edited only by patches."""
    if draft.checklist is None:
        settings = school()["checklist"]
        assert isinstance(settings, dict)
        draft.checklist = Checklist(introduction=str(settings["introduction"]), guidelines=str(settings["guidelines"]),
                                    disclaimer=str(school().get("checklist_disclaimer") or "").strip())
    return draft.checklist


def with_disclaimer(guidelines: str, disclaimer: str) -> str:
    """The disclaimer closes the guidelines as one more point; the methodologist's text is kept as written."""
    text = disclaimer.strip()
    if not text:
        return guidelines
    lines = text.splitlines()
    first = lines[0] if lines[0].lstrip().startswith(("-", "*", "•")) else f"- {lines[0]}"
    rest = [line if line.startswith(" ") or not line.strip() else f"  {line}" for line in lines[1:]]
    return "\n".join([guidelines.rstrip(), first, *rest])


def checklist_data(draft: ContentDraft) -> dict[str, object]:
    settings = school()["checklist"]
    assert isinstance(settings, dict)
    texts = ensure_checklist(draft)
    questions = [{"name": heading(draft, t), "description": "\n".join(f"- {item}" for item in t.peer_checks),
                  "rating": str(settings["default_rating"])} for t in ordered_tasks(draft)]
    return {"introduction": texts.introduction, "quick_actions": settings["quick_actions"], "language": settings["language"],
            "guidelines": with_disclaimer(texts.guidelines, texts.disclaimer),
            "sections": [{"kind": str(settings["section_kind"]), "name": settings["section_name"], "description": "", "questions": questions}]}


def checklist(draft: ContentDraft) -> str:
    data = checklist_data(draft)
    settings = school()["checklist"]
    sections = data["sections"]
    assert isinstance(sections, list)
    assert isinstance(settings, dict)
    text = environment().get_template("check-list.yml.j2").render(checklist={**settings, "introduction": data["introduction"],
                                                                             "guidelines": data["guidelines"]},
                                                                  questions=sections[0]["questions"])
    # The platform reads this file: it must parse back to exactly the data, or it is not handed out.
    if yaml.safe_load(text) != data:
        raise RenderError("check-list.yml не совпал с данными после чтения — ошибка шаблона")
    return text


def project_file(draft: ContentDraft, path: str) -> str | None:
    """A file of the project by its path from the root of the repository, as the README links to it and as the
    bundle will hold it: the README, the check-list, a file of materials/. None — the project has no such file: a
    file under src/ is the learner's to make, and a path with «..» names nothing here."""
    if path == "README.md":
        return readme(draft)
    if path == "check-list.yml":
        return checklist(draft)
    if not path.startswith("materials/"):
        return None
    name = path.removeprefix("materials/")
    if name and name == theory_path(draft):
        return theory_file(draft)
    material = next((m for m in draft.materials if m.path == name), None)
    if material is None:
        return None
    return material.content if material.content.endswith("\n") else material.content + "\n"


def bundle(draft: ContentDraft) -> bytes:
    """The repository layout of a school project: README, check-list, materials/, an empty src/ for the learner."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", readme(draft))
        archive.writestr("check-list.yml", checklist(draft))
        for material in draft.materials:
            archive.writestr(f"materials/{material.path}", material.content if material.content.endswith("\n") else material.content + "\n")
        # The summaries of the theory, where the plan puts them into a file: written by code from the agreed sections.
        if theory_path(draft):
            archive.writestr(f"materials/{theory_path(draft)}", theory_file(draft))
        if not draft.materials and not theory_path(draft):
            archive.writestr("materials/.gitkeep", "")
        archive.writestr("src/.gitkeep", "")
        archive.writestr("misc/images/.gitkeep", "")
    return buffer.getvalue()
