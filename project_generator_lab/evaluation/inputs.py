"""Source-bound assessment inputs and delivery roles; unknown facts are never negative grades."""
from typing import Literal

from pydantic import Field

from ..core.contracts import Contract
from .models import Snapshot, Unit

INPUT_UNIT = "input:contract"


class Fact(Contract):
    state: Literal["missing", "declared", "confirmed", "conflicting"] = "missing"
    value: str = ""
    sources: list[str] = Field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.state in {"declared", "confirmed"} and bool(self.value) and bool(self.sources)


class FileRole(Contract):
    purpose: Literal["instructions", "input", "starter", "tool", "learner_output", "reference", "reviewer", "scaffold", "unknown"] = "unknown"
    delivery: Literal["issued", "not_issued", "unknown"] = "unknown"
    basis: Literal["manifest", "reference", "declaration", "location_hint", "unknown"] = "unknown"
    sources: list[str] = Field(default_factory=list)


class Inputs(Contract):
    version: str = "1"
    origin: Literal["generator", "repository"]
    package_kind: Literal["student", "author", "unknown"] = "unknown"
    outcomes: Fact = Field(default_factory=Fact)
    audience: Fact = Field(default_factory=Fact)
    prerequisites: Fact = Field(default_factory=Fact)
    workload: Fact = Field(default_factory=Fact)
    curriculum: Fact = Field(default_factory=Fact)
    approved_files: Fact = Field(default_factory=Fact)
    distribution: Fact = Field(default_factory=Fact)
    format: Fact = Field(default_factory=Fact)
    task_mode: Literal["numbered", "whole_project"] = "numbered"
    optional_known: bool = True
    story_known: bool = True
    theory_known: bool = True
    files: dict[str, FileRole] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def unit(self) -> Unit:
        return Unit(id=INPUT_UNIT, title="Основания проверки и роли файлов", kind="inputs", text=self.model_dump_json())


def contract(snapshot: Snapshot) -> Inputs | None:
    unit = next((u for u in snapshot.units if u.id == INPUT_UNIT), None)
    if unit is None:
        return None
    result = Inputs.model_validate_json(unit.text)
    sources = {u.id for u in snapshot.units}
    for field in ("outcomes", "audience", "prerequisites", "workload", "curriculum", "approved_files", "distribution", "format"):
        fact = getattr(result, field)
        if any(source not in sources for source in fact.sources):
            raise ValueError(f"Неизвестный источник входного параметра: {field}")
    for path, role in result.files.items():
        if any(source not in sources for source in role.sources):
            raise ValueError(f"Неизвестный источник роли файла: {path}")
    return result


def missing_for(number: int, inputs: Inputs | None) -> list[str]:
    if inputs is None:
        return []  # Historical snapshots retain their recorded interpretation until a fresh check.
    fields: dict[int, tuple[str, ...]] = {1:("outcomes",), 2:("outcomes",), 3:("outcomes",),
        5:("audience",), 6:("audience", "workload"), 7:("audience", "workload"),
        8:("curriculum",), 35:("distribution",), 40:("approved_files",)}
    titles = {"outcomes":"исходные образовательные результаты", "audience":"целевая аудитория", "workload":"плановая трудоёмкость",
              "curriculum":"учебный план", "distribution":"подтверждённый состав выдачи участнику", "approved_files":"утверждённый перечень файлов"}
    missing = [titles[field] for field in fields.get(number, ()) if not getattr(inputs, field).available]
    if number in {53,54} and not inputs.format.available:
        missing.append("индивидуальный или групповой формат")
    if number in {3,4,19} and not inputs.optional_known:
        missing.append("явная обязательность и бонусная часть")
    return missing
