"""Read-only adapter of released repositories to the existing evaluator, without a generated draft."""
import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..benchmark import code_layer
from ..benchmark import repository as reader
from ..benchmark.word import markdown
from ..content.checks import Check
from .dependencies import bind, result_text
from .inputs import Fact, FileRole, Inputs
from .models import FileRecord, Snapshot, Unit
from .profiles import csv_units
from .snapshot import hashed

TEXT = reader.TEXT_FORMATS | {".html", ".css", ".toml", ".xml", ".ini", ".conf", ".gitignore", ".gitattributes"}
MAX_REPOSITORY_BYTES = 64_000_000


@dataclass(frozen=True)
class Identity:
    id: str
    owner_id: str


@dataclass(frozen=True)
class Imported:
    snapshot: Snapshot
    code_findings: list[Check]
    manifest: list[dict[str, object]]
    notes: list[str]


def declared_outcomes(project: reader.Project) -> list[str]:
    """Only explicitly titled outcomes are extracted; task goals are never invented as approved inputs."""
    outcomes: list[str] = []
    for section in project.readme.sections:
        if not re.search(r"образовательн.*результат|результаты обучения|чему.*науч|learning outcomes|what.*learn", section.title, re.I):
            continue
        for line in section.text.splitlines():
            if match := re.match(r"^\s*(?:[-*]|\d+[.)])\s+(.+)", line):
                outcomes.append(match[1])
    return outcomes


