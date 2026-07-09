"""Проверка орфографии, пунктуации и точечных редакторских дефектов текста.

Совмещает дешёвые детерминированные правила с модельной вычиткой по небольшим
окнам строк. Полностью самодостаточен: вынесено из ``checks.py``; импортирует
только листовой ``checker_base`` + доменные типы (никогда ``checks``), граф
ацикличен. ``checks`` реэкспортирует ``SpellingAndWordingChecker``, поэтому
``default_checkers`` и тесты не меняются.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _checked_at_from_record,
    _external_check_error,
    _finding,
    _hash_cache_key,
    _optional_model_text,
    _parse_confidence,
    _parse_optional_int,
    _result_items,
)
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
from content_factory.audit.openrouter import OpenRouterError
from content_factory.audit.text_utils import normalize_for_match


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
