"""Тесты DidacticQualityScorer: анти-self-bias, эскалация, сборка отчёта."""

from content_factory.generation.evaluation.didactic.jury import MockJuryBackend
from content_factory.generation.evaluation.didactic.models import DidacticQualityReport
from content_factory.generation.evaluation.didactic.scorer import DidacticQualityScorer

_JURY = ["openai/gpt-5.4", "deepseek/deepseek-v4", "google/gemini-3.1-pro"]

_LOW_QUALITY_MD = (
    "# Проект PjM15\n\n## Глава 2. Теория\n\n### 2.1. Раздел\n"
    + "Это типовое предложение про рабочий процесс проекта команды снова. " * 6
    + "\n| " + "слитая ячейка " * 20 + " | col |\n"
)


def _scorer(**kwargs) -> DidacticQualityScorer:
    return DidacticQualityScorer(
        backend_factory=lambda md: MockJuryBackend(),
        jury_models=list(_JURY),
        **kwargs,
    )


def test_scorer_excludes_generator_from_jury() -> None:
    report = _scorer().score(_LOW_QUALITY_MD, generator_model="openai/gpt-5.4")
    assert "openai/gpt-5.4" not in report.jury
    assert report.jury == ["deepseek/deepseek-v4", "google/gemini-3.1-pro"]
    assert report.n_jury == 2


def test_scorer_reports_all_dimensions_and_is_serializable() -> None:
    report = _scorer().score(_LOW_QUALITY_MD, generator_model="openai/gpt-5.4")
    assert isinstance(report, DidacticQualityReport)
    assert {d.dimension for d in report.dimensions} == {
        "coherence", "scaffolding", "example_quality", "cognitive_load", "school_tone", "naturalness",
    }
    payload = report.model_dump(mode="json")
    assert payload["n_jury"] == 2
    assert "overall_raw" in payload


def test_scorer_flags_low_quality_document() -> None:
    report = _scorer().score(_LOW_QUALITY_MD, generator_model="openai/gpt-5.4")
    assert report.needs_human_review
    assert any(reason.startswith("below_floor:") for reason in report.abstain_reasons)
    naturalness = next(d for d in report.dimensions if d.dimension == "naturalness")
    assert naturalness.score < 3.0
    assert naturalness.escalated


def test_scorer_clean_document_no_human_review() -> None:
    clean = (
        "# Проект PjM15\n\n## Глава 2. Теория\n\n### 2.1. Раздел\n"
        "Команда планирует спринт и фиксирует цели проекта. "
        "Аналитик описывает требования продукта и связанные риски. "
        "Разработчик готовит архитектуру решения под новую задачу.\n\n"
        "**Пример:** команда сверяет решение с требованиями. "
        "**Пример:** аналитик уточняет риски. "
        "**Пример:** разработчик проверяет артефакт.\n"
    )
    report = _scorer().score(clean, generator_model="openai/gpt-5.4")
    # Чистый текст: нет повторов/сломанных таблиц → naturalness/coherence не падают.
    naturalness = next(d for d in report.dimensions if d.dimension == "naturalness")
    assert naturalness.score >= 3.0
