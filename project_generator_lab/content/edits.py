"""Chat edits of the draft as explicit patches: proposed by the model, applied only by the methodologist.

A patch names a part (summary, task:N, theory, topic:N, frame, material:path), a field, and the full old and new
value. A stale patch (the field changed after the proposal) is refused, never half-applied. A link may enter the
text only if the search returned it or the methodologist typed it: a model cannot slip in an invented URL.
"""

import re

from fastapi import HTTPException
from pydantic import ValidationError

from . import render
from .handoff import STYLE_NAMES
from .models import (
    PRESENTATIONS,
    YES_NO,
    ContentChange,
    ContentDraft,
    ContentMessage,
    Link,
    Material,
    Patch,
    Value,
    example_items,
    list_items,
    material_path,
)
from .writer import EDITABLE, LIST_FIELDS, editable

# An address runs to the first space or quote; brackets are allowed (a Wikipedia address keeps «(…)»), and clean_url
# ends it where only punctuation follows: at a closing bracket without its pair inside the address, before a final .,;
URL = re.compile(r"https?://[^\s<>\"'»]+")
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\((?=https?://)")
TITLE_EDGES = " —–-:|•*\t"
WHY_EDGES = " —–-:|\t"


def kind_of(part: str) -> str:
    return part.split(":", 1)[0]


def part_label(part: str, draft: ContentDraft | None = None) -> str:
    """A part as the methodologist is told of it. With the draft — by the frame of its README: a task of a quest is
    «Quest N», a section of its theory «List N»."""
    kind, _, key = part.partition(":")
    quest = bool(draft and draft.style and draft.style.profile == "quest")
    return {"style": "Стиль проекта", "summary": "Название и аннотация", "task": f"{'Quest' if quest else 'Задание'} {key}", "theory": "Теория",
            "theory_plan": "Подача теории",
            "topic": render.topic_name(draft, key) if draft else f"Раздел 2.{key}",
            "frame": "Введение, инструкция, заключение", "material": f"Материал {key}", "checklist": "Чек-лист"}.get(kind, part)


def field_label(draft: ContentDraft, part: str, field: str) -> str:
    """A field as the card of the part names it: a task's fields go by the words of the README's frame."""
    kind = kind_of(part)
    return render.field_words(draft).get(field, EDITABLE[kind][field]) if kind == "task" else EDITABLE[kind][field]


def label(draft: ContentDraft, part: str, field: str) -> str:
    return f"{part_label(part, draft)} · {field_label(draft, part, field)}"


def target(draft: ContentDraft, part: str) -> object | None:
    kind, _, key = part.partition(":")
    if kind == "style":
        return draft.style
    if kind == "summary":
        return draft.summary
    if kind == "task":
        return draft.task(int(key)) if key.isdigit() else None
    if kind == "theory":
        return draft.theory
    if kind == "theory_plan":
        return draft.theory_plan
    if kind == "topic":
        return draft.theory.topic(int(key)) if draft.theory and key.isdigit() else None
    if kind == "frame":
        return draft.frame
    if kind == "checklist":
        return draft.checklist
    return draft.material(key)


def current(draft: ContentDraft, part: str, field: str) -> Value | None:
    """The field's value now; None when the part does not exist (a new material is the only part created by a patch)."""
    found = target(draft, part)
    if found is None:
        return None
    if field == "excerpt":
        # The excerpt is edited by its words alone; where it came from is not the chat's to change.
        if not (draft.theory and draft.theory.excerpt):
            return None
        return draft.theory.quoted().quote if draft.theory.quoted() else ""  # type: ignore[union-attr]
    value = getattr(found, field)
    if field == "target_words":
        return str(value)
    return [link.line() for link in value] if field == "links" else value


def clean_url(raw: str) -> str:
    """«…/Python_(язык))—глава» → «…/Python_(язык)»: the first unpaired closing bracket ends the address (it closes a
    Markdown link or a bracket around the address), and a final .,; is punctuation of the sentence."""
    depth = 0
    for i, char in enumerate(raw):
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                raw = raw[:i]
                break
            depth -= 1
    return raw.rstrip(".,;")


def parse_link(line: str) -> Link | None:
    """«Название — адрес — зачем» or a Markdown link «[название](адрес) — зачем»; None when the line has no address.
    A line means one link: its first address, with the Markdown title when the Markdown link starts before it. The
    line the draft prints for a link (Link.line) reads back into the same link — tests/content/test_parts.py checks it
    on generated lines (audit 01.10.2026: «[Доклад](url.)» was stored differently from what the patch showed and
    could not be reverted)."""
    first = URL.search(line)
    if not first:
        return None
    markdown = MARKDOWN_LINK.search(line)
    address = URL.match(line, markdown.end()) if markdown and markdown.start() < first.start() else None
    if markdown and address:
        url = clean_url(address.group(0))
        close = line.find(")", markdown.end() + len(url))
        title = " ".join(URL.sub(" ", markdown.group(1)).split()).strip(TITLE_EDGES) or url
        return Link(title=title, url=url, why=line[close + 1:].strip(WHY_EDGES) if close >= 0 else "")
    match = first
    url = clean_url(match.group(0))
    title = line[:match.start()].strip(TITLE_EDGES).rstrip(" ([") or url  # «Статья (https://…)»
    # What clean_url cut off the address (a final dot, a bracket) is not the start of «why».
    return Link(title=title, url=url, why=line[match.start() + len(url):].lstrip(WHY_EDGES + ".,;)").rstrip(WHY_EDGES))


# The style by any of its names: the model answers «quest», the methodologist types «квест».
PROFILES = {name: key for key, name in STYLE_NAMES.items()} | {key: key for key in STYLE_NAMES} | {"легенда": "legend"}


# How the theory is given, and «да / нет», by any of their names: the model answers with the key, the methodologist
# types the words the card shows.
GIVEN = {name.lower(): key for key, name in PRESENTATIONS.items()} | {key: key for key in PRESENTATIONS} | {
    "глава": "chapter", "глава в readme": "chapter", "короткая глава": "chapter", "конспект": "notes", "листки": "notes",
    "в материалах": "materials", "материалы": "materials", "и то и другое": "both", "оба": "both"}
ANSWERS = {name: key for key, name in YES_NO.items()} | {key: key for key in YES_NO} | {"есть": "yes", "нужна": "yes", "не нужна": "no"}


def said_as(value: Value) -> str:
    """A name as it was typed, to look up: lower case, no quotation marks, single spaces."""
    return " ".join(("\n".join(value) if isinstance(value, list) else value).lower().replace("«", " ").replace("»", " ").split())


def normalize(field: str, value: Value) -> Value:
    if field in {"profile", "written_for", "written_in"}:
        said = " ".join(("\n".join(value) if isinstance(value, list) else value).lower().replace("«", " ").replace("»", " ").split())
        return PROFILES.get(said, said)
    if field == "presentation":
        return GIVEN.get(said_as(value), said_as(value))
    if field in {"internet", "english"}:
        return ANSWERS.get(said_as(value), said_as(value))
    if field == "excerpt":
        return " ".join(("\n".join(value) if isinstance(value, list) else value).split())
    if field == "links":
        # A link is stored as a Link and read back as Link.line(): the patch keeps that same form, so apply, revert and
        # the staleness check compare like with like. A Markdown link «[a](url)» kept as typed read back as «a — url»,
        # and its revert was refused as «изменено позже» (audit 30.09.2026). A line without an address stays as it is:
        # build_patches refuses it.
        lines = list_items(value if isinstance(value, list) else value.splitlines())
        return [link.line() if (link := parse_link(line)) else line for line in lines]
    if field == "examples":
        # Kept as the draft keeps them — «ввод → вывод» with one arrow — so that the patch shows what will be stored,
        # and apply, revert and the staleness check compare like with like. A line that is no example stays as typed:
        # the contracts refuse it in words (refused).
        lines = [line.strip() for line in (value if isinstance(value, list) else value.splitlines()) if line.strip()]
        try:
            return example_items(lines)
        except ValueError:
            return lines
    if field == "content":
        # A file stays byte-for-byte as written, except that a text file ends with one newline.
        text = "\n".join(value) if isinstance(value, list) else value
        return text.rstrip() + "\n" if text.strip() else ""
    if field in LIST_FIELDS:
        return list_items(value if isinstance(value, list) else value.splitlines())
    return "\n".join(value).strip() if isinstance(value, list) else value.strip()


OPTIONAL_TEXT = {"context", "constraints", "transition", "question", "links", "instruction", "conclusion", "disclaimer",
                 "examples", "check", "story", "questions", "notes", "excerpt"}


def readable(error: ValidationError) -> str:
    """The first limit a value broke, in words a methodologist reads."""
    first = error.errors()[0]
    limit = (first.get("ctx") or {})
    kind = first.get("type", "")
    if kind == "string_too_short":
        return f"слишком коротко — нужно не меньше {limit.get('min_length')} знаков"
    if kind == "string_too_long":
        return f"слишком длинно — не больше {limit.get('max_length')} знаков"
    if kind == "too_short":
        return f"слишком мало пунктов — нужно не меньше {limit.get('min_length')}"
    if kind == "too_long":
        return f"слишком много пунктов — не больше {limit.get('max_length')}"
    if kind == "literal_error":
        # A value that is none of the names a field knows: said by the names the methodologist reads.
        where = tuple(first.get("loc", ()))
        if where[-2:] in (("style", "profile"), ("style", "written_for")) or where[-1:] == ("written_in",):
            return "стиль — «квест» или «рабочая легенда»"
        if where[-1:] == ("presentation",):
            return "подача теории — одна из: " + ", ".join(f"«{name}»" for name in PRESENTATIONS.values())
        if where[-1:] in (("internet",), ("english",)):
            return "здесь «да» или «нет»"
    return str(first.get("msg", "недопустимое значение")).removeprefix("Value error, ")


NOT_REWRITTEN = "это дословная цитата страницы — её можно убрать или вернуть, но не переписать"


def kept_excerpt(draft: ContentDraft, value: Value) -> bool:
    """The value is the excerpt as code took it from the page: the only words it may be put back with."""
    return bool(draft.theory and draft.theory.excerpt and value == draft.theory.excerpt.quote)


def refused(draft: ContentDraft, changes: list[tuple["Patch", Value]]) -> str:
    """Why these values cannot be written, checked on a copy: "" when they can. The draft itself is never touched,
    so a value the contracts refuse cannot reach the stored file (audit 30.09.2026: a short annotation applied with
    «пойдёт» was saved and made the whole draft unreadable)."""
    if any(patch.field == "excerpt" and value and not kept_excerpt(draft, value) for patch, value in changes):
        # Whatever asks — the model, or the methodologist through «Изменить» of a change that takes the excerpt out.
        return NOT_REWRITTEN
    probe = draft.model_copy(deep=True)
    try:
        for patch, value in changes:
            # Building a new material validates its own fields (a one-letter reason is too short a purpose).
            set_value(probe, patch, value)
        ContentDraft.model_validate(probe.model_dump())
    except ValidationError as error:
        return readable(error)
    except ValueError:
        return "Объём корпуса должен быть целым числом от 0 до 30000"
    return ""


def build_patches(draft: ContentDraft, changes: list[ContentChange], typed: str = "") -> tuple[list[Patch], list[str]]:
    """Validate what the model proposes; anything it cannot address is named for the methodologist, never applied.
    typed is the methodologist's own message: links written there are theirs and may be used."""
    allowed_links = set(draft.sources) | {clean_url(m.group(0)) for m in URL.finditer(typed)}
    allowed = editable(draft)
    patches: dict[tuple[str, str], Patch] = {}
    notes: list[str] = []
    for change in changes:
        part, field = change.part.strip(), change.field.strip()
        kind = kind_of(part)
        if kind == "material":
            try:
                part = "material:" + material_path(part.partition(":")[2])
            except ValueError as error:
                notes.append(f"Материал «{part.partition(':')[2]}» не добавлен: {error}.")
                continue
        if kind not in allowed or field not in allowed[kind]:
            notes.append(f"Правку «{part} · {field}» не могу применить: такого поля нет.")
            continue
        before = current(draft, part, field)
        new_material = kind == "material" and before is None
        if before is None and not (new_material and field == "content"):
            notes.append(f"«{part_label(part, draft)}» ещё не написано — правка не применена.")
            continue
        after = normalize(field, change.value)
        if before is None:
            before = ""
        # Links are compared in the form the draft keeps: a link stored by an older version may print a line that
        # reads back a little differently, and echoing it is not an edit.
        if after == (normalize(field, before) if field == "links" else before):
            notes.append(f"«{label(draft, part, field)}» уже содержит это значение.")
            continue
        if not after and not (kind == "material" and field == "content") and field not in OPTIONAL_TEXT:
            notes.append(f"«{label(draft, part, field)}» не может быть пустым.")
            continue
        if field == "excerpt" and after and not kept_excerpt(draft, after):
            # A quotation is the source's words or nothing: the chat may take it out, never write another one.
            notes.append(f"«{label(draft, part, field)}»: {NOT_REWRITTEN}. Другую найдёт поиск: попросите в чате найти выдержку заново "
                         "или нажмите «Найти выдержку из первоисточника».")
            continue
        if field == "links":
            known = set(before) | set(normalize(field, before)) if isinstance(before, list) else set()
            links = [parse_link(line) for line in after if line not in known]
            if any(link is None for link in links):
                notes.append(f"«{part_label(part, draft)} · Ссылки»: в строке нет адреса — правка не применена.")
                continue
            invented = [link.url for link in links if link and link.url not in allowed_links]
            if invented:
                notes.append(f"Ссылку {', '.join(invented)} не нашёл поиск и её нет в вашем сообщении — не добавляю. "
                             "Пришлите адрес в чат или впишите его сами кнопкой «Изменить».")
                continue
        numbers = sorted({n for n in change.task_numbers if draft.task(n) or any(t.number == n for t in draft.handoff.tasks)})
        if new_material and not numbers:
            notes.append(f"Для нового материала «{part.partition(':')[2]}» не указано, в каком задании он нужен.")
            continue
        patch = Patch(part=part, field=field, label=label(draft, part, field),
                      before=before, after=after, reason=change.reason, task_numbers=numbers)
        # Checked when proposed, not when accepted: the model's reply and the methodologist's «пойдёт» see only values
        # the draft can take.
        why = refused(draft, [(patch, after)])
        if why:
            notes.append(f"«{patch.label}»: {why} — правка не предложена.")
            continue
        patches[(part, field)] = patch
    for part, field, before, after, why in rewritten(draft, patches):
        # A text rewritten for the style in force is no longer of the other one. Said as a change of its own, which
        # the methodologist sees and which goes back with the text when the proposal is taken back.
        patches[part, field] = Patch(part=part, field=field, label=label(draft, part, field), before=before, after=after, reason=why)
    return list(patches.values()), notes


def rewritten(draft: ContentDraft, patches: dict[tuple[str, str], Patch]) -> list[tuple[str, str, str, str, str]]:
    """The marks «written for the other style» that a proposal lifts: of the story, when the story is rewritten; of a
    task, when its situation or what is to be done is. A mark the proposal sets itself is left to it. Such a mark was
    set once and never lifted, so the advice to rewrite the task could not be followed (independent review 06.10.2026).
    The style a text is rewritten for is the one the proposal leaves the project in: «давай квест, и сюжет такой: …»
    changes the style and the story at once, and that story is written for the quest."""
    if draft.style is None:
        return []
    changed = patches.get(("style", "profile"))
    aim = str(changed.after) if changed else draft.style.profile
    found: list[tuple[str, str, str, str, str]] = []
    if ("style", "story") in patches and ("style", "written_for") not in patches and draft.style.written_for != aim:
        found.append(("style", "written_for", draft.style.written_for, aim, "Сюжет переписан под выбранный стиль"))
    for task in draft.tasks:
        part = f"task:{task.number}"
        if (task.written_in and task.written_in != aim and (part, "written_in") not in patches
                and ((part, "context") in patches or (part, "steps") in patches)):
            found.append((part, "written_in", task.written_in, aim, "Задание переписано под выбранный стиль"))
    return found


def by_its_tasks(numbers: list[int]) -> str:
    """A material named by the tasks it is for: «Материал к заданию 2», «Материал к заданиям 1, 3»."""
    if not numbers:
        return "Материал проекта"
    return f"Материал к задани{'ю' if len(numbers) == 1 else 'ям'} {', '.join(str(n) for n in numbers)}"


def set_value(draft: ContentDraft, patch: Patch, value: Value) -> None:
    kind, _, key = patch.part.partition(":")
    if kind == "material":
        material = draft.material(key)
        if material is None:
            # What the file is for stands in the README of a legend, for the learner — and the reason of a change is
            # written to the methodologist. From 0.8 the line names the file by its tasks; what the learner does with
            # it the methodologist says in the chat («Зачем участнику»).
            line = by_its_tasks(patch.task_numbers) if draft.since("0.8") else patch.reason
            draft.materials.append(Material(path=key, kind="template", purpose=line, contains=patch.reason,
                                            tasks=patch.task_numbers, content=str(value), status="draft"))
            return
        if patch.field == "content" and not value:
            # An empty content removes the material: the chat's way to say «этот файл не нужен».
            draft.materials.remove(material)
            return
        setattr(material, patch.field, int(str(value)) if patch.field == "target_words" else value)
        if patch.field in {"target_words", "contains"}:
            material.status, material.generation_key, material.generation_parts = "planned", "", []
            return
        if material.status == "planned":
            material.status = "draft"
        return
    found = target(draft, patch.part)
    assert found is not None
    if kind == "style" and patch.field == "profile":
        # Named by the methodologist from now on: the assistant keeps it when it writes the style anew.
        draft.style.chosen_by = "chat"  # type: ignore[union-attr]
    if kind == "theory_plan" and patch.field == "presentation":
        draft.theory_plan.chosen_by = "chat"  # type: ignore[union-attr]
    if patch.field == "excerpt":
        # Taken out, or put back when the change is undone: the words themselves are never written from here.
        draft.theory.excerpt.removed = not value  # type: ignore[union-attr]
        return
    if patch.field == "links":
        lines = value if isinstance(value, list) else [value]
        value = [link for link in (parse_link(line) for line in lines) if link]  # type: ignore[misc]
    setattr(found, patch.field, value)


def apply(draft: ContentDraft, message: ContentMessage, values: dict[int, Value] | None = None) -> None:
    """Apply a proposal as a whole. An approved part stays approved: the methodologist accepted this exact text."""
    values = values or {}
    if message.status != "pending":
        raise HTTPException(409, "Эти правки уже приняты или отклонены")
    for patch in message.patches:
        now = current(draft, patch.part, patch.field)
        if (now if now is not None else "") != patch.before:
            raise HTTPException(409, f"«{patch.label}» изменилось после предложения. Попросите правку заново.")
    finals = [(patch, normalize(patch.field, values[i]) if i in values else patch.after) for i, patch in enumerate(message.patches)]
    # The whole proposal is tried on a copy first: the draft changes only when every value fits the contracts.
    why = refused(draft, finals)
    if why:
        raise HTTPException(422, f"Правки не прошли проверку: {why}")
    for patch, value in finals:
        patch.after, patch.edited = value, patch.edited or value != patch.after
        set_value(draft, patch, value)
    message.status = "applied"
    # Another waiting proposal on the same text is stale now: withdrawn here, whoever applied this one.
    expire_stale(draft)


def revert(draft: ContentDraft, message: ContentMessage) -> None:
    if message.status == "rejected":
        # Back to «waiting» only if it can still be applied: a stale proposal would sit there and block the route.
        for patch in message.patches:
            now = current(draft, patch.part, patch.field)
            if (now if now is not None else "") != patch.before:
                raise HTTPException(409, f"«{patch.label}» изменилось после предложения: попросите правку заново")
        # Two waiting proposals on one field cannot both be applied: the one brought back replaces the other.
        supersede([m for m in draft.messages if m is not message], {(p.part, p.field) for p in message.patches})
        message.status = "pending"
        return
    if message.status != "applied":
        raise HTTPException(409, "Отменять нечего")
    for patch in message.patches:
        now = current(draft, patch.part, patch.field)
        if now is None and patch.before != "":
            raise HTTPException(409, f"«{patch.label}»: удалённый материал так не вернуть — попросите добавить его заново")
        if (now if now is not None else "") != patch.after:
            raise HTTPException(409, f"«{patch.label}» уже изменено позже: отменить без конфликта нельзя")
    # set_value with an empty content removes a material: reverting its creation is the same call as any other.
    restored = [(patch, patch.before) for patch in reversed(message.patches)]
    why = refused(draft, restored)
    if why:
        raise HTTPException(409, f"Отменить нельзя: {why}")
    for patch, value in restored:
        set_value(draft, patch, value)
    message.status = "pending"
    expire_stale(draft)


def unchanged(value: Value | None) -> Value:
    """A part that does not exist yet reads as empty: that is what a proposal to create it says it was."""
    return value if value is not None else ""


def supersede(messages: list[ContentMessage], fields: set[tuple[str, str]]) -> str:
    """A new proposal for the same field replaces an older one still waiting: applying the old one later would either
    fail as stale or silently undo the newer wording. Returns the labels of what was replaced."""
    labels: list[str] = []
    for message in messages:
        if message.status == "pending" and {(p.part, p.field) for p in message.patches} & fields:
            message.status = "rejected"
            message.notes.append("Заменено следующим предложением.")
            labels += [p.label for p in message.patches]
    return ", ".join(dict.fromkeys(labels))


def expire_stale(draft: ContentDraft) -> list[str]:
    """Proposals whose text changed under them (a job rewrote the part, the methodologist edited it) are withdrawn
    with a note: left waiting, they could never be applied and would block «Применить и продолжить»."""
    labels: list[str] = []
    for message in draft.messages:
        if message.status != "pending":
            continue
        stale = [p for p in message.patches if unchanged(current(draft, p.part, p.field)) != p.before]
        if stale:
            message.status = "rejected"
            message.notes.append("Текст изменился после предложения — предложение снято. Попросите правку заново, если она ещё нужна.")
            labels += [p.label for p in stale]
    return labels
