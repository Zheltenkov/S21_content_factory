"""Доменные модели и строгие контракты аудита."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Criterion(StrEnum):
    """Критерии проверки из ТЗ и таблицы приоритетов."""

    ACTUALITY = "actuality"
    LINKS = "links"
    TECHNOLOGY_FRESHNESS = "technology_freshness"
    FACTS = "facts"
    MARKET_FIT = "market_fit"
    RIGHTS = "rights"
    CORRECTNESS = "correctness"
    READABILITY = "readability"
    CHECKLIST_ALIGNMENT = "checklist_alignment"
    WORKLOAD = "workload"
    EXAM = "exam"
    LANGUAGE = "language"
    IMAGE_QUALITY = "image_quality"


class Severity(StrEnum):
    """Критичность найденного случая."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


class Verdict(StrEnum):
    """Результат проверки."""

    FAIL = "fail"
    WARNING = "warning"
    UNKNOWN = "unknown"
    INFO = "info"
    PASS = "pass"


class IssueKind(StrEnum):
    """Тип записи для честного разделения дефектов, улучшений и вопросов к данным."""

    DEFECT = "defect"
    IMPROVEMENT = "improvement"
    QUESTION = "question"


class EntityType(StrEnum):
    """Тип извлечённой сущности."""

    LINK = "link"
    IMAGE = "image"
    DATE = "date"
    VERSION = "version"
    TECHNOLOGY = "technology"
    FACT_CANDIDATE = "fact_candidate"


CRITERION_LABELS: dict[Criterion, str] = {
    Criterion.ACTUALITY: "Актуальность",
    Criterion.LINKS: "Ссылки",
    Criterion.TECHNOLOGY_FRESHNESS: "Версии и технологии",
    Criterion.FACTS: "Факты, определения, примеры",
    Criterion.MARKET_FIT: "Соответствие рынку",
    Criterion.RIGHTS: "Оригинальность и права использования ресурсов",
    Criterion.CORRECTNESS: "Точность и корректность",
    Criterion.READABILITY: "Грамотность и читаемость текста",
    Criterion.CHECKLIST_ALIGNMENT: "Соответствие заданий проекта чек-листу",
    Criterion.WORKLOAD: "Трудоёмкость",
    Criterion.EXAM: "Экзамен",
    Criterion.LANGUAGE: "Язык",
    Criterion.IMAGE_QUALITY: "Качество изображений",
}

SEVERITY_LABELS: dict[Severity, str] = {
    Severity.CRITICAL: "Critical",
    Severity.MAJOR: "Major",
    Severity.MINOR: "Minor",
    Severity.INFO: "Info",
}

VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.FAIL: "Проблема",
    Verdict.WARNING: "Предупреждение",
    Verdict.UNKNOWN: "Нужна проверка",
    Verdict.INFO: "Информация",
    Verdict.PASS: "Проверено",
}

ISSUE_KIND_LABELS: dict[IssueKind, str] = {
    IssueKind.DEFECT: "Дефект",
    IssueKind.IMPROVEMENT: "Улучшение",
    IssueKind.QUESTION: "Вопрос",
}