def read(root: Path, *, readme: str = "", checklist: str = "", skip: tuple[str, ...] = (), inputs: Inputs | None = None) -> Imported:
    """Freeze the whole corpus, explicit input knowledge, and delivery uncertainty independently of layout."""
    if not (readme or checklist or skip):
        root = reader.find(root) or root
    project = reader.read(root, readme, checklist, skip)
    inputs = inputs.model_copy(deep=True) if inputs else Inputs(origin="repository")
    if project.readme.tasks:
        measured = code_layer.measure(project)
        tasks = code_layer.as_tasks(project, measured.paired)
        findings = measured.checks
    else:
        # A monolithic specification is one compound practical result, not zero tasks or invented exercises.
        section = next((s for s in project.readme.sections if re.search(r"техническое задание|technical specification|requirements|specification",s.title,re.I)), None)
        statement = section.text if section else project.text
        title = section.title if section else project.readme.title
        compound = reader.Task(title,1,"",title,False,statement,statement,False,[],[],[],[],{},None)
        project = project._replace(readme=project.readme._replace(tasks=[compound]))
        questions = project.checklist.questions if project.checklist else []
        paired = reader.Paired("whole_project",{0:questions},[],[])
        tasks = code_layer.as_tasks(project,paired)
        findings = []  # Numbering-specific checks do not apply to a single compound specification.
        inputs.task_mode,inputs.optional_known = "whole_project",False
        inputs.notes.append("Документ разобран как монолитное ТЗ; не утверждается наличие отдельной нумерации упражнений.")
    units: list[Unit] = []
    files: list[FileRecord] = []
    manifest: list[dict[str, object]] = []
    notes = [*project.notes, *inputs.notes]
    limitations: list[str] = []
    ignored = [root / name for name in skip]
    actual = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.relative_to(root).as_posix())
    if sum(p.stat().st_size for p in actual) > MAX_REPOSITORY_BYTES:
        raise ValueError("Комплект превышает безопасный лимит чтения 64 МБ")
    for path in actual:
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or ".git" in path.relative_to(root).parts:
            manifest.append({"path": name, "status": "excluded_link_or_git"})
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if any(path == excluded or excluded in path.parents for excluded in ignored):
            manifest.append({"path": name, "sha256": digest, "bytes": len(data), "status": "excluded_by_case_manifest"})
            continue
        status, text = "inventory_only", ""
        try:
            if name == project.readme_file:
                text, status = project.text, "readme_extracted" if path.suffix.lower() == ".docx" else "text_complete"
            elif path.suffix.lower() == ".docx":
                text, status = markdown(path), "docx_extracted"
            elif path.suffix.lower() == ".pdf":
                import io

                from pypdf import PdfReader

                pdf = PdfReader(io.BytesIO(data))
                if len(pdf.pages)>200:
                    raise ValueError("PDF превышает лимит200 страниц")
                text = "\n\n".join(f"[Страница {n}]\n{page.extract_text() or ''}" for n,page in enumerate(pdf.pages,1))
                status = "pdf_text_extracted"
            elif path.name.startswith(".env") or path.suffix.lower() in {".key", ".pem"}:
                status = "excluded_sensitive_config"
            elif path.suffix.lower() in TEXT or name in {"LICENSE", "CHANGELOG", "TAGS_FILES.txt"}:
                text, status = data.decode("utf-8-sig"), "text_complete"
            elif b"\0" not in data[:8192] and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".db", ".sqlite"}:
                try:
                    text,status=data.decode("utf-8-sig"),"text_complete"
                except UnicodeError:
                    pass
        except (UnicodeError, ValueError) as error:
            status = "unreadable_text"
            limitations.append(f"Файл {name} не прочитан: {type(error).__name__}.")
        manifest.append({"path": name, "sha256": digest, "bytes": len(data), "status": status})
        role = "scaffold" if path.name == ".gitkeep" else "unknown"
        files.append(FileRecord(path=name, present=True, sha256=digest, role=role, empty=not data))
        if status not in {"inventory_only", "excluded_sensitive_config"}:
            kind = "readme" if name == project.readme_file else "checklist" if name == project.checklist_file else "materials"
            units.append(Unit(id=f"file:{name}", title=name, text=text, kind=kind))
            if path.suffix.lower() in {".csv", ".tsv"}:
                try:
                    units.extend(csv_units(name,text,digest))
                    manifest[-1]["status"]="csv_complete_with_profile"
                except (ValueError, OverflowError, csv.Error) as error:
                    notes.append(f"Профиль {name} не построен: {type(error).__name__}; исходный текст сохранён.")
        if name in {project.readme_file, project.checklist_file}:
            inputs.files.setdefault(name,FileRole(purpose="instructions",delivery="issued",basis="declaration",sources=[f"file:{name}"]))
    # Stable one-based IDs are aliases, never a rewrite of the numbering or instructions in the source.
    for number, (task, source) in enumerate(zip(tasks, project.readme.tasks, strict=True), 1):
        for field, text in (("title", source.title), ("steps", source.raw), ("result", result_text(source.raw,task.result)),
                            ("expected", "\n".join(task.expected)), ("peer_checks", "\n".join(task.peer_checks))):
            units.append(Unit(id=f"task:{number}:{field}", title=f"{source.label} · {field}", text=text,
                              kind="tasks", part=f"task:{number}"))
    outcomes = declared_outcomes(project)
    if inputs.outcomes.available:
        try:
            values = json.loads(inputs.outcomes.value)
            if not isinstance(values,list) or not values or not all(isinstance(value,str) and value.strip() for value in values):
                raise ValueError("ОР должны быть непустым массивом строк")
            outcomes = values
        except ValueError:
            raise ValueError("В outcomes.value ожидается JSON-массив строк") from None
    elif outcomes:
        inputs.outcomes=Fact(state="declared",value=json.dumps(outcomes,ensure_ascii=False),sources=[f"file:{project.readme_file}"])
    if not outcomes:
        notes.append("Утверждённые ОР/УП не приложены; отдельный список ОР в README не извлечён. Покрытие утверждённых ОР подтвердить нельзя.")
    else:
        notes.append("ОР извлечены из явно озаглавленного раздела README; независимого утверждённого УП нет.")
    units.append(Unit(id="input:provided",title="Заявленные параметры чтения",kind="inputs",text=inputs.model_dump_json()))
    introduction = project.readme.annotation+"\n"+"\n".join(s.text for s in project.readme.sections if s.role in {"title", "introduction"})
    fmt = "group" if re.search(r"командный проект|командная работа|team project|group project", introduction, re.I) else "individual" if re.search(r"индивидуальный проект|individual project", introduction, re.I) else "unknown"
    if inputs.format.available:
        fmt = inputs.format.value
    elif fmt != "unknown":
        inputs.format=Fact(state="declared",value=fmt,sources=[f"file:{project.readme_file}"])
    if inputs.package_kind == "student" and not inputs.distribution.available:
        inputs.distribution=Fact(state="declared",value=inputs.package_kind,sources=["input:provided"])
    # Extract only explicitly named input sections, not a guessed learner profile or project outcome.
    for field, pattern in (("audience",r"аудитори|для кого|audience|target audience"),
                           ("prerequisites",r"предварительн|входной уровень|prerequisites|before you start"),
                           ("workload",r"трудо[её]мкост|время на проект|workload|time budget")):
        if not getattr(inputs,field).available:
            section=next((s for s in project.readme.sections if re.search(pattern,s.title,re.I)),None)
            if section and section.text.strip():
                setattr(inputs,field,Fact(state="declared",value=section.text,sources=[f"file:{project.readme_file}"]))
    inputs.story_known,inputs.theory_known=False,False
    optional=[n for n,t in enumerate(project.readme.tasks,1) if t.bonus]
    bind(files,units,inputs,optional)
    units.append(Unit(id="input:scope",title="Границы проверки",kind="inputs",text="Не подменяй отсутствующие исходные требования догадкой. Наличие файла в авторской папке не доказывает его выдачи слушателю. Роли и основания указаны в input:contract. Все записи CSV прочитаны кодом; модель видит полный профиль и явную выборку, а исходные строки доступны через requests. Изображения учитываются по описи, без оценки визуального содержания."))
    units.append(inputs.unit())
    units.append(Unit(id="source:manifest",title="Реестр чтения файлов",kind="inputs",text=json.dumps(manifest,ensure_ascii=False)))
    for file in files:
        units.append(Unit(id=f"inventory:{file.path}",title=f"Опись · {file.path}",kind="inventory",text=file.model_dump_json()))
    units.append(Unit(id="inventory",title="Полная опись",kind="inventory",text=json.dumps([f.model_dump() for f in files],ensure_ascii=False)))
    theory = any(s.role == "theory" or re.search(r"^sql|справ|шпаргал", s.title, re.I) for s in project.readme.sections) or any(re.search(r"theory|теори|sql_start|model\.md", f.path, re.I) for f in files)
    snapshot = Snapshot(digest="", content_digest=hashed(json.dumps(manifest, ensure_ascii=False)),
        title=project.readme.title, units=units, files=files, outcomes=outcomes,
        task_numbers=list(range(1, len(tasks) + 1)), optional_tasks=optional,
        format=fmt, has_story=bool(re.search(r"^>", project.text, re.M)), has_theory=theory,
        curriculum_available=inputs.curriculum.available, approved_files=json.loads(inputs.approved_files.value) if inputs.approved_files.available else None, limitations=limitations)
    snapshot.digest = hashed(snapshot.model_dump_json(exclude={"digest"}))
    notes.append("Изображения/бинарные файлы учтены по наличию и хешу; визуальный смысл и выполнение кода не проверялись.")
    return Imported(snapshot, findings, manifest, notes)
