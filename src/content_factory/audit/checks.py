"""Проверяющие модули для критериев аудита."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urldefrag, urlparse

import yaml

from content_factory.audit.artifacts import build_artifact_text_index
from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _checked_at_from_record,
    _enum_or_default,
    _external_check_error,
    _finding,
    _first_result_item,
    _first_source_url,
    _hash_cache_key,
    _model_context_priority,
    _model_text,
    _optional_model_text,
    _parse_confidence,
    _parse_optional_int,
    _result_items,
    _severity_from_verdict,
    _source_summary,
    _sources_from_item,
    _support_status_from_verdict,
    _verdict_from_model_value,
)
from content_factory.audit.checklist_grounding import assess_checklist_grounding
from content_factory.audit.checklist_matching import (
    assess_checklist_description_quality,
    extract_checklist_questions,
    match_checklist_to_readme,
)
from content_factory.audit.dependencies import (
    CompatibilityIssue,
    DependencyCandidate,
    DependencyMetadata,
    DependencyRegistryClient,
    DependencyRegistryError,
    dependency_cache_key,
    dependency_identity,
    extract_dependency_candidates,
    find_compatibility_issues,
    is_pinned_outdated,
    is_unbounded_spec,
    metadata_from_record,
    metadata_to_record,
)
from content_factory.audit.domain import (
    ContentFile,
    ContentUnit,
    Criterion,
    EntityType,
    Evidence,
    ExtractedEntity,
    Finding,
    Severity,
    TextLocation,
    Verdict,
)
from content_factory.audit.fact_claims import (
    FactCheckerPerplexity,
    ReadmeFactActualityChecker,
)
from content_factory.audit.image_rights import (
    image_evidence_queries,
    image_rights_signals,
    is_decorative_image,
    read_image_dimensions,
)
from content_factory.audit.market_fit_signals import (
    _first_market_location,
    _market_fit_evidence,
    _market_fit_recommendation,
    _market_fit_signal_count,
    _market_fit_signals,
    _market_fit_verdict,
    _merge_market_signals,
)
from content_factory.audit.openrouter import OpenRouterError
from content_factory.audit.regional_availability import (
    RegionalAvailabilityMatch,
    load_regional_availability_rules,
    match_regional_availability,
)
from content_factory.audit.rights import (
    DATASET_RE,
    MANIFEST_NAMES,
    CodeMatch,
    RightsSignal,
    grade_rights_signal,
    license_policy,
    resolve_dependency_licenses,
    scan_project_licenses,
)
from content_factory.audit.tech_freshness import (
    TechFreshnessChecker,
    TechnologyFreshnessChecker,  # noqa: F401 — реэкспорт совместимого алиаса для тестов
)
from content_factory.audit.text_utils import normalize_for_match
from content_factory.audit.url_helpers import (
    _check_url,
    _is_inside,
    _is_redirect_chain_error,
    _is_transient_http_status,
    _redirect_smells_like_rot,
    _url_policy_error,
)

MODEL_RUBRIC_ALLOWED_CRITERIA = {Criterion.WORKLOAD}






class StructureChecker(BaseChecker):
    """Проверяет наличие минимальной структуры учебного проекта."""

    name = "structure_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        findings: list[Finding] = []
        file_names = {file.relative_path.lower() for file in unit.files}
        has_readme = any(path.startswith("readme") and path.endswith(".md") for path in file_names)
        has_checklist = any(path.startswith("check-list") and path.endswith((".yml", ".yaml")) for path in file_names)
        if not has_readme:
            findings.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.READABILITY,
                    Severity.MAJOR,
                    Verdict.FAIL,
                    0.95,
                    None,
                    None,
                    [Evidence(title="Структура", detail="В единице контента не найден README*.md.")],
                    "Добавить основной README или проверить, что на вход передана корректная папка проекта.",
                    True,
                )
            )
        if not has_checklist:
            findings.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.CHECKLIST_ALIGNMENT,
                    Severity.MAJOR,
                    Verdict.FAIL,
                    0.95,
                    None,
                    None,
                    [Evidence(title="Структура", detail="В единице контента не найден check-list.yml или check-list.yaml.")],
                    "Добавить чек-лист проверки или исключить критерий соответствия чек-листу для этой единицы.",
                    True,
                )
            )
        return findings


class BrokenUrlSyntaxChecker(BaseChecker):
    """Ловит синтаксически сломанные URL до извлечения и сетевой проверки."""

    name = "broken_url_syntax_checker"
    BROKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("slash_backslash", re.compile(r"\bhttps?:/\\[^\s<>)\]}\"']+", re.IGNORECASE)),
        ("scheme_backslash", re.compile(r"\bhttps?:\\[^\s<>)\]}\"']+", re.IGNORECASE)),
        ("single_slash", re.compile(r"\bhttps?:/(?![/\\])[^\s<>)\]}\"']+", re.IGNORECASE)),
        ("missing_colon", re.compile(r"\bhttps?//[^\s<>)\]}\"']+", re.IGNORECASE)),
        ("space_after_scheme", re.compile(r"\bhttps?://\s+[^\s<>)\]}\"']+", re.IGNORECASE)),
        ("space_before_dot", re.compile(r"\bhttps?://[^\s/<>)]*\s+\.[^\s<>)\]}\"']+", re.IGNORECASE)),
        ("space_after_dot", re.compile(r"\bhttps?://[^\s/<>)]*\.\s+[^\s<>)\]}\"']+", re.IGNORECASE)),
        ("split_domain", re.compile(r"\bhttps?://[^\s/.<>)]{2,}\s+[^\s/<>)]*\.[^\s<>)\]}\"']+", re.IGNORECASE)),
    )

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        findings: list[Finding] = []
        seen: set[tuple[str, int, str]] = set()
        for file in unit.files:
            if not self._is_text_file(file):
                continue
            for line_number, line in enumerate(file.text.splitlines(), start=1):
                for issue_type, pattern in self.BROKEN_PATTERNS:
                    for match in pattern.finditer(line):
                        quote = match.group(0).strip().rstrip(".,;")
                        key = (file.relative_path, line_number, quote.lower())
                        if key in seen:
                            continue
                        seen.add(key)
                        findings.append(self._finding(unit, file.relative_path, line_number, quote, issue_type))
        return findings

    def _finding(self, unit: ContentUnit, file_path: str, line_number: int, quote: str, issue_type: str) -> Finding:
        """Создаёт детерминированную находку по опечатке в URL."""

        return _finding(
            unit,
            self.name,
            Criterion.LINKS,
            Severity.MAJOR,
            Verdict.FAIL,
            0.96,
            quote,
            TextLocation(file_path=file_path, line_start=line_number, line_end=line_number),
            [Evidence(title="Синтаксис ссылки", detail=f"URL записан с ошибкой и не может быть проверен как обычная ссылка: {quote}")],
            "Исправить схему ссылки и убрать лишние пробелы: ожидаемый формат — https://domain/path.",
            False,
            extra={"issue_type": "broken_url_syntax", "pattern": issue_type},
        )

    def _is_text_file(self, file: ContentFile) -> bool:
        """Ограничивает проверку текстовыми материалами, где встречаются ссылки."""

        name = Path(file.relative_path).name.lower()
        return file.kind in {"readme", "material", "checklist", "text"} or name.endswith((".md", ".txt", ".yml", ".yaml"))


class MarkdownStructureChecker(BaseChecker):
    """Проверяет структурные ошибки Markdown: заголовки, якоря, главы и списки."""

    name = "markdown_structure_checker"
    HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
    NUMBERED_LIST_RE = re.compile(r"^(\s*)(\d+)([.)])\s+\S")
    CHAPTER_RE = re.compile(r"\bchapter\s+([IVXLCDM]+)\b", re.IGNORECASE)
    ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        findings: list[Finding] = []
        seen: set[tuple[str, int, str, str]] = set()
        for file in unit.files:
            if not self._is_markdown_file(file):
                continue
            file_findings = [
                *self._heading_findings(unit, file),
                *self._numbered_list_findings(unit, file),
            ]
            for finding in file_findings:
                key = self._dedupe_key(finding)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(finding)
        return findings

    def _heading_findings(self, unit: ContentUnit, file: ContentFile) -> list[Finding]:
        """Проверяет повтор заголовков, дубли якорей и порядок Chapter I/II/III."""

        findings: list[Finding] = []
        headings: list[dict[str, object]] = []
        seen_titles: dict[str, dict[str, object]] = {}
        seen_anchors: dict[str, dict[str, object]] = {}
        for line_number, line in self._iter_markdown_lines(file):
            match = self.HEADING_RE.match(line.strip())
            if match is None:
                continue
            title = self._clean_heading(match.group(2))
            if not title:
                continue
            heading = {
                "line": line_number,
                "title": title,
                "level": len(match.group(1)),
                "anchor": self._github_anchor(title),
            }
            headings.append(heading)

            title_key = normalize_for_match(title)
            previous_title = seen_titles.get(title_key)
            if previous_title is None:
                seen_titles[title_key] = heading
            else:
                findings.append(
                    self._finding(
                        unit,
                        file.relative_path,
                        line_number,
                        title,
                        "duplicate_heading",
                        "Заголовок повторяется в этом же Markdown-файле.",
                        f"Переименовать один из заголовков или объединить разделы; первый такой заголовок находится на строке {previous_title['line']}.",
                        Severity.MINOR,
                    )
                )

            anchor = str(heading["anchor"])
            previous_anchor = seen_anchors.get(anchor)
            if previous_anchor is None:
                seen_anchors[anchor] = heading
            else:
                findings.append(
                    self._finding(
                        unit,
                        file.relative_path,
                        line_number,
                        title,
                        "duplicate_anchor",
                        f"Заголовок создаёт уже занятый Markdown-якорь `#{anchor}`.",
                        f"Сделать заголовок уникальным; первый заголовок с таким якорем находится на строке {previous_anchor['line']}.",
                        Severity.MINOR,
                    )
                )

        findings.extend(self._chapter_findings(unit, file.relative_path, headings))
        return findings

    def _chapter_findings(self, unit: ContentUnit, file_path: str, headings: list[dict[str, object]]) -> list[Finding]:
        """Проверяет монотонную последовательность Chapter I, II, III."""

        findings: list[Finding] = []
        previous_number: int | None = None
        previous_line: int | None = None
        for heading in headings:
            title = str(heading["title"])
            chapter_number = self._chapter_number(title)
            if chapter_number is None:
                continue
            line_number = int(str(heading["line"]))
            if previous_number is None:
                if chapter_number != 1:
                    findings.append(
                        self._finding(
                            unit,
                            file_path,
                            line_number,
                            title,
                            "chapter_sequence",
                            f"Первая найденная глава начинается с Chapter {self._roman(chapter_number)}, а ожидается Chapter I.",
                            "Проверить порядок глав и восстановить последовательность Chapter I, Chapter II, Chapter III.",
                            Severity.MINOR,
                        )
                    )
                previous_number = chapter_number
                previous_line = line_number
                continue
            if chapter_number == previous_number:
                previous_line = line_number
                continue
            expected = previous_number + 1
            if chapter_number != expected:
                findings.append(
                    self._finding(
                        unit,
                        file_path,
                        line_number,
                        title,
                        "chapter_sequence",
                        f"После Chapter {self._roman(previous_number)} на строке {previous_line} идёт Chapter {self._roman(chapter_number)}, ожидается Chapter {self._roman(expected)}.",
                        "Исправить номера глав или порядок разделов, чтобы оглавление шло без пропусков и возвратов.",
                        Severity.MINOR,
                    )
                )
            previous_number = chapter_number
            previous_line = line_number
        return findings

    def _numbered_list_findings(self, unit: ContentUnit, file: ContentFile) -> list[Finding]:
        """Проверяет ручную нумерацию списков внутри Markdown-блоков."""

        findings: list[Finding] = []
        block: list[tuple[int, int, str, str]] = []
        for line_number, line in self._iter_markdown_lines(file):
            if self.HEADING_RE.match(line.strip()):
                findings.extend(self._findings_from_list_block(unit, file.relative_path, block))
                block = []
                continue
            match = self.NUMBERED_LIST_RE.match(line)
            if match is not None:
                number = int(match.group(2))
                marker = match.group(3)
                block.append((line_number, number, marker, line.strip()))
                continue
            if line.strip() == "":
                continue
            findings.extend(self._findings_from_list_block(unit, file.relative_path, block))
            block = []
        findings.extend(self._findings_from_list_block(unit, file.relative_path, block))
        return findings

    def _findings_from_list_block(
        self,
        unit: ContentUnit,
        file_path: str,
        block: list[tuple[int, int, str, str]],
    ) -> list[Finding]:
        """Возвращает одну-две находки по одному блоку ручной нумерации."""

        if len(block) < 2:
            return []
        findings: list[Finding] = []
        numbers = [item[1] for item in block]
        markers = {item[2] for item in block}
        manual_parentheses = ")" in markers
        if markers == {"."} and len(set(numbers)) == 1 and numbers[0] == 1:
            return []
        if manual_parentheses and len(block) >= 3 and len(set(numbers)) == 1:
            line_number, number, _marker, quote = block[1]
            findings.append(
                self._finding(
                    unit,
                    file_path,
                    line_number,
                    quote,
                    "repeated_numbered_list_items",
                    f"В одном списке несколько пунктов подряд имеют номер {number}).",
                    "Пронумеровать пункты последовательно: 1), 2), 3) и далее.",
                    Severity.MINOR,
                )
            )
            return findings

        expected = numbers[0]
        previous_number = numbers[0]
        previous_line = block[0][0]
        for line_number, number, _marker, quote in block:
            if number != expected:
                issue_type = "numbered_list_reset" if number <= previous_number else "numbered_list_out_of_order"
                findings.append(
                    self._finding(
                        unit,
                        file_path,
                        line_number,
                        quote,
                        issue_type,
                        f"После пункта {previous_number}) на строке {previous_line} идёт пункт {number}), ожидается {expected}).",
                        "Исправить ручную нумерацию списка, чтобы номера шли последовательно внутри одного блока.",
                        Severity.MINOR,
                    )
                )
                break
            previous_number = number
            previous_line = line_number
            expected += 1
        return findings

    def _iter_markdown_lines(self, file: ContentFile) -> Iterable[tuple[int, str]]:
        """Идёт по Markdown-строкам, пропуская fenced code blocks."""

        in_fence = False
        for line_number, line in enumerate(file.text.splitlines(), start=1):
            if line.strip().startswith("```") or line.strip().startswith("~~~"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            yield line_number, line

    def _clean_heading(self, value: str) -> str:
        """Убирает Markdown-разметку, не влияющую на смысл заголовка."""

        cleaned = re.sub(r"`([^`]+)`", r"\1", value.strip())
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" #")

    def _github_anchor(self, title: str) -> str:
        """Строит приблизительный GitHub-compatible якорь для заголовка."""

        value = title.casefold().strip()
        value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
        value = re.sub(r"[\s_]+", "-", value, flags=re.UNICODE)
        return re.sub(r"-+", "-", value).strip("-")

    def _chapter_number(self, title: str) -> int | None:
        """Возвращает номер Chapter из римской записи."""

        match = self.CHAPTER_RE.search(title)
        if match is None:
            return None
        return self._roman_to_int(match.group(1).upper())

    def _roman_to_int(self, value: str) -> int | None:
        """Безопасно переводит римское число в int."""

        total = 0
        previous = 0
        for char in reversed(value):
            current = self.ROMAN_VALUES.get(char)
            if current is None:
                return None
            total = total - current if current < previous else total + current
            previous = max(previous, current)
        return total if total > 0 else None

    def _roman(self, value: int | None) -> str:
        """Форматирует небольшие номера глав римскими числами."""

        if value is None or value <= 0:
            return "?"
        pairs = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
        result = []
        remaining = value
        for number, roman in pairs:
            while remaining >= number:
                result.append(roman)
                remaining -= number
        return "".join(result)

    def _finding(
        self,
        unit: ContentUnit,
        file_path: str,
        line_number: int,
        quote: str,
        issue_type: str,
        evidence: str,
        recommendation: str,
        severity: Severity,
    ) -> Finding:
        """Создаёт находку по структурной ошибке Markdown."""

        return _finding(
            unit,
            self.name,
            Criterion.READABILITY,
            severity,
            Verdict.WARNING,
            0.88,
            quote[:320],
            TextLocation(file_path=file_path, line_start=line_number, line_end=line_number),
            [Evidence(title="Структура Markdown", detail=evidence)],
            recommendation,
            True,
            extra={"issue_type": issue_type},
        )

    def _dedupe_key(self, finding: Finding) -> tuple[str, int, str, str]:
        """Ключ для удаления повторов внутри одного файла."""

        location = finding.location
        return (
            location.file_path if location else "",
            location.line_start if location and location.line_start is not None else 0,
            str(finding.extra.get("issue_type") or ""),
            normalize_for_match(finding.quote or ""),
        )

    def _is_markdown_file(self, file: ContentFile) -> bool:
        """Ограничивает проверку Markdown-файлами и материалами с Markdown-разметкой."""

        name = Path(file.relative_path).name.lower()
        return name.endswith(".md") or file.kind in {"readme", "material"}


class LabelPunctuationChecker(BaseChecker):
    """Проверяет двоеточия в технических подписях перед значениями."""

    name = "label_punctuation_checker"
    LABEL_RE = re.compile(
        r"^\s*(?:[-*+]\s+|\d+[.)]\s+|[a-zа-яё][.)]\s+)?(?:>\s*)?(?:\*\*)?"
        r"(?P<label>Input operation|Input right operand|Output|Example|Result)"
        r"(?:\*\*)?(?P<rest>[^\n]*)$",
        re.IGNORECASE,
    )
    PROSE_PREFIX_RE = re.compile(
        r"^(?:of|for|from|when|if|that|should|must|is|are|will|can|means|section|description|"
        r"для|если|когда|котор|долж|явля|означ|раздел|описан)\b",
        re.IGNORECASE,
    )

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        findings: list[Finding] = []
        seen: set[tuple[str, int, str]] = set()
        for file in unit.files:
            if not self._is_checked_file(file):
                continue
            lines = list(self._iter_content_lines(file))
            for index, (line_number, line) in enumerate(lines):
                match = self.LABEL_RE.match(line)
                if match is None:
                    continue
                rest = match.group("rest").strip()
                if self._has_colon(rest):
                    continue
                if not self._has_value_after_label(rest, lines, index):
                    continue
                label = match.group("label")
                quote = line.strip()
                key = (file.relative_path, line_number, normalize_for_match(quote))
                if key in seen:
                    continue
                seen.add(key)
                findings.append(self._finding(unit, file.relative_path, line_number, quote, label))
        return findings

    def _has_value_after_label(self, rest: str, lines: list[tuple[int, str]], index: int) -> bool:
        """Проверяет, что после подписи действительно идёт значение."""

        if rest:
            return self._looks_like_inline_value(rest)
        for _line_number, candidate in lines[index + 1 : index + 4]:
            stripped = candidate.strip()
            if not stripped:
                continue
            if self.LABEL_RE.match(candidate):
                return False
            return self._looks_like_next_value(stripped)
        return False

    def _looks_like_inline_value(self, value: str) -> bool:
        """Распознаёт значение в той же строке после подписи."""

        stripped = value.strip()
        if not stripped or self.PROSE_PREFIX_RE.search(stripped):
            return False
        if stripped.startswith(("`", ">", "\"", "'", "«", "[", "{", "(", "-", "+")):
            return True
        if re.match(r"^\d", stripped):
            return True
        if "\\n" in stripped or "/" in stripped:
            return True
        return len(stripped.split()) <= 4 and len(stripped) <= 80

    def _looks_like_next_value(self, value: str) -> bool:
        """Распознаёт значение на следующей строке после подписи."""

        if not value or self.PROSE_PREFIX_RE.search(value):
            return False
        if value.startswith((">", "`", "|", "-", "*", "+", "\"", "'", "«", "[", "{", "(")):
            return True
        if re.match(r"^\d", value):
            return True
        return len(value.split()) <= 6 and len(value) <= 120

    def _has_colon(self, rest: str) -> bool:
        """Проверяет наличие двоеточия сразу после подписи."""

        return rest.lstrip().startswith((":","："))

    def _iter_content_lines(self, file: ContentFile) -> Iterable[tuple[int, str]]:
        """Идёт по строкам материала, пропуская fenced code blocks."""

        in_fence = False
        for line_number, line in enumerate(file.text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            yield line_number, line

    def _finding(self, unit: ContentUnit, file_path: str, line_number: int, quote: str, label: str) -> Finding:
        """Создаёт находку по пропущенному двоеточию после технической подписи."""

        return _finding(
            unit,
            self.name,
            Criterion.READABILITY,
            Severity.MINOR,
            Verdict.WARNING,
            0.92,
            quote[:320],
            TextLocation(file_path=file_path, line_start=line_number, line_end=line_number),
            [Evidence(title="Техническая подпись", detail=f"После подписи `{label}` ожидается двоеточие перед значением.")],
            f"Добавить двоеточие: `{label}:`.",
            False,
            extra={"issue_type": "missing_label_colon", "label": label},
        )

    def _is_checked_file(self, file: ContentFile) -> bool:
        """Ограничивает проверку Markdown/текстовыми материалами, исключая YAML-структуры."""

        name = Path(file.relative_path).name.lower()
        return file.kind in {"readme", "material", "text"} or name.endswith((".md", ".txt"))


class LinkChecker(BaseChecker):
    """Проверяет ссылки: локальные сразу, внешние при разрешённой сети."""

    name = "link_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        findings: list[Finding] = []
        for entity in _entities_of_type(entities, EntityType.LINK):
            parsed = urlparse(entity.value)
            if parsed.scheme not in {"http", "https"}:
                continue
            policy_error = _url_policy_error(entity.value)
            if policy_error is not None:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.INFO,
                        Verdict.UNKNOWN,
                        0.65,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Политика проверки ссылок", detail=policy_error, url=entity.value)],
                        "Проверить ссылку вручную.",
                        True,
                    )
                )
                continue
            if not context.settings.allow_network:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.INFO,
                        Verdict.UNKNOWN,
                        0.5,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Сеть отключена", detail=f"Ссылка не проверялась: {entity.value}", url=entity.value)],
                        "Запустить проверку с доступом к сети, чтобы подтвердить доступность ссылки.",
                        True,
                    )
                )
                continue

            status_code, final_url, error = _check_url(entity.value, context.settings.link_timeout_seconds)
            if error is not None:
                severity = Severity.MINOR if _is_redirect_chain_error(error) else Severity.INFO
                verdict = Verdict.WARNING if _is_redirect_chain_error(error) else Verdict.UNKNOWN
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        severity,
                        verdict,
                        0.65,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Ошибка запроса", detail=error, url=entity.value)],
                        "Перепроверить ссылку: ошибка может быть временной, сетевой или связанной с перенаправлениями.",
                        True,
                    )
                )
            elif _is_transient_http_status(status_code):
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.INFO,
                        Verdict.UNKNOWN,
                        0.65,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Временный HTTP-статус", detail=f"Получен статус {status_code}.", url=final_url or entity.value)],
                        "Повторить проверку позже: статус похож на временную недоступность или ограничение запросов.",
                        True,
                    )
                )
            elif status_code >= 400:
                severity = Severity.MAJOR
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        severity,
                        Verdict.FAIL,
                        0.9,
                        entity.quote,
                        entity.location,
                        [Evidence(title="HTTP-статус", detail=f"Получен статус {status_code}.", url=final_url or entity.value)],
                        "Заменить ссылку на актуальную или удалить зависимость от недоступного ресурса.",
                        True,
                    )
                )
            elif _redirect_smells_like_rot(entity.value, final_url):
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.MINOR,
                        Verdict.WARNING,
                        0.7,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Подозрительный редирект", detail=f"Финальный адрес: {final_url}.", url=final_url or entity.value)],
                        "Проверить, ведёт ли ссылка на нужный материал, а не на главную страницу или другой домен.",
                        True,
                    )
                )
        return findings


class LocalLinkChecker(BaseChecker):
    """Проверяет локальные Markdown-ссылки на файлы и изображения."""

    name = "local_link_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del context
        findings: list[Finding] = []
        for entity in [*list(_entities_of_type(entities, EntityType.IMAGE))]:
            target, _fragment = urldefrag(entity.value)
            parsed = urlparse(target)
            if parsed.scheme in {"http", "https"} or not target:
                continue
            source_file = unit.root_path / entity.location.file_path
            target_path = (source_file.parent / target).resolve()
            if not _is_inside(target_path, unit.root_path) or not target_path.exists():
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.LINKS,
                        Severity.MAJOR,
                        Verdict.FAIL,
                        0.95,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Локальный файл", detail=f"Файл не найден: {entity.value}")],
                        "Исправить путь к локальному ресурсу или добавить отсутствующий файл.",
                        True,
                    )
                )
        return findings


class ResourceAvailabilityChecker(BaseChecker):
    """Проверяет наличие локальных ресурсов, на которые опирается задание."""

    name = "resource_availability_checker"
    RESOURCE_EXTENSIONS = (
        "pcapng",
        "pcap",
        "csv",
        "tsv",
        "xlsx",
        "xls",
        "parquet",
        "json",
        "xml",
        "sql",
        "dump",
        "bak",
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "tgz",
        "xz",
        "bz2",
        "ova",
        "ovf",
        "vmdk",
        "qcow2",
        "img",
        "iso",
        "png",
        "jpg",
        "jpeg",
        "svg",
        "pdf",
    )
    RESOURCE_FILE_RE = re.compile(
        rf"`([^`\n]+\.(?:{'|'.join(RESOURCE_EXTENSIONS)}))`|"
        rf"(?<![\w./-])([\w./\\-]+\.(?:{'|'.join(RESOURCE_EXTENSIONS)}))\b",
        re.IGNORECASE,
    )
    ABSOLUTE_ENV_PATH_RE = re.compile(r"(?<![\w/])/(?:opt|mnt|srv|var|home)/[A-Za-z0-9._/-]+")
    ENVIRONMENT_GUIDE_RE = re.compile(
        r"\b(?:virtualbox|vbox|vm|virtual\s+machine)\b|(?:виртуальн\w*|вм|машин\w*|образ\w*)",
        re.IGNORECASE,
    )
    URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
    EXTERNAL_RESOURCE_HINT_RE = re.compile(
        r"\b(attached|downloadable|external)\s+(?:file|dataset|archive|resource)\b|"
        r"(\bприкрепл\w*\b|\bприложенн\w*\b|\bвложенн\w*\b|\bданн(?:ый|ые)?\s+файл|"
        r"\bвнешн\w*\s+файл|\bфайл\s+по\s+ссылк)",
        re.IGNORECASE,
    )
    REQUIRED_RESOURCE_RE = re.compile(
        r"\b(provided|attached|given|contains|included|download|load|open|analy[sz]e|dataset|dump|archive|"
        r"capture|image|picture|virtual\s+machine|vm|iso|pcap)\b|"
        r"(\bприлож\w*\b|\bвложен\w*\b|данн(?:ый|ые)?\s+файл|\bсодерж\w*\b|\bскача\w*\b|"
        r"\bзагруз\w*\b|\bоткр\w*\b|\bпроанализ\w*\b|\bдатасет\w*\b|набор\s+данных|"
        r"\bдамп\w*\b|\bархив\w*\b|виртуальн\w*|машин\w*|\bкартин\w*\b|\bизображ\w*\b|"
        r"\bpcap\b|\bснимок\b|\bзахват(?:а|ом)?\b)",
        re.IGNORECASE,
    )
    GENERIC_RESOURCE_RE = re.compile(
        r"\b(dataset|dump|archive|image|picture|virtual\s+machine|vm|pcap|capture)\b|"
        r"(\bдатасет\w*\b|набор\s+данных|\bдамп\w*\b|\bархив\w*\b|виртуальн(?:ая|ой)?\s+машин|"
        r"\bкартин\w*\b|\bизображ\w*\b|\bpcap\b|\bзахват(?:а|ом)?\b)",
        re.IGNORECASE,
    )
    OUTPUT_ARTIFACT_RE = re.compile(
        r"\b(save|write|create|generate|export|return|output|result|turn\s+in|submit)\b|"
        r"(сохран|созда|сгенер|экспорт|верн|вывед|результат|сда(?:ть|й)|положи)",
        re.IGNORECASE,
    )

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        available = self._available_resources(unit)
        external_refs = self._external_resource_refs(unit)
        findings: list[Finding] = []
        seen: set[tuple[str, int, str, str]] = set()
        for file in unit.files:
            if not self._is_instruction_file(file):
                continue
            for line_number, raw_line in enumerate(file.text.splitlines(), start=1):
                line = raw_line.strip()
                if not line:
                    continue
                line_findings = [
                    *self._missing_file_findings(unit, file.relative_path, line_number, line, available, external_refs),
                    *self._absolute_path_findings(unit, file.relative_path, line_number, line, available),
                    *self._generic_resource_findings(unit, file.relative_path, line_number, line, available),
                ]
                for finding in line_findings:
                    key = self._dedupe_key(finding)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(finding)
        for finding in self._environment_guide_findings(unit, available):
            key = self._dedupe_key(finding)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
        return findings

    def _missing_file_findings(
        self,
        unit: ContentUnit,
        file_path: str,
        line_number: int,
        line: str,
        available: set[str],
        external_refs: set[str],
    ) -> list[Finding]:
        """Ищет явно названные входные ресурсы, которых нет в проекте."""

        if self._line_has_external_source(line):
            return []
        if not self.REQUIRED_RESOURCE_RE.search(line):
            return []

        findings: list[Finding] = []
        for ref in self._file_refs(line):
            if self._looks_like_output_ref(line, ref):
                continue
            if self._resource_present(ref, external_refs):
                continue
            if self._resource_present(ref, available):
                continue
            findings.append(
                self._build_finding(
                    unit,
                    file_path,
                    line_number,
                    ref,
                    "missing_local_resource",
                    f"В инструкции указан локальный ресурс `{ref}`, но среди файлов проекта он не найден.",
                    "Добавить ресурс в материалы проекта или указать рабочую ссылку/путь, откуда его получить.",
                    Severity.MAJOR,
                    Verdict.FAIL,
                    0.9,
                )
            )
        return findings

    def _absolute_path_findings(
        self,
        unit: ContentUnit,
        file_path: str,
        line_number: int,
        line: str,
        available: set[str],
    ) -> list[Finding]:
        """Ловит ссылки на внешнее локальное окружение без подтверждающего ресурса."""

        if self.URL_RE.search(line):
            return []
        findings: list[Finding] = []
        for match in self.ABSOLUTE_ENV_PATH_RE.finditer(line):
            path = match.group(0).rstrip(".,;)")
            if self._has_environment_evidence(available):
                continue
            findings.append(
                self._build_finding(
                    unit,
                    file_path,
                    line_number,
                    path,
                    "unconfirmed_environment_path",
                    f"Инструкция ссылается на локальный путь `{path}`, но в проекте нет образа, архива или описания ресурса окружения.",
                    "Добавить подтверждение окружения: образ/архив/инструкцию получения ресурса или заменить путь на воспроизводимый источник.",
                    Severity.MAJOR,
                    Verdict.WARNING,
                    0.82,
                )
            )
        return findings

    def _generic_resource_findings(
        self,
        unit: ContentUnit,
        file_path: str,
        line_number: int,
        line: str,
        available: set[str],
    ) -> list[Finding]:
        """Ищет упоминание обязательного ресурса без файла, ссылки или имени ресурса."""

        if self.URL_RE.search(line) or self._file_refs(line) or self.ABSOLUTE_ENV_PATH_RE.search(line):
            return []
        marker = self.GENERIC_RESOURCE_RE.search(line)
        if marker is None or not self.REQUIRED_RESOURCE_RE.search(line):
            return []
        resource_kind = self._resource_kind(marker.group(0))
        if self._has_resource_of_kind(resource_kind, available):
            return []
        return [
            self._build_finding(
                unit,
                file_path,
                line_number,
                marker.group(0),
                "resource_without_artifact",
                "В тексте нужен локальный ресурс, но рядом нет имени файла, ссылки или приложенного материала.",
                "Указать конкретный файл/ссылку на ресурс или приложить его к проекту.",
                Severity.MAJOR,
                Verdict.WARNING,
                0.78,
                extra={"resource_kind": resource_kind},
            )
        ]

    def _available_resources(self, unit: ContentUnit) -> set[str]:
        """Собирает нормализованные имена и пути файлов проекта."""

        available: set[str] = set()
        for file in unit.files:
            path = file.relative_path.replace("\\", "/").lower()
            available.add(path)
            available.add(Path(path).name)
        for fs_path in unit.root_path.rglob("*"):
            if not fs_path.is_file():
                continue
            try:
                relative_path = fs_path.relative_to(unit.root_path).as_posix().lower()
            except ValueError:
                continue
            available.add(relative_path)
            available.add(fs_path.name.lower())
        return available

    def _file_refs(self, line: str) -> list[str]:
        """Достаёт имена файлов-ресурсов из строки."""

        refs: list[str] = []
        for match in self.RESOURCE_FILE_RE.finditer(line):
            ref = (match.group(1) or match.group(2) or "").strip().strip(".,;)")
            if ref and ref not in refs:
                refs.append(ref)
        return refs

    def _resource_present(self, ref: str, available: set[str]) -> bool:
        """Проверяет наличие ресурса по относительному пути или базовому имени."""

        normalized = ref.strip().replace("\\", "/").lower()
        basename = Path(normalized).name
        return normalized in available or basename in available

    def _looks_like_output_artifact(self, line: str) -> bool:
        """Отделяет входные ресурсы от файлов, которые студент должен создать."""

        if not self.OUTPUT_ARTIFACT_RE.search(line):
            return False
        return not self.REQUIRED_RESOURCE_RE.search(line.replace("expected", "").replace("ожида", ""))

    def _has_environment_evidence(self, available: set[str]) -> bool:
        """Проверяет, приложен ли образ или архив окружения."""

        return any(ref.endswith((".ova", ".ovf", ".vmdk", ".qcow2", ".img", ".iso", ".zip", ".rar", ".7z")) for ref in available)

    def _external_resource_refs(self, unit: ContentUnit) -> set[str]:
        """Собирает ресурсы, которые даны внешней ссылкой или явно приложены платформой."""

        refs: set[str] = set()
        for file in unit.files:
            if not self._is_instruction_file(file):
                continue
            is_readme = file.kind == "readme" or Path(file.relative_path).name.lower().startswith("readme")
            for line in file.text.splitlines():
                line_refs = self._file_refs(line)
                if not line_refs:
                    continue
                if self._line_has_external_source(line) or (is_readme and self.EXTERNAL_RESOURCE_HINT_RE.search(line)):
                    for ref in line_refs:
                        normalized = ref.strip().replace("\\", "/").lower()
                        refs.add(normalized)
                        refs.add(Path(normalized).name)
        return refs

    def _line_has_external_source(self, line: str) -> bool:
        """Понимает, что ресурс в строке уже дан через внешний источник."""

        return bool(self.URL_RE.search(line))

    def _looks_like_output_ref(self, line: str, ref: str) -> bool:
        """Проверяет, что конкретный файл является результатом, а не входом задания."""

        lowered = line.lower()
        ref_lower = ref.lower().strip("`")
        index = lowered.find(ref_lower)
        if index < 0:
            index = lowered.find(Path(ref_lower).name)
        if index < 0:
            return False
        before = lowered[max(0, index - 120) : index]
        after = lowered[index : min(len(lowered), index + len(ref_lower) + 80)]
        last_output = self._last_match_start(self.OUTPUT_ARTIFACT_RE, before)
        last_input = self._last_match_start(self.REQUIRED_RESOURCE_RE, before)
        return last_output >= 0 and last_output >= last_input and "expected" not in after and "ожида" not in after

    def _last_match_start(self, pattern: re.Pattern[str], text: str) -> int:
        """Возвращает позицию последнего совпадения или -1, если его нет."""

        result = -1
        for match in pattern.finditer(text):
            result = match.start()
        return result

    def _environment_guide_findings(self, unit: ContentUnit, available: set[str]) -> list[Finding]:
        """Ловит ситуацию, когда инструкция по ВМ есть, а воспроизводимого образа окружения нет."""

        if self._has_environment_evidence(available):
            return []
        findings: list[Finding] = []
        for path in sorted(unit.root_path.rglob("*")):
            if not path.is_file():
                continue
            try:
                relative_path = path.relative_to(unit.root_path).as_posix()
            except ValueError:
                continue
            name = path.name
            if not self.ENVIRONMENT_GUIDE_RE.search(name):
                continue
            findings.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.CORRECTNESS,
                    Severity.MAJOR,
                    Verdict.WARNING,
                    0.84,
                    name,
                    TextLocation(file_path=relative_path),
                    [
                        Evidence(
                            title="Локальное окружение",
                            detail=(
                                "В проекте есть инструкция или материал про виртуальную машину/VirtualBox, "
                                "но не найден образ, архив или другой воспроизводимый ресурс окружения."
                            ),
                        )
                    ],
                    "Приложить образ ВМ/архив окружения или заменить инструкцию на воспроизводимый источник получения окружения.",
                    True,
                    extra={"issue_type": "environment_guide_without_image"},
                )
            )
            break
        return findings

    def _has_resource_of_kind(self, resource_kind: str, available: set[str]) -> bool:
        """Проверяет наличие файла нужного класса среди материалов проекта."""

        suffixes_by_kind = {
            "dataset": (".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".json", ".xml"),
            "dump": (".dump", ".sql", ".bak", ".pcapng", ".pcap"),
            "archive": (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".xz", ".bz2"),
            "image": (".png", ".jpg", ".jpeg", ".svg"),
            "vm": (".ova", ".ovf", ".vmdk", ".qcow2", ".img", ".iso"),
            "pcap": (".pcapng", ".pcap"),
        }
        suffixes = suffixes_by_kind.get(resource_kind, ())
        return any(ref.endswith(suffixes) for ref in available)

    def _resource_kind(self, marker: str) -> str:
        """Нормализует тип упомянутого ресурса."""

        lowered = marker.lower()
        if "pcap" in lowered or "capture" in lowered or "захват" in lowered:
            return "pcap"
        if "вирту" in lowered or lowered in {"vm"} or "virtual" in lowered or "образ" in lowered:
            return "vm"
        if "архив" in lowered or "archive" in lowered:
            return "archive"
        if "дамп" in lowered or "dump" in lowered:
            return "dump"
        if "картин" in lowered or "изображ" in lowered or "image" in lowered or "picture" in lowered:
            return "image"
        return "dataset"

    def _is_instruction_file(self, file: ContentFile) -> bool:
        """Ограничивает проверку файлами, где описываются задания и критерии."""

        name = Path(file.relative_path).name.lower()
        return file.kind in {"readme", "material", "checklist", "text"} or name.endswith((".md", ".txt", ".yml", ".yaml"))

    def _build_finding(
        self,
        unit: ContentUnit,
        file_path: str,
        line_number: int,
        quote: str,
        issue_type: str,
        issue: str,
        recommendation: str,
        severity: Severity,
        verdict: Verdict,
        confidence: float,
        extra: dict[str, object] | None = None,
    ) -> Finding:
        """Создаёт строку отчёта по отсутствующему локальному ресурсу."""

        merged_extra: dict[str, object] = {"issue_type": issue_type}
        if extra:
            merged_extra.update(extra)
        return _finding(
            unit,
            self.name,
            Criterion.CORRECTNESS,
            severity,
            verdict,
            confidence,
            quote[:320],
            TextLocation(file_path=file_path, line_start=line_number, line_end=line_number),
            [Evidence(title="Локальный ресурс", detail=issue)],
            recommendation,
            True,
            extra=merged_extra,
        )

    def _dedupe_key(self, finding: Finding) -> tuple[str, int, str, str]:
        """Ключ для удаления дублей внутри одного прогона."""

        location = finding.location
        return (
            location.file_path if location else "",
            location.line_start if location and location.line_start is not None else 0,
            str(finding.extra.get("issue_type") or ""),
            normalize_for_match(finding.quote or ""),
        )


class ChecklistChecker(BaseChecker):
    """Проверяет наличие и базовое соответствие чек-листа README."""

    name = "checklist_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        checklist_files = [file for file in unit.files if file.kind == "checklist"]
        if not checklist_files:
            return []

        findings: list[Finding] = []
        readme_text = "\n".join(file.text for file in unit.files if file.kind == "readme")
        available_files = self._project_file_refs(unit)
        artifact_text_index = build_artifact_text_index(unit.root_path)
        for checklist_file in checklist_files:
            try:
                payload = yaml.safe_load(checklist_file.text) or {}
            except yaml.YAMLError as exc:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.CHECKLIST_ALIGNMENT,
                        Severity.CRITICAL,
                        Verdict.FAIL,
                        0.95,
                        None,
                        TextLocation(file_path=checklist_file.relative_path),
                        [Evidence(title="YAML", detail=f"Чек-лист не разбирается: {exc}")],
                        "Исправить структуру YAML, иначе чек-лист нельзя использовать для проверки.",
                        True,
                    )
                )
                continue

            questions = extract_checklist_questions(payload)
            question_names = [question.name for question in questions]
            if not question_names:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.CHECKLIST_ALIGNMENT,
                        Severity.MAJOR,
                        Verdict.FAIL,
                        0.9,
                        None,
                        TextLocation(file_path=checklist_file.relative_path),
                        [Evidence(title="Чек-лист", detail="Не найдены вопросы проверки в sections[].questions[].")],
                        "Проверить формат чек-листа: пункты должны быть представлены в sections[].questions[].",
                        True,
                    )
                )
                continue

            match_result = match_checklist_to_readme(question_names, readme_text)
            description_result = assess_checklist_description_quality(questions)
            grounding_issues = assess_checklist_grounding(
                questions,
                readme_text,
                available_files=available_files,
                artifact_text_index=artifact_text_index,
            )
            findings.extend(self._grounding_issue_findings(unit, checklist_file.relative_path, grounding_issues))
            evidence_detail = (
                f"Сильных совпадений: {match_result.strong_matched} из {match_result.total}; "
                f"слабых совпадений: {match_result.weak_matched} из {match_result.total}; "
                f"не сопоставлено: {len(match_result.unmatched_names)} из {match_result.total}. "
                f"Развёрнутых описаний: {description_result.complete} из {description_result.total}."
            )
            if match_result.unmatched_names:
                evidence_detail += f" Не сопоставлены: {', '.join(match_result.unmatched_names[:8])}."
            if description_result.incomplete_names:
                evidence_detail += f" Недостаточно описаны: {', '.join(description_result.incomplete_names[:8])}."

            severity = Severity.INFO
            verdict = Verdict.PASS
            recommendation_parts: list[str] = []
            confidence = 0.78
            if match_result.strong_ratio < 0.5:
                severity = Severity.MINOR
                verdict = Verdict.WARNING
                confidence = min(confidence, 0.65)
                recommendation_parts.append(
                    "Проверить связь пунктов чек-листа с требованиями README; текущий сигнал основан на лексическом сопоставлении."
                )
            if description_result.ratio == 0:
                severity = Severity.MAJOR
                verdict = Verdict.WARNING
                confidence = max(confidence, 0.82)
                recommendation_parts.append(
                    "Добавить развёрнутые описания пунктов: критерии приёмки, ожидаемые артефакты и примеры."
                )
            elif description_result.ratio < 0.8:
                if severity == Severity.INFO:
                    severity = Severity.MINOR
                    verdict = Verdict.WARNING
                    confidence = min(confidence, 0.72)
                recommendation_parts.append(
                    "Доработать пункты без критериев приёмки, ожидаемых артефактов или примеров."
                )

            if verdict != Verdict.PASS:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.CHECKLIST_ALIGNMENT,
                        severity,
                        verdict,
                        confidence,
                        None,
                        TextLocation(file_path=checklist_file.relative_path),
                        [
                            Evidence(
                                title="Связность README и чек-листа",
                                detail=evidence_detail,
                            )
                        ],
                        " ".join(recommendation_parts),
                        True,
                        extra={
                            "matched_ratio": match_result.ratio,
                            "strong_matched": match_result.strong_matched,
                            "weak_matched": match_result.weak_matched,
                            "strong_matched_questions": list(match_result.strong_matched_names),
                            "weak_matched_questions": list(match_result.weak_matched_names),
                            "unmatched_questions": list(match_result.unmatched_names),
                            "description_ratio": description_result.ratio,
                            "complete_description_questions": list(description_result.complete_names),
                            "incomplete_questions": list(description_result.incomplete_names),
                            "grounding_issues": [
                                {
                                    "question_name": issue.question_name,
                                    "issue_type": issue.issue_type,
                                    "detail": issue.detail,
                                    "evidence": issue.evidence,
                                    "severity": issue.severity.value,
                                }
                                for issue in grounding_issues
                            ],
                        },
                    )
                )
            else:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.CHECKLIST_ALIGNMENT,
                        Severity.INFO,
                        Verdict.PASS,
                        0.75,
                        None,
                        TextLocation(file_path=checklist_file.relative_path),
                        [
                            Evidence(
                                title="Чек-лист",
                                detail=evidence_detail,
                            )
                        ],
                        "Действий не требуется; структура чек-листа и базовая связность с README выглядят достаточными.",
                        False,
                        extra={
                            "matched_ratio": match_result.ratio,
                            "strong_matched": match_result.strong_matched,
                            "weak_matched": match_result.weak_matched,
                            "strong_matched_questions": list(match_result.strong_matched_names),
                            "weak_matched_questions": list(match_result.weak_matched_names),
                            "unmatched_questions": list(match_result.unmatched_names),
                            "description_ratio": description_result.ratio,
                            "complete_description_questions": list(description_result.complete_names),
                            "incomplete_questions": list(description_result.incomplete_names),
                            "grounding_issues": [],
                        },
                    )
                )
        return findings

    def _project_file_refs(self, unit: ContentUnit) -> list[str]:
        """Возвращает все файлы проекта, включая бинарные артефакты, не попавшие в текстовый ingestion."""

        refs: list[str] = []
        for file in unit.files:
            refs.append(file.relative_path)
        for path in unit.root_path.rglob("*"):
            if not path.is_file():
                continue
            try:
                refs.append(path.relative_to(unit.root_path).as_posix())
            except ValueError:
                continue
        return list(dict.fromkeys(refs))

    def _grounding_issue_findings(
        self,
        unit: ContentUnit,
        checklist_path: str,
        grounding_issues: list[Any],
    ) -> list[Finding]:
        """Преобразует конкретные расхождения README и чек-листа в атомарные находки."""

        findings: list[Finding] = []
        for issue in grounding_issues:
            findings.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.CHECKLIST_ALIGNMENT,
                    issue.severity,
                    Verdict.WARNING,
                    0.88 if issue.issue_type == "artifact_missing_expected_text" else 0.84,
                    issue.evidence,
                    TextLocation(file_path=checklist_path),
                    [Evidence(title="Проверяемое требование чек-листа", detail=issue.detail)],
                    self._grounding_recommendation(issue.issue_type),
                    True,
                    extra={
                        "issue_type": issue.issue_type,
                        "question_name": issue.question_name,
                        "grounding_evidence": issue.evidence,
                    },
                )
            )
        return findings

    def _grounding_recommendation(self, issue_type: str) -> str:
        """Даёт рекомендацию для конкретного типа расхождения README и чек-листа."""

        recommendations = {
            "artifact_missing_expected_text": (
                "Проверить приложенный артефакт: если маркер действительно отсутствует, "
                "убрать это требование из чек-листа или заменить артефакт."
            ),
            "ungrounded_command": "Либо описать эту команду в README, либо убрать её из проверок чек-листа.",
            "ungrounded_resource": "Приложить ресурс к проекту или убрать его из чек-листа.",
            "expected_file_name_mismatch": "Привести имя ожидаемого файла в README и чек-листе к одному варианту.",
            "ungrounded_sql_condition": "Описать это SQL-условие в задании или убрать его из ожидаемого решения чек-листа.",
            "ungrounded_self_join_order": "Явно описать порядок пар в README или убрать это требование из чек-листа.",
            "suspicious_duplicate_name_result": "Проверить ожидаемый вывод чек-листа и убрать дубли, если задание просит уникальный список.",
            "expected_output_semantic_mismatch": "Синхронизировать смысл ожидаемого вывода с формулировкой README.",
        }
        return recommendations.get(
            issue_type,
            "Проверить, не добавляет ли чек-лист требование, которого нет в README.",
        )


class LanguageCoverageChecker(BaseChecker):
    """Определяет наличие языковых версий RUS/ENG/UZ/TG."""

    name = "language_coverage_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        languages, mismatches = _detect_language_profile(unit)
        expected_languages = tuple(context.settings.expected_languages)
        missing_languages = tuple(language for language in expected_languages if language not in languages)
        coverage_ratio = (
            (len(expected_languages) - len(missing_languages)) / len(expected_languages)
            if expected_languages
            else None
        )
        findings: list[Finding] = []
        for mismatch in mismatches:
            findings.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.LANGUAGE,
                    Severity.MINOR,
                    Verdict.WARNING,
                    0.75,
                    None,
                    TextLocation(file_path=mismatch["file_path"]),
                    [
                        Evidence(
                            title="Несовпадение языка",
                            detail=f"В имени файла ожидается {mismatch['expected']}, по тексту похоже на {mismatch['detected']}.",
                        )
                    ],
                    "Проверить имя файла или содержимое языковой версии.",
                    True,
                    extra={
                        **mismatch,
                        "languages": sorted(languages),
                        "expected_languages": list(expected_languages),
                        "missing_languages": list(missing_languages),
                        "coverage_ratio": coverage_ratio,
                    },
                )
            )
        return findings


class ExamPresenceChecker(BaseChecker):
    """Ищет признаки финальной проверки или экзамена в единице контента."""

    name = "exam_presence_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        markers = ("exam", "final", "экзамен", "финаль", "итогов")
        matched_paths = [file.relative_path for file in unit.files if any(marker in file.relative_path.lower() for marker in markers)]
        if matched_paths:
            return []
        return []


class ImageQualityChecker(BaseChecker):
    """Проверяет размеры локальных изображений, на которые ссылается Markdown."""

    name = "image_quality_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        findings: list[Finding] = []
        for entity in _entities_of_type(entities, EntityType.IMAGE):
            target, _fragment = urldefrag(entity.value)
            parsed = urlparse(target)
            if parsed.scheme in {"http", "https"} or not target:
                continue
            source_file = unit.root_path / entity.location.file_path
            target_path = (source_file.parent / target).resolve()
            if not target_path.exists() or not _is_inside(target_path, unit.root_path):
                continue
            dimensions = read_image_dimensions(target_path)
            if dimensions is None:
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.IMAGE_QUALITY,
                        Severity.INFO,
                        Verdict.UNKNOWN,
                        0.45,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Изображение", detail=f"Не удалось определить размер: {entity.value}")],
                        "Проверить изображение вручную или добавить поддержку его формата.",
                        True,
                    )
                )
                continue
            width, height = dimensions
            if width < context.settings.min_image_width or height < context.settings.min_image_height:
                if is_decorative_image(entity.value, entity.quote, width, height):
                    continue
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.IMAGE_QUALITY,
                        Severity.MINOR,
                        Verdict.WARNING,
                        0.85,
                        entity.quote,
                        entity.location,
                        [Evidence(title="Размер изображения", detail=f"{width}x{height}, минимум {context.settings.min_image_width}x{context.settings.min_image_height}.")],
                        "Заменить изображение на более качественное или подтвердить, что малый размер допустим.",
                        True,
                    )
                )
        return findings


class ReadabilityChecker(BaseChecker):
    """Ищет незавершённые фрагменты и грубые проблемы читаемости."""

    name = "readability_checker"
    prompt_version = "readability_checker:v2"
    long_line_candidate_threshold = 260
    max_long_line_candidates = 8
    SYSTEM_PROMPT = """Ты проверяешь читаемость учебного материала.
