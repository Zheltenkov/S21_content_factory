from content_gen.agents.title_annotation import (
    _derive_specific_title_from_seed,
    _is_generic_project_title,
    _sanitize_annotation_text,
)
from content_gen.models.schemas import ProjectSeed


def test_sanitize_annotation_text_removes_readme_structure_noise():
    raw = (
        "Проект помогает разобраться с инфраструктурой и понять, зачем нужен устойчивый сервер. "
        "Дальше в README будут Глава 1, Глава 2 и практический блок. "
        "В результате ты получишь опыт выбора решений для проекта."
    )

    cleaned = _sanitize_annotation_text(raw, hi=520)

    assert "глава 1" not in cleaned.lower()
    assert "практический блок" not in cleaned.lower()
    assert "устойчивый сервер" in cleaned.lower()
    assert "получишь опыт" in cleaned.lower()


def test_sanitize_annotation_text_keeps_no_more_than_four_sentences():
    raw = (
        "Первое предложение про ценность проекта. "
        "Второе предложение про содержание работы. "
        "Третье предложение про ожидаемый результат. "
        "Четвертое предложение уточняет контекст. "
        "Пятое предложение уже лишнее."
    )

    cleaned = _sanitize_annotation_text(raw, hi=520)

    assert cleaned.count(".") <= 4


def test_generic_title_detection_rejects_plan_rabot():
    assert _is_generic_project_title("План работ")
    assert not _is_generic_project_title("Планирование спринта")


def test_derive_specific_title_from_seed_uses_sprint_context():
    seed = ProjectSeed(
        language="ru",
        project_type="individual",
        title_seed="План работ",
        project_description="Перевести сырой бэклог в дорожную карту ближайшего спринта",
        learning_outcomes=["Сформировать план спринта с зависимостями"],
        skills=["backlog", "roadmap"],
    )

    assert _derive_specific_title_from_seed(seed) == "Планирование спринта"
