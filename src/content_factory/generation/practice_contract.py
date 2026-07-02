"""Shared contract helpers for practice tasks and input materials."""

from __future__ import annotations

import re

from .models.schemas import PracticeTask

MATERIAL_REF_RE = re.compile(r"`?(materials/[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)`?", re.I)

SOLUTION_LIKE_STEM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"(^|[_-])answer($|[_-])",
        r"(^|[_-])solution($|[_-])",
        r"(^|[_-])final($|[_-])",
        r"(^|[_-])result($|[_-])",
        r"(^|[_-])output($|[_-])",
        r"(^|[_-])deliverable($|[_-])",
        r"(^|[_-])register($|[_-])",
        r"(^|[_-])registry($|[_-])",
        r"(^|[_-])matrix($|[_-])",
        r"(^|[_-])analysis($|[_-])",
        r"(^|[_-])classification($|[_-])",
        r"(^|[_-])classified($|[_-])",
        r"(^|[_-])plan($|[_-])",
        r"(^|[_-])strategy($|[_-])",
        r"(^|[_-])response($|[_-])",
        r"(^|[_-])recommendations?($|[_-])",
        r"(^|[_-])mitigation($|[_-])",
        r"(^|[_-])decision($|[_-])",
        r"(^|[_-])decision[_-]log($|[_-])",
        r"(^|[_-])summary($|[_-])",
        r"(^|[_-])report($|[_-])",
        r"(^|[_-])filled($|[_-])",
        r"(^|[_-])completed($|[_-])",
        r"(^|[_-])итог($|[_-])",
        r"(^|[_-])решени[ея]($|[_-])",
        r"(^|[_-])реестр($|[_-])",
        r"(^|[_-])матриц[аы]($|[_-])",
        r"(^|[_-])стратеги[яи]($|[_-])",
        r"(^|[_-])рекомендаци[яи]($|[_-])",
    )
)

RAW_MATERIAL_HINTS: tuple[str, ...] = (
    "raw",
    "source",
    "draft",
    "notes",
    "case",
    "incident",
    "event",
    "log",
    "interview",
    "chat",
    "email",
    "request",
    "requirements",
    "context",
    "previous_projects",
    "сырые",
    "исходн",
    "чернов",
    "замет",
    "кейс",
    "инцидент",
    "событи",
    "лог",
    "переписк",
    "письм",
    "запрос",
    "требован",
    "контекст",
)

PROCESSED_MATERIAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        r"\bготов\w*\s+(?:список|реестр|матриц\w*|swot|план|рекомендаци\w*|решени\w*|отч[её]т|таблиц\w*)\b",
        r"\bуже\s+(?:заполненн\w*|классифицир\w*|сформированн\w*|подготовленн\w*)\b",
        r"\bзаполненн\w+\s+(?:таблиц\w*|матриц\w*|шаблон\w*)\b",
        r"\bклассифицированн\w+\s+(?:список|таблиц\w*|элемент\w*|объект\w*|данн\w*)\b",
        r"\bитогов\w+\s+(?:таблиц\w*|документ\w*|отч[её]т\w*|презентаци\w*|решени\w*)\b",
        r"\b(?:план|матриц\w*|реестр|стратеги\w*|отч[её]т|решени\w*)\s+(?:уже\s+)?(?:готов|заполн|сформир|классифиц)",
    )
)


def extract_material_refs(text: str) -> list[str]:
    """Return unique materials/* file references from a text fragment."""
    refs: list[str] = []
    seen: set[str] = set()
    for match in MATERIAL_REF_RE.finditer(text or ""):
        ref = match.group(1)
        key = ref.lower()
        if key not in seen:
            refs.append(ref)
            seen.add(key)
    return refs


def _stem(path_or_filename: str) -> str:
    filename = (path_or_filename or "").replace("\\", "/").split("/")[-1]
    return re.sub(r"\.[A-Za-z0-9]+$", "", filename).lower()


def has_raw_material_hint(text: str) -> bool:
    """Check whether the surrounding text describes raw source material."""
    normalized = (text or "").lower()
    return any(hint in normalized for hint in RAW_MATERIAL_HINTS)


def is_solution_like_material_ref(path_or_filename: str, *, context: str = "") -> bool:
    """Detect materials that look like completed learner deliverables."""
    stem = _stem(path_or_filename)
    if not stem:
        return False
    if has_raw_material_hint(stem) or has_raw_material_hint(context):
        return False
    return any(pattern.search(stem) for pattern in SOLUTION_LIKE_STEM_PATTERNS)


def find_solution_like_material_refs(text: str) -> list[str]:
    """Find materials refs that likely leak a ready answer."""
    return [
        ref
        for ref in extract_material_refs(text)
        if is_solution_like_material_ref(ref, context=text)
    ]


def find_non_raw_material_issues(text: str) -> list[str]:
    """Detect input descriptions that provide classified or solved materials."""
    normalized = (text or "").strip()
    if not normalized:
        return []

    issues: list[str] = []
    solution_refs = find_solution_like_material_refs(normalized)
    if solution_refs:
        issues.extend(f"solution_like_ref:{ref}" for ref in solution_refs)

    for pattern in PROCESSED_MATERIAL_PATTERNS:
        match = pattern.search(normalized)
        if match:
            issues.append(f"processed_material_phrase:{match.group(0)}")

    result: list[str] = []
    seen: set[str] = set()
    for issue in issues:
        key = issue.lower()
        if key not in seen:
            result.append(issue)
            seen.add(key)
    return result


def raw_material_path_for_task(task_index: int) -> str:
    """Build a neutral raw-source filename for a task."""
    return f"materials/task_{task_index:02d}_source_notes.md"


def _contains_path(text: str, path: str) -> bool:
    return (path or "").lower() in (text or "").lower()


def task_uses_previous_artifact(task: PracticeTask, previous_task: PracticeTask | None) -> bool:
    """Check whether a task explicitly depends on the previous task artifact."""
    previous_location = str(getattr(previous_task, "artifact_location", "") or "") if previous_task else ""
    if not previous_location:
        return True
    blob = " ".join([
        str(getattr(task, "input_data", "") or ""),
        " ".join(getattr(task, "approach_bullets", []) or []),
        str(getattr(task, "goal", "") or ""),
        str(getattr(task, "expected_artifact", "") or ""),
    ])
    return _contains_path(blob, previous_location)


def normalize_task_input_for_learning_activity(
    input_data: str,
    *,
    task_index: int,
    previous_artifact_location: str | None = None,
) -> str:
    """Keep input data as raw evidence and chain later tasks to prior outputs."""
    normalized = (input_data or "").strip()
    material_issues = find_non_raw_material_issues(normalized)

    if material_issues:
        if previous_artifact_location:
            normalized = (
                f"Результат предыдущей задачи — см. файл `{previous_artifact_location}`. "
                "Используй его как основу для следующего решения."
            )
        else:
            raw_path = raw_material_path_for_task(task_index)
            normalized = (
                f"Сырые заметки, кейсы и наблюдения для анализа — см. файл `{raw_path}`. "
                "В файле только исходные сведения без классификации и выводов."
            )

    if task_index > 1 and previous_artifact_location and not _contains_path(normalized, previous_artifact_location):
        suffix = f"Результат предыдущей задачи — см. файл `{previous_artifact_location}`."
        normalized = f"{normalized.rstrip('.')}.\n{suffix}" if normalized else suffix

    return normalized
