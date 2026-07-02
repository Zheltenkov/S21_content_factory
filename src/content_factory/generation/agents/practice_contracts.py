"""Deterministic repair and contract helpers for practice tasks."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable

from ..models.schemas import PracticeTask, ProjectSeed

RX_ARTIFACT_PATH = re.compile(r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]+)")


ArtifactLocationBuilder = Callable[[ProjectSeed, int], str]
StyleRewrite = Callable[[str, str], str]
TokenSet = Callable[[str], set[str]]


def fix_result_artifact(
    result: str,
    seed: ProjectSeed,
    task_idx: int,
    artifact_location_for_task: ArtifactLocationBuilder,
) -> tuple[str, str]:
    """Ensure expected result contains a concrete artifact and location."""

    def _extract_path_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        for match in RX_ARTIFACT_PATH.finditer(text or ""):
            candidate = match.group(1).strip("`'\".,;:()[]{}")
            if "://" in candidate:
                continue
            if candidate not in candidates:
                candidates.append(candidate)
        return candidates

    def _pick_primary_path(candidates: list[str]) -> str:
        if not candidates:
            return ""
        preferred = [
            path for path in candidates
            if "/part-" in path.lower() and not path.lower().startswith("repo/")
        ]
        if preferred:
            return preferred[0]
        non_generic = [path for path in candidates if not path.lower().startswith("repo/")]
        return non_generic[0] if non_generic else ""

    artifact_keywords = [
        r"файл\s+\w+",
        r"отчет",
        r"отч[её]т",
        r"код",
        r"скриншот",
        r"результат",
        r"документ",
        r"артефакт",
        r"README",
        r"markdown",
        r"текст",
        r"речь",
        r"выступлен",
        r"таблиц",
        r"матриц",
        r"схем",
        r"план",
        r"презентац",
        r"\.md",
        r"\.py",
        r"\.txt",
        r"\.xlsx",
        r"\.csv",
        r"\.png",
    ]
    has_artifact = any(re.search(keyword, result, flags=re.I) for keyword in artifact_keywords)

    location_keywords = [
        r"путь",
        r"репозиторий",
        r"repo/",
        r"по пути",
        r"размещ[её]н",
        r"находится",
        r"директори",
        r"папк",
        r"рабоч",
        r"файл",
    ]
    has_location = any(re.search(keyword, result, flags=re.I) for keyword in location_keywords)

    artifact_location = ""
    location_match = re.search(r"\((?:где найти|where|путь|path):\s*([^)]+)\)", result, flags=re.I)
    if location_match:
        artifact_location = location_match.group(1).strip()
        result = re.sub(r"\s*\((?:где найти|where|путь|path):\s*([^)]+)\)\s*$", "", result).strip()

    path_candidates = _extract_path_candidates(result)
    explicit_artifact_path = _pick_primary_path(path_candidates)
    if explicit_artifact_path:
        artifact_location = explicit_artifact_path

    if not artifact_location:
        artifact_location = artifact_location_for_task(seed, task_idx)

    if explicit_artifact_path:
        for candidate in path_candidates:
            if not candidate.lower().startswith("repo/"):
                continue
            result = result.replace(f" Размещен в репозитории по пути {candidate}", "")
            result = result.replace(f" Размещен в репозитории по пути `{candidate}`", "")
            result = result.replace(f", размещенная в репозитории по пути `{candidate}`", "")
            result = result.replace(f", размещённая в репозитории по пути `{candidate}`", "")
            result = re.sub(
                rf"\.?\s*[^.`\n]*`?{re.escape(candidate)}`?",
                "",
                result,
                count=1,
            )
        result = re.sub(r"\s{2,}", " ", result).strip()

    if not has_artifact or not has_location:
        if not has_artifact:
            artifact_type = "Файл README.md"
        else:
            artifact_match = re.search(
                r"(файл|отчет|отч[её]т|код|скриншот|документ|текст|таблица|матрица|схема|план|презентация)"
                r"\s+([^\s,\.]+)?",
                result,
                flags=re.I,
            )
            artifact_type = artifact_match.group(0) if artifact_match else "Файл README.md"

        if explicit_artifact_path:
            result = re.sub(r"\s{2,}", " ", result).strip()
        elif not has_artifact and not has_location:
            result = f"{artifact_type} размещён по пути `{artifact_location}`."
        elif not has_artifact:
            result = f"{artifact_type}. {result} Артефакт размещён по пути `{artifact_location}`."
        elif not has_location:
            result = f"{result} Артефакт размещён по пути `{artifact_location}`."

        print(f"  ⚠️ Добавлен артефакт/локация в результат задачи {task_idx + 1}", file=sys.stderr, flush=True)

    return result, artifact_location


def is_generic_expected_artifact(result: str) -> bool:
    """Detect deliverable text that only points to a file without saying what is checked."""
    text = (result or "").strip()
    if not text:
        return True

    without_paths = RX_ARTIFACT_PATH.sub("", text)
    without_code = re.sub(r"`[^`]*`", "", without_paths)
    normalized = re.sub(r"\s+", " ", without_code).strip(" .").lower()
    if not normalized:
        return True

    generic_patterns = [
        r"^(артефакт|результат|документ)\s+размещ",
        r"^файл\s+readme\.md(?:\s+с\s+результатом\s+задачи)?\s+размещ",
        r"^файл\s+\w+(?:\.\w+)?\s+размещ",
    ]
    if any(re.search(pattern, normalized, flags=re.I) for pattern in generic_patterns):
        return True

    words = re.findall(r"[А-Яа-яЁёA-Za-z0-9]+", normalized)
    return len(words) <= 8 and "размещ" in normalized and ("путь" in normalized or "файл" in normalized)


def artifact_kind_from_task(task: PracticeTask) -> str:
    """Infer a concrete review subject from the task instead of using a generic placeholder."""
    text = " ".join(
        [
            task.title or "",
            task.goal or "",
            task.situation or "",
            task.expected_artifact or "",
        ]
    ).lower()
    if any(marker in text for marker in ("дорожн", "roadmap", "роадмап")):
        return "дорожной картой проекта"
    if any(marker in text for marker in ("зависим", "гант", "критическ", "срок")):
        return "планом зависимостей, критическим участком и выводом по срокам"
    if any(marker in text for marker in ("структур", "декомпоз", "wbs", "работ")):
        return "структурой работ и декомпозицией задач"
    if any(marker in text for marker in ("бэклог", "backlog", "приоритет")):
        return "разбором бэклога, приоритетами и обоснованием очередности"
    if any(marker in text for marker in ("выступ", "реч", "sermon")):
        return "структурой выступления и итоговыми тезисами"
    if any(marker in text for marker in ("матриц", "таблиц")):
        return "таблицей решений и обоснованием выбора"
    return "решением по задаче"


def describe_expected_artifact(task: PracticeTask, artifact_location: str, language: str) -> str:
    """Build a concrete expected-result contract for P2P review."""
    if language != "ru":
        return (
            "README.md document with the task solution: assumptions, decision, rationale and final conclusion. "
            f"The artifact is located at `{artifact_location}`."
        )

    artifact_kind = artifact_kind_from_task(task)
    task_context = " ".join(
        [
            task.title or "",
            task.goal or "",
            task.situation or "",
            task.constraints_or_risk or "",
        ]
    ).lower()
    stakeholder_tail = " для заказчика" if "заказчик" in task_context else ""
    return (
        f"Документ README.md с {artifact_kind}: содержит исходные допущения, решение, "
        f"обоснование выбора и итоговый вывод{stakeholder_tail}. "
        f"Артефакт размещён по пути `{artifact_location}`."
    )


def normalize_sentence(text: str) -> str:
    """Normalize one user-facing checklist or approach sentence."""
    normalized = re.sub(r"\s+", " ", (text or "").strip()).strip(" -")
    if not normalized:
        return ""
    if normalized[0].islower():
        normalized = normalized[0].upper() + normalized[1:]
    if normalized[-1] not in ".!?":
        normalized += "."
    return normalized


def is_observable_p2p_criterion(text: str) -> bool:
    """Return whether a criterion can be observed in peer review."""
    signals = [
        "содержит",
        "указан",
        "указаны",
        "есть",
        "присутствует",
        "присутствуют",
        "нет",
        "совпадает",
        "заполнен",
        "заполнены",
        "описан",
        "описаны",
        "размещен",
        "размещён",
        "добавлен",
        "добавлены",
        "оформлен",
        "оформлена",
        "перечислен",
        "перечислены",
        "зафиксирован",
        "зафиксированы",
        "учтен",
        "учтены",
        "обоснован",
        "обоснование",
        "путь",
        "файл",
        "раздел",
        "схема",
        "таблица",
        "презентация",
        "минимум",
        "по указанному пути",
        "в документе",
        "в таблице",
        "на схеме",
    ]
    normalized = (text or "").strip().lower()
    return len(normalized) >= 12 and any(signal in normalized for signal in signals)


def artifact_review_subject(expected_artifact: str, artifact_location: str) -> tuple[str, str]:
    """Infer checklist wording subject from artifact type."""
    text = f"{expected_artifact or ''} {artifact_location or ''}".lower()
    if any(ext in text for ext in (".png", ".svg", ".drawio", ".mmd")) or "схем" in text or "диаграм" in text:
        return "схеме", "Схема"
    if any(ext in text for ext in (".xlsx", ".csv")) or "таблиц" in text:
        return "таблице", "Таблица"
    if any(ext in text for ext in (".ppt", ".pptx")) or "презентац" in text:
        return "презентации", "Презентация"
    return "документе", "Документ"


def normalize_approach_bullets(
    bullets: list[str],
    theory_support: list[str],
    language: str,
    *,
    style_rewrite: StyleRewrite,
    token_set: TokenSet,
) -> list[str]:
    """Normalize approach bullets and add a theory anchor when needed."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for bullet in bullets:
        normalized = normalize_sentence(style_rewrite((bullet or "").strip(), language))
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)

    if not cleaned:
        cleaned = ["Зафиксируй решение по задаче и подготовь проверяемый артефакт."]

    needs_theory_anchor = bool(theory_support) and not any(
        any(token in bullet.lower() for token in token_set(topic))
        for bullet in cleaned
        for topic in theory_support
    )
    if needs_theory_anchor:
        theory_anchor = ", ".join(theory_support[:2])
        theory_bullet = style_rewrite(
            f"Проверь, что решение прямо опирается на темы из теории: {theory_anchor}.",
            language,
        )
        theory_bullet = normalize_sentence(theory_bullet)
        if len(cleaned) >= 6:
            cleaned[-1] = theory_bullet
        else:
            cleaned.append(theory_bullet)

    return cleaned[:6]


