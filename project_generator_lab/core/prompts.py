"""Prompt store: prompts are files next to the module that uses them, never strings in code.

A run freezes the texts it used, so a later edit of a prompt never changes what an old run meant.
"""

import hashlib
from pathlib import Path


class PromptStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def text(self, name: str) -> str:
        return (self.folder / f"{name}.md").read_text(encoding="utf-8")

    def snapshot(self, *names: str) -> dict[str, str]:
        return {name: self.text(name) for name in names}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
