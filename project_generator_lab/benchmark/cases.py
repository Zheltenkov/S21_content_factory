"""The folder of a case.

    <case>/
        meta.yaml      what is known of the case: profile, level, whether it is kept back for the release run;
                       for a version of a project whose quality is known — its series and its rank in it;
                       for a project kept in a working folder — which file is its README, which its check-list,
                       what in the folder is not the project, which files are damaged on purpose
                       (readme, checklist, skip, broken)
        inputs/        what the methodologist had before the project existed: the row of the plan, the passport, the brief
        etalon/        the project that was made: its repository
        answers.yaml   the methodologist's answers to the questions the module usually asks (question: answer)

Only `etalon/` is needed to measure a project that exists. A folder that is itself a repository (a README in it) is
a case of one etalon: fifteen projects unpacked from an archive need no rearranging to be measured.

Inputs written from the finished project hold the answer and flatter the result: `inputs/` is for what there was
before. A case kept back (`split: held_out`) is not measured in an ordinary run — figures tuned on the whole set
say nothing of a project the set does not have.
"""

from pathlib import Path
from typing import Annotated, Literal, NamedTuple

import yaml
from pydantic import Field, ValidationError

from ..core.contracts import Contract
from . import repository

MAX_YAML = 200_000
MAX_CASES = 500
MAX_PATH = 500
MAX_SKIP = 100


class Meta(Contract):
    profile: str = Field(default="", max_length=200)  # what the project is about: «SQL, бассейн»
    level: str = Field(default="", max_length=100)
    # tuning — prompts may be adjusted on it; held_out — measured only before a release; empty — not decided yet.
    split: Literal["", "tuning", "held_out"] = ""
    note: str = Field(default="", max_length=2000)
    # For a project kept in a working folder, not in a repository of the school — by paths from the project's folder:
    # which file is its README (a Word document too) and its check-list, and what in the folder is not the project.
    readme: str = Field(default="", max_length=MAX_PATH)
    checklist: str = Field(default="", max_length=MAX_PATH)
    skip: list[Annotated[str, Field(min_length=1, max_length=MAX_PATH)]] = Field(default_factory=list, max_length=MAX_SKIP)
    # The files of the project damaged on purpose — a table with a broken row that a task asks to mend: they are
    # not asked to read as their format, and the report says so.
    broken: list[Annotated[str, Field(min_length=1, max_length=MAX_PATH)]] = Field(default_factory=list, max_length=MAX_SKIP)
    # Versions of one project whose order of quality is known — «D1» of 07.09, 14.09 and 28.09 — are a series; rank
    # says which is better (a greater one), 0 — not known. A measure is itself measured on them: it has to put a
    # better version above a worse one. Neither is ever shown to a model that judges the project.
    series: str = Field(default="", max_length=100)
    rank: int = Field(default=0, ge=0, le=1000)


class Case(NamedTuple):
    name: str
    folder: Path
    meta: Meta
    etalon: Path | None  # the folder of the etalon's repository
    inputs: list[str]  # the files of inputs/, by name
    answers: dict[str, str]


def mapping(path: Path) -> dict[str, object]:
    """A YAML file that is a mapping; a missing file is an empty one."""
    if not path.is_file():
        return {}
    if path.stat().st_size > MAX_YAML:
        raise ValueError(f"{path.parent.name}/{path.name}: больше {MAX_YAML // 1000} КБ")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, RecursionError) as error:
        raise ValueError(f"{path.parent.name}/{path.name}: не читается как YAML ({type(error).__name__})") from error
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.parent.name}/{path.name}: ожидается «ключ: значение»")
    return {str(key): value for key, value in data.items()}


def is_case(folder: Path) -> bool:
    return (folder / "meta.yaml").is_file() or (folder / "etalon").is_dir() or (folder / "inputs").is_dir() or repository.find(folder) is not None


def load(folder: Path) -> Case:
    """The case in the folder. What is wrong with its files is a ValueError that names the file."""
    # What is known of a case is one file. An archive wraps a repository in a folder of its name, and a meta.yaml put
    # beside the README there, or inside etalon/, is the case's too: passing it over would measure a case that was
    # kept back. Two such files, or one under another extension, are refused — which of them is right is not guessed.
    homes = dict.fromkeys(home for place in (folder, folder / "etalon") for home in (place, repository.find(place)) if home and home.is_dir())
    metas = [child for home in homes for child in sorted(home.iterdir()) if child.is_file() and child.name.lower() in ("meta.yaml", "meta.yml")]
    if any(meta.name.lower() == "meta.yml" for meta in metas):
        raise ValueError(f"{folder.name}: файл meta.yml не читается — нужен meta.yaml")
    if len(metas) > 1:
        raise ValueError(f"{folder.name}: meta.yaml лежит в нескольких местах ({', '.join(meta.relative_to(folder).as_posix() for meta in metas)}) — оставьте один")
    try:
        meta = Meta.model_validate(mapping(metas[0] if metas else folder / "meta.yaml"))
    except ValidationError as error:
        fields = ", ".join(sorted({".".join(str(part) for part in issue["loc"]) or "файл" for issue in error.errors()}))
        raise ValueError(f"{folder.name}/meta.yaml: не подходят поля: {fields}. Известные: {', '.join(Meta.model_fields)}") from error
    answers = {key: str(value) for key, value in mapping(folder / "answers.yaml").items()}
    # A case whose meta.yaml names the README is a project by that word: the folder need not hold a «README.md».
    etalon = folder / "etalon" if (folder / "etalon").is_dir() else folder if meta.readme or repository.find(folder) else None
    inputs = sorted(p.relative_to(folder / "inputs").as_posix() for p in (folder / "inputs").rglob("*") if p.is_file()) if (folder / "inputs").is_dir() else []
    return Case(folder.name, folder, meta, etalon, inputs, answers)


def find(root: Path) -> tuple[list[Case], list[str]]:
    """The cases under the root — the root itself when it is one, else its subfolders — and what was passed over:
    a folder that is not a case, a case whose files do not read."""
    if not root.is_dir():
        raise ValueError(f"каталога «{root}» нет")
    folders = [root] if is_case(root) else sorted(child for child in root.iterdir() if child.is_dir() and not child.name.startswith("."))
    cases: list[Case] = []
    passed: list[str] = []
    for folder in folders[:MAX_CASES]:
        if not is_case(folder):
            passed.append(f"«{folder.name}» — не случай: нет meta.yaml, etalon/, inputs/ и README")
            continue
        try:
            cases.append(load(folder))
        except ValueError as error:
            passed.append(str(error))
    if len(folders) > MAX_CASES:
        passed.append(f"каталогов {len(folders)}, прочитаны первые {MAX_CASES}")
    return cases, passed
