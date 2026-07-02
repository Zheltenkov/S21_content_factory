from content_gen.project_seed_provider import ProjectSeedProvider


def test_provider_prefers_current_project_seed_payload() -> None:
    result = ProjectSeedProvider.build_for_regeneration(
        language="ru",
        project_seed={
            "language": "ru",
            "project_type": "individual",
            "direction": "PjM",
            "thematic_block": "Блок 1",
            "title_seed": "Публичные выступления",
            "project_description": "Проект про подготовку выступления.",
            "learning_outcomes": ["Понимает структуру публичного выступления."],
            "skills": ["Подготовка тезисов"],
            "required_tools": ["Git"],
        },
    )

    assert result.source == "request.project_seed"
    assert result.seed.title_seed == "Публичные выступления"
    assert result.seed.learning_outcomes == ["Понимает структуру публичного выступления."]
    assert result.seed.skills == ["Подготовка тезисов"]


def test_provider_builds_seed_from_nested_curriculum_project_payload() -> None:
    result = ProjectSeedProvider.build_for_regeneration(
        language="ru",
        curriculum_project={
            "block": {"code": "PjM", "name": "Блок 1. Введение"},
            "audience_level": "base",
            "project": {
                "title": "Прототипирование",
                "description": "Проект про быстрый прототип.",
                "learning_outcomes": "Понимает назначение прототипа.\nУмеет описывать сценарий.",
                "skills": "Работа с Figma; Согласование макета",
                "required_software": "Figma, Miro",
                "format": "group",
                "group_size": 3,
                "sjm": "Команда согласует ранний прототип.",
                "platform_name": "PjM19_ProtDsgn",
            },
        },
    )

    assert result.source == "request.curriculum_project"
    assert result.seed.direction == "PjM"
    assert result.seed.thematic_block == "Блок 1. Введение"
    assert result.seed.project_type == "group"
    assert result.seed.group_size == 3
    assert result.seed.required_tools == []
    assert result.seed.required_software == ["Figma", "Miro"]
    assert result.seed.learning_outcomes == [
        "Понимает назначение прототипа.",
        "Умеет описывать сценарий.",
    ]
    assert result.seed.skills == ["Работа с Figma", "Согласование макета"]


def test_provider_uses_cached_seed_payload_when_request_has_no_project_metadata() -> None:
    result = ProjectSeedProvider.build_for_regeneration(
        language="ru",
        cached_result={
            "project_seed_payload": {
                "language": "ru",
                "project_type": "individual",
                "title_seed": "Риски проекта",
                "project_description": "Проект про управление рисками.",
                "learning_outcomes": ["Знает виды проектных рисков."],
                "skills": ["Оценка рисков"],
            }
        },
    )

    assert result.source == "cache.project_seed_payload"
    assert result.seed.title_seed == "Риски проекта"
    assert result.seed.learning_outcomes == ["Знает виды проектных рисков."]


def test_provider_returns_minimal_fallback_seed_without_structured_project_metadata() -> None:
    result = ProjectSeedProvider.build_for_regeneration(language="ru")

    assert result.source == "fallback.minimal"
    assert result.has_structured_project_source is False
    assert result.seed.learning_outcomes == []
    assert result.seed.skills == []
