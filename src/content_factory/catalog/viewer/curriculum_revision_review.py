"""Correct grounded curriculum decisions and replay them over an archived intake."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.pipeline.curriculum.revision_proposals import Proposal, _brief_passages, _ground_proposal


class StaleRevisionError(ValueError):
    """A different version of the brief or plan now backs the review form."""


def _digest(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def apply_reviewed_revisions(spec: dict[str, Any], history: list[dict[str, Any]]) -> None:
    """Replay confirmed replacements in order while retaining unaffected model decisions."""
    if not history:
        return
    decisions = list(spec.get("revision_decisions") or [])
    # Do not apply an approval issued against different source text or changed historical facts.
    source_hash = _digest([spec.get("source_curriculum"), spec.get("requirements_text")])
    for event in history:
        if event["source_hash"] != source_hash:
            continue
        decision = event["decision"]
        affected = set(decision["source_ids"])
        decisions = [d for d in decisions if not affected.intersection(d.get("source_ids", []))]
        decisions.append(decision)
    if not any(event["source_hash"] == source_hash for event in history):
        return
    passages = _brief_passages(spec["requirements_text"])
    requirements = [p for p in passages if p["listed"]]
    counts = Counter(s for d in decisions for s in d.get("source_ids", []))
    pending_sources = [s["source_id"] for s in spec["source_curriculum"] if counts[s["source_id"]] != 1]
    assigned = {ref for d in decisions if d.get("disposition") not in {"omit", "hold"} for ref in d.get("requirement_refs", [])}
    missing = [p["ref"] for p in requirements if p["ref"] not in assigned]
    generation = dict(spec.get("revision_generation") or {})
    spec["revision_decisions"] = decisions
    spec["revision_generation"] = {
        **generation, "requirements": requirements, "unassigned_requirements": missing,
        "pending_source_ids": pending_sources,
        "state": "partial" if pending_sources or missing else "complete",
        "provider_issues": generation.get("provider_issues", generation.get("issues", [])),
        "issues": [f"{source}: требуется решение по прежней работе." for source in pending_sources],
    }


@dataclass
class RevisionEditor:
    brief_id: int
    plan_id: int
    spec: dict[str, Any]
    metadata: dict[str, Any]
    token: str

    @property
    def passages(self) -> list[dict[str, Any]]:
        return _brief_passages(self.spec["requirements_text"])


def load_revision_editor(conn: CatalogConnection, plan_id: int, *, for_update: bool = False) -> RevisionEditor:
    from .intake_dag import load_brief_spec_for_plan

    # POST holds both identities until the replacement plan and its row lineage are stored.
    sql = """SELECT b.id AS brief_id, b.metadata_json, p.payload_json
             FROM curriculum_plan p JOIN profile_brief b ON p.brief_id = b.id WHERE p.id = ?"""
    row = conn.execute(sql + (" FOR UPDATE OF b, p" if for_update else ""), (plan_id,)).fetchone()
    if not row:
        raise StaleRevisionError("Эта версия УП недоступна. Откройте текущую карточку УП из реестра.")
    brief_id = int(row["brief_id"])
    spec = load_brief_spec_for_plan(conn, brief_id)
    if not spec.get("source_curriculum"):
        raise ValueError("В брифе нет реестра прежних работ.")
    metadata = json.loads(row["metadata_json"] or "{}")
    # Row edits can be committed before the derived plan JSON is refreshed.
    row_query = "SELECT * FROM curriculum_plan_row WHERE plan_id = ? ORDER BY id"
    live_rows = conn.execute(row_query + (" FOR UPDATE" if for_update else ""), (plan_id,)).fetchall()
    token = _digest([plan_id, row["payload_json"], spec["source_curriculum"], spec["requirements_text"],
                     spec.get("revision_decisions"), metadata.get("revision_reviews"), [dict(r) for r in live_rows]])
    return RevisionEditor(brief_id, plan_id, spec, metadata, token)


def _saved_evidence(current: dict[str, Any]) -> list[dict[str, str]]:
    """Recover grounded excerpts from both structured decisions and the original display string."""
    if isinstance(current.get("evidence"), list):
        return [{"ref": item["ref"], "quote": item["quote"]} for item in current["evidence"]]
    # The archived format was 'ref «excerpt» / ref «excerpt»'. Keep repeated refs and
    # inner quotation marks intact; the grounding check still validates every excerpt.
    citations = str(current.get("source_ref") or "")
    parts = re.split(r"» / (?=[^\s]+ «)", citations.removesuffix("»"))
    evidence = []
    for part in parts:
        ref, sep, quote = part.partition(" «")
        if sep and ref and quote:
            evidence.append({"ref": ref, "quote": quote})
    return evidence


def editor_context(editor: RevisionEditor, selected: str | None) -> dict[str, Any]:
    """Prefill the selected work, including every member of an existing merge."""
    works = editor.spec["source_curriculum"]
    source = next((unit for unit in works if unit["source_id"] == selected), works[0])
    current: dict[str, Any] = next(
        (d for d in editor.spec.get("revision_decisions", []) if source["source_id"] in d.get("source_ids", [])), {},
    )
    refs = {item["ref"] for item in _saved_evidence(current)}
    selected_refs = [p["ref"] for p in editor.passages if p["ref"] in refs]
    historical = bool(refs.intersection(current.get("source_ids", [])))
    return {"editor": editor, "works": works, "source": source, "current": current, "passages": editor.passages,
            "selected_refs": selected_refs, "historical": historical}


def confirm_revision(
    conn: CatalogConnection, editor: RevisionEditor, form: Mapping[str, str],
    source_ids: Sequence[str], refs: Sequence[str], *, actor: str,
) -> None:
    """Stage a single validated decision for the plan build's transaction."""
    if form.get("version") != editor.token:
        raise StaleRevisionError("Решения или строки УП изменились. Обновите страницу и сверьте изменения.")
    if form.get("confirmed") != "yes":
        raise ValueError("Для записи требуется явное подтверждение методолога.")
    units = {unit["source_id"]: unit for unit in editor.spec["source_curriculum"]}
    by_ref = {p["ref"]: p["text"] for p in editor.passages}
    if any(ref not in by_ref for ref in refs):
        raise ValueError("Указана неизвестная строка брифа.")
    current = editor_context(editor, form.get("selected") or next(iter(source_ids), None))["current"]
    # Keep selected original excerpts, including multiple excerpts from one passage.
    historical_refs = set(source_ids) if form.get("historical") == "yes" else set()
    selected_refs = set(refs) | historical_refs
    evidence = [item for item in _saved_evidence(current) if item["ref"] in selected_refs]
    saved_refs = {item["ref"] for item in evidence}
    evidence.extend({"ref": ref, "quote": by_ref[ref]} for ref in dict.fromkeys(refs) if ref not in saved_refs)
    if historical_refs and not historical_refs.intersection(saved_refs):
        for source_id in source_ids:
            unit = units.get(source_id, {})
            quote = next((s for s in [unit.get("description", ""), *unit.get("outcomes", [])] if len(s.strip()) >= 12), "")
            if quote:
                evidence.append({"ref": source_id, "quote": quote})
    team_mode = form.get("target_team_mode", current.get("target_team_mode", ""))
    if team_mode not in {"", "individual", "team"}:
        raise ValueError("Неизвестная форма сдачи.")
    modes = {units[s].get("team_mode") for s in source_ids if s in units} - {None, "", "unknown"}
    if len(modes) > 1 and not team_mode:
        raise ValueError("При объединении разных форм сдачи выберите итоговую форму.")
    # Take omitted optional values from the server snapshot. Only known proposal
    # fields can be carried forward; approval and provenance are assigned below.
    fields = {key: current[key] for key in Proposal.model_fields if key in current}
    for key in ("disposition", "title", "participant_action", "artifact", "validation_criteria",
                "rationale", "review_question", "case_context", "choose_one_group"):
        if key in form:
            fields[key] = form[key].strip()
    for key in ("required_tools", "lost_outcomes", "inputs", "prerequisite_source_ids"):
        if key in form:
            fields[key] = [line.strip() for line in form[key].splitlines() if line.strip()]
    if "effort_hours" in form:
        fields["effort_hours"] = form["effort_hours"].strip().replace(",", ".") or None
    for key in ("group_size", "peer_reviews"):
        if key in form:
            raw = form[key].strip()
            if raw and not raw.isdecimal():
                raise ValueError("Размер группы и количество проверок задаются целыми числами.")
            fields[key] = int(raw) if raw else None
    if "is_capstone" in form or "edit_capstone" in form:
        fields["is_capstone"] = form.get("is_capstone") == "yes"
    if "is_entry_project" in form or "edit_entry_project" in form:
        fields["is_entry_project"] = form.get("is_entry_project") == "yes"
    if "acceptance_check" in form or "acceptance_expected" in form:
        acceptance = {"check": form.get("acceptance_check", "").strip(),
                      "expected_result": form.get("acceptance_expected", "").strip()}
        fields["acceptance_test"] = acceptance if any(acceptance.values()) else None
    outcomes = dict(fields.get("learning_outcomes") or {})
    for category in ("know", "can", "skills"):
        key = "outcomes_" + category
        if key in form:
            outcomes[category] = [line.strip() for line in form[key].splitlines() if line.strip()]
    fields["learning_outcomes"] = outcomes
    try:
        proposal = Proposal.model_validate({**fields, "source_ids": list(source_ids), "evidence": evidence})
        decision = _ground_proposal(proposal, units, by_ref)
    except ValidationError as exc:
        invalid_fields = ", ".join(".".join(map(str, entry["loc"])) for entry in exc.errors())
        raise ValueError(f"Проверьте поля решения ({invalid_fields}).") from exc
    listed = {p["ref"] for p in editor.passages if p["listed"]}
    decision.update(approved=True, target_team_mode=team_mode,
                    requirement_refs=[ref for ref in decision["requirement_refs"] if ref in listed])
    history = list(editor.metadata.get("revision_reviews") or [])
    history.append({
        "actor": actor, "at": datetime.now(UTC).isoformat(), "plan_id": editor.plan_id,
        "source_hash": _digest([editor.spec["source_curriculum"], editor.spec["requirements_text"]]),
        "decision": decision,
    })
    conn.execute("UPDATE profile_brief SET metadata_json = ? WHERE id = ?",
                 (json.dumps({**editor.metadata, "revision_reviews": history}, ensure_ascii=False), editor.brief_id))