Тебе дадут строки-кандидаты, которые технически длинные. Не считай длину строки самостоятельной ошибкой.
Оцени, мешает ли фрагмент методической читаемости: перегружен ли он несколькими мыслями,
списками без структуры, длинной инструкцией без разбивки.
Если длинная строка является таблицей, кодом, ссылкой, командой, цитатой, YAML/JSON или нормально читаемым абзацем, верни verdict='pass'.
Верни только JSON: {"verdict":"pass|warning|fail|unknown","severity":"info|minor|major","confidence":0.0,
"problem_lines":[1],"evidence":"","recommendation":""}.
verdict='warning' ставь только когда текст реально стоит разбить или переписать для учебной читаемости.
verdict='fail' используй только для грубой проблемы, которая серьёзно мешает понять задание.
verdict='unknown' используй, если контекста недостаточно.
Все пояснения и рекомендации пиши на русском языке."""

    PLACEHOLDER_RE = re.compile(
        r"\b(TODO|TBD|FIXME|lorem ipsum)\b|"
        r"\bздесь\s+будет\s+(?:текст|описание|картинка|изображение|пример|раздел|таблица|ссылка)\b|"
        r"\b(?:дописать|заглушка)\b",
        re.IGNORECASE,
    )

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        findings: list[Finding] = []
        for file in unit.files:
            check_long_lines = file.kind in {"readme", "material"}
            long_lines: list[tuple[int, int, str]] = []
            for index, line in enumerate(file.text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                placeholder = self.PLACEHOLDER_RE.search(stripped)
                if placeholder:
                    findings.append(
                        _finding(
                            unit,
                            self.name,
                            Criterion.READABILITY,
                            Severity.MAJOR,
                            Verdict.FAIL,
                            0.9,
                            stripped[:320],
                            TextLocation(file_path=file.relative_path, line_start=index, line_end=index),
                            [Evidence(title="Незавершённый фрагмент", detail=f"Найден маркер: {placeholder.group(0)}")],
                            "Заменить заглушку на финальный текст или удалить незавершённый фрагмент.",
                            True,
                        )
                    )
                if check_long_lines and len(stripped) > self.long_line_candidate_threshold:
                    long_lines.append((index, len(stripped), stripped[:700]))
            if long_lines:
                finding = self._model_long_line_finding(unit, file.relative_path, long_lines, context)
                if finding is not None:
                    findings.append(finding)
        return findings

    def _model_long_line_finding(
        self,
        unit: ContentUnit,
        file_path: str,
        long_lines: list[tuple[int, int, str]],
        context: CheckContext,
    ) -> Finding | None:
        """Передаём длинные строки модели: сама длина строки не является вердиктом."""

        if context.model_client is None:
            return None

        candidates = [
            {"line": line, "length": length, "text": text}
            for line, length, text in long_lines[: self.max_long_line_candidates]
        ]
        prompt_payload = {
            "file_path": file_path,
            "candidate_rule": (
                f"Строки длиннее {self.long_line_candidate_threshold} символов "
                "отправлены только как кандидаты."
            ),
            "candidates": candidates,
        }
        prompt = json.dumps(prompt_payload, ensure_ascii=False, indent=2)
        cache_key = _hash_cache_key("readability", f"{file_path}|{prompt}")
        try:
            record, cache_hit = _cached_model_json(
                context,
                "readability",
                cache_key,
                context.model_client,
                self.SYSTEM_PROMPT,
                prompt,
                self.prompt_version,
            )
        except OpenRouterError as exc:
            return _external_check_error(unit, self.name, Criterion.READABILITY, exc)

        item = _first_result_item(record.get("response"))
        if item is None:
            return None
        verdict = _enum_or_default(Verdict, item.get("verdict"), Verdict.UNKNOWN)
        if verdict not in {Verdict.WARNING, Verdict.FAIL}:
            return None

        severity = _enum_or_default(Severity, item.get("severity"), Severity.MINOR)
        problem_lines = _readability_problem_lines(item.get("problem_lines"))
        location = (
            TextLocation(file_path=file_path, line_start=problem_lines[0], line_end=problem_lines[-1])
            if problem_lines
            else TextLocation(file_path=file_path)
        )
        evidence_text = _model_text(
            item,
            ("evidence", "reason", "explanation"),
            "Модель оценила длинные строки как проблему читаемости.",
        )
        recommendation = _model_text(
            item,
            ("recommendation", "fix", "action"),
            "Разбить перегруженный фрагмент на короткие абзацы или пункты.",
        )
        return _finding(
            unit,
            self.name,
            Criterion.READABILITY,
            severity,
            verdict,
            _parse_confidence(item.get("confidence")),
            None,
            location,
            [Evidence(title="Оценка читаемости LLM", detail=evidence_text)],
            recommendation,
            True,
            extra={
                "candidate_count": len(long_lines),
                "problem_lines": problem_lines,
                "cache_hit": cache_hit,
                "examples": [candidate["text"] for candidate in candidates[:5]],
            },
            checked_at=_checked_at_from_record(record),
            prompt_version=self.prompt_version,
        )


class SpellingAndWordingChecker(BaseChecker):
    """Ищет точечные редакторские дефекты в учебном тексте."""

    name = "spelling_wording_checker"
    prompt_version = "spelling_wording_checker:v1"
    model_window_size = 25
    max_model_windows_per_unit = 12
    min_model_confidence = 0.65
    max_model_findings_per_file = 20
    MODEL_ISSUE_TYPES = {"typo", "tautology", "case", "wording", "quote_style"}
    RULE_ARTIFACT_SUFFIXES = {".drawio", ".xml", ".svg"}
    MAX_RULE_ARTIFACT_BYTES = 1_000_000
    ASP_NET_RE = re.compile(r"\basp\.?net\b", re.IGNORECASE)
    SYSTEM_PROMPT = """Ты редактор учебных материалов. Ищи только точечные дефекты текста:
