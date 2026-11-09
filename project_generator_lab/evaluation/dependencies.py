"""Conservative file-purpose and dependency binding from explicit declarations and commands."""
import posixpath
import re
from urllib.parse import unquote, urlsplit

from .inputs import FileRole, Inputs
from .models import FileRecord, Unit

PATH = re.compile(r"(?<![\w/])(?:\./)?(?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]+(?!\w|\.\w)")
URL = re.compile(r"(?:https?://|git@)[^\s<>`\"']+")
LINK = re.compile(r"\[[^\]\n]*\]\(<?([^\s)>]+)>?(?:\s+[^)]*)?\)")
WRITE = re.compile(r"созда[йт]|сохрани|запиши|реализу|разработ|\bcreate\b|\bwrite\b|\bsave\b|\bimplement\b|\bsubmit\b",re.I)
READ = re.compile(r"использ|прочита|загруз|запуст|из файла|на основе|по данным|\busing\b|\bfrom\b|\bread\b|\bload\b|\brun\b", re.I)
RUN = re.compile(r"\b(?:python[23]?|bash|sh|node)\s+(?!-m\b)(?:-[\w-]+\s+)*([\w./-]+\.[A-Za-z0-9]+)")
PRIVATE = re.compile(r"не выда[её]тся пирам|не для (?:пиров|участников)|только для проверяющего|пиру не выда|reviewer only|instructor only|not for students",re.I)
RESULT = re.compile(r"^\s*(?:\*\*)?(?:сдать|результат|формат сдачи|result|output|deliverables|submit)\s*:(?:\*\*)?\s*(.+)$",re.I|re.M)


def result_text(text: str, parsed: str) -> str:
    found = [parsed,*RESULT.findall(text)]
    output_column: int | None = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            output_column = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if any(re.search(r"что сделать|действия|\baction\b|what to do",cell,re.I) for cell in cells):
            output_column = next((i for i,cell in enumerate(cells) if re.search(r"файл|\bfile\b|\bpath\b",cell,re.I)),None)
        elif output_column is not None and len(cells)>output_column:
            found.append(cells[output_column])
    return "\n".join(dict.fromkeys(value for value in found if value.strip()))


def paths(text: str, base: str = "") -> set[str]:
    found = {m.group().removeprefix("./") for m in PATH.finditer(URL.sub("",text))}
    for link in LINK.finditer(text):
        value = unquote(link[1]).split("#",1)[0].split("?",1)[0]
        if not value or urlsplit(value).scheme or value.startswith(("/", "\\")):
            continue
        found.add(posixpath.normpath(posixpath.join(base,value)))
    return {name for name in found if ".." not in name.split("/") and not name.endswith("/")}


def bind(files: list[FileRecord], units: list[Unit], inputs: Inputs, optional_tasks: list[int]) -> None:
    """A declared generated output is not a missing issued input; a referenced tool can live in any folder."""
    records = {f.path:f for f in files}
    for name, declared in inputs.files.items():
        if declared.basis == "manifest" and declared.delivery == "issued" and declared.purpose != "learner_output":
            records.setdefault(name,FileRecord(path=name,present=False,required=True))
    outputs: set[str] = set()
    for unit in units:
        if unit.kind != "tasks":
            continue
        if unit.id.endswith(":result"):
            outputs.update(paths(unit.text))
        for line in unit.text.splitlines():
            # Bind the nearest action to each path; a sentence can name both output and input.
            for match in PATH.finditer(URL.sub("", line)):
                prefix = line[:match.start()]
                writes, reads = list(WRITE.finditer(prefix)), list(READ.finditer(prefix))
                if writes and (not reads or writes[-1].start() > reads[-1].start()):
                    outputs.add(match.group().removeprefix("./"))
    for name, file in records.items():
        role = inputs.files.setdefault(name, FileRole())
        if role.basis == "manifest":
            continue
        if file.role == "scaffold":
            role.purpose, role.delivery, role.basis = "scaffold", "issued", "declaration"
        elif name in outputs:
            role.purpose, role.basis = "learner_output", "reference"
        elif source := next((u for u in units if u.id == f"file:{name}"),None):
            if PRIVATE.search(source.text[:12000]):
                role.purpose, role.basis, role.sources = "reviewer", "declaration", [source.id]
        if inputs.package_kind == "student" and role.delivery == "unknown":
            role.delivery = "issued"
    for unit in units:
        if unit.kind != "tasks" or not unit.id.endswith(":steps"):
            continue
        number = int(unit.id.split(":")[1])
        commands = {m[1].removeprefix("./") for m in RUN.finditer(unit.text)}
        for name in sorted(paths(unit.text) | commands):
            # A relative image/link inside a declared output document can point to a sibling output.
            siblings = [output for output in outputs if posixpath.basename(output) == name]
            if name in outputs or "/" not in name and len(siblings) == 1:
                continue
            # Bare prose paths can be illustrative. Missing files block only when actually linked or executed.
            linked = any(name in paths(match.group()) for match in LINK.finditer(unit.text))
            if name not in records and name not in commands and not linked:
                continue
            file = records.setdefault(name,FileRecord(path=name,present=False))
            role = inputs.files.setdefault(name,FileRole())
            if role.basis != "manifest":
                role.purpose = "tool" if name in commands else "input"
                role.delivery,role.basis = "issued","reference"
                role.sources = sorted(set(role.sources)|{unit.id})
            if role.delivery == "issued":
                file.tasks = sorted(set(file.tasks)|{number})
                file.reference_units = sorted(set(file.reference_units)|{unit.id})
                file.required = file.required is True or number not in optional_tasks
    for name,file in records.items():
        role = inputs.files[name]
        file.role = "scaffold" if role.purpose == "scaffold" else "issued" if role.delivery == "issued" else "reviewer" if role.purpose == "reviewer" else "learner_output" if role.purpose == "learner_output" else "unknown"
    files[:] = [records[name] for name in sorted(records)]
