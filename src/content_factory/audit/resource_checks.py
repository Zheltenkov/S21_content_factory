"""Проверки доступности ресурсов проекта и соответствия чек-листу.

``ResourceAvailabilityChecker`` следит, что упомянутые в материалах файлы, наборы
данных и внешние ресурсы действительно приложены или явно описаны.
``ChecklistChecker`` сверяет README с чек-листом ревью (grounding, покрытие
вопросов, качество описаний). Оба детерминированы и самодостаточны: вынесено из
``checks.py``; импортируют только листовой ``checker_base`` + доменные типы +
модули оценки чек-листа (никогда ``checks``). ``checks`` реэкспортирует классы,
поэтому ``default_checkers`` и тесты не меняются.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from content_factory.audit.artifacts import build_artifact_text_index
from content_factory.audit.checker_base import BaseChecker, CheckContext, _finding
from content_factory.audit.checklist_grounding import assess_checklist_grounding
from content_factory.audit.checklist_matching import (
    assess_checklist_description_quality,
    extract_checklist_questions,
    match_checklist_to_readme,
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
from content_factory.audit.text_utils import normalize_for_match


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
