"""Bounded views of the complete frozen source corpus and auditable on-demand reads."""
import csv
import io
import json
from dataclasses import dataclass, field

from ..content.checks import Check
from .models import EvidenceRequest, Snapshot, Unit

INLINE_LIMIT = 24000
MAX_FRAGMENT = 24000
MAX_EVIDENCE_ROUNDS = 2


class ContextLimit(ValueError):
    pass


@dataclass
class Packed:
    payload: dict[str, object]
    visible: dict[str, list[str]]
    fragments: list[dict[str, object]] = field(default_factory=list)

    def text(self, criteria: list[dict[str, object]], limit: int) -> str:
        value = json.dumps({**self.payload, "evidence_fragments":self.fragments, "supplied_criteria":criteria}, ensure_ascii=False)
        if len(value) > limit:
            raise ContextLimit("Обязательные источники и запрошенные фрагменты превышают лимит контекста")
        return value


def pack(snapshot: Snapshot, numbers: list[int], findings: list[Check], limit: int) -> Packed:
    """Keep the whole project map and instructions; bulk data use complete code profiles plus raw retrieval."""
    selected: list[Unit] = []
    optional: list[Unit] = []
    coverage: list[dict[str, object]] = []
    profiled = {u.part for u in snapshot.units if u.kind == "dataset_profile"}
    for unit in snapshot.units:
        bulk = unit.id in profiled or (len(unit.text) > INLINE_LIMIT and unit.kind not in {"inputs", "readme", "checklist", "tasks", "inventory"})
        if bulk:
            coverage.append({"unit":unit.id,"title":unit.title,"characters":len(unit.text),
                             "view":"complete_code_profile_and_samples" if unit.id in profiled else "available_by_request"})
            continue
        if unit.kind in {"inputs", "readme", "checklist", "tasks", "inventory", "dataset_profile", "dataset_sample"}:
            selected.append(unit)
        else:
            optional.append(unit)
    base: dict[str, object] = {"project_snapshot":snapshot.model_dump(exclude={"units"}),
        "units":[u.model_dump() for u in selected], "source_coverage":coverage,
        "code_findings":[c.model_dump() for c in findings]}
    # Reserve space for rubrics and requested evidence; never silently clip mandatory instructions.
    if len(json.dumps(base, ensure_ascii=False)) > limit - 24000:
        raise ContextLimit("Обязательные входы, README и чек-лист превышают лимит модельного контекста")
    for unit in optional:
        candidate = [*selected, unit]
        if len(json.dumps({**base,"units":[u.model_dump() for u in candidate]},ensure_ascii=False)) <= limit - 40000:
            selected.append(unit)
        else:
            coverage.append({"unit":unit.id,"title":unit.title,"characters":len(unit.text),"view":"available_by_request"})
    base["units"] = [u.model_dump() for u in selected]
    return Packed(base, {u.id:[u.text] for u in selected})


def retrieve(request: EvidenceRequest, snapshot: Snapshot) -> dict[str, object]:
    """Return a contiguous exact fragment from the snapshot, never read mutable paths or execute queries."""
    unit = next((u for u in snapshot.units if u.id == request.unit), None)
    if unit is None:
        return {"unit":request.unit,"error":"Источник отсутствует в замороженном снимке"}
    if request.end < request.start or request.end - request.start > 100:
        return {"unit":unit.id,"error":"Запрашивай диапазон не больше101 строки/записи"}
    lines = unit.text.splitlines(keepends=True)
    start, end = request.start, request.end
    if request.mode == "search":
        if not request.query:
            return {"unit":unit.id,"error":"Для поиска нужна непустая буквальная строка"}
        indices = [i for i, line in enumerate(lines) if request.query.casefold() in line.casefold()]
        if not indices:
            return {"unit":unit.id,"query":request.query,"matches":0,"text":""}
        start, end = max(1,indices[0]-2), min(len(lines),indices[0]+5)
    elif request.mode == "csv_records":
        if not unit.id.lower().endswith((".csv", ".tsv")):
            return {"unit":unit.id,"error":"Источник не является CSV/TSV"}
        stream = io.StringIO(unit.text, newline="")
        try:
            dialect = csv.Sniffer().sniff(unit.text[:8192],delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(stream,dialect)
        found_start = found_end = None
        try:
            next(reader, None)
            previous = reader.line_num
            for number, _ in enumerate(reader,1):
                if number == request.start:
                    found_start = previous+1
                if number == request.end:
                    found_end = reader.line_num
                    break
                previous = reader.line_num
        except csv.Error:
            return {"unit":unit.id,"error":"Записи CSV не удалось разобрать; запроси физические строки"}
        if found_start is None:
            return {"unit":unit.id,"error":"Запрошенные записи отсутствуют"}
        start, end = found_start, found_end or reader.line_num
    text = "".join(lines[start-1:end])
    if len(text) > MAX_FRAGMENT:
        return {"unit":unit.id,"error":"Фрагмент слишком велик; сузь диапазон или поиск"}
    return {"unit":unit.id,"source_title":unit.title,"start_line":start,"end_line":min(end,len(lines)),"text":text}
