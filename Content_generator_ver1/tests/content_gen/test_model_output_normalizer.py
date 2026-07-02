from content_gen.recovery import ModelOutputNormalizer


def test_model_output_normalizer_converts_legacy_theory_headings() -> None:
    result = ModelOutputNormalizer().normalize_theory_markdown(
        "## Глава 2. Теория\n\n"
        "### Часть 1. Риски\n\n"
        "Текст.\n"
    )

    assert result.changed is True
    assert "### 2.1. Риски" in result.markdown
    assert result.changes == ["theory_heading:Часть 1->2.1"]


def test_model_output_normalizer_converts_legacy_practice_headings() -> None:
    result = ModelOutputNormalizer().normalize_practice_markdown(
        "## Глава 3. Практика\n\n"
        "### Задача 2. Карта решений\n\n"
        "Текст.\n"
    )

    assert result.changed is True
    assert "### Задание 2. Карта решений" in result.markdown
    assert result.changes == ["practice_heading:Задача 2->Задание 2"]


def test_model_output_normalizer_can_process_full_readme() -> None:
    result = ModelOutputNormalizer().normalize_readme_markdown(
        "# Проект\n\n"
        "## Глава 2. Теория\n\n"
        "### Часть 1. Контекст\n\n"
        "Текст.\n\n"
        "## Глава 3. Практика\n\n"
        "### Задача 1. Собрать артефакт\n\n"
        "Текст.\n"
    )

    assert "### 2.1. Контекст" in result.markdown
    assert "### Задание 1. Собрать артефакт" in result.markdown
    assert result.changes == [
        "theory_heading:Часть 1->2.1",
        "practice_heading:Задача 1->Задание 1",
    ]
