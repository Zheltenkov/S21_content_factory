"""Детерминированные проверки структуры и оформления учебных материалов.

Не обращаются к внешним источникам и модели: проверяют наличие минимальной
структуры проекта, корректность Markdown, синтаксис ссылок, пунктуацию подписей
и присутствие раздела с экзаменом/оценкой. Вынесено из ``checks.py``; импортирует
только листовой ``checker_base`` + доменные типы (никогда ``checks``). ``checks``
реэкспортирует классы, поэтому ``default_checkers`` и тесты не меняются.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from content_factory.audit.checker_base import BaseChecker, CheckContext, _finding
from content_factory.audit.domain import (
    ContentFile,
    ContentUnit,
    Criterion,
    Evidence,
    ExtractedEntity,
    Finding,
    Severity,
    TextLocation,
    Verdict,
)
from content_factory.audit.text_utils import normalize_for_match


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
