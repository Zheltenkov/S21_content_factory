from types import SimpleNamespace

from api.services.regeneration_service import _learning_context_from_cache, _learning_context_from_seed_and_cache
from content_gen.models.schemas import ProjectSeed


def _seed(**overrides) -> ProjectSeed:
    data = {
        "language": "ru",
        "project_type": "individual",
        "title_seed": "Проект",
        "project_description": "Описание проекта.",
        "learning_outcomes": ["Понимает рабочий контекст проекта."],
        "skills": ["Анализ требований"],
    }
    data.update(overrides)
    return ProjectSeed(**data)


def test_learning_context_prefers_seed_over_cache() -> None:
    learning_outcomes, skills, source = _learning_context_from_seed_and_cache(
        _seed(),
        {
            "project_seed_payload": {
                "learning_outcomes": ["Знает устаревший outcome."],
                "skills": ["Устаревший навык"],
            }
        },
    )

    assert source == "seed"
    assert learning_outcomes == ["Понимает рабочий контекст проекта."]
    assert skills == ["Анализ требований"]


def test_learning_context_fills_missing_seed_fields_from_structured_cache() -> None:
    learning_outcomes, skills, source = _learning_context_from_seed_and_cache(
        _seed(learning_outcomes=[], skills=[]),
        {
            "project_seed_payload": {
                "learning_outcomes": ["Умеет согласовывать проектный артефакт."],
                "skills": "Согласование артефакта\nP2P-проверка",
            }
        },
    )

    assert source == "cache.project_seed_payload"
    assert learning_outcomes == ["Умеет согласовывать проектный артефакт."]
    assert skills == ["Согласование артефакта", "P2P-проверка"]


def test_learning_context_reads_cached_result_spec_without_llm() -> None:
    learning_outcomes, skills, source = _learning_context_from_cache(
        {
            "result": SimpleNamespace(
                spec=SimpleNamespace(
                    learning_outcomes=["Знает критерии оценки README."],
                    skills=["Проверка структуры"],
                )
            )
        }
    )

    assert source == "cache.result.spec"
    assert learning_outcomes == ["Знает критерии оценки README."]
    assert skills == ["Проверка структуры"]


def test_learning_context_reports_unavailable_without_seed_or_cache_context() -> None:
    learning_outcomes, skills, source = _learning_context_from_seed_and_cache(
        _seed(learning_outcomes=[], skills=[]),
        None,
    )

    assert source == "unavailable"
    assert learning_outcomes == []
    assert skills == []
