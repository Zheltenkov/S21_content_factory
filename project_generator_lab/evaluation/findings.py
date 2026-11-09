"""Source-bound groups of remarks. Group counts never act as numeric penalties."""
import hashlib
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field

from ..core.contracts import Contract
from .models import Evaluation, Evidence, FileRecord, Verdict


class Finding(Contract):
    id: str
    title: str
    level: Literal["critical", "defect", "partial"]
    basis: Literal["missing_dependency", "criterion"]
    criteria: list[int]
    reason: str
    suggestion: str
    evidence: list[Evidence] = Field(default_factory=list)


def mentions(path: str, text: str) -> bool:
    # Match whole repository paths; assets/a.csv is not the same input as assets/a.csv.backup.
    return bool(re.search(r"(?<![\w/.-])(?:\./)?" + re.escape(path) + r"(?![\w/-]|\.\w)", text))


def tied_to_missing(verdict: Verdict, missing: FileRecord, report: Evaluation) -> bool:
    """Merge only an explicit absent-file fact and its declared reference, never shared file names alone."""
    units = {u.id: u.text for u in report.snapshot.units}
    if not verdict.evidence or any(e.unit not in units or e.quote not in units[e.unit] for e in verdict.evidence):
        return False
    absence = f"inventory:{missing.path}"
    explicit_absence = any(e.unit == absence for e in verdict.evidence)
    reference = any(e.unit in missing.reference_units and mentions(missing.path,e.quote) for e in verdict.evidence)
    if verdict.method == "code":
        # A code verdict can contain several independent path failures. Do not lose an unmatched cause.
        absent = [f for f in report.snapshot.files if not f.present and f.role == "issued"]
        all_references_bound = all(e.unit.startswith("inventory:") or
            any(e.unit in f.reference_units and mentions(f.path,e.quote) for f in absent) for e in verdict.evidence)
        return (explicit_absence or reference) and all_references_bound
    if not (explicit_absence and reference):
        return False
    allowed_inventory = {f"inventory:{f.path}" for f in report.snapshot.files
                         if f.path == missing.path or f.present and PurePosixPath(f.path).name == PurePosixPath(missing.path).name}
    return all(e.unit in allowed_inventory or e.unit in missing.reference_units and mentions(missing.path,e.quote)
               for e in verdict.evidence)


def grouped(report: Evaluation, verdicts: list[Verdict]) -> list[Finding]:
    """One proven missing dependency can affect many requirements; other causes stay separate."""
    problems = [v for v in verdicts if v.criterion <= 40 and v.state == "assessed"
                and v.mark in {-1,0} and v.evidence_valid]
    missing = sorted((f for f in report.snapshot.files if not f.present and f.role == "issued"),key=lambda f:f.path)
    buckets: dict[str,list[Verdict]] = {}
    records = {f.path:f for f in missing}
    for verdict in problems:
        matched = [f.path for f in missing if tied_to_missing(verdict,f,report)]
        # Multiple missing inputs remain separate causes; no inferred common parent-folder cause.
        for key in matched or [f"criterion:{verdict.criterion}"]:
            buckets.setdefault(key,[]).append(verdict)
    found: list[Finding] = []
    for key,members in buckets.items():
        critical = any(v.mark == -1 and v.confirmed_blocker for v in members)
        level: Literal["critical","defect","partial"] = "critical" if critical else "defect" if any(v.mark == -1 for v in members) else "partial"
        criteria = sorted({v.criterion for v in members})
        if key in records:
            inventory_unit = next((u for u in report.snapshot.units if u.id == f"inventory:{key}"),None)
            evidence = [Evidence(unit=inventory_unit.id,quote=inventory_unit.text[:1800])] if inventory_unit else []
            found.append(Finding(id="missing:"+hashlib.sha256(key.encode()).hexdigest()[:16],
                title=f"Не выдан необходимый файл: {key}",level=level,basis="missing_dependency",criteria=criteria,
                reason="Отсутствие файла подтверждено описью. Связанные критерии описывают последствия этой зависимости, а не отдельные штрафы.",
                suggestion="Добавьте требуемый файл или исправьте ссылку и команду в инструкции. После правки перепроверьте связанные критерии.",evidence=evidence))
        else:
            member = members[0]
            criterion = next(c for c in report.profile.criteria if c.number == member.criterion)
            found.append(Finding(id=key,title=criterion.title,level=level,basis="criterion",criteria=criteria,
                                 reason=member.reason,suggestion=member.suggestion,evidence=member.evidence))
    return sorted(found,key=lambda f:({"critical":0,"defect":1,"partial":2}[f.level],f.id))