опечатки, тавтологию, ошибки падежа/согласования, неудачные формулировки и смешение кавычек/бэктиков.
Не отмечай длинные строки, стиль заголовков, фактические ошибки, актуальность технологий, битые ссылки,
структуру чек-листа и любые проблемы кода.
Тебе дадут окно строк с исходными номерами. Возвращай только JSON:
{"findings":[{"line":12,"issue_type":"typo|tautology|case|wording|quote_style",
"quote":"точная цитата из строки","issue":"краткое обоснование на русском",
"suggestion":"конкретная правка на русском","confidence":0.0}]}.
Если точечных редакторских дефектов нет, верни {"findings":[]}.
Не придумывай проблему без точной цитаты из строки."""

    RULE_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
        (
            "typo",
            re.compile(r"\bCOMANY\b", re.IGNORECASE),
            "Опечатка в слове COMPANY.",
            "Исправить COMANY на COMPANY.",
        ),
        (
            "wording",
            re.compile(r"(?i)\bкомпилируем(?:ый|ого|ым)\s+многопоточный\s+язык\b"),
            "Формулировка про многопоточность языка звучит неточно и может сбивать студента.",
            "Заменить на «компилируемый язык программирования с поддержкой конкурентности».",
        ),
        (
            "wording",
            re.compile(r"(?i)\bустановка\s+для\s+редактора\s+Visual Studio Code\b"),
            "Неловкая формулировка про установку для редактора.",
            "Переформулировать как «хорошим выбором также является плагин Go для Visual Studio Code».",
        ),
        (
            "tautology",
            re.compile(r"(?i)\bзавершить\s+нажатие\s+нужно\s+нажатием\s+Enter\b"),
            "Тавтология: «нажатие нужно нажатием».",
            "Заменить на «завершить ввод нужно нажатием Enter».",
        ),
        (
            "case",
            re.compile(r"(?i)\bпоступают\s+неупорядоченные\b"),
            "Ошибка согласования: здесь нужен творительный падеж.",
            "Заменить на «поступают неупорядоченными».",
        ),
        (
            "case",
            re.compile(r"(?i)\bспециализаци(?:ю|я)\s+врача\b.*\bи\s+дата\s+визита\b"),
            "Ошибка падежного согласования в перечислении.",
            "Заменить «и дата визита» на «и дату визита».",
        ),
        (
            "case",
            re.compile(r"(?i)\bв\s+компании\s+работались\b"),
            "Ошибка согласования: компания не «работалась», в компании люди «работали».",
            "Заменить на «в компании работали».",
        ),
    )
    CODE_QUOTE_RE = re.compile(r"(«[A-Za-z][A-Za-z0-9 _./:-]{2,}»|\"[A-Za-z][A-Za-z0-9 _./:-]{2,}\")")

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        findings: list[Finding] = []
        seen: set[tuple[str, int, str, str]] = set()
        model_windows_used = 0
        for file in unit.files:
            if not self._is_editorial_file(file):
                continue
            lines = list(self._iter_prose_lines(file.text))
            for line_number, line in lines:
                findings.extend(self._rule_findings(unit, file, line_number, line, seen))
            if context.model_client is None or not self._uses_model_for_file(file):
                continue
            remaining_windows = self.max_model_windows_per_unit - model_windows_used
            if remaining_windows <= 0:
                continue
            model_findings, windows_used = self._model_findings(unit, file, lines, context, seen, remaining_windows)
            model_windows_used += windows_used
            findings.extend(model_findings)
        findings.extend(self._artifact_rule_findings(unit, seen))
        return findings

    def _artifact_rule_findings(
        self,
        unit: ContentUnit,
        seen: set[tuple[str, int, str, str]],
    ) -> list[Finding]:
        """Применяет строгие редакторские правила к тексту внутри диаграмм и XML-артефактов."""

        findings: list[Finding] = []
        loaded_paths = {file.relative_path.replace("\\", "/").lower() for file in unit.files}
        for path in sorted(unit.root_path.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in self.RULE_ARTIFACT_SUFFIXES:
                continue
            try:
                relative_path = path.relative_to(unit.root_path).as_posix()
            except ValueError:
                continue
            if relative_path.lower() in loaded_paths:
                continue
            try:
                if path.stat().st_size > self.MAX_RULE_ARTIFACT_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            synthetic_file = ContentFile(
                relative_path=relative_path,
                absolute_path=path,
                kind="text",
                text=text,
                size_bytes=path.stat().st_size,
            )
            for line_number, line in enumerate(text.splitlines(), start=1):
                if not line.strip():
                    continue
                findings.extend(self._rule_findings(unit, synthetic_file, line_number, line, seen))
        return findings

    def _rule_findings(
        self,
        unit: ContentUnit,
        file: ContentFile,
        line_number: int,
        line: str,
        seen: set[tuple[str, int, str, str]],
    ) -> list[Finding]:
        """Применяет дешёвые правила к одной строке."""

        findings: list[Finding] = []
        for issue_type, pattern, issue, suggestion in self.RULE_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            quote = match.group(0)
            finding = self._build_finding(unit, file.relative_path, line_number, quote, issue_type, issue, suggestion, 0.88, "rule")
            if self._remember(seen, finding):
                findings.append(finding)

        asp_net_match = self.ASP_NET_RE.search(line)
        if asp_net_match is not None and self._is_java_project(unit):
            finding = self._build_finding(
                unit,
                file.relative_path,
                line_number,
                asp_net_match.group(0),
                "wording",
                "В Java-проекте упомянут ASP.NET; это выглядит как ошибочная или нерелевантная технология в списке материалов.",
                "Заменить ASP.NET на релевантный Java-материал или явно пояснить, зачем здесь сравнение с ASP.NET.",
                0.9,
                "rule",
            )
            if self._remember(seen, finding):
                findings.append(finding)

        quote_style_match = self.CODE_QUOTE_RE.search(line)
        if quote_style_match is not None and "`" in line:
            quote = quote_style_match.group(0)
            finding = self._build_finding(
                unit,
                file.relative_path,
                line_number,
                quote,
                "quote_style",
                "В одной строке смешаны типографские кавычки и бэктики для технических фрагментов.",
                "Оформить технические сообщения единообразно, например через бэктики.",
                0.82,
                "rule",
            )
            if self._remember(seen, finding):
                findings.append(finding)
        return findings

    def _is_java_project(self, unit: ContentUnit) -> bool:
        """Определяет Java-проект по имени, README или исходным файлам."""

        if re.search(r"\bjava\b|_jv_|java", unit.name, re.IGNORECASE):
            return True
        for file in unit.files:
            if Path(file.relative_path).suffix.lower() == ".java":
                return True
            if file.kind == "readme" and re.search(r"\bjava\b", file.text[:4000], re.IGNORECASE):
                return True
        return any(path.suffix.lower() == ".java" for path in unit.root_path.rglob("*") if path.is_file())

    def _model_findings(
        self,
        unit: ContentUnit,
        file: ContentFile,
        lines: list[tuple[int, str]],
        context: CheckContext,
        seen: set[tuple[str, int, str, str]],
        remaining_windows: int,
    ) -> tuple[list[Finding], int]:
        """Запускает модельную вычитку по небольшим окнам строк."""

        findings: list[Finding] = []
        windows_used = 0
        if context.model_client is None:
            return findings, windows_used
        for window in self._line_windows(lines):
            if windows_used >= remaining_windows or len(findings) >= self.max_model_findings_per_file:
                break
            if not self._has_editorial_signal(window):
                continue
            windows_used += 1
            prompt = self._window_prompt(file.relative_path, window)
            cache_key = _hash_cache_key("spelling_wording", f"{file.relative_path}|{prompt}")
            try:
                record, cache_hit = _cached_model_json(
                    context,
                    "spelling_wording",
                    cache_key,
                    context.model_client,
                    self.SYSTEM_PROMPT,
                    prompt,
                    self.prompt_version,
                )
            except OpenRouterError as exc:
                findings.append(_external_check_error(unit, self.name, Criterion.READABILITY, exc))
                break

            for item in _result_items(record.get("response")):
                finding = self._finding_from_model_item(unit, file.relative_path, window, item, cache_hit, record)
                if finding is None or not self._remember(seen, finding):
                    continue
                findings.append(finding)
                if len(findings) >= self.max_model_findings_per_file:
                    break
        return findings, windows_used

    def _finding_from_model_item(
        self,
        unit: ContentUnit,
        file_path: str,
        window: list[tuple[int, str]],
        item: dict[str, Any],
        cache_hit: bool,
        record: dict[str, Any],
    ) -> Finding | None:
        """Валидирует JSON модели и превращает его в найденный случай."""

        line_number = _parse_optional_int(item.get("line") or item.get("line_start"))
        if line_number is None:
            return None
        line_map = dict(window)
        line_text = line_map.get(line_number)
        if line_text is None:
            return None

        issue_type = str(item.get("issue_type") or "").strip().lower()
        if issue_type not in self.MODEL_ISSUE_TYPES:
            return None
        quote = _optional_model_text(item.get("quote"))
        issue = _optional_model_text(item.get("issue") or item.get("evidence") or item.get("reason"))
        suggestion = _optional_model_text(item.get("suggestion") or item.get("recommendation") or item.get("fix"))
        confidence = _parse_confidence(item.get("confidence"))
        if not quote or not issue or not suggestion or confidence < self.min_model_confidence:
            return None
        if quote not in line_text:
            return None

        return self._build_finding(
            unit,
            file_path,
            line_number,
            quote,
            issue_type,
            issue,
            suggestion,
            confidence,
            "model",
            extra={"cache_hit": cache_hit, "window_size": len(window)},
            checked_at=_checked_at_from_record(record),
            prompt_version=self.prompt_version,
        )

    def _build_finding(
        self,
        unit: ContentUnit,
        file_path: str,
        line_number: int,
        quote: str,
        issue_type: str,
        issue: str,
        suggestion: str,
        confidence: float,
        source_kind: str,
        extra: dict[str, object] | None = None,
        checked_at: datetime | None = None,
        prompt_version: str | None = None,
    ) -> Finding:
        """Создаёт редакторское замечание в общем формате отчёта."""

        merged_extra: dict[str, object] = {"issue_type": issue_type, "source_kind": source_kind}
        if extra:
            merged_extra.update(extra)
        return _finding(
            unit,
            self.name,
            Criterion.READABILITY,
            Severity.MINOR,
            Verdict.WARNING,
            confidence,
            quote[:320],
            TextLocation(file_path=file_path, line_start=line_number, line_end=line_number),
            [Evidence(title="Редакторская проверка", detail=issue)],
            suggestion,
            True,
            extra=merged_extra,
            checked_at=checked_at,
            prompt_version=prompt_version,
        )

    def _window_prompt(self, file_path: str, window: list[tuple[int, str]]) -> str:
        """Собирает компактное окно строк с исходной нумерацией."""

        payload = {
            "file_path": file_path,
            "task": "Найти только точечные редакторские дефекты в этих строках.",
            "lines": [{"line": line_number, "text": line} for line_number, line in window],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _line_windows(self, lines: list[tuple[int, str]]) -> Iterable[list[tuple[int, str]]]:
        """Режет текст на окна по 20-30 строк без изменения исходных номеров."""

        for index in range(0, len(lines), self.model_window_size):
            window = lines[index : index + self.model_window_size]
            if window:
                yield window

    def _iter_prose_lines(self, text: str) -> Iterable[tuple[int, str]]:
        """Возвращает строки естественного языка, пропуская кодовые блоки."""

        in_fence = False
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            stripped = raw_line.strip()
            if stripped.startswith(("```", "~~~")):
                in_fence = not in_fence
                continue
            if in_fence or not stripped:
                continue
            prose_line = re.sub(r"^#{1,6}\s*", "", stripped)
            if self._is_non_prose_line(prose_line):
                continue
            yield line_number, prose_line

    def _has_editorial_signal(self, window: list[tuple[int, str]]) -> bool:
        """Отсекает окна без связного текста, чтобы не платить за разметку и код."""

        text = " ".join(line for _, line in window)
        letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
        return len(letters) >= 80 and any(char in text for char in ".:;!?—-")

    def _is_editorial_file(self, file: ContentFile) -> bool:
        """Ограничивает редакторскую проверку текстовыми материалами."""

        path = file.relative_path.lower()
        return file.kind in {"readme", "material", "checklist"} or path.endswith((".md", ".txt", ".yml", ".yaml"))

    def _uses_model_for_file(self, file: ContentFile) -> bool:
        """Модельная вычитка ограничена README и учебными материалами."""

        return file.kind in {"readme", "material"} or file.relative_path.lower().endswith(".md")

    def _is_non_prose_line(self, line: str) -> bool:
        """Убирает строки, где редакторская проверка почти всегда даёт шум."""

        if line.startswith("<!--"):
            return True
        if re.fullmatch(r"[-*_=\s]{3,}", line):
            return True
        if re.fullmatch(r"https?://\S+", line):
            return True
        if line.startswith("|") and line.endswith("|"):
            return True
        return False

    def _remember(self, seen: set[tuple[str, int, str, str]], finding: Finding) -> bool:
        """Не допускает дублей между правилами и модельным слоем."""

        location = finding.location
        line_start = location.line_start if location and location.line_start is not None else 0
        key = (
            location.file_path if location else "",
            line_start,
            str(finding.extra.get("issue_type") or ""),
            normalize_for_match(finding.quote or ""),
        )
        if key in seen:
            return False
        seen.add(key)
        return True


class LocalConsistencyChecker(BaseChecker):
    """Проверяет внутренние противоречия в README без обращения к внешним источникам."""

    name = "local_consistency_checker"
    ASC_RE = re.compile(r"(?i)\b(?:возрастан\w*|ascending|asc)\b")
    DESC_RE = re.compile(r"(?i)\b(?:убыван\w*|descending|desc)\b")
    SORT_RE = re.compile(r"(?i)\b(?:сортир\w*|отсортир\w*|sort(?:ed|ing)?|order(?:ed|ing)?)\b")
    WORD_AS_SYMBOL_RE = re.compile(
        r"(?i)\b(?:словом\s+является\s+любой\s+(?:символ|character)|"
        r"word\s+is\s+(?:any\s+)?(?:symbol|character))\b"
    )
    EMAIL_RE = re.compile(r"\bE-?mail\b", re.IGNORECASE)
    TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
    FIELD_LIST_MARKER_RE = re.compile(
        r"(?i)(?:следующ(?:ие|ими)\s+пол(?:я|ями)|пол(?:я|ями)\s*:|following\s+fields|fields\s*:)"
    )
    FUNCTION_LENGTH_RANGE_RE = re.compile(
        r"(?P<start>\d{1,3})\s*(?:-|–|—|to|до)\s*(?P<end>\d{1,3})\s*"
        r"(?:lines?|строк(?:и|ах|ам)?|стр\.)",
        re.IGNORECASE,
    )
    FUNCTION_CONTEXT_RE = re.compile(r"\b(functions?|methods?)\b|(?:функци\w*|метод\w*)", re.IGNORECASE)

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities, context
        findings: list[Finding] = []
        seen: set[tuple[str, int, str]] = set()
        for file in unit.files:
            if not self._is_readme(file):
                continue
            lines = [(index, line.rstrip()) for index, line in enumerate(file.text.splitlines(), start=1)]
            for finding in (
                self._sort_direction_findings(unit, file, lines)
                + self._word_definition_findings(unit, file, lines)
                + self._field_variant_findings(unit, file, lines)
                + self._table_field_mismatch_findings(unit, file, lines)
            ):
                if self._remember(seen, finding):
                    findings.append(finding)
        for finding in self._function_length_range_findings(unit):
            if self._remember(seen, finding):
                findings.append(finding)
        return findings

    def _sort_direction_findings(
        self,
        unit: ContentUnit,
        file: ContentFile,
        lines: list[tuple[int, str]],
    ) -> list[Finding]:
        """Ищет взаимоисключающие направления сортировки в одном разделе README."""

        findings: list[Finding] = []
        for section in self._sections(lines):
            directional_lines = [
                (line_number, text.strip())
                for line_number, text in section
                if self._has_sort_direction(text) and self.SORT_RE.search(text)
            ]
            for index, (left_line, left_text) in enumerate(directional_lines):
                left_direction = self._sort_direction(left_text)
                if left_direction is None or left_direction == "both":
                    continue
                for right_line, right_text in directional_lines[index + 1 :]:
                    if right_line - left_line > 25:
                        break
                    right_direction = self._sort_direction(right_text)
                    if right_direction is None or right_direction == "both" or right_direction == left_direction:
                        continue
                    quote = f"{left_text[:180]} / {right_text[:180]}"
                    findings.append(
                        self._build_finding(
                            unit,
                            file.relative_path,
                            left_line,
                            right_line,
                            quote,
                            "sort_direction_conflict",
                            "В одном разделе направление сортировки описано по-разному: сначала как возрастание, затем как убывание.",
                            "Оставить одно направление сортировки и привести описание, пункты требований и примеры к одному варианту.",
                            0.9,
                        )
                    )
                    break
        return findings

    def _word_definition_findings(
        self,
        unit: ContentUnit,
        file: ContentFile,
        lines: list[tuple[int, str]],
    ) -> list[Finding]:
        """Ловит локально некорректное определение слова как одного символа."""

        findings: list[Finding] = []
        for line_number, text in lines:
            match = self.WORD_AS_SYMBOL_RE.search(text)
            if match is None:
                continue
            findings.append(
                self._build_finding(
                    unit,
                    file.relative_path,
                    line_number,
                    line_number,
                    match.group(0),
                    "invalid_definition",
                    "Определение противоречит обычному смыслу: слово не является одним символом.",
                    "Заменить на «Словом является любая последовательность символов, разделённая пробелами».",
                    0.92,
                )
            )
        return findings

    def _field_variant_findings(
        self,
        unit: ContentUnit,
        file: ContentFile,
        lines: list[tuple[int, str]],
    ) -> list[Finding]:
        """Ищет разные написания одного и того же поля внутри README."""

        variants: dict[str, list[tuple[int, str]]] = {}
        for line_number, text in lines:
            for match in self.EMAIL_RE.finditer(text):
                raw = match.group(0)
                canonical = raw.lower().replace("-", "")
                variants.setdefault(canonical, []).append((line_number, raw))

        findings: list[Finding] = []
        for canonical, mentions in variants.items():
            raw_forms = {raw.lower() for _, raw in mentions}
            if canonical != "email" or len(raw_forms) < 2:
                continue
            line_number, raw = mentions[-1]
            findings.append(
                self._build_finding(
                    unit,
                    file.relative_path,
                    line_number,
                    line_number,
                    raw,
                    "field_name_variant",
                    "Одно поле внутри README названо разными вариантами: Email и E-mail.",
                    "Выбрать одно написание поля и использовать его во всём README, чек-листе и примерах.",
                    0.86,
                )
            )
        return findings

    def _table_field_mismatch_findings(
        self,
        unit: ContentUnit,
        file: ContentFile,
        lines: list[tuple[int, str]],
    ) -> list[Finding]:
        """Сверяет описанные поля с ближайшей Markdown-таблицей."""

        findings: list[Finding] = []
        for index, (line_number, text) in enumerate(lines):
            headers = self._table_headers(text)
            if not headers:
                continue
            expected = self._expected_fields_before(lines, index)
            if not expected:
                continue
            normalized_headers = {self._normalize_field_name(header) for header in headers}
            missing = [field for field in expected if self._normalize_field_name(field) not in normalized_headers]
            if not missing:
                continue
            findings.append(
                self._build_finding(
                    unit,
                    file.relative_path,
                    line_number,
                    line_number,
                    text.strip()[:260],
                    "table_description_mismatch",
                    f"Описание перед таблицей перечисляет поля, которых нет в заголовке таблицы: {', '.join(missing)}.",
                    "Синхронизировать описание и таблицу: добавить недостающие поля или исправить описание.",
                    0.84,
                    extra={"missing_fields": missing, "table_headers": headers},
                )
            )
        return findings

    def _function_length_range_findings(self, unit: ContentUnit) -> list[Finding]:
        """Сравнивает числовые требования к размеру функций между README и материалами."""

        ranges: list[tuple[tuple[int, int], str, int, str]] = []
        for file in unit.files:
            if not self._is_instruction_file(file):
                continue
            for line_number, text in enumerate(file.text.splitlines(), start=1):
                if not self.FUNCTION_CONTEXT_RE.search(text):
                    continue
                for match in self.FUNCTION_LENGTH_RANGE_RE.finditer(text):
                    start = int(match.group("start"))
                    end = int(match.group("end"))
                    if start > end:
                        start, end = end, start
                    ranges.append(((start, end), file.relative_path, line_number, text.strip()))
        if len({item[0] for item in ranges}) < 2:
            return []

        first = ranges[0]
        second = next(item for item in ranges[1:] if item[0] != first[0])
        quote = f"{first[3][:140]} / {second[3][:140]}"
        return [
            self._build_finding(
                unit,
                second[1],
                second[2],
                second[2],
                quote,
                "function_length_range_conflict",
                (
                    f"В разных материалах указаны разные ограничения размера функции: "
                    f"{first[0][0]}-{first[0][1]} строк и {second[0][0]}-{second[0][1]} строк."
                ),
                "Оставить одно ограничение размера функции и синхронизировать README, материалы и чек-лист.",
                0.88,
                extra={
                    "first_file": first[1],
                    "first_line": first[2],
                    "first_range": list(first[0]),
                    "second_range": list(second[0]),
                },
            )
        ]

    def _sections(self, lines: list[tuple[int, str]]) -> Iterable[list[tuple[int, str]]]:
        """Разбивает README на разделы по Markdown-заголовкам."""

        current: list[tuple[int, str]] = []
        for line_number, text in lines:
            if re.match(r"^\s*#{1,6}\s+", text) and current:
                yield current
                current = []
            current.append((line_number, text))
        if current:
            yield current

    def _has_sort_direction(self, text: str) -> bool:
        """Проверяет, есть ли в строке направление сортировки."""

        return bool(self.ASC_RE.search(text) or self.DESC_RE.search(text))

    def _sort_direction(self, text: str) -> str | None:
        """Нормализует направление сортировки в строке."""

        has_asc = bool(self.ASC_RE.search(text))
        has_desc = bool(self.DESC_RE.search(text))
        if has_asc and has_desc:
            return "both"
        if has_asc:
            return "asc"
        if has_desc:
            return "desc"
        return None

    def _table_headers(self, text: str) -> list[str]:
        """Достаёт заголовки Markdown-таблицы из строки."""

        match = self.TABLE_ROW_RE.match(text)
        if match is None:
            return []
        cells = [self._clean_field_name(cell) for cell in match.group(1).split("|")]
        headers = [cell for cell in cells if cell]
        if len(headers) < 2 or all(re.fullmatch(r":?-{3,}:?", cell) for cell in headers):
            return []
        return headers

    def _expected_fields_before(self, lines: list[tuple[int, str]], table_index: int) -> list[str]:
        """Ищет ближайшее текстовое описание ожидаемых полей перед таблицей."""

        for _line_number, text in reversed(lines[max(0, table_index - 8) : table_index]):
            if not self.FIELD_LIST_MARKER_RE.search(text):
                continue
            fields = self._extract_field_names(text)
            if fields:
                return fields
        return []

    def _extract_field_names(self, text: str) -> list[str]:
        """Вынимает имена полей из фразы с перечислением."""

        fields: list[str] = []
        for pattern in (r"`([^`]+)`", r"\*\*([^*]+)\*\*", r"<([^>]+)>"):
            fields.extend(match.strip() for match in re.findall(pattern, text) if match.strip())
        if fields:
            return fields

        tail = text.split(":", 1)[-1]
        parts = re.split(r",|\s+и\s+|\s+and\s+", tail)
        for part in parts:
            candidate = self._clean_field_name(part)
            if re.fullmatch(r"[A-Za-zА-Яа-яЁё][\w -]{1,40}", candidate):
                fields.append(candidate)
        return fields

    def _clean_field_name(self, value: str) -> str:
        """Очищает имя поля от Markdown-разметки и служебных символов."""

        cleaned = re.sub(r"[*_`]+", "", value).strip()
        return cleaned.strip(" .,:;")

    def _normalize_field_name(self, value: str) -> str:
        """Приводит имя поля к виду для сравнения."""

        return re.sub(r"[^0-9a-zа-яё]+", "", value.lower())

    def _is_readme(self, file: ContentFile) -> bool:
        """Ограничивает проверку README-файлами."""

        name = Path(file.relative_path).name.lower()
        return file.kind == "readme" or name.startswith("readme")

    def _is_instruction_file(self, file: ContentFile) -> bool:
        """Берёт README и методические материалы, где могут расходиться требования."""

        name = Path(file.relative_path).name.lower()
        return file.kind in {"readme", "material", "text"} or name.endswith((".md", ".txt"))

    def _build_finding(
        self,
        unit: ContentUnit,
        file_path: str,
        line_start: int,
        line_end: int,
        quote: str,
        issue_type: str,
        issue: str,
        recommendation: str,
        confidence: float,
        extra: dict[str, object] | None = None,
    ) -> Finding:
        """Создаёт находку по локальному противоречию."""

        merged_extra: dict[str, object] = {"issue_type": issue_type}
        if extra:
            merged_extra.update(extra)
        return _finding(
            unit,
            self.name,
            Criterion.FACTS,
            Severity.MINOR,
            Verdict.WARNING,
            confidence,
            quote[:320],
            TextLocation(file_path=file_path, line_start=line_start, line_end=line_end),
            [Evidence(title="Локальная согласованность", detail=issue)],
            recommendation,
            True,
            extra=merged_extra,
        )

    def _remember(self, seen: set[tuple[str, int, str]], finding: Finding) -> bool:
        """Не допускает повторов внутри одного README."""

        location = finding.location
        key = (
            location.file_path if location else "",
            location.line_start if location and location.line_start is not None else 0,
            str(finding.extra.get("issue_type") or ""),
        )
        if key in seen:
            return False
        seen.add(key)
        return True


class RightsAndOriginalityChecker(BaseChecker):
    """Проверяет права на материалы и признаки заимствований."""

    name = "rights_originality_checker"
    prompt_version = "rights_originality_checker:v1"
    max_external_lookups = 6
    PROVENANCE_SYSTEM_PROMPT = """Ты собираешь доказательства о происхождении и правах на ресурс из учебного контента.
