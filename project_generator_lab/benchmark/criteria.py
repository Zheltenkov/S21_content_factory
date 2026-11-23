"""The criteria the judge marks a project by, read from criteria.yaml.

The file is data somebody else wrote — the rules of good content and the rubrics of the programme passport — with a
few fields of routing added: who can confirm a sign, what it needs beyond the project itself, which call it goes in.
What is wrong with the file is said by the name of the criterion; a file that reads wrong is never half-used.
"""

import re
from pathlib import Path
from typing import Literal, NamedTuple

import yaml
from pydantic import Field, ValidationError

from ..core.contracts import Contract

FILE = Path(__file__).with_name("criteria.yaml")
IDENT = re.compile(r"^[A-Z]\d{2}$")
MARKS = ("0", "1", "2", "na", "no_data")
Need = Literal["outcomes", "hours", "approved_files", "standard_texts", "track"]
Shown = Literal["readme", "materials", "checklist", "files"]
Stage = Literal["concept", "content"]


class Source(Contract):
    title: str = Field(min_length=1)
    short: str = Field(min_length=1)  # the name of the source in the head of a column
    received: str = ""
    formula: str = ""
    scale: dict[str, str]  # what 0, 1 and 2 mean in the words of the source


class Group(Contract):
    title: str = Field(min_length=1)
    shows: list[Shown] = Field(min_length=1)


class Criterion(Contract):
    id: str
    source: str
    section: str = Field(min_length=1)
    text: str = Field(min_length=1)
    # judge — a model marks it by the text; run — it takes running the files or opening the links; expert — the
    # subject expert: a plausible text is not a confirmation.
    by: Literal["judge", "run", "expert"]
    needs: list[Need] = Field(default_factory=list)  # what the sign cannot be marked without, beyond the project
    group: str
    stages: list[Stage] = Field(min_length=1)
    scope: str = ""  # the part of the sign that is marked, when not the whole of it
    note: str = ""
    # What a mark has to be shown by. presence — the sign is that something is there: 1 and 2 take a quote of it.
    # absence — the sign is that something is not («нет заглушек»): 0 and 1 take a quote of what breaks it.
    evidence: Literal["presence", "absence"] = "presence"
    # Why the mark of the criterion is asked for and shown but is not in the total; empty — it is counted. The judge
    # is not told: the question stays the question, only the sum is another.
    uncounted: str = ""

    def quoted(self, mark: str) -> bool:
        """Whether this mark of the criterion counts only with a quote."""
        return mark in (("0", "1") if self.evidence == "absence" else ("1", "2"))


class Criteria(Contract):
    version: str = Field(min_length=1)
    date: str = ""
    status: str = ""
    sources: dict[str, Source]
    scale: dict[str, str]
    groups: dict[str, Group]
    criteria: list[Criterion] = Field(min_length=1)


class Left(NamedTuple):
    """A criterion the judge is not asked, and why."""

    criterion: Criterion
    why: str  # «run», «expert», or «needs: outcomes, hours»


def load(path: Path = FILE) -> Criteria:
    """The criteria of the file. A file that does not read, or names what is not there — ValueError."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        found = Criteria.model_validate(data)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        raise ValueError(f"{path.name}: не читается ({type(error).__name__})") from error
    except ValidationError as error:
        where = "; ".join(f"{'.'.join(str(part) for part in issue['loc'])}: {issue['type']}" for issue in error.errors()[:8])
        raise ValueError(f"{path.name}: не подходит по форме ({where})") from error
    if set(found.scale) != set(MARKS):
        raise ValueError(f"{path.name}: шкала должна состоять из {', '.join(MARKS)}")
    for name, source in found.sources.items():
        if set(source.scale) != {"0", "1", "2"}:
            raise ValueError(f"{path.name}: у источника «{name}» шкала должна состоять из 0, 1, 2")
    seen: set[str] = set()
    for criterion in found.criteria:
        if not IDENT.match(criterion.id) or criterion.id in seen:
            raise ValueError(f"{path.name}: номер критерия «{criterion.id}» повторяется или записан не как буква и две цифры")
        seen.add(criterion.id)
        if criterion.source not in found.sources:
            raise ValueError(f"{path.name}: у критерия {criterion.id} источник «{criterion.source}», которого нет в sources")
        if criterion.group not in found.groups:
            raise ValueError(f"{path.name}: у критерия {criterion.id} группа «{criterion.group}», которой нет в groups")
    return found


def asked(criteria: Criteria, stage: Stage, known: frozenset[str] = frozenset()) -> tuple[dict[str, list[Criterion]], list[Left]]:
    """The criteria of a stage divided into those the judge is asked — by group, in the order of the file — and those
    it is not asked, each with the reason. `known` — what there is besides the project (see Need)."""
    by_group: dict[str, list[Criterion]] = {}
    left: list[Left] = []
    for criterion in criteria.criteria:
        if stage not in criterion.stages:
            continue
        lacking = [need for need in criterion.needs if need not in known]
        if criterion.by != "judge":
            left.append(Left(criterion, criterion.by))
        elif lacking:
            left.append(Left(criterion, "needs: " + ", ".join(lacking)))
        else:
            by_group.setdefault(criterion.group, []).append(criterion)
    return by_group, left