def ensure_p2p_criteria(
    criteria: list[str],
    artifact_location: str,
    expected_artifact: str,
    theory_support: list[str],
    language: str,
    *,
    style_rewrite: StyleRewrite,
) -> list[str]:
    """Make P2P criteria binary, observable and bound to the artifact."""
    normalized: list[str] = []
    seen: set[str] = set()
    for criterion in criteria or []:
        item = normalize_sentence(style_rewrite((criterion or "").strip(), language))
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)

    subject_case, subject_title = artifact_review_subject(expected_artifact, artifact_location)
    if artifact_location and not any(
        signal in " ".join(normalized).lower()
        for signal in ("по указанному пути", "размещ", artifact_location.lower())
    ):
        normalized.append(f"Артефакт размещён по указанному пути `{artifact_location}`.")

    observable_count = sum(1 for item in normalized if is_observable_p2p_criterion(item))
    filler_candidates: list[str] = []

    if subject_case == "таблице":
        filler_candidates.append("В таблице заполнены все обязательные строки и столбцы без пустых ключевых ячеек.")
    elif subject_case == "схеме":
        filler_candidates.append("На схеме подписаны все ключевые элементы и связи между ними.")
    elif subject_case == "презентации":
        filler_candidates.append("В презентации выделены ключевые тезисы, опорные слайды и итоговый вывод.")
    else:
        filler_candidates.append("В документе есть отдельные разделы с решением, аргументацией и итоговым выводом.")

    if theory_support:
        theory_anchor = ", ".join(theory_support[:2])
        filler_candidates.append(f"{subject_title} явно использует понятия из теории: {theory_anchor}.")

    filler_candidates.append(
        "Формулировки в артефакте конкретны и позволяют другому участнику проверить результат без устных пояснений."
    )

    for candidate in filler_candidates:
        if len(normalized) >= 5 and observable_count >= 3:
            break
        key = candidate.lower()
        if key in seen:
            continue
        normalized.append(candidate)
        seen.add(key)
        if is_observable_p2p_criterion(candidate):
            observable_count += 1

    return normalized[:5]


def extract_sjm_task_anchors(sjm: str) -> list[str]:
    """Extract compact SJM anchors that must remain visible in practice tasks."""
    text = (sjm or "").strip()
    low = text.lower()
    if not low:
        return []

    anchors: list[str] = []
    if "заказчик" in low:
        anchors.append("заказчик")

    role_match = re.search(r"ты\s+[—-]\s+([^\.!\n]+)", low)
    if role_match:
        role = role_match.group(1).strip(" ,")
        if role:
            anchors.append(role)

    for pattern in (
        r"\b\d+\s*(?:минут[аы]?|час(?:а|ов)?|дн(?:я|ей)?|недел[ьяи]?|месяц(?:а|ев)?)\b",
        r"бюджет",
        r"релиз",
        r"срок",
        r"договор[её]н",
        r"план",
    ):
        match = re.search(pattern, low)
        if match:
            anchors.append(match.group(0))

    dedup: list[str] = []
    for anchor in anchors:
        anchor = anchor.strip()
        if anchor and anchor not in dedup:
            dedup.append(anchor)
    return dedup[:5]