Верни только JSON: {"likely_source":"","license":"","confidence":0.0,"sources":[{"title":"","url":""}],"note":""}.
Не делай вывод о нарушении: укажи вероятный источник и лицензию, если нашёл.
Если источников нет, оставь sources пустым и confidence низким. Пиши пояснения на русском."""

    def __init__(self, code_similarity_index: dict[str, list[CodeMatch]] | None = None) -> None:
        self.code_similarity_index = code_similarity_index or {}

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        signals: list[RightsSignal] = []
        signals.extend(self._project_license_signals(unit))
        signals.extend(self._dependency_license_signals(unit, context))
        signals.extend(image_rights_signals(unit, entities))
        signals.extend(self._dataset_rights_signals(unit))
        signals.extend(self._code_similarity_signals(unit))
        signals.extend(self._external_evidence_signals(unit, entities, context))

        findings: list[Finding] = []
        for signal in signals:
            severity, verdict, needs_review = grade_rights_signal(signal)
            findings.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.RIGHTS,
                    severity,
                    verdict,
                    signal.confidence,
                    signal.quote,
                    signal.location,
                    [Evidence(title=signal.title, detail=signal.detail, url=signal.url)],
                    signal.recommendation,
                    needs_review,
                    extra={
                        "kind": signal.kind,
                        "risk": signal.risk,
                        "deterministic": signal.deterministic,
                    },
                    source=signal.source,
                )
            )
        return findings

    def _project_license_signals(self, unit: ContentUnit) -> list[RightsSignal]:
        has_license_file = any(
            Path(file.relative_path).name.lower().startswith(("license", "notice"))
            for file in unit.files
        )
        readme_mentions_license = any(
            ("license" in file.text.lower() or "лицензи" in file.text.lower())
            for file in unit.files
            if file.kind == "readme"
        )
        scan = scan_project_licenses(unit.root_path)
        if has_license_file or readme_mentions_license or (scan is not None and scan.spdx):
            return []
        return [
            RightsSignal(
                kind="project_license",
                risk="no_license_only",
                deterministic=True,
                title="Лицензия проекта",
                detail="Не найден LICENSE/NOTICE и нет упоминания лицензии в README.",
                recommendation="Проверить, нужна ли лицензия для материалов и кода этой единицы.",
                confidence=0.6,
            )
        ]

    def _dependency_license_signals(self, unit: ContentUnit, context: CheckContext) -> list[RightsSignal]:
        manifests = [file for file in unit.files if Path(file.relative_path).name.lower() in MANIFEST_NAMES]
        signals: list[RightsSignal] = []
        seen: set[tuple[str, str, str]] = set()
        if context.settings.allow_network:
            registry_client = DependencyRegistryClient(context.settings.link_timeout_seconds)
            for candidate in extract_dependency_candidates(unit):
                if candidate.group in {"engine", "runtime"}:
                    continue
                metadata = _dependency_registry_metadata(candidate, registry_client, context)
                if metadata is None or not metadata.license_spdx:
                    continue
                signal = _dependency_license_signal(candidate.name, metadata.license_spdx, metadata.source_url, candidate.location)
                if signal is None:
                    continue
                key = (candidate.ecosystem, candidate.name.lower(), metadata.license_spdx)
                if key in seen:
                    continue
                seen.add(key)
                signals.append(signal)

        for package, spdx in resolve_dependency_licenses(manifests):
            signal = _dependency_license_signal(package, spdx, None, None)
            if signal is not None:
                key = ("local", package.lower(), spdx or "")
                if key not in seen:
                    seen.add(key)
                    signals.append(signal)
        return signals

    def _dataset_rights_signals(self, unit: ContentUnit) -> list[RightsSignal]:
        signals: list[RightsSignal] = []
        for file in unit.files:
            if file.kind not in {"readme", "material", "text"}:
                continue
            for line_number, line in enumerate(file.text.splitlines(), start=1):
                if not DATASET_RE.search(line):
                    continue
                if self._has_license_terms_near(file.text, line.strip()):
                    continue
                signals.append(
                    RightsSignal(
                        kind="dataset_rights",
                        risk="no_source",
                        deterministic=True,
                        title="Датасет без условий использования",
                        detail=f"Упоминание датасета без источника или лицензии: {line.strip()[:240]}",
                        recommendation="Добавить ссылку на датасет, его лицензию и условия использования.",
                        quote=line.strip()[:500],
                        location=TextLocation(file_path=file.relative_path, line_start=line_number, line_end=line_number),
                        confidence=0.7,
                    )
                )
        return signals[:5]

    def _code_similarity_signals(self, unit: ContentUnit) -> list[RightsSignal]:
        signals: list[RightsSignal] = []
        for match in self.code_similarity_index.get(unit.unit_id, []):
            if match.similarity < 0.8 or match.attributed:
                continue
            signals.append(
                RightsSignal(
                    kind="code_similarity",
                    risk="no_source",
                    deterministic=True,
                    title="Похожий код без атрибуции",
                    detail=f"Совпадение {match.similarity:.0%} с единицей {match.other_unit_id} без ссылки на источник.",
                    recommendation="Проверить заимствование между сдачами и добавить атрибуцию либо переработать код.",
                    source=match.other_unit_id,
                    confidence=min(1.0, max(0.0, match.similarity)),
                )
            )
        return signals

    def _external_evidence_signals(
        self,
        unit: ContentUnit,
        entities: list[ExtractedEntity],
        context: CheckContext,
    ) -> list[RightsSignal]:
        if not context.settings.allow_network or context.fact_model_client is None:
            return []

        signals: list[RightsSignal] = []
        for query in self._evidence_queries(unit, entities)[: self.max_external_lookups]:
            prompt = json.dumps(query, ensure_ascii=False, indent=2)
            try:
                record, _cache_hit = _cached_model_json(
                    context,
                    "rights",
                    _hash_cache_key("rights", prompt),
                    context.fact_model_client,
                    self.PROVENANCE_SYSTEM_PROMPT,
                    prompt,
                    self.prompt_version,
                )
            except OpenRouterError:
                continue
            item = _first_result_item(record.get("response")) or {}
            sources = _sources_from_item(item)
            if not sources:
                continue
            note = _model_text(item, ("note", "likely_source", "license"), "Поиск нашёл возможный источник ресурса.")
            signals.append(
                RightsSignal(
                    kind=str(query["kind"]),
                    risk="no_source",
                    deterministic=False,
                    title=str(query["title"]),
                    detail=note,
                    recommendation="Передать методологу: подтвердить источник и права по найденным ссылкам.",
                    quote=cast("str | None", query.get("quote")),
                    location=cast("TextLocation | None", query.get("location")),
                    source=_source_summary(sources),
                    url=_first_source_url(sources),
                    confidence=_parse_confidence(item.get("confidence")),
                )
            )
        return signals

    def _evidence_queries(self, unit: ContentUnit, entities: list[ExtractedEntity]) -> list[dict[str, object]]:
        queries: list[dict[str, object]] = []
        for file in unit.files:
            if file.kind not in {"readme", "material", "text"}:
                continue
            for line_number, line in enumerate(file.text.splitlines(), start=1):
                if DATASET_RE.search(line) and not self._has_license_terms_near(file.text, line.strip()):
                    queries.append(
                        {
                            "kind": "dataset_rights",
                            "title": "Возможный источник датасета",
                            "text": f"Найди источник, лицензию и условия использования датасета из фрагмента: {line.strip()}",
                            "quote": line.strip()[:500],
                            "location": TextLocation(file_path=file.relative_path, line_start=line_number, line_end=line_number),
                        }
                    )
        queries.extend(image_evidence_queries(entities))
        return queries

    def _has_license_terms_near(self, text: str, needle: str) -> bool:
        position = text.lower().find(needle.lower())
        if position < 0:
            return False
        fragment = text[max(0, position - 300) : position + len(needle) + 300]
        return bool(re.search(r"license|licence|terms|rights|лицензи|услови|права|cc-by|mit|apache", fragment, flags=re.IGNORECASE))


RightsChecker = RightsAndOriginalityChecker


class MarketFitChecker(BaseChecker):
    """Проверяет наличие прикладного бизнес-контекста в учебном проекте."""

    name = "market_fit_checker"
    prompt_version = "market_fit_checker:v1"
    signal_labels = {
        "real_data": "Работа с реальными данными",
        "business_context": "Бизнес-контекст",
        "success_metrics": "Бизнес-метрики или требования",
    }
    signal_patterns: dict[str, tuple[str, ...]] = {
        "real_data": (
            r"\b(dataset|datasets|real data|production data|historical data|customer data|sales data|transaction data|"
            r"kaggle|open data|huggingface datasets|uci repository|data source)\b",
            r"(датасет\w*|выборк\w*|реальн\w*\s+данн\w*|историческ\w*\s+данн\w*|открыт\w*\s+данн\w*|"
            r"обезличенн\w*\s+данн\w*|данн\w*\s+(?:клиент\w*|пользовател\w*|продаж\w*|транзакц\w*|заказ\w*|заявк\w*)|"
            r"набор\s+данн\w*)",
        ),
        "business_context": (
            r"\b(business problem|business case|customer problem|stakeholder|user persona|target audience|use case|client need|"
            r"business process|market segment|customer base|online booking|manual labour|manual labor|employee labour costs|"
            r"employee labor costs|barbershop|barbershops|booking system)\b",
            r"(бизнес[-\s]?задач\w*|бизнес[-\s]?контекст\w*|проблем\w*\s+бизнес\w*|заказчик\w*|"
            r"целев\w*\s+аудитори\w*|пользовательск\w*\s+сценари\w*|потребност\w*\s+(?:клиент\w*|пользовател\w*)|"
            r"бизнес[-\s]?процесс\w*|сегмент\w*\s+рынк\w*|клиентск\w*\s+баз\w*|онлайн[-\s]?запис\w*|"
            r"ручн\w*\s+труд\w*|трудозатрат\w*|барбершоп\w*)",
        ),
        "success_metrics": (
            r"\b(kpi|conversion|revenue|retention|churn|nps|ltv|cac|arpu|roi|gmv|mau|dau|sla|"
            r"business metric|business requirement|quality target|service level|time to resolution)\b",
            r"(бизнес[-\s]?метрик\w*|метрик\w*\s+успех\w*|kpi|конверси\w*|выручк\w*|удержан\w*|отток\w*|"
            r"средн\w*\s+чек\w*|стоимост\w*\s+(?:привлечени\w*|обработк\w*)|врем\w*\s+обработк\w*|\bsla\b|"
            r"бизнес[-\s]?требован\w*|требован\w*\s+бизнес\w*|целев\w*\s+показател\w*)",
        ),
    }
    SYSTEM_PROMPT = """Ты проверяешь соответствие учебного проекта прикладной рыночной задаче.
