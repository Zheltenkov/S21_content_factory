"""Edits of the brief as explicit patches: proposed by the model or typed by the methodologist, checked against the
brief contract, applied as a whole or not at all. A stale patch (its field changed after the proposal) is refused or
withdrawn, never half-applied.
"""

from fastapi import HTTPException
from pydantic import ValidationError

from .models import Brief, Run, exclusions, loose
from .thread import FIELDS, Message, Patch, ProposedChange, Thread, Value, readable


def build_patches(draft: Brief, changes: list[ProposedChange], origin_override: str | None = None) -> tuple[list[Patch], list[str]]:
    """Validate proposals against the brief contract; invalid ones are reported, never applied silently."""
    current = draft.model_dump()
    latest: dict[str, ProposedChange] = {}
    for change in changes:
        latest[change.field] = change
    notes: list[str] = []
    try:
        normalized = Brief.model_validate({**current, **{k: c.value for k, c in latest.items()}}).model_dump()
        accepted = list(latest.values())
    except ValidationError:
        # Keep every independently valid change; name the rejected ones for the methodologist.
        normalized, accepted = dict(current), []
        for change in latest.values():
            try:
                normalized = Brief.model_validate({**normalized, change.field: change.value}).model_dump()
                accepted.append(change)
            except ValidationError as error:
                notes.append(f"Правка «{FIELDS[change.field]}» не прошла проверку: {readable(error)}")
    patches = []
    for change in accepted:
        if normalized[change.field] == current[change.field]:
            notes.append(f"«{FIELDS[change.field]}» уже содержит это значение.")
            continue
        patches.append(Patch(field=change.field, label=FIELDS[change.field], before=current[change.field],
                             after=normalized[change.field], reason=change.reason,
                             origin=origin_override or change.origin, question_numbers=change.question_numbers))
    return patches, notes


def apply_patches(thread: Thread, message: Message, values: dict[int, Value] | None = None) -> None:
    """Apply a pending proposal as a whole; a stale or invalid proposal is refused, never half-applied."""
    values = values or {}
    current = thread.draft.model_dump()
    if message.status != "pending":
        raise HTTPException(409, "Эти правки уже приняты или отклонены")
    for patch in message.patches:
        if current[patch.field] != patch.before:
            raise HTTPException(409, f"Поле «{patch.label}» изменилось после предложения. Попросите правку заново.")
    updated = dict(current)
    for index, patch in enumerate(message.patches):
        updated[patch.field] = values.get(index, patch.after)
    try:
        brief = Brief.model_validate(updated)
    except ValidationError as error:
        raise HTTPException(422, f"Правки не прошли проверку: {readable(error)}") from error
    normalized = brief.model_dump()
    for index, patch in enumerate(message.patches):
        if index in values:
            patch.after, patch.edited = normalized[patch.field], True
    thread.draft, message.status = brief, "applied"
    # Another waiting proposal on the same fields is stale now: withdrawn here, whoever applied this one.
    expire_stale(thread)


def expire_stale(thread: Thread) -> list[str]:
    """Proposals whose fields changed under them are withdrawn with a note: left waiting, they could never be applied
    and «Применить правки и продолжить» would fail on them every time (independent review 30.09.2026: apply M1,
    the model proposes M2 on the same field, revert M1 — M2 was stale and blocked the route)."""
    current = thread.draft.model_dump()
    labels: list[str] = []
    for message in thread.messages:
        if message.status == "pending" and any(current[p.field] != p.before for p in message.patches):
            message.status = "rejected"
            message.notes.append("Поле изменилось после предложения — предложение снято. Попросите правку заново, если она ещё нужна.")
            labels += [p.label for p in message.patches]
    return labels


def supersede(messages: list[Message], fields: set[str]) -> str:
    """A new proposal for a field replaces an older one still waiting for a decision on the same field."""
    labels: list[str] = []
    for message in messages:
        if message.status == "pending" and {p.field for p in message.patches} & fields:
            message.status = "rejected"
            message.notes.append("Заменено следующим предложением.")
            labels += [p.label for p in message.patches]
    return ", ".join(dict.fromkeys(labels))


def whole(run: Run, key: str, text: str) -> bool:
    """A fragment may be dropped as a whole when it is short or when the unrelated part is nearly all of it."""
    spans = [item.text or item.quote for item in (run.input_card.off_topic if run.input_card else []) if item.evidence_id == key]
    return len(text) <= 600 or any(len(span) >= 0.9 * len(text) for span in spans)


def checked_exclusions(run: Run, patches: list[Patch], notes: list[str]) -> tuple[list[Patch], list[str]]:
    """A new «не учитывать» line must point to a fragment and to text that are really there; otherwise it would cut
    nothing, or the wrong thing. Such a proposal is refused with the reason, not applied."""
    texts = {e.id: e.text for e in run.evidence}
    kept = []
    for patch in patches:
        if patch.field != "excluded_sources":
            kept.append(patch)
            continue
        added = exclusions(str(patch.after or ""))
        before = exclusions(str(patch.before or ""))
        problems = []
        for key, cuts in added.items():
            new = [c for c in cuts if c not in before.get(key, [])]
            if not new:
                continue
            if key not in texts:
                problems.append(f"фрагмента {key} нет во входах")
                continue
            if None in new and not whole(run, key, texts[key]):
                problems.append(f"фрагмент {key} исключается целиком, а в нём есть и другой текст — нужна точная цитата")
            problems += [f"в {key} нет текста «{c[:60]}»" for c in new if c is not None and not loose(c).search(texts[key])]
        if problems:
            notes.append("Исключение не предложено: " + "; ".join(problems) + ". Уточните, какой текст не учитывать.")
        else:
            kept.append(patch)
    return kept, notes
