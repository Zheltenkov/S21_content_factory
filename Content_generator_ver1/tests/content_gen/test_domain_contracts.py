from content_gen.domain_contracts import (
    SectionContextPolicy,
    StaticInstructionLeakGuard,
    build_narrative_contract,
    render_narrative_contract_section,
)
from content_gen.models.schemas import ProjectSeed


def _seed(**overrides) -> ProjectSeed:
    payload = {
        "language": "ru",
        "project_type": "individual",
        "direction": "PjM",
        "title_seed": "Risk Management",
        "project_description": "Команда запускает IT-проект и должна управлять рисками при ограниченных сроках.",
        "learning_outcomes": ["Идентифицировать и приоритизировать проектные риски"],
        "skills": ["управление рисками"],
        "sjm": "Ты — junior project manager в небольшой IT-команде. Заказчик меняет требования, сроки сжаты.",
    }
    payload.update(overrides)
    return ProjectSeed(**payload)


def test_build_narrative_contract_from_curriculum_and_sjm() -> None:
    contract = build_narrative_contract(
        _seed(required_tools=["Miro"], storytelling_type="role_play"),
        {"block_name": "Project Manager", "sjm_context": "fallback"},
        [{"title": "Previous"}],
    )

    assert contract.is_actionable
    assert contract.storytelling_type == "role_play"
    assert "project manager" in contract.student_role.lower()
    assert "IT-команде" in contract.working_case
    assert contract.data_sources
    assert len(contract.artifact_chain) >= 2
    rendered = render_narrative_contract_section(contract)
    assert "NARRATIVE CONTRACT" in rendered
    assert "Тип сторителлинга: role_play" in rendered


def test_build_narrative_contract_respects_disabled_storytelling() -> None:
    contract = build_narrative_contract(
        _seed(storytelling_type="none"),
        {"block_name": "Project Manager", "sjm_context": "fallback"},
        [],
    )

    assert contract.storytelling_type == "none"
    assert contract.working_case == "Команда запускает IT-проект и должна управлять рисками при ограниченных сроках."


def test_static_instruction_leak_guard_respects_project_topic() -> None:
    guard = StaticInstructionLeakGuard()
    text = "Репозиторий нужен для сдачи проекта. Риск влияет на сроки."

    cleaned = guard.strip(text, topic_text="Управление рисками проекта")

    assert "Репозиторий" not in cleaned
    assert "Риск влияет" in cleaned

    allowed = guard.strip("GitLab используется как предмет анализа.", topic_text="Проект про GitLab workflow")

    assert "GitLab" in allowed


def test_section_context_policy_blocks_static_instruction_context_for_theory() -> None:
    policy = SectionContextPolicy.for_theory()

    assert policy.filter_context_value("static_instruction_context", "Работай в репозитории.") == ""
    assert policy.find_forbidden_markers("Методы проверки через P2P не относятся к рискам.")


def test_section_context_policy_filters_payload_by_schema() -> None:
    policy = SectionContextPolicy.for_dataset()
    payload = {
        "practice_plan_contract": {"task_count": 2},
        "evidence_specs": [{"path": "materials/task_01_source_notes.md"}],
        "practice_tasks": [{"title": "Task"}],
        "required_tools": ["Miro"],
        "instruction_text": "Загрузи результат в репозиторий.",
        "markdown": "## Глава 2\nТекст",
        "unexpected": "hidden",
    }

    filtered = policy.filter_context_payload(payload, topic_text="Анализ наблюдений")

    assert "practice_plan_contract" in filtered
    assert "evidence_specs" in filtered
    assert filtered["required_tools"] == ["Miro"]
    assert "practice_tasks" in filtered
    assert "unexpected" not in filtered
    assert "instruction_text" not in filtered
    assert "markdown" not in filtered
