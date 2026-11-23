"""Durable answers and source precedence, independent of a model's chat history."""

import hashlib
import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import Field

from ..core.contracts import Contract

if TYPE_CHECKING:
    from .thread import Message


class Resolution(Contract):
    message_id: str = ""
    number: int = 0
    key: str = ""
    question: str
    answer: str
    basis: dict[str, str] = Field(default_factory=dict)


def signature(text: str) -> str:
    return " ".join(re.findall(r"[\wё]+", text.casefold().replace("ё", "е")))


def basis(values: Mapping[str, object], dependencies: list[str]) -> dict[str, str]:
    # An explicit dependency list permits precise reopening. With no list the answer is still passed to the model,
    # but code suppresses only the same wording, never guesses that different questions are equivalent.
    return {key: hashlib.sha256(json.dumps(values[key], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for key in dependencies if key in values}


def resolved(question: str, key: str, values: Mapping[str, object], answers: list[Resolution]) -> bool:
    for answer in reversed(answers):
        same = signature(question) == signature(answer.question)
        # A semantic key may suppress a reworded question only with explicit, unchanged dependencies.
        same_key = bool(key and answer.key == key and answer.basis)
        if (same or same_key) and all(basis(values, [name]).get(name) == digest for name, digest in answer.basis.items()):
            return True
    return False


def received_numbers(messages: list["Message"], run_id: str) -> set[int]:
    """Received answers cease to be questions while their edits wait; rejected edits reopen them."""
    return {a.number for m in messages if m.run_id == run_id and m.status != "rejected" for a in m.answers}