На входе есть результаты правил: наличие реальных данных, бизнес-контекста, бизнес-метрик или требований.
Проверь, не пропустили ли правила перефразированный бизнес-контекст.
Верни только JSON: {"verdict":"pass|warning|unknown","severity":"info|minor|major","confidence":0.0,
"evidence":"","recommendation":"","real_data":true,"business_context":true,"success_metrics":true}.
real_data=true ставь только при реальном, внешнем, публичном, историческом или production-like датасете; тестовые фикстуры, мок-данные и технические отчёты не считаются.
business_context=true ставь только если есть бизнес-проблема, целевая аудитория, заказчик, пользовательский сценарий или бизнес-процесс.
success_metrics=true ставь только если есть бизнес-метрики, бизнес-требования, целевые показатели или ограничения результата.
Не ставь severity='critical'. Если данных мало, ставь verdict='unknown'.
Все пояснения и рекомендации пиши на русском языке."""

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        signals = _market_fit_signals(unit, self.signal_patterns)
        if _market_fit_signal_count(signals) == 0:
            return []
        finding = self._finding_from_signals(unit, signals, model_item=None, record=None, cache_hit=False)
        if context.model_client is None or finding.verdict == Verdict.PASS:
            return [finding]

        model_result = self._model_refinement(unit, signals, context)
        if model_result is None:
            return [finding]
        item, record, cache_hit = model_result
        return [self._finding_from_signals(unit, signals, model_item=item, record=record, cache_hit=cache_hit)]

    def _model_refinement(
        self,
        unit: ContentUnit,
        signals: dict[str, dict[str, object]],
        context: CheckContext,
    ) -> tuple[dict[str, Any], dict[str, Any], bool] | None:
        """Уточняет слабые эвристические сигналы моделью."""

        if context.model_client is None:
            return None
        payload = {
            "unit": unit.name,
            "signals": signals,
            "context": _compact_unit_context(unit, limit=8000),
        }
        prompt = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            record, cache_hit = _cached_model_json(
                context,
                "market_fit",
                _hash_cache_key("market_fit", prompt),
                context.model_client,
                self.SYSTEM_PROMPT,
                prompt,
                self.prompt_version,
            )
        except OpenRouterError:
            return None
        item = _first_result_item(record.get("response"))
        return (item, record, cache_hit) if item is not None else None

    def _finding_from_signals(
        self,
        unit: ContentUnit,
        signals: dict[str, dict[str, object]],
        model_item: dict[str, Any] | None,
        record: dict[str, Any] | None,
        cache_hit: bool,
    ) -> Finding:
        """Собирает одну строку отчёта по трём под-оценкам."""

        merged = _merge_market_signals(signals, model_item)
        score = sum(1 for item in merged.values() if item["present"])
        verdict, severity = _market_fit_verdict(score)
        confidence = 0.65 + 0.1 * score
        if model_item is not None:
            verdict = _verdict_from_model_value(model_item.get("verdict"), verdict)
            severity = _enum_or_default(Severity, model_item.get("severity"), severity)
            if severity == Severity.CRITICAL:
                severity = Severity.MAJOR
            confidence = _parse_confidence(model_item.get("confidence"))

        evidence_text = _market_fit_evidence(merged, self.signal_labels)
        if model_item is not None:
            model_evidence = _optional_model_text(model_item.get("evidence"))
            if model_evidence:
                evidence_text = f"{evidence_text} Модель: {model_evidence}"
        recommendation = _market_fit_recommendation(merged, model_item)
        return _finding(
            unit,
            self.name,
            Criterion.MARKET_FIT,
            severity,
            verdict,
            confidence,
            None,
            _first_market_location(merged),
            [Evidence(title="Проверка соответствия рынку", detail=evidence_text)],
            recommendation,
            verdict != Verdict.PASS,
            extra={
                "market_fit_score": score,
                "sub_checks": merged,
                "model_refined": model_item is not None,
                "cache_hit": cache_hit,
            },
            checked_at=_checked_at_from_record(record) if record is not None else None,
            prompt_version=self.prompt_version if model_item is not None else None,
        )


class DependencyFreshnessChecker(BaseChecker):
    """Проверяет зависимости проекта через официальные реестры и запасной поиск."""

    name = "dependency_freshness_checker"
    prompt_version = "dependency_freshness_checker:v1"
    max_candidates = 50
    SYSTEM_PROMPT = """Ты проверяешь актуальность зависимости проекта.
