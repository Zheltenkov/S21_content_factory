from content_factory.content_profile import infer_content_profile, resolve_content_profile
from content_factory.generation.agents.enhancement_planner import EnhancementPlanner
from content_factory.generation.agents.intro_rules import IntroRulesAgent
from content_factory.generation.agents.practice_prompting import determine_practice_content_type
from content_factory.generation.agents.theory_prompting import determine_theory_content_type
from content_factory.generation.models.schemas import ProjectSeed


def _seed(**overrides: object) -> ProjectSeed:
    payload: dict[str, object] = {
        "language": "ru",
        "project_type": "individual",
        "direction": "PjM",
        "thematic_block": "Проектирование цифрового продукта",
        "title_seed": "Схема решения и прототип разработки",
        "project_description": "Спроектировать API и реализовать воспроизводимый прототип.",
        "skills": ["Проектирование API", "Разработка прототипа"],
        "required_tools": ["Python", "Git", "Docker"],
        "learning_outcomes": ["Реализует и проверяет технический прототип"],
    }
    payload.update(overrides)
    return ProjectSeed(**payload)


def test_project_signals_override_no_code_direction_fallback() -> None:
    decision = resolve_content_profile(_seed())

    assert decision.profile == "hard_code"
    assert decision.source == "project_signals"


def test_research_project_in_same_direction_stays_no_code() -> None:
    decision = infer_content_profile(
        direction="PjM",
        title="Отчёт о клиентском исследовании",
        description="Провести интервью и подготовить выводы о сегментах клиентов.",
        skills=["Проведение интервью", "Сегментация клиентов"],
        artifact="Исследовательский отчёт",
    )

    assert decision.profile == "no_code"
    assert decision.source == "project_signals"


def test_explicit_profile_has_priority() -> None:
    decision = resolve_content_profile(_seed(project_content_type="hybrid"))

    assert decision.profile == "hybrid"
    assert decision.source == "explicit"


def test_generation_agents_share_one_profile_decision() -> None:
    seed = _seed()
    expected = resolve_content_profile(seed).profile

    assert determine_practice_content_type(seed) == expected
    assert determine_theory_content_type(seed) == expected
    assert IntroRulesAgent._determine_content_type(object.__new__(IntroRulesAgent), seed) == expected
    assert EnhancementPlanner._determine_content_type(object.__new__(EnhancementPlanner), seed) == expected
