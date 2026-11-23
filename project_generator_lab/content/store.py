"""Draft storage: one JSON file per approved concept version, written atomically, readable by its owner only."""

import json
import os
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

from ..core.access import current_owner
from ..core.storage import Locks, parse_stored, write_model
from .models import ContentDraft

PACKAGE = Path(__file__).resolve().parent.parent
CONTENT_DIR = Path(os.getenv("PROJECT_LAB_CONTENT_DIR", PACKAGE / "data" / "content"))
# Every change is lock → load → change → save, so a background writer and the chat never overwrite each other.
lock = Locks()


def path(draft_id: str) -> Path:
    return CONTENT_DIR / f"{draft_id}.json"


def exists(draft_id: str) -> bool:
    return path(draft_id).is_file()


def save(draft: ContentDraft) -> None:
    write_model(path(draft.id), draft)


def load(draft_id: str) -> ContentDraft:
    file = path(draft_id)
    if not file.is_file() or owner(draft_id) not in {"", current_owner.get()}:
        # The owner is checked before anything else: someone else's draft does not exist for you, readable or not.
        # A very old file without an owner falls through to validation below and is refused as an earlier version.
        raise HTTPException(404, "Черновик контента не найден")
    try:
        draft = parse_stored(ContentDraft, file.read_text(encoding="utf-8"))
    except ValidationError as error:
        # A draft of an earlier generator version: never guessed into the new shape, set aside on the next start.
        raise HTTPException(409, "Черновик создан прошлой версией генератора. Нажмите «Подготовить контент», чтобы начать заново; "
                                 "старый файл сохранится рядом.") from error
    # Knowing the id does not give access to another methodologist's draft.
    if draft.owner_id != current_owner.get():
        raise HTTPException(404, "Черновик контента не найден")
    return draft


def owner(draft_id: str) -> str:
    """Who the stored draft belongs to, read without validating the rest: an old or damaged draft still has an owner."""
    try:
        data = json.loads(path(draft_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("owner_id", "")) if isinstance(data, dict) else ""


def readable(draft_id: str) -> bool:
    try:
        parse_stored(ContentDraft, path(draft_id).read_text(encoding="utf-8"))
        return True
    except (ValidationError, OSError):
        return False


def set_aside(draft_id: str) -> None:
    """Keep an unreadable old draft next to the new one instead of deleting it."""
    old = path(draft_id)
    old.replace(old.with_name(f"{draft_id}.old-{int(old.stat().st_mtime)}.json.bak"))


def drafts() -> list[ContentDraft]:
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    found = []
    for file in CONTENT_DIR.glob("*.json"):
        try:
            found.append(parse_stored(ContentDraft, file.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            continue
    return found
