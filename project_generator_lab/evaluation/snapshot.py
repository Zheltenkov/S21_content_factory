"""Freeze exactly the content, issued files and allowed inputs that a check saw."""
import csv
import hashlib
import io
import json
import zipfile

from ..content import render
from ..content.models import ContentDraft
from .dependencies import bind
from .inputs import Fact, FileRole, Inputs
from .models import Context, FileRecord, Snapshot, Unit
from .profiles import csv_units
from .resources import inventory

FIELD_TITLES = {"title": "название", "goal": "цель", "context": "ситуация", "steps": "действия участника",
                "expected": "ожидаемый результат", "result": "формат сдачи", "peer_checks": "чек-лист P2P",
                "check": "способ проверки", "constraints": "ограничения", "examples": "примеры"}


def hashed(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def build(draft: ContentDraft, context: Context, approved_files: list[str] | None = None) -> Snapshot:
    # Work on a copy: rendering fills legacy checklist defaults, but evaluation must not edit the draft.
    draft = draft.model_copy(deep=True)
    handoff = draft.handoff.model_dump(exclude={"generation", "collaboration_mode"})
    units = [Unit(id="handoff", title="Согласованный замысел и входы", kind="inputs", text=json.dumps(handoff, ensure_ascii=False)),
             *context.units]
    notes = list(context.limitations)
    if not context.full_sources:
        for i, source in enumerate(draft.handoff.sources):
            units.append(Unit(id=f"source:{i}", title=source.name, kind="inputs", text=source.text))
            if source.cropped:
                notes.append(f"Источник «{source.name}» передан сокращённо: {len(source.text)} из {source.total_chars} знаков.")
    for task in draft.tasks:
        for field in ("title", "goal", "context", "steps", "expected", "result", "peer_checks", "check", "constraints", "examples", "needs"):
            value = getattr(task, field, "")
            text = "\n".join(value) if isinstance(value, list) else str(value)
            units.append(Unit(id=f"task:{task.number}:{field}", title=f"Задание {task.number} · {FIELD_TITLES.get(field, 'необходимые материалы')}",
                              kind="tasks", part=f"task:{task.number}", text=text))
    files = []
    try:
        with zipfile.ZipFile(io.BytesIO(render.bundle(draft))) as archive:
            for name in archive.namelist():
                text = archive.read(name).decode("utf-8")
                material = draft.material(name.removeprefix("materials/")) if name.startswith("materials/") else None
                kind = "checklist" if name == "check-list.yml" else "readme" if name == "README.md" else "materials"
                part = f"material:{material.path}" if material else "full"
                units.append(Unit(id=f"file:{name}", title=name, text=text, kind=kind, part=part))
                if name.lower().endswith((".csv", ".tsv")):
                    try:
                        units.extend(csv_units(name, text, hashed(text)))
                    except (ValueError, OverflowError, csv.Error):
                        units.append(Unit(id=f"data:{name}:status", title=f"Чтение · {name}", kind="inputs",
                                          text="Сводка CSV не построена. Полный исходный текст сохранён; проверяй его непосредственно, не предполагай корректность данных."))
                files.append(FileRecord(path=name, present=True, empty=not text.strip(), sha256=hashed(text),
                                        role="scaffold" if name.endswith(".gitkeep") else "issued"))
    except (ValueError, render.RenderError, KeyError) as error:
        notes.append(f"Не удалось собрать выдаваемый комплект: {type(error).__name__}.")
    inventory(draft, files, units, approved_files)
    # Store genuine handoff facts separately from generated content; an empty input is never inferred.
    inputs = Inputs(origin="generator", package_kind="student")
    for field, value in (("outcomes", json.dumps(draft.handoff.outcomes, ensure_ascii=False) if draft.handoff.outcomes else ""),
                         ("audience", draft.handoff.audience), ("prerequisites", draft.handoff.start_level),
                         ("workload", str(draft.handoff.hours) if draft.handoff.hours else ""), ("format", draft.handoff.format)):
        if value:
            setattr(inputs, field, Fact(state="confirmed", value=value, sources=["handoff"]))
    if context.curriculum_available:
        inputs.curriculum = Fact(state="confirmed", value="УП передан в контексте", sources=[u.id for u in context.units] or ["handoff"])
    if files:
        inputs.distribution = Fact(state="confirmed", value="Фактически собранный комплект участника", sources=["inventory"])
    if approved_files is not None:
        inputs.approved_files = Fact(state="confirmed", value=json.dumps(approved_files, ensure_ascii=False), sources=["inventory"])
    inputs.files = {f.path: FileRole(purpose="scaffold" if f.role == "scaffold" else "instructions" if f.path in {"README.md", "check-list.yml"} else "input",
                                   delivery="issued", basis="manifest", sources=[f"inventory:{f.path}"]) for f in files}
    bind(files, units, inputs, [t.number for t in draft.handoff.tasks if t.optional])
    units.append(inputs.unit())
    expected_tasks = {t.number for t in draft.handoff.tasks}
    if not draft.summary or not draft.frame or not draft.theory or {t.number for t in draft.tasks} != expected_tasks:
        notes.append("Комплект ещё не полностью подготовлен: отсутствуют предусмотренные части контента.")
    if len({u.id for u in units}) != len(units):
        raise ValueError("Повторяющийся идентификатор источника в снимке проверки")
    for file in files:
        units.append(Unit(id=f"inventory:{file.path}", title=f"Опись · {file.path}", kind="inventory", text=file.model_dump_json()))
    units.append(Unit(id="inventory", title="Полная опись выдаваемых файлов", kind="inventory",
                      text=json.dumps([f.model_dump() for f in files], ensure_ascii=False)))
    snapshot = Snapshot(digest="", content_digest=draft.digest(), title=draft.handoff.title, units=units, files=files,
                approved_files=approved_files, outcomes=draft.handoff.outcomes, task_numbers=sorted(expected_tasks),
                optional_tasks=[t.number for t in draft.handoff.tasks if t.optional], format=draft.handoff.format,
                has_story=bool(draft.handoff.storytelling or draft.style and draft.style.story),
                has_theory=bool(draft.theory and draft.theory.topics), curriculum_available=context.curriculum_available,
                limitations=list(dict.fromkeys(notes)))
    snapshot.digest = hashed(snapshot.model_dump_json(exclude={"digest"}))
    return snapshot