Официальный реестр не дал уверенного ответа, поэтому нужен запасной поиск по открытым источникам.
Верни только JSON: {"verdict":"pass|warning|fail|unknown","severity":"info|minor|major","confidence":0.0,
"support_status":"","latest_version":"","recommended_version":"","evidence":"","sources":[{"title":"","url":""}],"recommendation":""}.
Не придумывай версии и источники. Если источников недостаточно, ставь verdict='unknown'.
Все пояснения и рекомендации пиши на русском языке."""

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        candidates = extract_dependency_candidates(unit)[: self.max_candidates]
        if not candidates:
            return []
        registry_candidates = [candidate for candidate in candidates if candidate.group not in {"engine", "runtime"}]
        if not registry_candidates:
            return []
        if not context.settings.allow_network and context.fact_model_client is None:
            return [self._network_required_finding(unit, registry_candidates)]

        findings: list[Finding] = []
        metadata_by_key: dict[tuple[str, str], DependencyMetadata] = {}
        registry_client = DependencyRegistryClient(context.settings.link_timeout_seconds)
        for candidate in registry_candidates:
            metadata = self._registry_metadata(candidate, registry_client, context)
            if metadata is None:
                fallback = self._fallback_model_finding(unit, candidate, context)
                if fallback is not None:
                    findings.append(fallback)
                continue
            metadata_by_key[dependency_identity(candidate)] = metadata
            dependency_finding = self._finding_from_dependency(unit, candidate, metadata)
            if dependency_finding is not None:
                findings.append(dependency_finding)

        findings.extend(self._compatibility_findings(unit, candidates, metadata_by_key))
        return findings

    def _registry_metadata(
        self,
        candidate: DependencyCandidate,
        registry_client: DependencyRegistryClient,
        context: CheckContext,
    ) -> DependencyMetadata | None:
        """Получает метаданные официального реестра с кэшированием."""

        return _dependency_registry_metadata(candidate, registry_client, context)

    def _finding_from_dependency(
        self,
        unit: ContentUnit,
        candidate: DependencyCandidate,
        metadata: DependencyMetadata,
    ) -> Finding | None:
        """Создаёт находку по актуальности одной зависимости."""

        if candidate.ecosystem == "docker" and candidate.spec == "latest":
            return _finding(
                unit,
                self.name,
                Criterion.TECHNOLOGY_FRESHNESS,
                Severity.MINOR,
                Verdict.WARNING,
                0.8,
                _dependency_quote(candidate),
                candidate.location,
                [Evidence(title="Docker", detail="Образ использует тег latest.", url=metadata.source_url)],
                "Закрепить конкретный тег образа, чтобы окружение проекта было воспроизводимым.",
                True,
                source=metadata.source_url,
                checked_at=metadata.checked_at,
                support_status="не закреплено",
            )
        if is_unbounded_spec(candidate.spec) and candidate.name.lower() not in {"python", "node"}:
            return _finding(
                unit,
                self.name,
                Criterion.TECHNOLOGY_FRESHNESS,
                Severity.INFO,
                Verdict.UNKNOWN,
                0.7,
                _dependency_quote(candidate),
                candidate.location,
                [Evidence(title="Официальный реестр", detail="Версия зависимости не ограничена.", url=metadata.source_url)],
                "Закрепить допустимый диапазон версий или подтвердить, что плавающая версия допустима.",
                True,
                source=metadata.source_url,
                checked_at=metadata.checked_at,
                support_status="не закреплено",
                latest_version=metadata.latest_version,
                recommended_version=metadata.latest_version,
            )
        if is_pinned_outdated(candidate.spec, metadata.latest_version):
            return _finding(
                unit,
                self.name,
                Criterion.TECHNOLOGY_FRESHNESS,
                Severity.MINOR,
                Verdict.WARNING,
                0.85,
                _dependency_quote(candidate),
                candidate.location,
                [Evidence(title="Официальный реестр", detail="Закреплённая версия ниже последней.", url=metadata.source_url)],
                "Проверить совместимость и обновить зависимость до поддерживаемой версии.",
                True,
                source=metadata.source_url,
                checked_at=metadata.checked_at,
                support_status="есть новая версия",
                latest_version=metadata.latest_version,
                recommended_version=metadata.latest_version,
            )
        return _finding(
            unit,
            self.name,
            Criterion.TECHNOLOGY_FRESHNESS,
            Severity.INFO,
            Verdict.PASS,
            0.75,
            _dependency_quote(candidate),
            candidate.location,
            [Evidence(title="Официальный реестр", detail="Зависимость проверена, явных проблем не найдено.", url=metadata.source_url)],
            "Действий не требуется; при обновлении проекта повторить проверку совместимости.",
            False,
            source=metadata.source_url,
            checked_at=metadata.checked_at,
            support_status="проверено",
            latest_version=metadata.latest_version,
        )

    def _compatibility_findings(
        self,
        unit: ContentUnit,
        candidates: list[DependencyCandidate],
        metadata_by_key: dict[tuple[str, str], DependencyMetadata],
    ) -> list[Finding]:
        findings: list[Finding] = []
        for issue in find_compatibility_issues(candidates, metadata_by_key):
            findings.append(_finding_from_dependency_issue(unit, self.name, issue))
        return findings

    def _fallback_model_finding(
        self,
        unit: ContentUnit,
        candidate: DependencyCandidate,
        context: CheckContext,
    ) -> Finding | None:
        """Использует Perplexity как запасной источник, если официальный реестр не дал ответ."""

        if context.fact_model_client is None:
            return _finding(
                unit,
                self.name,
                Criterion.TECHNOLOGY_FRESHNESS,
                Severity.INFO,
                Verdict.UNKNOWN,
                0.45,
                _dependency_quote(candidate),
                candidate.location,
                [Evidence(title="Официальный реестр", detail="Не удалось проверить зависимость через официальный источник.")],
                "Повторить проверку позже или включить модельный контур для запасной проверки.",
                True,
                support_status="не проверялось",
            )

        prompt = json.dumps(
            {
                "ecosystem": candidate.ecosystem,
                "name": candidate.name,
                "declared_version": candidate.spec,
                "file_path": candidate.location.file_path,
                "line_start": candidate.location.line_start,
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            record, cache_hit = _cached_model_json(
                context,
                "dependency_fallback",
                _hash_cache_key("dependency_fallback", prompt),
                context.fact_model_client,
                self.SYSTEM_PROMPT,
                prompt,
                self.prompt_version,
            )
        except OpenRouterError as exc:
            return _external_check_error(unit, self.name, Criterion.TECHNOLOGY_FRESHNESS, exc)

        item = _first_result_item(record.get("response"))
        if item is None:
            return None
        finding = _finding_from_dependency_model_item(unit, self.name, candidate, item, record, cache_hit, self.prompt_version)
        return finding if finding.verdict != Verdict.PASS else None

    def _network_required_finding(self, unit: ContentUnit, candidates: list[DependencyCandidate]) -> Finding:
        """Фиксирует, что зависимости найдены, но внешняя сверка не выполнялась."""

        preview = ", ".join(f"{item.name}{item.spec}" for item in candidates[:12])
        return _finding(
            unit,
            self.name,
            Criterion.TECHNOLOGY_FRESHNESS,
            Severity.INFO,
            Verdict.UNKNOWN,
            0.55,
            None,
            None,
            [Evidence(title="Зависимости", detail=f"Найдено зависимостей: {len(candidates)}. Пример: {preview}")],
            "Включить сеть или модельный контур, чтобы сверить версии и совместимость зависимостей.",
            True,
            extra={"candidate_count": len(candidates)},
            support_status="не проверялось",
        )


class RegionalAvailabilityChecker(BaseChecker):
    """Проверяет доступность сервисов и технологий из РФ по кураторской базе."""

    name = "regional_availability_checker"

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        rules = load_regional_availability_rules(context.settings.input_path)
        if not rules:
            return []

        findings: list[Finding] = []
        seen: set[tuple[str, str, str, int | None]] = set()
        for entity in entities:
            if entity.entity_type not in {EntityType.LINK, EntityType.TECHNOLOGY, EntityType.VERSION}:
                continue
            match = match_regional_availability(entity.value, rules)
            if match is None:
                continue
            key = (match.rule.pattern.lower(), entity.location.file_path, entity.value.lower(), entity.location.line_start)
            if key in seen:
                continue
            seen.add(key)
            findings.append(_finding_from_regional_availability_match(unit, self.name, match, entity))

        for candidate in extract_dependency_candidates(unit):
            match = match_regional_availability(candidate.name, rules)
            if match is None:
                continue
            key = (match.rule.pattern.lower(), candidate.location.file_path, candidate.name.lower(), candidate.location.line_start)
            if key in seen:
                continue
            seen.add(key)
            findings.append(_finding_from_regional_availability_match(unit, self.name, match, candidate))
        return findings


class CurriculumRelevanceChecker(BaseChecker):
    """Модельно-правиловая проверка методической уместности технологий и тем."""

    name = "curriculum_relevance_checker"
    prompt_version = "curriculum_relevance_checker:v1"
    model_context_limit = 14000
    min_model_confidence = 0.55
    allowed_criteria = {Criterion.CORRECTNESS, Criterion.TECHNOLOGY_FRESHNESS}
    min_confidence_by_issue_type = {
        "language_material_conflict": 0.62,
        "language_tooling_conflict": 0.62,
        "inappropriate_tool": 0.65,
        "outdated_approach": 0.72,
        "missing_key_topic": 0.74,
        "absolute_practice_claim": 0.7,
    }
    review_terms = (
        "C++ code style in Java or C# project",
        "ASP.NET",
        "Google C++ style in non-C++ project",
        "C99",
        "C11",
        "finite state machine",
        "debugger",
        "unsigned types",
        "define",
        "include",
        "preprocessor",
    )
    CPP_STYLE_RE = re.compile(r"\b(?:google\s+)?c\+\+[^.\n]{0,80}\b(?:style|guide|стайл|гайд)\b|\bcppguide\b", re.IGNORECASE)
    ASP_NET_RE = re.compile(r"\basp\.?net\b", re.IGNORECASE)
    MAKEFILE_RE = re.compile(r"\bmakefile\b|\bmake\b[^.\n]{0,80}\b(?:build|target|сборк)", re.IGNORECASE)
    JAVA_NATIVE_BUILD_RE = re.compile(r"\b(?:maven|gradle|pom\.xml|build\.gradle)\b", re.IGNORECASE)
    C_STANDARD_RE = re.compile(r"\b(?:c99|c11)\b", re.IGNORECASE)
    REVIEW_TOPIC_RE = re.compile(
        r"\b(finite\s+state\s+machine|state\s+machine|debugger|unsigned\s+types?|#?\s*define|#?\s*include|preprocessor)\b|"
        r"(конечн\w*\s+автомат\w*|отладчик\w*|беззнаков\w*\s+тип\w*|препроцессор\w*)",
        re.IGNORECASE,
    )
    JAVA_RE = re.compile(r"\bjava\b", re.IGNORECASE)
    C_LANGUAGE_RE = re.compile(r"(?<![a-zа-яё])c(?![a-zа-яё+#])", re.IGNORECASE)

    SYSTEM_PROMPT = """Ты эксперт-методолог и проверяешь учебный проект на уместность технологий, подходов и ключевых тем.
