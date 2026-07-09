"""Language coverage and readability audit checks.

Вынесено из ``checks.py``; импортирует только листовой ``checker_base`` и
доменные типы. ``checks`` реэкспортирует классы, поэтому ``default_checkers`` и
существующие тестовые импорты не меняются.
"""

from __future__ import annotations

import json
import re

from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _checked_at_from_record,
    _enum_or_default,
    _external_check_error,
    _finding,
    _first_result_item,
    _hash_cache_key,
    _model_text,
    _parse_confidence,
    _parse_optional_int,
)
from content_factory.audit.domain import (
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
