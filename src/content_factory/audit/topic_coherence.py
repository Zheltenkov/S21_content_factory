"""Проверка тематической связности README.

Сверяет тело README с ЕГО СОБСТВЕННОЙ заявленной темой (заголовок/вступление README + название и
пункты ``check-list.yml``) и находит предложения, которые явно не по теме или прямо противоречат
теме — например, тема про управление проектами, а во фрагменте предложение про черепах.

Это НЕ ``CurriculumRelevanceChecker`` (тот сверяет контент с внешней программой курса). Здесь —
внутренняя связность документа. Полностью самодостаточен: импортирует только листовой
``checker_base`` + доменные типы; ``checks`` реэкспортирует ``TopicCoherenceChecker``.

Модельная проверка: один запрос на README (кэшируется по хешу темы+тела), высокая точность —
флагаем только уверенные случаи (порог ``min_model_confidence``).
"""

from __future__ import annotations

import json
from typing import Any

import yaml

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
from content_factory.audit.checklist_matching import extract_checklist_question_names
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


class TopicCoherenceChecker(BaseChecker):
    """Ищет предложения README не по теме / противоречащие заявленной теме проекта."""

    name = "topic_coherence_checker"
    prompt_version = "topic_coherence_checker:v1"
    model_context_limit = 14000
    #: Высокая точность: пропускаем только уверенные находки, чтобы не шуметь легитимными аналогиями.
    min_model_confidence = 0.75

    SYSTEM_PROMPT = (
        "Ты редактор учебных README. Тебе дают ЗАЯВЛЕННУЮ ТЕМУ проекта и ТЕЛО README с номерами "
        "строк. Найди предложения тела, которые ЯВНО не по теме или ПРЯМО противоречат заявленной "
        "теме (например, тема про управление проектами, а предложение про черепах).\n"
        "Правила высокой точности: не флагай легитимные аналогии, примеры, метафоры и вводные "
        "фразы, если они служат теме. Сомневаешься — не флагай. Обычную смену подтемы внутри темы "
        "не считай нарушением.\n"
        "Для КАЖДОГО найденного предложения верни: quote (точная цитата), line (номер строки из "
        "тела), reason (кратко почему не по теме), kind ('off_topic' если просто не по теме, "
        "'contradiction' если противоречит теме), confidence (0..1). Рекомендации и причины пиши "
        "на русском.\n"
        'Ответ — строго JSON вида {"items": [{"quote": ..., "line": ..., "reason": ..., '
        '"kind": ..., "confidence": ...}]}. Если нарушений нет — {"items": []}.'
    )

    def check(self, unit: ContentUnit, entities: list[ExtractedEntity], context: CheckContext) -> list[Finding]:
        del entities
        if context.model_client is None:
            return []

        theme = self._declared_theme(unit)
        body = self._numbered_readme(unit)
        if not theme.strip() or not body.strip():
            return []

        prompt = json.dumps({"declared_theme": theme, "readme_body": body}, ensure_ascii=False, indent=2)
        try:
            record, cache_hit = _cached_model_json(
                context,
                "topic_coherence",
                _hash_cache_key("topic_coherence", f"{self.prompt_version}|{prompt}"),
                context.model_client,
                self.SYSTEM_PROMPT,
                prompt,
                self.prompt_version,
            )
        except OpenRouterError as exc:
            return [_external_check_error(unit, self.name, Criterion.TOPIC_RELEVANCE, exc)]

        readme_path = self._primary_readme_path(unit)
        findings: list[Finding] = []
        seen: set[tuple[int | None, str | None]] = set()
        for item in _result_items(record.get("response")):
            finding = self._finding_from_item(unit, item, record, cache_hit, readme_path)
            if finding is None:
                continue
            key = (finding.location.line_start if finding.location else None, finding.quote)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
        return findings

    def _finding_from_item(
        self,
        unit: ContentUnit,
        item: dict[str, Any],
        record: dict[str, Any],
        cache_hit: bool,
        readme_path: str,
    ) -> Finding | None:
        """Строит находку из одного экспертного элемента; отсекает неуверенные и пустые."""
        if _parse_confidence(item.get("confidence")) < self.min_model_confidence:
            return None
        quote = _optional_model_text(item.get("quote"))
        reason = _model_text(item, ("reason", "explanation", "evidence"), "Не соответствует заявленной теме проекта.")
        if not quote and not reason:
            return None

        kind = _model_text(item, ("kind", "issue_type", "type"), "off_topic").strip().lower()
        if kind == "contradiction":
            severity, verdict = Severity.MAJOR, Verdict.FAIL
        else:
            kind, severity, verdict = "off_topic", Severity.MINOR, Verdict.WARNING

        line = _parse_optional_int(item.get("line") if item.get("line") is not None else item.get("line_start"))
        location: TextLocation | None = None
        if readme_path:
            location = TextLocation(file_path=readme_path, line_start=line, line_end=line) if line else TextLocation(file_path=readme_path)

        recommendation = _model_text(
            item,
            ("recommendation", "suggestion"),
            "Убрать предложение или переформулировать под заявленную тему проекта.",
        )
        return _finding(
            unit,
            self.name,
            Criterion.TOPIC_RELEVANCE,
            severity,
            verdict,
            _parse_confidence(item.get("confidence")),
            quote,
            location,
            [Evidence(title="Соответствие теме", detail=reason)],
            recommendation,
            True,
            extra={"issue_type": kind, "source": "model", "cache_hit": cache_hit, "model": record.get("model")},
            checked_at=_checked_at_from_record(record),
            prompt_version=self.prompt_version,
        )

    def _readme_files(self, unit: ContentUnit) -> list[Any]:
        return [file for file in unit.files if file.kind == "readme"]

    def _primary_readme_path(self, unit: ContentUnit) -> str:
        files = sorted(self._readme_files(unit), key=lambda file: file.relative_path)
        return files[0].relative_path if files else ""

    def _numbered_readme(self, unit: ContentUnit) -> str:
        """Тело README с номерами строк, чтобы модель вернула точную привязку (обрезка по лимиту)."""
        chunks: list[str] = []
        total = 0
        for file in sorted(self._readme_files(unit), key=lambda file: file.relative_path):
            lines = file.text.splitlines()
            numbered = "\n".join(f"{index}: {line}" for index, line in enumerate(lines, start=1))
            chunk = f"Файл: {file.relative_path}\n{numbered}"
            chunks.append(chunk)
            total += len(chunk)
            if total >= self.model_context_limit:
                break
        return "\n\n---\n\n".join(chunks)[: self.model_context_limit]

    def _declared_theme(self, unit: ContentUnit) -> str:
        """Собирает заявленную тему: имя проекта + заголовок/вступление README + чек-лист."""
        parts: list[str] = []
        if unit.name:
            parts.append(f"Проект: {unit.name}")
        readme_text = "\n".join(file.text for file in self._readme_files(unit))
        title = self._readme_title(readme_text)
        if title:
            parts.append(f"Заголовок README: {title}")
        intro = self._readme_intro(readme_text)
        if intro:
            parts.append(f"Вступление README: {intro}")
        for file in unit.files:
            if file.kind != "checklist":
                continue
            try:
                data = yaml.safe_load(file.text)
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict):
                continue
            checklist_name = str(data.get("name") or "").strip()
            if checklist_name:
                parts.append(f"Тема из чек-листа: {checklist_name}")
            checklist_intro = str(data.get("introduction") or "").strip()
            if checklist_intro:
                parts.append(f"Описание из чек-листа: {checklist_intro[:500]}")
            names = extract_checklist_question_names(data)
            if names:
                parts.append("Пункты чек-листа: " + "; ".join(names[:20]))
        return "\n".join(parts)[:4000]

    @staticmethod
    def _readme_title(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    @staticmethod
    def _readme_intro(text: str) -> str:
        """Первый содержательный абзац README (не заголовок)."""
        paragraphs: list[str] = []
        buffer: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                if buffer:
                    paragraphs.append(" ".join(buffer))
                    buffer = []
                continue
            buffer.append(stripped)
        if buffer:
            paragraphs.append(" ".join(buffer))
        return paragraphs[0][:600] if paragraphs else ""
