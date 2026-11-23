"""Resource dependencies are derived independently of the legacy editorial path checker."""
import posixpath
import re
from urllib.parse import unquote, urlsplit

from ..content.models import ContentDraft
from .models import Evidence, FileRecord, Snapshot, Unit

MATERIAL_PATH = re.compile(r"(?<![\w/])(?:\./)?materials/[A-Za-z0-9_.\-/]+\.[A-Za-z0-9]+")
URL = re.compile(r"(?:https?://|git@)[^\s<>`\"']+")
LINK = re.compile(r"\[[^\]\n]*\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)")
FENCE = re.compile(r"```.*?```|~~~.*?~~~", re.S)


def material_paths(text: str) -> set[str]:
    # URLs and sample code are not declarations of files in this repository.
    return {posixpath.normpath(m.group().removeprefix("./"))
            for m in MATERIAL_PATH.finditer(URL.sub("", FENCE.sub("", text)))}


def inventory(draft: ContentDraft, files: list[FileRecord], units: list[Unit], approved: list[str] | None) -> None:
    """Join actual ZIP entries, planned materials and task references by canonical path.

    materials/ is the existing issued-input contract; src/ is learner output. Explicit
    submission paths take precedence over textual mentions, unless the material is
    explicitly planned for issue. Ambiguous references remain unknown, not blockers.
    """
    records = {f.path: f for f in files}
    optional = {t.number for t in draft.handoff.tasks if t.optional}
    planned = {f"materials/{m.path}": m for m in draft.materials}
    outputs = {t.number: material_paths(t.result) for t in draft.tasks}
    references: dict[str, set[int]] = {}
    witnesses: dict[str, set[str]] = {}
    for unit in units:
        if unit.kind != "tasks" or unit.id.rsplit(":", 1)[-1] not in {"context", "steps", "needs", "constraints"}:
            continue
        number = int(unit.id.split(":")[1])
        for path in material_paths(unit.text):
            if path in outputs.get(number, set()) and path not in planned:
                continue
            references.setdefault(path, set()).add(number)
            witnesses.setdefault(path, set()).add(unit.id)
    # Persisted units and their digest must be identical across Python hash seeds and restarts.
    for path in sorted(set(records) | set(planned) | set(references) | set(approved or [])):
        if path not in records:
            records[path] = FileRecord(path=path, present=False)
            files.append(records[path])
        file = records[path]
        task_numbers = references.get(path, set()) | (set(planned[path].tasks) if path in planned else set())
        file.tasks = sorted(task_numbers)
        file.reference_units = sorted(witnesses.get(path, set()))
        file.required = (bool(task_numbers - optional) if task_numbers else
                         True if path in (approved or []) else
                         False if file.role == "scaffold" else None)


def path_issues(snapshot: Snapshot) -> list[tuple[str, Evidence, bool]]:
    """Report only demonstrable local path defects; external commands need execution."""
    issues: list[tuple[str, Evidence, bool]] = []
    units = {u.id: u for u in snapshot.units}
    for file in snapshot.files:
        if file.present or file.role != "issued":
            continue
        for name in file.reference_units:
            unit = units[name]
            issues.append((f"Путь {file.path} не ведёт к выданному материалу.",
                           Evidence(unit=name, quote=unit.text[:1800]), file.required is True))
    # Absolute workstation links are broken for peers. Repository clone URLs are valid syntax.
    for unit in snapshot.units:
        if unit.kind != "tasks":
            continue
        for match in LINK.finditer(FENCE.sub("", unit.text)):
            target = unquote(match.group(1))
            parsed = urlsplit(target)
            if target.startswith(("/", "\\")) or parsed.scheme.lower() == "file" or re.match(r"^[A-Za-z]:", target):
                issues.append((f"Ссылка ведёт на локальный путь компьютера: {target}.",
                               Evidence(unit=unit.id, quote=match.group()[:1800]),
                               int(unit.id.split(":")[1]) not in snapshot.optional_tasks))
    return issues
