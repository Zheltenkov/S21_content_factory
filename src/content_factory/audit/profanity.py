"""Проверка недопустимой (обсценной) лексики в тексте учебных материалов.

Детектор мата/обсценной лексики, устойчивый к типовой маскировке. Две ступени:

1. **Детерминированная** (всегда, без сети/LLM): текст «разворачивается» — латинские гомоглифы
   и leet приводятся к кириллице (1:1, смещения сохраняются), затем корни мата ищутся regex'ом,
   допускающим 0-2 символа пунктуации МЕЖДУ буквами (``х.у.й``, ``х-у-й``, ``xуй``). Граница
   ``(?<![а-яё])`` перед корнем не даёт ложных срабатываний внутри легитимных слов
   (``страхуй``, ``психуй`` — перед корнем стоит буква). Морфология — суффикс ``[а-яё]*``.
2. **LLM-добивка** (только при ``use_model``): один запрос ищет замаскированные формы, которые
   не берёт простой фильтр — разрядка пробелами (``х у й``), буква заменена символом (``х*й``),
   транслит. Высокая точность (порог уверенности).

Самодостаточен: импортирует только листовой ``checker_base`` + доменные типы. ``checks``
реэкспортирует ``ProfanityChecker``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from content_factory.audit.checker_base import (
    BaseChecker,
    CheckContext,
    _cached_model_json,
    _checked_at_from_record,
    _external_check_error,
    _finding,
    _hash_cache_key,
    _model_text,
    _optional_model_text,
    _parse_confidence,
    _parse_optional_int,
    _result_items,
)
from content_factory.audit.domain import (
    ContentUnit,
    Criterion,
    Evidence,
    ExtractedEntity,
    Finding,
    Severity,
    Verdict,
)
from content_factory.audit.openrouter import OpenRouterError
from content_factory.audit.text_utils import line_for_offset, quote_around

#: Латинские гомоглифы и leet → кириллица (1:1, длина строки не меняется — смещения совпадают).
_FOLD = str.maketrans(
    {
        "a": "а", "e": "е", "o": "о", "c": "с", "p": "р", "x": "х", "y": "у", "k": "к", "m": "м",
        "t": "т", "u": "и", "0": "о", "3": "е", "@": "а", "$": "с",
    }
)

#: Однозначные корни мата (без ambiguous типа «хер»/«сук»). Суффиксы добираются морфологией.
_ROOTS = (
    "хуй", "хуе", "хуё", "хуя", "хую", "пизд", "пезд", "ебал", "ебан", "ебат", "ебуч", "ебло",
    "еблан", "ёбан", "бляд", "блят", "муда", "мудо", "мудя", "мудак", "залуп", "гандон", "гондон",
    "пидор", "пидар", "пидр",
)

#: Приставки, с которыми мат-корень образует слово (нахуй, похую, охуеть, …).
_PREFIXES = (
    "недо", "пере", "разъ", "подо", "ото", "обо", "на", "по", "от", "до", "за", "ни", "вы",
    "при", "про", "под", "раз", "со", "о", "у",
)

#: Между буквами корня допускаем 0-2 символа пунктуации (не пробел, не буква/цифра).
_LETTER_JUNK = r"[^\w\s]{0,2}"


def _root_regex(root: str) -> str:
    return _LETTER_JUNK.join(re.escape(char) for char in root)


_PROFANITY_RE = re.compile(
    r"(?<![а-яёa-z0-9])"
    r"(?:" + "|".join(_PREFIXES) + r")?"
    r"(?:" + "|".join(_root_regex(root) + r"[а-яё]*" for root in _ROOTS) + r")",
    re.IGNORECASE,
)

_SCAN_KINDS = {"readme", "material", "text", "checklist"}


class ProfanityChecker(BaseChecker):
    """Находит обсценную лексику (в т.ч. замаскированную) в тексте материалов."""

    name = "profanity_checker"
    prompt_version = "profanity_checker:v1"
    model_context_limit = 14000
    min_model_confidence = 0.75

    SYSTEM_PROMPT = (
        "Ты модератор учебных README. Тебе дают текст с номерами строк. Найди ЗАМАСКИРОВАННУЮ "
        "обсценную лексику (русский мат и грубые оскорбления), которую мог обойти простой фильтр: "
        "разрядка пробелами (х у й), буква заменена символом (х*й, п@зда), транслит (blyad), "
        "необычные символы. НЕ включай явный чистый мат без маскировки — его уже ловит фильтр.\n"
        "Высокая точность: не флагай нейтральные слова, совпавшие по буквам (страхуй, психуй, "
        "мудрый). Сомневаешься — не флагай.\n"
        'Ответ — строго JSON {"items": [{"quote": <строка целиком>, "line": <номер>, '
        '"reason": <что замаскировано>, "confidence": 0..1}]}. Если ничего нет — {"items": []}.'
    )

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        findings: list[Finding] = []
        seen: set[tuple[str, int | None, str]] = set()

        # Ступень 1 — детерминированная, по каждому текстовому файлу.
        for file in unit.files:
            if file.kind not in _SCAN_KINDS:
                continue
            folded = file.text.translate(_FOLD).lower()
            for match in _PROFANITY_RE.finditer(folded):
                start, end = match.start(), match.end()
                line = line_for_offset(file.text, start)
                quote = quote_around(file.text, start, end)
                key = (file.relative_path, line, quote)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    _finding(
                        unit,
                        self.name,
                        Criterion.PROFANITY,
                        Severity.MAJOR,
                        Verdict.FAIL,
                        0.95,
                        quote,
                        _location(file.relative_path, line),
                        [Evidence(title="Недопустимая лексика", detail="Обнаружена обсценная лексика в тексте.")],
                        "Убрать обсценную лексику из текста материала.",
                        True,
                        extra={"issue_type": "profanity", "source": "rule", "match": file.text[start:end][:60]},
                    )
                )

        # Ступень 2 — LLM-добивка на скрытую маскировку.
        if context.model_client is not None:
            findings.extend(self._model_findings(unit, context, seen))
        return findings

    def _model_findings(
        self,
        unit: ContentUnit,
        context: CheckContext,
        seen: set[tuple[str, int | None, str]],
    ) -> list[Finding]:
        client = context.model_client
        if client is None:
            return []
        readme_path, body = self._numbered_body(unit)
        if not body.strip():
            return []
        prompt = json.dumps({"text": body}, ensure_ascii=False, indent=2)
        try:
            record, cache_hit = _cached_model_json(
                context,
                "profanity",
                _hash_cache_key("profanity", f"{self.prompt_version}|{prompt}"),
                client,
                self.SYSTEM_PROMPT,
                prompt,
                self.prompt_version,
            )
        except OpenRouterError as exc:
            return [_external_check_error(unit, self.name, Criterion.PROFANITY, exc)]

        results: list[Finding] = []
        for item in _result_items(record.get("response")):
            if _parse_confidence(item.get("confidence")) < self.min_model_confidence:
                continue
            quote = _optional_model_text(item.get("quote")) or ""
            reason = _model_text(item, ("reason", "explanation"), "Замаскированная обсценная лексика.")
            if not quote:
                continue
            line = _parse_optional_int(item.get("line") if item.get("line") is not None else item.get("line_start"))
            key = (readme_path, line, quote)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                _finding(
                    unit,
                    self.name,
                    Criterion.PROFANITY,
                    Severity.MAJOR,
                    Verdict.FAIL,
                    _parse_confidence(item.get("confidence")),
                    quote,
                    _location(readme_path, line),
                    [Evidence(title="Недопустимая лексика", detail=reason)],
                    "Убрать замаскированную обсценную лексику из текста материала.",
                    True,
                    extra={"issue_type": "profanity", "source": "model", "cache_hit": cache_hit, "model": record.get("model")},
                    checked_at=_checked_at_from_record(record),
                    prompt_version=self.prompt_version,
                )
            )
        return results

    def _numbered_body(self, unit: ContentUnit) -> tuple[str, str]:
        """Пронумерованный текст материалов (README первым) для модельной ступени."""
        files = [file for file in unit.files if file.kind in _SCAN_KINDS]
        files.sort(key=lambda file: (0 if file.kind == "readme" else 1, file.relative_path))
        primary = files[0].relative_path if files else ""
        chunks: list[str] = []
        total = 0
        for file in files:
            numbered = "\n".join(f"{index}: {line}" for index, line in enumerate(file.text.splitlines(), start=1))
            chunk = f"Файл: {file.relative_path}\n{numbered}"
            chunks.append(chunk)
            total += len(chunk)
            if total >= self.model_context_limit:
                break
        return primary, "\n\n---\n\n".join(chunks)[: self.model_context_limit]


def _location(file_path: str, line: int | None) -> Any:
    from content_factory.audit.domain import TextLocation

    if not file_path:
        return None
    return TextLocation(file_path=file_path, line_start=line, line_end=line) if line else TextLocation(file_path=file_path)
