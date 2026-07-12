from content_factory.catalog.pipeline.curriculum import PlanNode, ProjectBlueprint, SkillOccurrence
from content_factory.catalog.pipeline.stage_dag_to_up import (
    _deduplicate_project_name,
    _localized_role,
    _project_name,
)


def test_localized_role_translates_known_english_brief_role() -> None:
    assert _localized_role("beginning technological entrepreneur") == "начинающий технологический предприниматель"


def test_localized_role_preserves_domain_specific_role() -> None:
    assert _localized_role("инженер данных") == "инженер данных"


def test_project_name_preserves_accepted_stage_title_until_duplicate_exists() -> None:
    node = PlanNode("S1", "Разработка прототипа", "Разработка", "Создание продукта", 4, (), (), (), ())
    project = ProjectBlueprint(
        occurrences=[SkillOccurrence(node, role="primary")],
        block_key="Создание продукта",
        artifact="Работающий прототип",
        title="Создание продукта",
    )

    assert _project_name(project, 1, 1, "Создание продукта") == "Создание продукта"


def test_duplicate_project_names_get_stable_distinguishing_anchor() -> None:
    first_node = PlanNode("S1", "Анализ гипотез", "Исследование", "Исследование", 3, (), (), (), ())
    second_node = PlanNode("S2", "Проверка гипотез", "Исследование", "Исследование", 3, (), (), (), ())
    first = ProjectBlueprint([SkillOccurrence(first_node, role="primary")], "Исследование", "Отчёт")
    second = ProjectBlueprint([SkillOccurrence(second_node, role="primary")], "Исследование", "Отчёт")
    seen: set[str] = set()

    assert _deduplicate_project_name("Исследование", first, seen, 1) == "Исследование"
    assert _deduplicate_project_name("Исследование", second, seen, 2) == "Исследование: Проверка гипотез"
