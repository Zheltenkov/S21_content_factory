"""Materialize parsed theory data into typed TheoryPart objects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config.thresholds import THRESHOLDS
from ..models.schemas import ProjectSeed, TheoryPart
from .theory_generation import semantic_cover, theory_anchor_terms
from .theory_sanitizer import _sanitize_theory_body_text, _sanitize_theory_example_text


StyleRewrite = Callable[[str, str], str]


class TheoryPartMaterializer:
    """Build and polish one typed TheoryPart from parsed markdown data."""

    def __init__(self, *, style_rewrite: StyleRewrite) -> None:
        self.style_rewrite = style_rewrite

    def materialize(self, part_data: dict[str, Any], seed: ProjectSeed) -> TheoryPart:
        """Convert one parsed part dictionary into a polished TheoryPart."""
        example = (
            _sanitize_theory_example_text(self.style_rewrite(part_data["example"], seed.language))
            if part_data["example"]
            else ""
        )
        main_body = part_data["main_body"]
        covers = semantic_cover(main_body, seed.learning_outcomes)
        if not covers and not main_body.endswith("]"):
            main_body = main_body.rstrip() + " [LO: нет прямого покрытия]."

        part = TheoryPart(
            title=part_data["title"],
            body=main_body,
            example=example,
            bridge_questions=part_data["qs"],
            covers_outcomes=covers,
        )
        return self.polish_part(part, seed)

    def polish_part(self, part: TheoryPart, seed: ProjectSeed) -> TheoryPart:
        """Locally align theory text with didactics after generation or editing."""
        lo, hi = THRESHOLDS["theory_words_per_part"]
        anchors = theory_anchor_terms(seed)
        part.body = _sanitize_theory_body_text(part.body, part.title, seed, anchors, lo, hi)
        part.example = _sanitize_theory_example_text(self.style_rewrite(part.example or "", seed.language))
        part.bridge_questions = [
            question.strip()
            for question in (part.bridge_questions or [])
            if question and question.strip()
        ][:2]
        return part
