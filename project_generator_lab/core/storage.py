"""Atomic JSON snapshots on disk: a crash never leaves half a file behind."""

import asyncio
import json
import logging
import weakref
from pathlib import Path
from typing import Any, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from .contracts import STORED_SCHEMA

M = TypeVar("M", bound=BaseModel)
logger = logging.getLogger(__name__)


def write_model(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def read_owned(path: Path, model: type[M], owner: str, missing: str, unreadable: str) -> M:
    """A stored snapshot of this owner. The owner is read first, without validating the rest: someone else's file does
    not exist for you, readable or not. A file this version cannot read is a clear 409, not a server error
    (audit 30.09.2026: a damaged or older run or thread gave 500 on every screen that opened it)."""
    if not path.is_file():
        raise HTTPException(404, missing)
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError as error:
        raise HTTPException(409, unreadable) from error
    if not isinstance(data, dict) or data.get("owner_id", "") != owner:
        raise HTTPException(404, missing)
    try:
        return parse_stored(model, text)
    except ValidationError as error:
        raise HTTPException(409, unreadable) from error


def parse_stored(model: type[M], text: str) -> M:
    """A stored file as this release reads it: strictly (JSON mode, as the files were always read), unless a newer
    release wrote it. Then the fields this release does not know are left out and logged.

    Whatever number the file carried, what is held now has this release's shape, so it carries this release's number
    (contracts.STORED_SCHEMA) and is saved with it. An older file read and saved again used to keep its old number
    while gaining the new fields; the previous release, rolled back to, then took it for a file of its own and refused
    the fields it did not know (independent review 05.10.2026)."""
    try:
        head = json.loads(text)
    except ValueError:
        return model.model_validate_json(text)  # broken JSON: the usual ValidationError
    version = head.get("schema_version", 0) if isinstance(head, dict) else 0
    if not isinstance(version, int) or version <= STORED_SCHEMA:
        return current(model.model_validate_json(text))
    context: dict[str, Any] = {"newer": True}
    stored = model.model_validate_json(text, context=context)
    logger.warning("stored %s of schema %s read by schema %s; left out: %s", model.__name__, version, STORED_SCHEMA,
                   ", ".join(context.get("left_out", [])) or "nothing")
    return current(stored)


def current(stored: M) -> M:
    """The model with this release's schema number, when it keeps one."""
    if getattr(stored, "schema_version", STORED_SCHEMA) != STORED_SCHEMA:
        return stored.model_copy(update={"schema_version": STORED_SCHEMA})
    return stored


class Locks:
    """One asyncio lock per key (a thread, a draft), kept only while someone holds it or waits for it. A plain dict kept
    a lock for every thread ever opened for the life of the server (audit 30.09.2026). The caller holds the lock it got
    until `async with` ends, so a second caller in that time gets the same one; afterwards nothing refers to it and it
    goes."""

    def __init__(self) -> None:
        self.held: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def __call__(self, key: str) -> asyncio.Lock:
        lock = self.held.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.held[key] = lock
        return lock
