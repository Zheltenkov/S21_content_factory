"""Проверка, что чек-лист не добавляет требования поверх README."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from content_factory.audit.artifacts import ArtifactTextIndex, artifact_refs_with_extensions
from content_factory.audit.checklist_matching import ChecklistQuestion
from content_factory.audit.domain import Severity


EXERCISE_HEADING_RE = re.compile(
    r"(?im)^#{1,6}\s*(?:exercise|task|quest|chapter|задани[ея]|упражнени[ея])\s*0*(\d+)\b"
)
QUESTION_NUMBER_RE = re.compile(r"(?i)(?:exercise|task|quest|chapter|задани[ея]|упражнени[ея])\s*0*(\d+)")
SELF_JOIN_ID_ORDER_RE = re.compile(r"\b(?P<left>[a-z]\w*)\.id\s*(?P<op>>|<)\s*(?P<right>[a-z]\w*)\.id\b", re.IGNORECASE)
QUOTED_VALUE_RE = re.compile(r'"([^"\n]{2,120})"')
RESOURCE_EXTENSIONS = (
    "dox",
    "docx",
    "pcapng",
    "pcap",
    "json",
    "csv",
    "tsv",
    "xlsx",
    "xls",
    "zip",
    "tar",
    "gz",
    "png",
    "jpg",
    "jpeg",
    "svg",
    "pdf",
    "txt",
)
FILE_REF_RE = re.compile(
    rf"`([^`\n]+\.(?:{'|'.join(RESOURCE_EXTENSIONS)}))`|"
    rf"(?<![\w./\\-])([A-Za-zА-Яа-яЁё0-9_./\\<>-]+\.(?:{'|'.join(RESOURCE_EXTENSIONS)}))\b",
    re.IGNORECASE,
)
LOOSE_DOC_REF_RE = re.compile(
    r"(?<!\w)((?:ex|task|quest|day)\d{1,3}[A-Za-zА-Яа-яЁё0-9_./\\ <>\-]*\.(?:docx|dox))\b",
    re.IGNORECASE,
)
GROUNDING_COMMANDS = {
    "whoami",
    "id",
    "uname",
    "ls",
    "hostname",
    "ifconfig",
    "ipconfig",
    "tcpdump",
    "tshark",
}
SQL_CONDITION_RE = re.compile(
    r"\b(?:[a-z_]\w*\.)?[a-z_]\w*\s*(?:=|<>|!=|>=|<=|>|<)\s*"
    r"(?:'[^'\n]{1,80}'|\"[^\"\n]{1,80}\"|\d+(?:\.\d+)?|(?:[a-z_]\w*\.)?[a-z_]\w*)",
    re.IGNORECASE,
)
SQL_MARKER_RE = re.compile(r"\b(select|where|join|from|order\s+by|group\s+by|having|insert|update|delete)\b", re.IGNORECASE)
PIZZERIA_NAMES = {
    "pizza hut",
    "dominos",
    "domino's",
    "papa johns",
    "papa john's",
}
ARTIFACT_CONTENT_RE = re.compile(
    r"\b(expected|contains?|included|attached|provided|dump|capture|pcap|trace|output|commands?)\b|"
    r"(\bожида\w*\b|\bсодерж\w*\b|\bприлож\w*\b|\bвложен\w*\b|\bдамп\w*\b|\bзахват\w*\b|\bвывод\w*\b|\bкоманд\w*\b)",
    re.IGNORECASE,
)
ARTIFACT_CONTENT_EXTENSIONS = ("pcapng", "pcap", "dump", "log", "txt", "csv", "tsv", "json", "xml", "sql")


@dataclass(frozen=True)
class ChecklistGroundingIssue:
    """Конкретное требование чек-листа, не подтверждённое текстом задания."""

    question_name: str
    issue_type: str
    detail: str
    severity: Severity
    evidence: str


def assess_checklist_grounding(
    questions: list[ChecklistQuestion],
    readme_text: str,
    available_files: Iterable[str] | None = None,
    artifact_text_index: ArtifactTextIndex | None = None,
) -> list[ChecklistGroundingIssue]:
    """Ищет узкие, но сильные признаки расхождения README и чек-листа."""

    readme_sections = _split_readme_sections(readme_text)
    available_file_refs = tuple(available_files or ())
    issues: list[ChecklistGroundingIssue] = []
    for question in questions:
        exercise_number = _extract_question_number(question.name)
        readme_section = readme_sections.get(exercise_number, "") if exercise_number is not None else ""

        if readme_section:
            issues.extend(_find_self_join_ordering_issues(question, readme_section))
            issues.extend(_find_duplicate_name_result_issues(question, readme_section))
            issues.extend(_find_sql_condition_issues(question, readme_section))
            issues.extend(_find_expected_output_semantic_issues(question, readme_section))
        issues.extend(_find_artifact_grounding_issues(question, readme_text, available_file_refs))
        artifact_content_issues = _find_artifact_content_issues(
            question,
            readme_section or readme_text,
            available_file_refs,
            artifact_text_index,
        )
        command_issues = _find_command_grounding_issues(question, readme_section or readme_text)
        issues.extend(_suppress_commands_covered_by_artifact_issue(command_issues, artifact_content_issues))
        issues.extend(artifact_content_issues)
    return _dedupe_issues(issues)


def _find_self_join_ordering_issues(
    question: ChecklistQuestion,
    readme_section: str,
) -> list[ChecklistGroundingIssue]:
    """Ловит скрытое требование к порядку пары через `p1.id > p2.id`."""

    checklist_text = question.description_text
    if not _readme_describes_pair_result(readme_section):
        return []

    compact_readme = _compact_sql_text(readme_section)
    issues: list[ChecklistGroundingIssue] = []
    for match in SELF_JOIN_ID_ORDER_RE.finditer(checklist_text):
        predicate = match.group(0)
        if _compact_sql_text(predicate) in compact_readme:
            continue
        issues.append(
            ChecklistGroundingIssue(
                question_name=question.name,
                issue_type="ungrounded_self_join_order",
                detail=(
                    "Чек-лист фиксирует порядок пары через сравнение идентификаторов, "
                    "но README не описывает это как требование."
                ),
                severity=Severity.MAJOR,
                evidence=predicate,
            )
        )
    return issues


def _find_duplicate_name_result_issues(
    question: ChecklistQuestion,
    readme_section: str,
) -> list[ChecklistGroundingIssue]:
    """Ловит повторяющиеся строки результата, когда README просит список названий."""

    if not _readme_asks_for_pizzeria_names(readme_section):
        return []

    values = [value.strip() for value in QUOTED_VALUE_RE.findall(question.description_text)]
    repeated_values = [(value, count) for value, count in Counter(values).items() if count >= 3]
    if not repeated_values:
        return []

    value, count = sorted(repeated_values, key=lambda item: (-item[1], item[0]))[0]
    return [
        ChecklistGroundingIssue(
            question_name=question.name,
            issue_type="suspicious_duplicate_name_result",
            detail=(
                "Чек-лист ожидает несколько одинаковых строк результата, "
                "хотя README формулирует результат как список названий."
            ),
            severity=Severity.MAJOR,
            evidence=f'{value} × {count}',
        )
    ]


def _find_artifact_grounding_issues(
    question: ChecklistQuestion,
    readme_text: str,
    available_files: Iterable[str],
) -> list[ChecklistGroundingIssue]:
    """Сверяет ресурсы и ожидаемые файлы чек-листа с README и вложениями проекта."""

    checklist_refs = _extract_file_refs(f"{question.name} {question.description_text}")
    if not checklist_refs:
        return []

    readme_refs = _extract_file_refs(readme_text)
    attached_refs = tuple(_basename(file_ref) for file_ref in available_files)
    known_refs = tuple(dict.fromkeys([*readme_refs, *attached_refs]))
    issues: list[ChecklistGroundingIssue] = []
    for expected in checklist_refs:
        if _file_ref_present(expected, known_refs):
            continue
        similar = _find_similar_file_ref(expected, known_refs)
        if similar is not None:
            issues.append(
                ChecklistGroundingIssue(
                    question_name=question.name,
                    issue_type="expected_file_name_mismatch",
                    detail=(
                        "Чек-лист ожидает файл с именем, которое похоже на ресурс из README или вложений, "
                        "но не совпадает с ним буквально."
                    ),
                    severity=Severity.MAJOR,
                    evidence=f"{expected} vs {similar}",
                )
            )
            continue
        if _is_required_resource_ref(expected, question.description_text):
            issues.append(
                ChecklistGroundingIssue(
                    question_name=question.name,
                    issue_type="ungrounded_resource",
                    detail="Чек-лист ссылается на ресурс или файл, который не упомянут в README и не найден среди файлов проекта.",
                    severity=Severity.MAJOR,
                    evidence=expected,
                )
            )
    return issues


def _find_command_grounding_issues(
    question: ChecklistQuestion,
    readme_section: str,
) -> list[ChecklistGroundingIssue]:
    """Проверяет, что команды из чек-листа действительно описаны в README."""

    checklist_commands = _extract_commands(question.description_text)
    if not checklist_commands:
        return []
    readme_commands = _extract_commands(readme_section)
    issues: list[ChecklistGroundingIssue] = []
    for command in sorted(checklist_commands - readme_commands):
        issues.append(
            ChecklistGroundingIssue(
                question_name=question.name,
                issue_type="ungrounded_command",
                detail="Чек-лист проверяет команду или артефакт выполнения, которого нет в соответствующем задании README.",
                severity=Severity.MAJOR,
                evidence=command,
            )
        )
    return issues


def _find_artifact_content_issues(
    question: ChecklistQuestion,
    readme_context: str,
    available_files: Iterable[str],
    artifact_text_index: ArtifactTextIndex | None,
) -> list[ChecklistGroundingIssue]:
    """Проверяет ожидаемые маркеры внутри приложенных артефактов."""

    if artifact_text_index is None or not ARTIFACT_CONTENT_RE.search(question.description_text):
        return []

    artifact_refs = _artifact_refs_for_question(question.name, question.description_text, readme_context, available_files)
    if not artifact_refs:
        return []

    expected_markers = _extract_expected_artifact_markers(question.description_text)
    if not expected_markers:
        return []

    issues: list[ChecklistGroundingIssue] = []
    missing_labels: list[str] = []
    for marker in expected_markers:
        refs_with_text = [ref for ref in artifact_refs if artifact_text_index.has_text_for(ref)]
        if not refs_with_text:
            continue
        if _artifact_expectation_satisfied(question.description_text, marker, refs_with_text, artifact_text_index):
            continue
        expected_label = f"вывод {marker}" if _expects_command_output(question.description_text, marker) else marker
        if expected_label not in missing_labels:
            missing_labels.append(expected_label)
    if missing_labels:
        refs_with_text = [ref for ref in artifact_refs if artifact_text_index.has_text_for(ref)]
        issues.append(
            ChecklistGroundingIssue(
                question_name=question.name,
                issue_type="artifact_missing_expected_text",
                detail=(
                    "Чек-лист ожидает маркер или команду внутри приложенного артефакта, "
                    "но быстрый анализ содержимого этого маркера не нашёл."
                ),
                severity=Severity.MAJOR,
                evidence=f"{', '.join(missing_labels[:5])} не найден в {', '.join(refs_with_text[:3])}",
            )
        )
    return issues


def _suppress_commands_covered_by_artifact_issue(
    command_issues: list[ChecklistGroundingIssue],
    artifact_issues: list[ChecklistGroundingIssue],
) -> list[ChecklistGroundingIssue]:
    """Не дублирует общий сигнал команды, если есть более сильный сигнал по артефакту."""

    artifact_evidence = " ".join(issue.evidence.lower() for issue in artifact_issues)
    return [
        issue
        for issue in command_issues
        if issue.issue_type != "ungrounded_command" or issue.evidence.lower() not in artifact_evidence
    ]


def _artifact_expectation_satisfied(
    checklist_text: str,
    marker: str,
    artifact_refs: Iterable[str],
    artifact_text_index: ArtifactTextIndex,
) -> bool:
    """Проверяет маркер в артефакте с учётом требования к выводу команды."""

    if _expects_command_output(checklist_text, marker):
        return any(
            _artifact_text_has_command_output(text, marker)
            for ref in artifact_refs
            for text in artifact_text_index.texts_for(ref)
        )
    return any(artifact_text_index.contains(ref, marker) for ref in artifact_refs)


def _find_sql_condition_issues(
    question: ChecklistQuestion,
    readme_section: str,
) -> list[ChecklistGroundingIssue]:
    """Ловит SQL-предикаты из чек-листа, которые не описаны в README."""

    checklist_text = _strip_resource_refs_for_sql(question.description_text)
    if not SQL_MARKER_RE.search(checklist_text):
        return []

    compact_readme = _compact_sql_text(readme_section)
    issues: list[ChecklistGroundingIssue] = []
    for match in SQL_CONDITION_RE.finditer(checklist_text):
        predicate = match.group(0).strip()
        if SELF_JOIN_ID_ORDER_RE.search(predicate):
            continue
        if _sql_condition_semantically_described(predicate, readme_section):
            continue
        if _compact_sql_text(predicate) in compact_readme:
            continue
        issues.append(
            ChecklistGroundingIssue(
                question_name=question.name,
                issue_type="ungrounded_sql_condition",
                detail="Чек-лист фиксирует SQL-условие, которое не сформулировано в README.",
                severity=Severity.MAJOR,
                evidence=predicate,
            )
        )
    return issues[:3]


def _find_expected_output_semantic_issues(
    question: ChecklistQuestion,
    readme_section: str,
) -> list[ChecklistGroundingIssue]:
    """Проверяет очевидное смысловое несовпадение ожидаемого вывода и формулировки README."""

    if not _readme_asks_for_pizza_names_only(readme_section):
        return []
    values = [value.strip().lower() for value in QUOTED_VALUE_RE.findall(question.description_text)]
    pizzeria_values = [value for value in values if value in PIZZERIA_NAMES]
    if not pizzeria_values:
        return []
    return [
        ChecklistGroundingIssue(
            question_name=question.name,
            issue_type="expected_output_semantic_mismatch",
            detail="README просит названия пицц, а чек-лист ожидает значение, похожее на название пиццерии.",
            severity=Severity.MAJOR,
            evidence=", ".join(sorted(set(pizzeria_values))),
        )
    ]


def _artifact_refs_for_question(
    question_name: str,
    checklist_text: str,
    readme_context: str,
    available_files: Iterable[str],
) -> tuple[str, ...]:
    """Находит артефакты, к которым относится утверждение чек-листа."""

    explicit_refs = artifact_refs_with_extensions(_extract_file_refs(checklist_text), ARTIFACT_CONTENT_EXTENSIONS)
    if explicit_refs:
        return _prefer_refs_for_question(question_name, explicit_refs)

    lowered = f"{checklist_text}\n{readme_context}".lower()
    if not re.search(r"\b(pcap|pcapng|capture|dump|trace|log)\b|(\bдамп\w*\b|\bзахват\w*\b)", lowered):
        return ()
    extensions = ARTIFACT_CONTENT_EXTENSIONS
    if re.search(r"\b(pcap|pcapng|capture)\b|(\bзахват\w*\b)", lowered):
        extensions = ("pcap", "pcapng")
    elif re.search(r"\b(log|trace)\b", lowered):
        extensions = ("log", "txt")
    refs = artifact_refs_with_extensions(available_files, extensions)
    return _prefer_refs_for_question(question_name, refs)


def _prefer_refs_for_question(question_name: str, refs: Iterable[str]) -> tuple[str, ...]:
    """Если в пути артефакта есть номер задания, выбирает артефакты той же задачи."""

    result = tuple(dict.fromkeys(refs))
    number = _extract_question_number(question_name)
    if number is None:
        return result
    pattern = re.compile(rf"(?:task|quest|exercise|ex)[_-]?0?{number}(?!\d)|[/\\]0?{number}[/\\]", re.IGNORECASE)
    preferred = tuple(ref for ref in result if pattern.search(ref))
    return preferred or result


def _extract_expected_artifact_markers(checklist_text: str) -> tuple[str, ...]:
    """Достаёт маркеры, которые чек-лист ожидает увидеть в приложенном артефакте."""

    markers: list[str] = []
    for command in _ordered_commands(_extract_commands(checklist_text)):
        markers.append(command)
    for value in QUOTED_VALUE_RE.findall(checklist_text):
        normalized = value.strip()
        if 2 <= len(normalized) <= 80 and normalized.lower() not in {item.lower() for item in markers}:
            markers.append(normalized)
    return tuple(markers)


def _ordered_commands(commands: Iterable[str]) -> tuple[str, ...]:
    """Стабильно сортирует команды, отдавая приоритет наиболее диагностичным маркерам."""

    priority = ("whoami", "id", "uname", "ls", "hostname", "ifconfig", "ipconfig", "tcpdump", "tshark")
    command_set = set(commands)
    return tuple([command for command in priority if command in command_set] + sorted(command_set - set(priority)))


def _expects_command_output(checklist_text: str, marker: str) -> bool:
    """Понимает, что чек-лист ждёт не просто команду, а её вывод в артефакте."""

    if marker.lower() not in GROUNDING_COMMANDS:
        return False
    lowered = checklist_text.lower()
    return (
        "command output" in lowered
        or "result of" in lowered
        or "output from" in lowered
        or "reverse shell" in lowered
        or "shell" in lowered
        or "вывод команд" in lowered
        or "результат команд" in lowered
        or "след" in lowered and "команд" in lowered
    )


def _artifact_text_has_command_output(text: str, command: str) -> bool:
    """Отличает упоминание команды от правдоподобного вывода команды."""

    lowered = text.lower()
    command_lower = command.lower()
    for match in re.finditer(rf"(?<![\w-]){re.escape(command_lower)}(?![\w-])", lowered):
        tail = lowered[match.end() : match.end() + 160]
        if _tail_contains_command_output(tail, command_lower):
            return True
    return False


def _tail_contains_command_output(tail: str, command: str) -> bool:
    """Ищет содержательный фрагмент после команды до следующего маркера команды."""

    cut_points = [len(tail)]
    for known_command in GROUNDING_COMMANDS:
        match = re.search(rf"(?<![\w-]){re.escape(known_command)}(?![\w-])", tail)
        if match is not None:
            cut_points.append(match.start())
    segment = tail[: min(cut_points)]
    segment = re.sub(r"\b(result of|command output|output from)\b\s*:?", " ", segment)
    tokens = re.findall(r"[a-zа-яё0-9_./-]+", segment)
    noise = {
        "get",
        "post",
        "http",
        "host",
        "random",
        "payload",
        "example",
        "test",
        "tcp",
        "udp",
        "result",
        "of",
    }
    meaningful = [token for token in tokens if len(token) >= 2 and token not in noise]
    return bool(meaningful)


def _split_readme_sections(readme_text: str) -> dict[int, str]:
    """Разбивает README на секции заданий по markdown-заголовкам."""

    matches = list(EXERCISE_HEADING_RE.finditer(readme_text))
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        number = int(match.group(1))
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(readme_text)
        sections.setdefault(number, readme_text[start:end])
    return sections


def _extract_question_number(name: str) -> int | None:
    """Достаёт номер задания из имени пункта чек-листа."""

    match = QUESTION_NUMBER_RE.search(name)
    return int(match.group(1)) if match else None


def _readme_describes_pair_result(readme_section: str) -> bool:
    """Понимает, что секция README описывает результат из пар сущностей."""

    lowered = readme_section.lower()
    return (
        ("person_name1" in lowered and "person_name2" in lowered)
        or "pairs of" in lowered
        or "пары" in lowered
        or "парами" in lowered
    )


def _readme_asks_for_pizzeria_names(readme_section: str) -> bool:
    """Понимает формулировку «вернуть названия пиццерий», а не конкретные пиццы."""

    lowered = readme_section.lower()
    asks_for_names = (
        "names of pizzerias" in lowered
        or "name of pizzerias" in lowered
        or "названия пиццерий" in lowered
        or "название пиццерий" in lowered
    )
    asks_for_specific_pizzas = (
        "pizza_name" in lowered
        or "names of pizzas" in lowered
        or bool(re.search(r"\bназвания\s+пицц(?:ы)?\b", lowered))
    )
    return asks_for_names and not asks_for_specific_pizzas


def _readme_asks_for_pizza_names_only(readme_section: str) -> bool:
    """Отделяет запрос названий пицц от запроса пиццерий или пары «пицца + пиццерия»."""

    lowered = readme_section.lower()
    asks_for_pizzas = (
        "names of pizzas" in lowered
        or "pizza names" in lowered
        or bool(re.search(r"\bназвания\s+пицц(?:ы)?\b", lowered))
    )
    asks_for_pizzerias = (
        "names of pizzerias" in lowered
        or "name of pizzerias" in lowered
        or "pizzeria" in lowered
        or "пиццер" in lowered
    )
    return asks_for_pizzas and not asks_for_pizzerias


def _compact_sql_text(value: str) -> str:
    """Сжимает SQL-фрагмент для сравнения без влияния пробелов."""

    return re.sub(r"\s+", "", value.lower())


def _sql_condition_semantically_described(predicate: str, readme_section: str) -> bool:
    """Не считаем SQL-предикат лишним, если README явно описывает его словами."""

    normalized = predicate.lower()
    lowered_readme = readme_section.lower()
    same_field_match = re.search(
        r"\b(?:[a-z_]\w*\.)?(?P<left>[a-z_]\w*)\s*=\s*(?:[a-z_]\w*\.)?(?P<right>[a-z_]\w*)\b",
        normalized,
    )
    if same_field_match and same_field_match.group("left") == same_field_match.group("right"):
        field = same_field_match.group("left")
        return (
            f"same {field}" in lowered_readme
            or f"one {field}" in lowered_readme
            or f"одинаков" in lowered_readme and field in lowered_readme
            or f"совпада" in lowered_readme and field in lowered_readme
        )
    return False


def _strip_resource_refs_for_sql(text: str) -> str:
    """Убирает имена файлов и шаблонные плейсхолдеры, чтобы они не выглядели как SQL-операторы."""

    without_files = FILE_REF_RE.sub(" ", text)
    return re.sub(r"<[^>\n]+>", " ", without_files)


def _extract_file_refs(text: str) -> tuple[str, ...]:
    """Достаёт ресурсные файлы из README или чек-листа."""

    refs: list[str] = []
    matches = [match.group(1) or match.group(2) or "" for match in FILE_REF_RE.finditer(text)]
    matches.extend(match.group(1) for match in LOOSE_DOC_REF_RE.finditer(text))
    for raw_ref in matches:
        ref = _normalize_file_ref_text(raw_ref.strip())
        if not ref:
            continue
        basename = _basename(ref)
        if basename and basename.lower() not in {item.lower() for item in refs}:
            refs.append(basename)
    return tuple(refs)


def _is_required_resource_ref(file_ref: str, checklist_text: str) -> bool:
    """Отделяет приложенные ресурсы от файлов, которые студент должен создать сам."""

    extension = _extension(file_ref)
    if extension in {"dox", "docx", "pcapng", "pcap", "xlsx", "xls", "zip", "tar", "gz", "png", "jpg", "jpeg", "svg", "pdf"}:
        return True
    lowered = checklist_text.lower()
    return (
        extension in {"json", "csv", "tsv", "txt"}
        and re.search(r"\b(provided|attached|contains|archive|dataset|capture|resource|input)\b|"
                      r"(прилож|архив|датасет|ресурс|дамп|входн)", lowered) is not None
    )


def _extract_commands(text: str) -> set[str]:
    """Достаёт ограниченный набор команд, которые важны как артефакты задания."""

    lowered = text.lower()
    commands: set[str] = set()
    for command in GROUNDING_COMMANDS:
        if command in {"id", "ls", "uname"}:
            pattern = (
                rf"`\s*{re.escape(command)}(?:\s|`)"
                rf"|\bresult\s+of\s*:\s*{re.escape(command)}\b"
                rf"|\bcommand\s+output\s+from\s+`?\s*{re.escape(command)}\b"
            )
        else:
            pattern = rf"(?<![\w-]){re.escape(command)}(?![\w-])"
        if re.search(pattern, lowered):
            commands.add(command)
    return commands


def _file_ref_present(expected: str, known_refs: Iterable[str]) -> bool:
    """Проверяет точное наличие файла по базовому имени."""

    expected_name = _basename(expected).lower()
    return any(_basename(ref).lower() == expected_name for ref in known_refs)


def _find_similar_file_ref(expected: str, known_refs: Iterable[str]) -> str | None:
    """Ищет похожее имя ресурса с тем же расширением."""

    expected_name = _basename(expected)
    expected_key = _artifact_key(expected_name)
    expected_ext = _extension(expected_name)
    candidates: list[tuple[float, str]] = []
    for known_ref in known_refs:
        known_name = _basename(known_ref)
        if _extension(known_name) != expected_ext or known_name.lower() == expected_name.lower():
            continue
        known_key = _artifact_key(known_name)
        if known_key == expected_key:
            return known_name
        score = SequenceMatcher(None, expected_key, known_key).ratio()
        if score >= 0.82:
            candidates.append((score, known_name))
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (-item[0], item[1]))[0][1]


def _artifact_key(value: str) -> str:
    """Нормализует имя ресурса для поиска близких вариантов."""

    stem = _basename(value).rsplit(".", 1)[0].lower()
    stem = re.sub(r"<[^>]+>", "", stem)
    stem = stem.replace("use case", "uc").replace("usecase", "uc").replace("use_case", "uc")
    stem = stem.replace("produse caset prefix", "")
    stem = stem.replace("product prefix", "").replace("productprefix", "").replace("prefix", "")
    stem = stem.replace("префикс продукта", "").replace("префикспродукта", "").replace("префикс", "")
    return re.sub(r"[^a-zа-яё0-9]+", "", stem)


def _extension(value: str) -> str:
    """Возвращает расширение файла без точки."""

    name = _basename(value).lower()
    return name.rsplit(".", 1)[-1] if "." in name else ""


def _basename(value: str) -> str:
    """Возвращает имя файла без каталогов, поддерживая Windows- и POSIX-разделители."""

    normalized = _normalize_file_ref_text(value)
    return re.split(r"[\\/]", normalized.strip())[-1].strip()


def _normalize_file_ref_text(value: str) -> str:
    """Убирает Markdown-экранирование, которое не является частью имени файла."""

    return (
        value.strip()
        .replace("\\_", "_")
        .replace("\\-", "-")
        .replace("\\.", ".")
        .strip("`'\".,;)")
    )


def _dedupe_issues(issues: list[ChecklistGroundingIssue]) -> list[ChecklistGroundingIssue]:
    """Убирает повторы, если один и тот же сигнал найден несколькими правилами."""

    result: list[ChecklistGroundingIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        key = (issue.question_name, issue.issue_type, issue.evidence)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result
