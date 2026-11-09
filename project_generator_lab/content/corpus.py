"""Measured text datasets: bounded batches, deduplication and resumable progress."""

import hashlib
import json
import re
from collections.abc import Mapping

WORD = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*", re.UNICODE)


def word_count(text: str) -> int:
    return len(WORD.findall(text))


def fingerprint(spec: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(dict(spec), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def merge(parts: list[str]) -> str:
    # Preserve complete dialogue blocks. Duplication is reported, never silently padded or trimmed into size.
    return "\n\n".join(p.strip() for p in parts if p.strip()) + "\n"


def duplicate_blocks(text: str) -> int:
    blocks = [" ".join(b.casefold().split()) for b in re.split(r"\n\s*\n", text) if word_count(b) >= 20]
    return len(blocks) - len(set(blocks))
