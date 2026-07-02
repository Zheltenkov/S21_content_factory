import pytest

from api.services.readme_improvement_service import (
    ExtractForImprovementCommand,
    GenerateImprovedCommand,
    ReadmeImprovementNotFoundError,
    ReadmeImprovementService,
)
from api.utils.improvement_cache import clear_cache
from content_gen.models.schemas import ProjectSeed


async def _noop_log_writer(**_kwargs):
    return None


@pytest.mark.asyncio
async def test_extract_for_improvement_uses_curriculum_payload_without_llm() -> None:
    clear_cache()
    service = ReadmeImprovementService(log_writer=_noop_log_writer)

    result = await service.extract_for_improvement(
        ExtractForImprovementCommand(
            request_id="extract_1",
            user_id="user_1",
            readme_text="# README",
            curriculum_project={
                "block": {"code": "PjM", "name": "Блок 1"},
                "project": {
                    "title": "Прототипирование",
                    "description": "Проект про прототип.",
                    "learning_outcomes": "Понимает назначение прототипа.\nУмеет описывать сценарий.",
                    "skills": "Работа с Figma; Согласование макета",
                    "required_software": "Figma, Miro",
                    "format": "group",
                    "tasks_count": 2,
                },
            },
        )
    )

    assert result.metadata == {"source": "curriculum"}
    assert result.partial_seed["title_seed"] == "Прототипирование"
    assert result.partial_seed["learning_outcomes"] == [
        "Понимает назначение прототипа.",
        "Умеет описывать сценарий.",
    ]
    assert result.partial_seed["skills"] == ["Работа с Figma", "Согласование макета"]
    assert result.classification["project_type"] == "group"


@pytest.mark.asyncio
async def test_generate_improved_readme_requires_original_readme() -> None:
    clear_cache()
    service = ReadmeImprovementService(log_writer=_noop_log_writer)

    with pytest.raises(ReadmeImprovementNotFoundError):
        await service.generate_improved_readme(
            GenerateImprovedCommand(
                extract_request_id="missing",
                generation_request_id="gen_1",
                user_id="user_1",
                seed=ProjectSeed(
                    language="ru",
                    project_type="individual",
                    project_description="Описание.",
                    tasks_count=2,
                ),
            )
        )