Верни только JSON: {"findings":[{"criterion":"correctness|technology_freshness","issue_type":"inappropriate_tool|outdated_approach|language_material_conflict|missing_key_topic","severity":"info|minor|major","verdict":"warning|fail|unknown","confidence":0.0,"quote":"","file_path":"","line_start":1,"evidence":"","recommendation":""}]}.
Ищи только методические проблемы, которые требуют внимания: инструмент не подходит цели курса; подход устарел именно как учебная практика; рекомендованный материал противоречит языку проекта; в задании не хватает ключевой темы, без которой студент не поймёт ожидаемое решение.
Особенно проверь: C++ code style в Java или C#, ASP.NET в Java, C99/C11, finite state machine, debugger, unsigned types, define/include/preprocessor.
Не проверяй битые ссылки, версии библиотек, права, язык перевода, орфографию, чек-лист и обычную фактологию: для этого есть отдельные модули.
Если проблема только в том, что есть более новая версия библиотеки или языка, не создавай находку.
Не создавай находку только из-за обычного упоминания Makefile, Google C++ Style Guide, debugger, include или preprocessor; нужна явная методическая несовместимость с языком, целью задания или ожидаемым способом решения.
Не ставь severity='critical'. Если убедительной проблемы нет, верни пустой список findings.
Все пояснения и рекомендации пиши на русском языке."""

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        rule_signals = self._rule_signals(unit)
        findings = [self._finding_from_signal(unit, signal) for signal in rule_signals if signal.get("strong")]
        if context.model_client is None:
            return findings

        prompt = self._model_prompt(unit, rule_signals)
        if not prompt.strip():
            return findings

        try:
            record, cache_hit = _cached_model_json(
                context,
                "curriculum_relevance",
                _hash_cache_key("curriculum_relevance", f"{self.prompt_version}|{prompt}"),
                context.model_client,
                self.SYSTEM_PROMPT,
                prompt,
                self.prompt_version,
            )
        except OpenRouterError as exc:
            findings.append(_external_check_error(unit, self.name, Criterion.CORRECTNESS, exc))
            return findings

        seen = {self._dedupe_key(finding) for finding in findings}
        for item in _result_items(record.get("response")):
            if self._is_uninformative_model_item(item):
                continue
            finding = self._finding_from_model_item(unit, item, record, cache_hit)
            key = self._dedupe_key(finding)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
        return findings

    def _rule_signals(self, unit: ContentUnit) -> list[dict[str, object]]:
        """Выделяет сильные и слабые методические сигналы для модели."""

        language_hints = self._language_hints(unit)
        signals: list[dict[str, object]] = []
        for file in unit.files:
            if not self._is_instruction_file(file):
                continue
            for line_number, line in enumerate(file.text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                signals.extend(self._line_signals(unit, file.relative_path, line_number, stripped, language_hints))
        signals.extend(self._unit_topic_signals(unit, language_hints))
        return self._dedupe_signals(signals)

    def _line_signals(
        self,
        unit: ContentUnit,
        file_path: str,
        line_number: int,
        line: str,
        language_hints: set[str],
    ) -> list[dict[str, object]]:
        """Проверяет одну строку на известные методические конфликтные паттерны."""

        del unit
        line_languages = self._line_language_hints(line)
        effective_languages = language_hints | line_languages
        signals: list[dict[str, object]] = []
        if self.CPP_STYLE_RE.search(line) and "java" in effective_languages:
            target_language = "Java"
            signals.append(
                self._signal(
                    "language_material_conflict",
                    file_path,
                    line_number,
                    line,
                    Criterion.CORRECTNESS,
                    Severity.MAJOR if target_language == "Java" else Severity.MINOR,
                    f"Рекомендован C++ style guide, хотя проект относится к {target_language}.",
                    "Заменить рекомендацию на стиль и материалы для фактического языка проекта.",
                    strong=True,
                )
            )
        if self.CPP_STYLE_RE.search(line) and "c" in effective_languages and "cpp" not in effective_languages:
            signals.append(
                self._signal(
                    "language_material_conflict",
                    file_path,
                    line_number,
                    line,
                    Criterion.CORRECTNESS,
                    Severity.MINOR,
                    "В задании по C рекомендован Google C++ Style Guide; это не стандартный ориентир для C.",
                    "Заменить рекомендацию на согласованный стиль для C или явно объяснить, почему выбран C++ style guide.",
                    strong=True,
                )
            )
        if self.ASP_NET_RE.search(line) and "java" in effective_languages:
            signals.append(
                self._signal(
                    "inappropriate_tool",
                    file_path,
                    line_number,
                    line,
                    Criterion.CORRECTNESS,
                    Severity.MAJOR,
                    "В Java-проекте упомянут ASP.NET как технологический ориентир.",
                    "Проверить стек задания и заменить ASP.NET на релевантный Java-инструмент или явно объяснить сравнение.",
                    strong=True,
                )
            )
        if self.MAKEFILE_RE.search(line) and "java" in effective_languages and not self.JAVA_NATIVE_BUILD_RE.search(line):
            signals.append(
                self._signal(
                    "language_tooling_conflict",
                    file_path,
                    line_number,
                    line,
                    Criterion.CORRECTNESS,
                    Severity.MINOR,
                    "В Java-проекте сборка задана через Makefile без объяснения, почему не используется стандартный Java-инструмент сборки.",
                    "Проверить, почему не используется стандартный для Java инструмент сборки вроде Maven или Gradle, либо явно объяснить учебную причину Makefile.",
                    strong=True,
                )
            )
        if self.C_STANDARD_RE.search(line):
            signals.append(
                self._signal(
                    "outdated_approach",
                    file_path,
                    line_number,
                    line,
                    Criterion.TECHNOLOGY_FRESHNESS,
                    Severity.MINOR,
                    "В задании явно упомянут стандарт C99/C11; нужно оценить, соответствует ли он текущей методической политике курса.",
                    "Подтвердить требуемый стандарт C в методических материалах или обновить формулировку до поддерживаемого стандарта.",
                    strong=False,
                )
            )
        if self.REVIEW_TOPIC_RE.search(line):
            signals.append(
                self._signal(
                    "topic_review",
                    file_path,
                    line_number,
                    line,
                    Criterion.CORRECTNESS,
                    Severity.INFO,
                    "Строка содержит тему из методического списка наблюдения.",
                    "Проверить, что тема уместна для цели задания и раскрыта на достаточном уровне.",
                    strong=False,
                )
            )
        if re.search(r"(?i)(?:отказаться\s+от\s+использования|avoid|never\s+use).{0,80}\bgoto\b", line):
            signals.append(
                self._signal(
                    "absolute_practice_claim",
                    file_path,
                    line_number,
                    line,
                    Criterion.CORRECTNESS,
                    Severity.MINOR,
                    "Материал формулирует отказ от goto как абсолютное правило; для учебного контекста C это требует пояснения, а не категоричного запрета.",
                    "Смягчить формулировку: объяснить, почему goto ограничивают в структурном программировании и в каких случаях он встречается в реальном C-коде.",
                    strong=True,
                )
            )
        return signals

    def _unit_topic_signals(self, unit: ContentUnit, language_hints: set[str]) -> list[dict[str, object]]:
        """Создаёт сигналы по отсутствующим ключевым темам, когда в проекте есть явные предпосылки."""

        if "c" not in language_hints:
            return []
        instruction_text = "\n".join(
            file.text for file in unit.files if self._is_instruction_file(file)
        )
        code_text = self._code_sample_text(unit)
        combined = f"{instruction_text}\n{code_text}"
        signals: list[dict[str, object]] = []
        anchor = self._first_instruction_anchor(unit)
        if anchor is None:
            return []
        file_path, line_number, quote = anchor

        if self._uses_preprocessor(code_text) and not self._explains_preprocessor(instruction_text):
            signals.append(
                self._signal(
                    "missing_key_topic",
                    file_path,
                    line_number,
                    quote,
                    Criterion.CORRECTNESS,
                    Severity.MINOR,
                    "В проекте используются `#define`/`#include`, но в учебном тексте нет отдельного объяснения препроцессора и директив.",
                    "Добавить короткое объяснение `#define`, `#include` и роли препроцессора перед заданиями, где они используются.",
                    strong=True,
                )
            )
        if self._looks_like_c_data_types_project(instruction_text) and not self._mentions_unsigned_types(instruction_text):
            signals.append(
                self._signal(
                    "missing_key_topic",
                    file_path,
                    line_number,
                    quote,
                    Criterion.CORRECTNESS,
                    Severity.MINOR,
                    "Проект знакомит с числовыми типами C, но не упоминает беззнаковые типы данных.",
                    "Добавить пример или пояснение по `unsigned`-типам и ограничениям их применения.",
                    strong=True,
                )
            )
        if self._looks_like_step_by_step_c_intro(instruction_text) and not self._mentions_debugger(instruction_text):
            signals.append(
                self._signal(
                    "missing_key_topic",
                    file_path,
                    line_number,
                    quote,
                    Criterion.CORRECTNESS,
                    Severity.INFO,
                    "В вводном C-проекте нет упоминания отладчика, хотя задания требуют понимать выполнение программы по шагам.",
                    "Добавить краткую подсказку по использованию отладчика или отдельное упражнение на пошаговый разбор программы.",
                    strong=True,
                )
            )
        if self._looks_like_console_game_project(combined) and not self._mentions_state_machine(instruction_text):
            signals.append(
                self._signal(
                    "missing_key_topic",
                    file_path,
                    line_number,
                    quote,
                    Criterion.CORRECTNESS,
                    Severity.INFO,
                    "В проекте с интерактивной логикой/игрой нет пояснения про конечный автомат.",
                    "Добавить описание конечного автомата как способа моделировать состояния игры или интерактивной программы.",
                    strong=True,
                )
            )
        return signals

    def _signal(
        self,
        issue_type: str,
        file_path: str,
        line_number: int,
        quote: str,
        criterion: Criterion,
        severity: Severity,
        evidence: str,
        recommendation: str,
        *,
        strong: bool,
    ) -> dict[str, object]:
        """Собирает единый контракт правила для отчёта и модельного уточнения."""

        return {
            "issue_type": issue_type,
            "file_path": file_path,
            "line_start": line_number,
            "quote": quote[:320],
            "criterion": criterion.value,
            "severity": severity.value,
            "evidence": evidence,
            "recommendation": recommendation,
            "strong": strong,
        }

    def _finding_from_signal(self, unit: ContentUnit, signal: dict[str, object]) -> Finding:
        """Преобразует сильный правиловой сигнал в строку отчёта."""

        criterion = _enum_or_default(Criterion, signal.get("criterion"), Criterion.CORRECTNESS)
        if criterion == Criterion.ACTUALITY:
            criterion = Criterion.TECHNOLOGY_FRESHNESS
        if criterion not in self.allowed_criteria:
            criterion = Criterion.CORRECTNESS
        severity = _enum_or_default(Severity, signal.get("severity"), Severity.MINOR)
        if severity == Severity.CRITICAL:
            severity = Severity.MAJOR
        file_path = str(signal.get("file_path") or "")
        line_start = _parse_optional_int(signal.get("line_start"))
        location = TextLocation(file_path=file_path, line_start=line_start, line_end=line_start) if file_path and line_start else None
        issue_type = str(signal.get("issue_type") or "methodology")
        return _finding(
            unit,
            self.name,
            criterion,
            severity,
            Verdict.WARNING,
            0.86,
            str(signal.get("quote") or "") or None,
            location,
            [Evidence(title="Методическая уместность", detail=str(signal.get("evidence") or ""))],
            str(signal.get("recommendation") or "Проверить методическую уместность формулировки."),
            True,
            extra={"issue_type": issue_type, "source": "rule", "cache_hit": False},
        )

    def _finding_from_model_item(
        self,
        unit: ContentUnit,
        item: dict[str, Any],
        record: dict[str, Any],
        cache_hit: bool,
    ) -> Finding:
        """Преобразует экспертный JSON модели в строгий доменный объект."""

        verdict = _verdict_from_model_value(item.get("verdict"), Verdict.UNKNOWN)
        criterion = _enum_or_default(Criterion, item.get("criterion"), Criterion.CORRECTNESS)
        if criterion == Criterion.ACTUALITY:
            criterion = Criterion.TECHNOLOGY_FRESHNESS
        if criterion not in self.allowed_criteria:
            criterion = Criterion.CORRECTNESS
        severity = _enum_or_default(Severity, item.get("severity"), _severity_from_verdict(verdict))
        if severity == Severity.CRITICAL:
            severity = Severity.MAJOR
        file_path = str(item.get("file_path") or "")
        line_start = _parse_optional_int(item.get("line_start"))
        location = TextLocation(file_path=file_path, line_start=line_start, line_end=line_start) if file_path and line_start else None
        issue_type = _model_text(item, ("issue_type",), "methodology")
        evidence_text = _model_text(item, ("evidence", "reason", "explanation"), "Методическая проверка без отдельного пояснения.")
        return _finding(
            unit,
            self.name,
            criterion,
            severity,
            verdict,
            _parse_confidence(item.get("confidence")),
            _optional_model_text(item.get("quote")),
            location,
            [Evidence(title="Методическая уместность", detail=evidence_text)],
            _model_text(item, ("recommendation", "suggestion"), "Проверить методическую уместность формулировки."),
            verdict != Verdict.PASS,
            extra={"issue_type": issue_type, "source": "model", "cache_hit": cache_hit, "model": record.get("model")},
            checked_at=_checked_at_from_record(record),
            prompt_version=self.prompt_version,
        )

    def _model_prompt(self, unit: ContentUnit, rule_signals: list[dict[str, object]]) -> str:
        """Формирует вход для методического эксперта с номерами строк."""

        numbered_context = self._numbered_context(unit, self.model_context_limit)
        if not numbered_context.strip():
            return ""
        payload = {
            "check_date": datetime.now(UTC).date().isoformat(),
            "unit": unit.name,
            "language_hints": sorted(self._language_hints(unit)),
            "focus_questions": [
                "уместен ли инструмент для цели курса",
                "не устарел ли подход как учебная практика",
                "не противоречат ли материалы рекомендованному языку проекта",
                "хватает ли ключевых тем для выполнения задания",
            ],
            "watch_terms": self.review_terms,
            "rule_candidates": rule_signals[:20],
            "numbered_context": numbered_context,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _numbered_context(self, unit: ContentUnit, limit: int) -> str:
        """Собирает контекст с номерами строк, чтобы модель вернула точную привязку."""

        chunks: list[str] = []
        total = 0
        for file in sorted(unit.files, key=lambda item: _model_context_priority(item.kind, item.relative_path)):
            if not self._is_instruction_file(file):
                continue
            lines = file.text.splitlines()
            numbered = "\n".join(f"{index}: {line}" for index, line in enumerate(lines, start=1))
            chunk = f"Файл: {file.relative_path}\n{numbered[:3500]}"
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                break
        return "\n\n---\n\n".join(chunks)[:limit]

    def _is_uninformative_model_item(self, item: dict[str, Any]) -> bool:
        """Отбрасывает пустые или слишком слабые экспертные ответы."""

        verdict = _verdict_from_model_value(item.get("verdict"), Verdict.UNKNOWN)
        if verdict == Verdict.PASS:
            return True
        if self._is_tooling_mention_without_curriculum_conflict(item):
            return True
        confidence = _parse_confidence(item.get("confidence"))
        issue_type = _model_text(item, ("issue_type",), "methodology")
        threshold = self.min_confidence_by_issue_type.get(issue_type, self.min_model_confidence)
        if confidence < threshold:
            return True
        return not any(
            _optional_model_text(item.get(key))
            for key in ("quote", "evidence", "reason", "explanation", "recommendation", "suggestion")
        )

    def _is_tooling_mention_without_curriculum_conflict(self, item: dict[str, Any]) -> bool:
        """Отбрасывает модельные строки, где инструмент просто упомянут без конфликта."""

        issue_type = _model_text(item, ("issue_type",), "methodology")
        if issue_type not in {
            "inappropriate_tool",
            "outdated_approach",
            "language_material_conflict",
            "language_tooling_conflict",
        }:
            return False
        text = " ".join(
            _optional_model_text(item.get(key)) or ""
            for key in ("quote", "evidence", "reason", "explanation", "recommendation", "suggestion")
        ).lower()
        if "makefile" in text or "google c++ style" in text or "cppguide" in text:
            return not any(marker in text for marker in (" java", "java-", "asp.net", "c#"))
        return False

    def _dedupe_key(self, finding: Finding) -> tuple[str, int, str, str]:
        """Ключ для удаления дублей между правилами и моделью."""

        location = finding.location
        return (
            location.file_path if location else "",
            location.line_start if location and location.line_start is not None else 0,
            str(finding.extra.get("issue_type") or ""),
            normalize_for_match(finding.quote or ""),
        )

    def _dedupe_signals(self, signals: list[dict[str, object]]) -> list[dict[str, object]]:
        """Удаляет повторные кандидаты по строке и типу проблемы."""

        result: list[dict[str, object]] = []
        seen: set[tuple[str, int, str, str]] = set()
        for signal in signals:
            key = (
                str(signal.get("file_path") or ""),
                int(str(signal.get("line_start") or 0)),
                str(signal.get("issue_type") or ""),
                normalize_for_match(str(signal.get("quote") or "")),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(signal)
        return result

    def _language_hints(self, unit: ContentUnit) -> set[str]:
        """Выводит примерный язык проекта из файлов, имени и текста задания."""

        hints: set[str] = set()
        name = unit.name.lower()
        if re.search(r"\bjava\b|_jv_|java", name):
            hints.add("java")
        if re.search(r"\bc\+\+\b|cpp|cxx", name):
            hints.add("cpp")
        if re.search(r"\bcsharp\b|c#", name):
            hints.add("csharp")
        for file in unit.files:
            suffix = Path(file.relative_path).suffix.lower()
            if suffix == ".java":
                hints.add("java")
            elif suffix in {".c", ".h"}:
                hints.add("c")
            elif suffix in {".cpp", ".cc", ".cxx", ".hpp"}:
                hints.add("cpp")
            elif suffix == ".cs":
                hints.add("csharp")
            elif suffix in {".js", ".ts"}:
                hints.add("javascript")
            if file.kind == "readme":
                hints.update(self._line_language_hints(file.text[:4000]))
        for path in unit.root_path.rglob("*"):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".java":
                hints.add("java")
            elif suffix in {".c", ".h"}:
                hints.add("c")
            elif suffix in {".cpp", ".cc", ".cxx", ".hpp"}:
                hints.add("cpp")
            elif suffix == ".cs":
                hints.add("csharp")
            elif suffix in {".js", ".ts"}:
                hints.add("javascript")
        return hints

    def _line_language_hints(self, text: str) -> set[str]:
        """Находит прямые упоминания языка в строке или коротком фрагменте."""

        hints: set[str] = set()
        if self.JAVA_RE.search(text):
            hints.add("java")
        if self.C_LANGUAGE_RE.search(text):
            hints.add("c")
        if re.search(r"\bc\+\+\b|cpp", text, re.IGNORECASE):
            hints.add("cpp")
        if re.search(r"\bc#\b|csharp", text, re.IGNORECASE):
            hints.add("csharp")
        return hints

    def _code_sample_text(self, unit: ContentUnit) -> str:
        """Собирает небольшой срез C-кода, чтобы понять, какие темы реально используются."""

        chunks: list[str] = []
        for file in unit.files:
            if Path(file.relative_path).suffix.lower() in {".c", ".h"}:
                chunks.append(file.text[:2000])
        if chunks:
            return "\n".join(chunks)
        for path in sorted(unit.root_path.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".c", ".h"}:
                continue
            try:
                if path.stat().st_size > 300_000:
                    continue
                chunks.append(path.read_text(encoding="utf-8", errors="ignore")[:2000])
            except OSError:
                continue
            if len(chunks) >= 20:
                break
        return "\n".join(chunks)

    def _first_instruction_anchor(self, unit: ContentUnit) -> tuple[str, int, str] | None:
        """Возвращает первую содержательную строку README как привязку для общих методических сигналов."""

        for file in sorted(unit.files, key=lambda item: _model_context_priority(item.kind, item.relative_path)):
            if not self._is_instruction_file(file):
                continue
            for line_number, line in enumerate(file.text.splitlines(), start=1):
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    return file.relative_path, line_number, stripped[:320]
        return None

    def _uses_preprocessor(self, code_text: str) -> bool:
        """Проверяет, используются ли в кодовых примерах директивы препроцессора."""

        return bool(re.search(r"(?m)^\s*#\s*(?:define|include)\b", code_text))

    def _explains_preprocessor(self, instruction_text: str) -> bool:
        """Отличает простое наличие `#include` в требовании от пояснения темы препроцессора."""

        lowered = instruction_text.lower()
        if re.search(r"препроцесс|preprocessor|pre-processor|директив[аы]\s+препроцесс", lowered):
            return True
        return bool(re.search(r"(?:что\s+такое|what\s+is|explain|объясн).{0,80}(?:#?\s*define|#?\s*include)", lowered))

    def _looks_like_c_data_types_project(self, instruction_text: str) -> bool:
        """Ищет признаки вводного проекта по типам данных C."""

        lowered = instruction_text.lower()
        return bool(
            re.search(r"\b(int|char|float|double)\b", lowered)
            and re.search(r"(тип(?:ы|ах)?\s+данных|data\s+types?|числ\w*\s+тип|number\s+types?)", lowered)
        )

    def _mentions_unsigned_types(self, instruction_text: str) -> bool:
        """Проверяет, раскрыты ли беззнаковые типы."""

        return bool(re.search(r"\bunsigned\b|беззнаков\w*", instruction_text, re.IGNORECASE))

    def _looks_like_step_by_step_c_intro(self, instruction_text: str) -> bool:
        """Находит вводный C-проект с несколькими заданиями и компиляцией."""

        lowered = instruction_text.lower()
        return bool(
            ("gcc" in lowered or "компил" in lowered)
            and len(re.findall(r"\b(?:quest|task|exercise|задани[ея])\s*0?\d+", lowered)) >= 3
        )

    def _mentions_debugger(self, instruction_text: str) -> bool:
        """Проверяет, есть ли в материале отладчик или пошаговое выполнение."""

        return bool(re.search(r"\bdebugger\b|\bgdb\b|отладчик\w*|пошагов\w*\s+выполн", instruction_text, re.IGNORECASE))

    def _looks_like_console_game_project(self, text: str) -> bool:
        """Определяет задания с простой интерактивной игрой или символьной графикой."""

        return bool(
            re.search(r"\b(game|pong|console\s+graphics|symbolic\s+graphics|ascii\s+graphics)\b", text, re.IGNORECASE)
            or re.search(r"символьн\w*\s+график|консольн\w*\s+игр|игр[ауы]\s+.+(?:клавиатур|управлен)", text, re.IGNORECASE)
        )

    def _mentions_state_machine(self, instruction_text: str) -> bool:
        """Проверяет, упоминается ли конечный автомат."""

        return bool(re.search(r"finite\s+state\s+machine|state\s+machine|конечн\w*\s+автомат", instruction_text, re.IGNORECASE))

    def _is_instruction_file(self, file: ContentFile) -> bool:
        """Ограничивает проверку методическими и заданческими материалами."""

        name = Path(file.relative_path).name.lower()
        return file.kind in {"readme", "checklist", "material", "text"} or name.endswith((".md", ".txt", ".yml", ".yaml"))


class ModelRubricChecker(BaseChecker):
    """Модельная проверка критериев, которые трудно закрыть правилами."""

    name = "model_rubric_checker"
    prompt_version = "model_rubric_checker:v1"

    SYSTEM_PROMPT = """Ты проверяешь учебный контент как инженер-методолог.