class AuditSettings(BaseModel):
    """Настройки одного запуска аудита."""

    input_path: Path
    output_path: Path
    allow_network: bool = False
    use_model: bool = False
    include_unknown: bool = True
    expected_languages: tuple[str, ...] = ("RUS", "ENG", "UZ", "TG")
    max_file_bytes: int = 2_000_000
    link_timeout_seconds: float = 8.0
    min_image_width: int = 640
    min_image_height: int = 360
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    openrouter_fact_model: str | None = None
    openrouter_tech_model: str | None = None
    openrouter_base_url: str | None = None
    lean_checkers: bool = False
    cache_path: Path | None = None

    @field_validator("input_path", "output_path", "cache_path")
    @classmethod
    def expand_path(cls, value: Path | None) -> Path | None:
        """Приводим путь к абсолютному виду, чтобы отчёты были воспроизводимыми."""

        if value is None:
            return None
        return value.expanduser().resolve()

    @field_validator("expected_languages", mode="before")
    @classmethod
    def normalize_expected_languages(cls, value: object) -> tuple[str, ...]:
        """Приводим политику языков к кодам RUS/ENG/UZ/TG без дублей."""

        if value is None:
            return ()
        raw_items = value.split(",") if isinstance(value, str) else value
        if not isinstance(raw_items, list | tuple | set):
            return ("RUS", "ENG", "UZ", "TG")
        aliases = {
            "RU": "RUS",
            "RUS": "RUS",
            "RUSSIAN": "RUS",
            "РУС": "RUS",
            "ENG": "ENG",
            "EN": "ENG",
            "ENGLISH": "ENG",
            "UZ": "UZ",
            "UZB": "UZ",
            "UZBEK": "UZ",
            "TG": "TG",
            "TAJ": "TG",
            "TAJIK": "TG",
        }
        result: list[str] = []
        for item in raw_items:
            code = aliases.get(str(item).strip().upper())
            if code and code not in result:
                result.append(code)
        return tuple(result)


class ContentFile(BaseModel):
    """Один проверяемый файл внутри единицы контента."""

    relative_path: str
    absolute_path: Path
    kind: str
    text: str
    size_bytes: int

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ContentUnit(BaseModel):
    """Минимальная единица аудита: обычно одна папка учебного проекта."""

    unit_id: str
    name: str
    root_path: Path
    relative_path: str
    branch: str | None = None
    files: list[ContentFile] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class TextLocation(BaseModel):
    """Положение фрагмента в исходном файле."""

    file_path: str
    line_start: int | None = None
    line_end: int | None = None


class ExtractedEntity(BaseModel):
    """Сущность, которую можно отправить на проверку."""

    entity_id: str
    entity_type: EntityType
    value: str
    quote: str
    location: TextLocation
    context: str | None = None


class Evidence(BaseModel):
    """Доказательство или техническое основание вердикта."""

    title: str
    detail: str
    url: str | None = None


class Finding(BaseModel):
    """Один найденный случай для таблицы результата."""

    finding_id: str
    unit_id: str
    branch: str | None
    criterion: Criterion
    issue_kind: IssueKind = IssueKind.DEFECT
    severity: Severity
    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str | None = None
    location: TextLocation | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    source: str | None = None
    checked_at: datetime | None = None
    support_status: str | None = None
    latest_version: str | None = None
    recommended_version: str | None = None
    prompt_version: str | None = None
    recommendation: str
    needs_human_review: bool = False
    checker_name: str
    extra: dict[str, Any] = Field(default_factory=dict)


class ModelUsageSummary(BaseModel):
    """Сводная статистика платных и кэшированных модельных проверок."""

    calls_total: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, dict[str, int | float]] = Field(default_factory=dict)


class RunStep(BaseModel):
    """Статус и длительность шага конвейера."""

    name: str
    status: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    detail: str | None = None


class RunSummary(BaseModel):
    """Сводка по запуску аудита."""

    started_at: datetime
    finished_at: datetime | None = None
    input_path: str
    units_total: int = 0
    files_total: int = 0
    findings_total: int = 0
    affected_units_total: int = 0
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_criterion: dict[str, int] = Field(default_factory=dict)
    by_branch: dict[str, int] = Field(default_factory=dict)
    by_unit: dict[str, int] = Field(default_factory=dict)
    model_usage: ModelUsageSummary = Field(default_factory=ModelUsageSummary)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    steps: list[RunStep] = Field(default_factory=list)
    model_used: bool = False
    network_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class AuditReport(BaseModel):
    """Полный отчёт аудита."""

    summary: RunSummary
    units: list[ContentUnit]
    entities: list[ExtractedEntity]
    findings: list[Finding]
