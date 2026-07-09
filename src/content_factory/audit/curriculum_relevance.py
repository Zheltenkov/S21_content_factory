"""Проверка соответствия учебного проекта заявленной программе (кривой обучения).

Сопоставляет сигналы из материалов (язык, тема, инструментарий, уровень) с
ожидаемой программой курса, выявляет пропущенные ключевые темы, устаревшие
подходы и конфликты инструментов — детерминированными правилами и модельной
проверкой. Полностью самодостаточен: вынесено из ``checks.py``; импортирует
только листовой ``checker_base`` + доменные типы (никогда ``checks``). ``checks``
реэкспортирует ``CurriculumRelevanceChecker``, поэтому ``default_checkers`` и
тесты не меняются.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _checked_at_from_record,
    _enum_or_default,
    _external_check_error,
    _finding,
    _hash_cache_key,
    _model_context_priority,
    _model_text,
    _optional_model_text,
    _parse_confidence,
    _parse_optional_int,
    _result_items,
    _severity_from_verdict,
    _verdict_from_model_value,
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
