"""Shared deterministic project-level content profile resolution."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

ContentProfile = Literal["hard_code", "low_code", "hybrid", "no_code"]
ProfileSource = Literal["explicit", "project_signals", "direction_fallback", "default"]

_HARD_CODE_DIRECTIONS = {
    "C", "CPP", "C++", "JAVA", "GO", "RUST", "BACKEND", "MOBILE", "WEB",
    "FRONTEND", "FULLSTACK", "DEV", "SWE",
}
_LOW_CODE_DIRECTIONS = {
    "DS", "DO", "QA", "BIO", "BIOINF", "DEVOPS", "DATA", "ML", "AI",
    "TESTING", "AUTOMATION",
}
_NO_CODE_DIRECTIONS = {
    "PJM", "UX", "CB", "KB", "BSA", "BA", "PM", "CYBER", "SECURITY",
    "PRODUCT", "DESIGN", "MANAGEMENT", "ANALYST",
}

_HARD_SIGNALS: dict[str, int] = {
    "исходный код": 4,
    "программир": 4,
    "разработк": 2,
    "реализац": 2,
    "прототип": 1,
    "backend": 3,
    "frontend": 3,
    "fullstack": 3,
    "python": 3,
    "javascript": 3,
    "typescript": 3,
    "java": 3,
    "golang": 3,
    "api": 2,
    "sdk": 2,
}
_LOW_CODE_SIGNALS: dict[str, int] = {
    "docker": 2,
    "git": 1,
    " ci ": 2,
    "тест": 1,
    "автоматизац": 2,
    "пайплайн": 2,
    "pipeline": 2,
    "инфраструктур": 2,
    "разверт": 2,
    "развёрт": 2,
    "данн": 1,
    "модел": 1,
    "llm": 1,
    " ai ": 1,
    "архитектур": 1,
}
_NO_CODE_SIGNALS: dict[str, int] = {
    "исследован": 2,
    "отчёт": 2,
    "отчет": 2,
    "клиент": 1,
    "маркет": 2,
    "продаж": 2,
    "стратег": 2,
    "управлен": 2,
    "презентац": 1,
    "интервью": 1,
    "сегмент": 1,
    "документ": 1,
}


class ContentProfileDecision(BaseModel):
    """Resolved profile with evidence suitable for lineage and UI explanations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: ContentProfile
    source: ProfileSource
    hard_score: int = 0
    low_score: int = 0
    no_code_score: int = 0
    matched_signals: list[str] = Field(default_factory=list)


def resolve_content_profile(seed: Any) -> ContentProfileDecision:
    """Resolve a profile from a seed-like object without relying on direction first."""

    return infer_content_profile(
        explicit_profile=getattr(seed, "project_content_type", None),
        direction=str(getattr(seed, "direction", "") or ""),
        thematic_block=str(getattr(seed, "thematic_block", "") or ""),
        title=str(getattr(seed, "title_seed", "") or ""),
        description=str(getattr(seed, "project_description", "") or ""),
        skills=_string_items(getattr(seed, "skills", None)),
        required_tools=_string_items(getattr(seed, "required_tools", None)),
        required_software=_string_items(getattr(seed, "required_software", None)),
        learning_outcomes=_string_items(getattr(seed, "learning_outcomes", None)),
        artifact=str(getattr(seed, "additional_materials", "") or ""),
    )


def infer_content_profile(
    *,
    explicit_profile: str | None = None,
    direction: str = "",
    thematic_block: str = "",
    title: str = "",
    description: str = "",
    skills: list[str] | tuple[str, ...] = (),
    required_tools: list[str] | tuple[str, ...] = (),
    required_software: list[str] | tuple[str, ...] = (),
    learning_outcomes: list[str] | tuple[str, ...] = (),
    artifact: str = "",
) -> ContentProfileDecision:
    """Resolve a profile from structured UP project fields."""

    explicit = str(explicit_profile or "").strip().lower()
    if explicit in {"hard_code", "low_code", "hybrid", "no_code"}:
        return ContentProfileDecision(profile=cast(ContentProfile, explicit), source="explicit")

    text = " ".join(
        [
            title,
            description,
            thematic_block,
            artifact,
            *[str(item) for item in skills],
            *[str(item) for item in required_tools],
            *[str(item) for item in required_software],
            *[str(item) for item in learning_outcomes],
        ]
    ).casefold()
    padded_text = f" {text} "
    hard_score, hard_matches = _score_signals(padded_text, _HARD_SIGNALS)
    low_score, low_matches = _score_signals(padded_text, _LOW_CODE_SIGNALS)
    no_code_score, no_code_matches = _score_signals(padded_text, _NO_CODE_SIGNALS)
    matches = [*hard_matches, *low_matches, *no_code_matches]

    if hard_score >= 3 and no_code_score >= 3:
        profile: ContentProfile = "hybrid"
    elif hard_score >= 3:
        profile = "hard_code"
    elif low_score >= 2 and no_code_score >= 3:
        profile = "hybrid"
    elif low_score >= 2:
        profile = "low_code"
    elif no_code_score >= 2:
        profile = "no_code"
    else:
        profile = _profile_from_direction(direction or thematic_block)
        source: ProfileSource = "direction_fallback" if direction or thematic_block else "default"
        return ContentProfileDecision(
            profile=profile,
            source=source,
            hard_score=hard_score,
            low_score=low_score,
            no_code_score=no_code_score,
            matched_signals=matches,
        )
    return ContentProfileDecision(
        profile=profile,
        source="project_signals",
        hard_score=hard_score,
        low_score=low_score,
        no_code_score=no_code_score,
        matched_signals=matches,
    )


def _score_signals(text: str, signals: dict[str, int]) -> tuple[int, list[str]]:
    matched = [signal.strip() for signal in signals if signal in text]
    return sum(signals[signal] for signal in signals if signal in text), matched


def _string_items(value: object) -> list[str]:
    """Normalize scalar and collection fields without splitting strings into characters."""

    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _profile_from_direction(direction: str) -> ContentProfile:
    normalized = direction.strip().upper()
    if normalized in _HARD_CODE_DIRECTIONS:
        return "hard_code"
    if normalized in _LOW_CODE_DIRECTIONS:
        return "low_code"
    if normalized in _NO_CODE_DIRECTIONS:
        return "no_code"
    return "low_code"