Верни только JSON: {"findings": [ ... ]}.
Каждый элемент: criterion, severity, verdict, confidence, quote, file_path, line_start, evidence, recommendation.
Критерий только один: workload.
Все текстовые поля ответа пиши на русском языке.
Не используй английский язык в рекомендации, если только цитируешь исходный термин из материала.
Не придумывай источники. Если доказательств мало, ставь verdict='unknown' и needs_human_review=true.
Для workload не ставь severity='critical': это консультационный критерий до калибровки на данных.
Для workload ставь verdict='unknown', если нет данных о реальном времени прохождения или трудозатратах.
Не проверяй фактологию, рынок, чек-лист, ссылки, права, язык, изображения и актуальность технологий: эти зоны закрывают отдельные специализированные модули."""

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        if context.model_client is None:
            return []
        compact_context = _compact_unit_context(unit)
        if not compact_context.strip():
            return []
        try:
            response = context.model_client.complete_json(self.SYSTEM_PROMPT, compact_context)
        except OpenRouterError as exc:
            return [
                _finding(
                    unit,
                    self.name,
                    Criterion.CORRECTNESS,
                    Severity.INFO,
                    Verdict.UNKNOWN,
                    0.3,
                    None,
                    None,
                    [Evidence(title="Модельная проверка", detail=str(exc))],
                    "Повторить модельную проверку после устранения ошибки провайдера.",
                    True,
                )
            ]
        context.record_model_result(context.model_client, cache_hit=False, prompt_version=self.prompt_version)

        findings: list[Finding] = []
        for item in response.get("findings", []):
            if not isinstance(item, dict):
                continue
            finding = _finding_from_model_item(unit, self.name, item, self.prompt_version)
            if finding.criterion not in MODEL_RUBRIC_ALLOWED_CRITERIA:
                continue
            if not _is_actionable_model_rubric_finding(finding):
                continue
            findings.append(finding)
        return findings


def default_checkers(
    use_model: bool,
    code_similarity_index: dict[str, list[CodeMatch]] | None = None,
    lean: bool = False,
) -> list[BaseChecker]:
    """Возвращает набор проверок для первого рабочего прототипа."""

    from content_factory.audit.extra_checkers import (
        CourseMaterialRelevanceChecker,
        CrossFileConsistencyChecker,
    )

    checkers: list[BaseChecker] = [
        StructureChecker(),
        BrokenUrlSyntaxChecker(),
        MarkdownStructureChecker(),
        LabelPunctuationChecker(),
        SpellingAndWordingChecker(),
        LocalConsistencyChecker(),
        ChecklistChecker(),
        ResourceAvailabilityChecker(),
        LinkChecker(),
        LocalLinkChecker(),
        LanguageCoverageChecker(),
        ExamPresenceChecker(),
        ImageQualityChecker(),
        RightsAndOriginalityChecker(code_similarity_index=code_similarity_index),
        MarketFitChecker(),
        DependencyFreshnessChecker(),
        RegionalAvailabilityChecker(),
        TechFreshnessChecker(),
        CurriculumRelevanceChecker(),
        CrossFileConsistencyChecker(),
        CourseMaterialRelevanceChecker(),
    ]
    if use_model:
        checkers.append(ReadmeFactActualityChecker())
        checkers.append(FactCheckerPerplexity())
        checkers.append(ModelRubricChecker())
    if lean:
        # Убираем дорогие/нулевые по точности правила: фактчек Perplexity, readme-факты, tech-freshness.
        _drop = {"fact_checker_perplexity", "readme_fact_actuality_checker", "tech_freshness_checker"}
        checkers = [c for c in checkers if c.name not in _drop]
    return checkers


def _entities_of_type(entities: Iterable[ExtractedEntity], entity_type: EntityType) -> Iterable[ExtractedEntity]:
    """Фильтруем сущности по типу."""

    return (entity for entity in entities if entity.entity_type == entity_type)


def _detect_language_profile(unit: ContentUnit) -> tuple[set[str], list[dict[str, str]]]:
    """Определяем языковые версии и сверяем явные суффиксы с содержимым."""

    languages: set[str] = set()
    mismatches: list[dict[str, str]] = []
    for file in unit.files:
        lower_path = file.relative_path.lower()
        expected = _language_from_path(lower_path)
        detected = _language_from_content(file.text)
        if expected:
            languages.add(expected)
        elif detected:
            languages.add(detected)
        elif file.kind == "readme":
            languages.add("ENG")

        if expected and detected and expected != detected:
            mismatches.append({"file_path": file.relative_path, "expected": expected, "detected": detected})
    return languages, mismatches


def _language_from_path(lower_path: str) -> str | None:
    """Достаём явный язык из имени файла."""

    if "_rus" in lower_path or "рус" in lower_path:
        return "RUS"
    if "_uzb" in lower_path or "_uz" in lower_path:
        return "UZ"
    if "_tg" in lower_path or "taj" in lower_path:
        return "TG"
    if "_eng" in lower_path:
        return "ENG"
    return None


def _language_from_content(text: str) -> str | None:
    """Дешёвый кросс-чек языка по содержимому без внешних зависимостей."""

    sample = text[:6000].lower()
    letters = [char for char in sample if char.isalpha()]
    if len(letters) < 40:
        return None

    cyrillic = sum(1 for char in letters if "а" <= char <= "я" or char == "ё")
    latin = sum(1 for char in letters if "a" <= char <= "z")
    tajik_markers = set("қғӯҳҷӣ")
    if any(char in tajik_markers for char in sample):
        return "TG"

    uzbek_markers = ("o‘", "g‘", "o'", "g'", "bo'lim", "uchun", "kerak", "loyiha", "tekshir")
    if latin > cyrillic * 2 and any(marker in sample for marker in uzbek_markers):
        return "UZ"
    if cyrillic > latin * 2:
        return "RUS"
    if latin > cyrillic * 2:
        return "ENG"
    return None


def _compact_unit_context(unit: ContentUnit, limit: int = 12000) -> str:
    """Собираем компактный контекст для модельной проверки."""

    chunks: list[str] = []
    ordered_files = sorted(unit.files, key=lambda file: _model_context_priority(file.kind, file.relative_path))
    for file in ordered_files:
        if file.kind not in {"readme", "checklist", "material"}:
            continue
        fragment = file.text[:3000]
        chunks.append(f"Файл: {file.relative_path}\n{fragment}")
        if sum(len(chunk) for chunk in chunks) >= limit:
            break
    return "\n\n---\n\n".join(chunks)[:limit]


def _dependency_registry_metadata(
    candidate: DependencyCandidate,
    registry_client: DependencyRegistryClient,
    context: CheckContext,
) -> DependencyMetadata | None:
    """Получает метаданные зависимости из реестра через общий кэш аудита."""

    if not context.settings.allow_network:
        return None
    cache_key = dependency_cache_key(candidate)
    if context.cache is not None:
        cached = context.cache.get("dependency_registry", cache_key)
        if cached is not None:
            try:
                return metadata_from_record(cached)
            except (KeyError, ValueError, TypeError):
                pass
    try:
        metadata = registry_client.fetch(candidate)
    except DependencyRegistryError:
        return None
    if context.cache is not None:
        context.cache.set("dependency_registry", cache_key, metadata_to_record(metadata))
        context.cache.save()
    return metadata


def _dependency_license_signal(
    package: str,
    spdx: str | None,
    source_url: str | None,
    location: TextLocation | None,
) -> RightsSignal | None:
    """Преобразует лицензию пакета в сигнал по правам."""

    policy = license_policy(spdx)
    if policy == "deny":
        return RightsSignal(
            kind="dependency_license",
            risk="violation",
            deterministic=True,
            title="Несовместимая лицензия зависимости",
            detail=f"Зависимость {package} указана с лицензией {spdx}, которая требует отдельного согласования.",
            recommendation=f"Заменить {package} на пермиссивный аналог или согласовать использование.",
            location=location,
            source=spdx,
            url=source_url,
            confidence=0.9,
        )
    if policy == "review" and spdx is not None:
        return RightsSignal(
            kind="dependency_license",
            risk="unverifiable",
            deterministic=True,
            title="Лицензия зависимости требует разбора",
            detail=f"{package}: {spdx}. Условия лицензии нужно проверить вручную.",
            recommendation=f"Проверить условия лицензии {package} и допустимость использования в учебном проекте.",
            location=location,
            source=spdx,
            url=source_url,
            confidence=0.55,
        )
    return None


def _finding_from_dependency_issue(unit: ContentUnit, checker_name: str, issue: CompatibilityIssue) -> Finding:
    """Преобразует конфликт зависимостей в строку отчёта."""

    detail = (
        f"{issue.dependency.name}{issue.dependency.spec} требует {issue.related_name}{issue.required_spec}; "
        f"в проекте указано: {_dependency_name_with_spec(issue.related_name, issue.declared_spec)}. {issue.reason}"
    )
    return _finding(
        unit,
        checker_name,
        Criterion.TECHNOLOGY_FRESHNESS,
        Severity.MAJOR,
        Verdict.WARNING,
        0.8,
        _dependency_quote(issue.dependency),
        issue.dependency.location,
        [Evidence(title="Совместимость зависимостей", detail=detail)],
        "Согласовать версии зависимостей или явно добавить недостающую peer-зависимость.",
        True,
        extra={
            "dependency": issue.dependency.name,
            "related_dependency": issue.related_name,
            "declared_spec": issue.declared_spec,
            "required_spec": issue.required_spec,
        },
        support_status="конфликт ограничений",
    )


def _finding_from_regional_availability_match(
    unit: ContentUnit,
    checker_name: str,
    match: RegionalAvailabilityMatch,
    source_entity: ExtractedEntity | DependencyCandidate,
) -> Finding:
    """Преобразует правило региональной доступности в строку отчёта."""

    severity = {
        "unavailable": Severity.MAJOR,
        "limited": Severity.MINOR,
        "manual_review": Severity.INFO,
    }.get(match.rule.status, Severity.INFO)
    status_label = {
        "unavailable": "недоступно в РФ",
        "limited": "ограничено в РФ",
        "manual_review": "проверить доступность из РФ",
    }.get(match.rule.status, "проверить доступность из РФ")
    quote = source_entity.quote if isinstance(source_entity, ExtractedEntity) else _dependency_quote(source_entity)
    return _finding(
        unit,
        checker_name,
        Criterion.TECHNOLOGY_FRESHNESS,
        severity,
        Verdict.WARNING if match.rule.status in {"unavailable", "limited"} else Verdict.UNKNOWN,
        0.85,
        quote,
        source_entity.location,
        [Evidence(title="Доступность из РФ", detail=match.rule.reason, url=match.rule.source)],
        "Заменить сервис на доступный аналог, добавить зеркало или явно описать обходной вариант для учебного проекта.",
        True,
        extra={
            "regional_profile": "ru",
            "matched_value": match.value,
            "matched_pattern": match.rule.pattern,
            "rule_updated_at": match.rule.updated_at,
        },
        source=match.rule.source,
        support_status=status_label,
    )


def _finding_from_dependency_model_item(
    unit: ContentUnit,
    checker_name: str,
    candidate: DependencyCandidate,
    item: dict[str, Any],
    record: dict[str, Any],
    cache_hit: bool,
    prompt_version: str,
) -> Finding:
    """Преобразует запасную проверку зависимости через Perplexity в строку отчёта."""

    verdict = _verdict_from_model_value(item.get("verdict"), Verdict.UNKNOWN)
    severity = _enum_or_default(Severity, item.get("severity"), _severity_from_verdict(verdict))
    evidence_text = _model_text(item, ("evidence", "reason", "explanation"), "Запасная проверка зависимости без пояснения.")
    sources = _sources_from_item(item)
    return _finding(
        unit,
        checker_name,
        Criterion.TECHNOLOGY_FRESHNESS,
        severity,
        verdict,
        _parse_confidence(item.get("confidence")),
        _dependency_quote(candidate),
        candidate.location,
        [Evidence(title="Запасная проверка зависимости", detail=evidence_text, url=_first_source_url(sources))],
        str(item.get("recommendation") or "Проверить зависимость вручную."),
        verdict != Verdict.PASS,
        extra={"cache_hit": cache_hit, "ecosystem": candidate.ecosystem},
        source=_source_summary(sources),
        checked_at=_checked_at_from_record(record),
        support_status=str(item.get("support_status") or _support_status_from_verdict(verdict)),
        latest_version=_optional_model_text(item.get("latest_version")),
        recommended_version=_optional_model_text(item.get("recommended_version")),
        prompt_version=prompt_version,
    )


def _dependency_quote(candidate: DependencyCandidate) -> str:
    """Показывает зависимость в коротком виде для цитаты отчёта."""

    return _dependency_name_with_spec(candidate.name, candidate.spec)


def _dependency_name_with_spec(name: str, spec: str) -> str:
    """Склеивает имя и ограничение версии без лишних пробелов."""

    return f"{name}{spec}" if spec else f"{name}: не указано"


def _finding_from_model_item(
    unit: ContentUnit,
    checker_name: str,
    item: dict[str, object],
    prompt_version: str | None = None,
) -> Finding:
    """Преобразуем ответ модели в строгий доменный объект."""

    criterion = _enum_or_default(Criterion, item.get("criterion"), Criterion.CORRECTNESS)
    severity = _enum_or_default(Severity, item.get("severity"), Severity.INFO)
    verdict = _enum_or_default(Verdict, item.get("verdict"), Verdict.UNKNOWN)
    file_path = str(item.get("file_path") or "") or None
    line_start = _parse_optional_int(item.get("line_start"))
    location = TextLocation(file_path=file_path or "", line_start=line_start, line_end=line_start) if file_path and line_start else None
    evidence_text = str(item.get("evidence") or "Модельная проверка без отдельного источника.")
    sources = _sources_from_item(item)
    return _finding(
        unit,
        checker_name,
        criterion,
        severity,
        verdict,
        _parse_confidence(item.get("confidence")),
        str(item.get("quote") or "") or None,
        location,
        [Evidence(title="Модельная проверка", detail=evidence_text)],
        str(item.get("recommendation") or "Проверить случай вручную."),
        True,
        source=_source_summary(sources),
        prompt_version=prompt_version,
    )


def _is_actionable_model_rubric_finding(finding: Finding) -> bool:
    """Отсекает общие advisory-ответы модели без конкретного проверяемого места."""

    if finding.verdict == Verdict.UNKNOWN:
        return False
    if finding.confidence < 0.7:
        return False
    if finding.location is None and not finding.quote:
        return False
    return True




def _readability_problem_lines(value: object) -> list[int]:
    """Нормализуем список строк, которые модель сочла проблемными для чтения."""

    if value is None:
        return []
    raw_values = value if isinstance(value, list) else [value]
    lines: list[int] = []
    for raw_value in raw_values:
        line = _parse_optional_int(raw_value)
        if line is not None and line > 0 and line not in lines:
            lines.append(line)
    return sorted(lines)
