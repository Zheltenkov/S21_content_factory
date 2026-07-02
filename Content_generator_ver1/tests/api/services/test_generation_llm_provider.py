import pytest
from pydantic import ValidationError

from api.services.generation_resume_service import GenerationResumeService
from content_gen.models.schemas import ProjectSeed


class DummyOrchestrator:
    def __init__(self, llm_client, **_kwargs):
        self.llm_client = llm_client


def _minimal_seed(**overrides) -> dict:
    data = {
        "language": "ru",
        "project_type": "individual",
        "project_description": "Описание проекта.",
        "tasks_count": 2,
    }
    data.update(overrides)
    return data


def test_project_seed_accepts_supported_llm_provider() -> None:
    seed = ProjectSeed(**_minimal_seed(llm_provider="polza"))

    assert seed.llm_provider == "polza"


def test_project_seed_rejects_unknown_llm_provider() -> None:
    with pytest.raises(ValidationError):
        ProjectSeed(**_minimal_seed(llm_provider="unknown"))


def test_generation_resume_service_passes_provider_to_llm_factory() -> None:
    calls = []

    def llm_factory(provider=None):
        calls.append(provider)
        return {"provider": provider}

    service = GenerationResumeService(
        llm_factory=llm_factory,
        orchestrator_cls=DummyOrchestrator,
    )

    orchestrator = service._build_orchestrator(
        human_review_enabled=False,
        request_id="request-1",
        user_id="user-1",
        llm_provider="gigachat",
    )

    assert calls == ["gigachat"]
    assert orchestrator.llm_client == {"provider": "gigachat"}
