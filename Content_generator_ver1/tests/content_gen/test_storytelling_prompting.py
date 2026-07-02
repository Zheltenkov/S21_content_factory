from content_gen.agents.practice_prompting import build_practice_sjm_section
from content_gen.agents.theory_prompting import build_theory_sjm_section
from content_gen.models.schemas import ProjectSeed


def _seed(**overrides) -> ProjectSeed:
    payload = {
        "language": "ru",
        "project_type": "individual",
        "direction": "PjM",
        "project_description": "Команда планирует небольшой IT-проект.",
        "learning_outcomes": ["Планировать практический результат"],
        "skills": ["Project planning"],
        "storytelling_type": "sjm",
        "sjm": "Ты junior PM. Заказчик меняет сроки, команде нужен проверяемый план.",
    }
    payload.update(overrides)
    return ProjectSeed(**payload)


def test_default_sjm_storytelling_is_practice_oriented_in_prompts() -> None:
    seed = _seed()

    theory_section = build_theory_sjm_section(seed)
    practice_section = build_practice_sjm_section(seed)

    assert "практико-ориентированным" in theory_section
    assert "теория только поддерживает контекст" in theory_section
    assert "применяется прежде всего к практической части" in practice_section
    assert "ситуацию, роль, ограничения, артефакт и критерии" in practice_section


def test_disabled_storytelling_stays_disabled_in_prompts() -> None:
    seed = _seed(storytelling_type="none")

    assert "Сторителлинг/кейс отключен" in build_theory_sjm_section(seed)
    assert "Сторителлинг/кейс отключен" in build_practice_sjm_section(seed)
