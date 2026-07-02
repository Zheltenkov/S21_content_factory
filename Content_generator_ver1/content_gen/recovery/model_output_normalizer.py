"""Normalize tolerated LLM output variants into canonical README fragments."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelOutputNormalization:
    """Result of canonicalizing one model output fragment."""

    markdown: str
    changes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Whether the normalizer had to rewrite legacy model output."""
        return bool(self.changes)


class ModelOutputNormalizer:
    """Canonicalizes known legacy headings returned by LLMs.

    Public README contracts are strict: theory uses ``### 2.N.`` and practice
    uses ``### Задание N.``. This adapter is only a recovery boundary for raw
    model output and old paused-session markdown.
    """

    _LEGACY_THEORY_HEADING_RE = re.compile(
        r"^(?P<prefix>###\s+)Часть\s+(?P<number>\d+)\.\s*(?P<title>.+?)\s*$",
        flags=re.M | re.I,
    )
    _LEGACY_PRACTICE_HEADING_RE = re.compile(
        r"^(?P<prefix>###\s+)Задача\s+(?P<number>\d+)\.\s*(?P<title>.+?)\s*$",
        flags=re.M | re.I,
    )

    def normalize_theory_markdown(self, markdown: str) -> ModelOutputNormalization:
        """Convert legacy theory headings into canonical ``2.N`` headings."""
        changes: list[str] = []

        def replace(match: re.Match[str]) -> str:
            number = match.group("number")
            title = match.group("title").strip()
            changes.append(f"theory_heading:Часть {number}->2.{number}")
            return f"{match.group('prefix')}2.{number}. {title}"

        normalized = self._LEGACY_THEORY_HEADING_RE.sub(replace, markdown or "")
        return ModelOutputNormalization(markdown=normalized, changes=changes)

    def normalize_practice_markdown(self, markdown: str) -> ModelOutputNormalization:
        """Convert legacy practice headings into canonical ``Задание`` headings."""
        changes: list[str] = []

        def replace(match: re.Match[str]) -> str:
            number = match.group("number")
            title = match.group("title").strip()
            changes.append(f"practice_heading:Задача {number}->Задание {number}")
            return f"{match.group('prefix')}Задание {number}. {title}"

        normalized = self._LEGACY_PRACTICE_HEADING_RE.sub(replace, markdown or "")
        return ModelOutputNormalization(markdown=normalized, changes=changes)

    def normalize_readme_markdown(self, markdown: str) -> ModelOutputNormalization:
        """Canonicalize all known legacy README headings in one pass."""
        theory = self.normalize_theory_markdown(markdown)
        practice = self.normalize_practice_markdown(theory.markdown)
        return ModelOutputNormalization(
            markdown=practice.markdown,
            changes=[*theory.changes, *practice.changes],
        )
