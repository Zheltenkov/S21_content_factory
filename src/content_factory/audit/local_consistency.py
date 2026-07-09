"""Проверка внутренней согласованности README без внешних источников.

Ищет противоречия внутри одного материала: расхождения списков полей и таблиц,
конфликтующие указания направления сортировки, диапазоны длины функций,
несогласованные определения. Полностью детерминирован и самодостаточен: вынесено
из ``checks.py``; импортирует только листовой ``checker_base`` + доменные типы
(никогда ``checks``). ``checks`` реэкспортирует ``LocalConsistencyChecker``,
поэтому ``default_checkers`` и тесты не меняются.
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
