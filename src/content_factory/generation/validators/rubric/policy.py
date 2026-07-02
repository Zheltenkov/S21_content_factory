"""Rubric post-processing policy for blocking failures and warnings."""

from __future__ import annotations

from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel


WARNING_PREFIX = "Предупреждение:"
SOFT_METHODS = {CheckMethod.AI_AGENT, CheckMethod.SBERT, CheckMethod.HYBRID}
SEMANTIC_TITLE_HINTS = (
    "смыслов",
    "нарратив",
    "когерент",
    "связност",
    "тон",
    "сторител",
    "соответств",
    "формулиров",
    "читабельн",
    "редактур",
    "пример",
    "кейс",
)


def rubric_item_status(item: CriteriaItem) -> str:
    """Return UI/API status for a normalized rubric item."""
    if item.score == 1 and item.strictness == StrictnessLevel.SOFT and _has_warning_comment(item):
        return "warning"
    return "passed" if item.score == 1 else "failed"


def apply_rubric_warning_policy(items: list[CriteriaItem]) -> list[CriteriaItem]:
    """Convert non-blocking rubric failures into diagnostic warnings.

    Hard criteria still fail. Soft/editorial/semantic criteria become warnings so
    methodologists see what to improve without treating debatable checks as
    broken generation.
    """
    return [_normalize_item(item) for item in items]


def _normalize_item(item: CriteriaItem) -> CriteriaItem:
    if item.score != 0 or not _is_non_blocking_failure(item):
        return item

    details = dict(item.details or {})
    details.update({
        "severity": "warning",
        "blocking": False,
        "original_score": item.score,
        "policy": "soft_rubric_warning",
    })

    comments = item.comments or [item.description or "Критерий требует ручной проверки."]
    warning_comments = [
        comment if str(comment).strip().lower().startswith(WARNING_PREFIX.lower()) else f"{WARNING_PREFIX} {comment}"
        for comment in comments
    ]

    return item.model_copy(
        update={
            "score": 1,
            "comments": warning_comments,
            "details": details,
            "strictness": StrictnessLevel.SOFT,
        },
        deep=True,
    )


def _is_non_blocking_failure(item: CriteriaItem) -> bool:
    if item.strictness == StrictnessLevel.SOFT:
        return True
    if item.check_method not in SOFT_METHODS:
        return False
    return _looks_semantic_or_editorial(item)


def _looks_semantic_or_editorial(item: CriteriaItem) -> bool:
    haystack = " ".join([
        item.id,
        item.title or "",
        item.description or "",
        " ".join(item.comments or []),
    ]).casefold()
    return any(hint in haystack for hint in SEMANTIC_TITLE_HINTS)


def _has_warning_comment(item: CriteriaItem) -> bool:
    comments = " ".join(str(comment) for comment in item.comments or [])
    return "предупреждение" in comments.casefold() or "warning" in comments.casefold()
