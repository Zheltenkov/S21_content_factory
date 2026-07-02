from types import SimpleNamespace

from content_gen.agents.theory import TheoryResult
from content_gen.config.loader import get_agent_config
from content_gen.models.readme_document import ReadmeDocument
from content_gen.models.schemas import ProjectContextMeta, ProjectSeed, TheoryPart
from content_gen.theory_phase_executor import TheoryPhaseExecutor, _remove_static_instruction_leaks
from content_gen.validators.theory_checks import TheoryChecks


def _make_seed() -> ProjectSeed:
    return ProjectSeed(
        language="ru",
        project_type="individual",
        thematic_block="PjM",
        audience_level="base",
        required_tools=["Miro"],
        title_seed="Test",
        project_description="Проект про планирование и коммуникацию в команде.",
        learning_outcomes=["Понимать базовые принципы планирования проекта"],
        skills=["Планирование", "Коммуникация"],
    )


def _valid_body() -> str:
    definitions = (
        "**Коммуникация** — это согласованный обмен информацией внутри команды. "
        "**Риск** — это событие, которое может сорвать срок, объём или качество результата. "
    )
    filler_sentence = (
        "Ты смотришь на сроки, роли, ожидания, договорённости и последствия решений в проекте. "
    )
    filler = filler_sentence * 28
    return (definitions + filler).strip()


def _invalid_part(idx: int) -> TheoryPart:
    return TheoryPart(
        title=f"Часть {idx}",
        body=_valid_body(),
        example="",
        bridge_questions=[],
    )


def test_theory_user_template_formats_mermaid_init_literal() -> None:
    template = get_agent_config("theory").get_prompt("user_template")

    rendered = template.format(
        n_parts=3,
        content_type_section="no_code",
        formulas_code_requirements="без формул",
        direction="PjM",
        track="Блок",
        project_description="Описание",
        skills="Навык",
        learning_outcomes="LO",
        context_summary="Контекст",
        narrative_anchor="Мост",
        platform_name="Проект",
        gitlab_link="—",
        required_software="—",
        workload_hours="1",
        curriculum_context_section="Контекст УП",
        sjm_section="SJM",
        include_formulas=False,
        include_tables=True,
        include_diagrams=True,
        i="{i}",
    )

    assert "%%{init...}%%" in rendered
    assert "%%{{init" not in rendered


def test_theory_executor_renders_theory_as_typed_document() -> None:
    seed = _make_seed()
    document = ReadmeDocument.from_markdown(
        "# README\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "Черновик.\n\n"
        "## Глава 3. Практический блок\n\n"
        "Практика."
    )
    part = TheoryPart(
        title="Коммуникация",
        body=_valid_body(),
        example="Команда фиксирует договоренности перед запуском.",
        bridge_questions=["Что ты проверишь перед практикой?"],
    )

    updated = TheoryPhaseExecutor(SimpleNamespace()).render_document(document, [part], seed)

    assert updated.section_by_title_fragment("2.1").title == "2.1. Коммуникация"
    assert "Команда фиксирует договоренности" in updated.section_by_title_fragment("2.1").to_markdown()
    assert updated.section_by_title_fragment("Глава 3") is not None


def test_theory_executor_replaces_enhanced_theory_as_typed_document() -> None:
    seed = _make_seed()
    document = ReadmeDocument.from_markdown(
        "# README\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "Старый текст.\n\n"
        "## Глава 3. Практический блок\n\n"
        "Практика."
    )
    enhanced = (
        "# README\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "Новая теория.\n\n"
        "### 2.1. Новый раздел\n\n"
        "Детали.\n\n"
        "## Глава 3. Практический блок\n\n"
        "Практика."
    )

    updated = TheoryPhaseExecutor._replace_with_enhanced_theory_document(document, enhanced, seed)

    assert updated.section_by_title_fragment("2.1").body == "Детали."
    assert "Старый текст" not in updated.to_markdown()
    assert updated.section_by_title_fragment("Глава 3") is not None


def test_theory_executor_accepts_enhanced_theory_body_as_typed_chapter_content() -> None:
    seed = _make_seed()
    document = ReadmeDocument.from_markdown(
        "# README\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "Старый текст.\n\n"
        "## Глава 3. Практический блок\n\n"
        "Практика."
    )
    enhanced_body = (
        "Новая теория.\n\n"
        "### 2.1. Новый раздел\n\n"
        "Детали."
    )

    updated = TheoryPhaseExecutor._replace_with_enhanced_theory_document(document, enhanced_body, seed)

    assert updated.section_by_title_fragment("2.1").body == "Детали."
    assert "Старый текст" not in updated.to_markdown()
    assert updated.section_by_title_fragment("Глава 3") is not None


class _TheoryAgentStub:
    def generate(
        self,
        seed,
        context_meta,
        desired_parts=3,
        practice_plan_contract=None,
        section_context=None,
    ):
        return TheoryResult(parts=[_invalid_part(1), _invalid_part(2), _invalid_part(3)])


class _IdentityAgent:
    def ensure_definitions(self, part, seed):
        return part

    def fix_length(self, part, seed):
        return part

    def improve_readability(self, part, seed):
        return part


class _EnhancementStub:
    def enhance(self, parts, seed):
        return parts, [], []


class _EditorStub:
    def edit_theory_parts(self, parts, seed):
        return parts


class _RegenerationStub:
    def regenerate(self, original_md, comments, language):
        return SimpleNamespace(
            regenerated_md=(
                "### Часть 1. Исправленная часть\n\n"
                f"{_valid_body()}\n\n"
                "**Пример:** В 2024 году команда сократила задержки, когда заранее описала риски и роли.\n\n"
                "**Вопросы к практике:**\n"
                "- Как ты опишешь риски и роли для своей проектной задачи?\n"
            )
        )


class _OrchestratorStub:
    def __init__(self):
        self.theory = _TheoryAgentStub()
        self.definitions_agent = _IdentityAgent()
        self.length_agent = _IdentityAgent()
        self.readability_agent = _IdentityAgent()
        self.theory_checks = TheoryChecks()
        self.regeneration = _RegenerationStub()
        self.theory_enhancement = _EnhancementStub()
        self.content_editor = _EditorStub()
        self.cancellation_token = None
        self.progress_tracker = None


def test_theory_executor_returns_only_final_issues_after_regeneration():
    orchestrator = _OrchestratorStub()
    seed = _make_seed()
    context_meta = ProjectContextMeta(track="PjM", thematic_block="PjM")
    markdown = "## Глава 2. Теоретический блок\n\nЧерновик\n\n## Глава 3. Практический блок\n"

    result = TheoryPhaseExecutor(orchestrator).execute(
        seed,
        context_meta,
        markdown,
    )

    assert len(result.theory_parts) == 3
    assert not [issue for issue in result.issues if getattr(issue, "severity", None) == "hard"]
    assert "### 2.1." in result.markdown
    assert "### Часть 1." not in result.markdown
    assert "**Пример:**" in result.markdown
    assert "**Вопросы к практике:**" in result.markdown
    assert any("локальная коррекция сняла замечания качества" in warning for warning in result.warnings)


def test_static_instruction_leak_is_removed_from_theory_body():
    text = (
        "Теперь, когда ты освоил базовые навыки работы с репозиторием и методами проверки через P2P, "
        "перейдём к рискам. Риск — это событие, которое может повлиять на срок проекта."
    )

    cleaned = _remove_static_instruction_leaks(text)

    assert "репозиторием" not in cleaned
    assert "P2P" not in cleaned
    assert "Риск" in cleaned
