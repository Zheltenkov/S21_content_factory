from content_gen.models.schemas import PracticeTask
from content_gen.practice_phase_executor import _render_practice_block


def test_public_practice_block_uses_source_pdf_template() -> None:
    tasks = [
        PracticeTask(
            title="Собрать карту решений",
            situation="Команда спорит, какие решения оставить в первом релизе, и ревьюеру нужен проверяемый артефакт.",
            input_data="Сырые заметки — см. файл `materials/task_01_source_notes.md`",
            goal="Сопоставь варианты решений и выбери рабочий набор.",
            approach_bullets=["Выдели критерии выбора.", "Сравни варианты по влиянию на результат."],
            expected_artifact="Markdown-файл с картой решений",
            artifact_location="PjM21_Project/part-03/task-01/decision_map.md",
            p2p_criteria=[
                "В документе перечислены минимум 3 варианта решений",
                "Выбранный вариант обоснован через критерии",
                "Файл размещен по указанному пути",
            ],
        ),
        PracticeTask(
            title="Проверить итог",
            input_data="Карта решений из задания 1",
            goal="Проверь связность итогового решения.",
            approach_bullets=["Сверь решение с целью проекта.", "Отметь спорные места."],
            expected_artifact="Итоговый чек-лист",
            artifact_location="PjM21_Project/part-03/task-02/final_check.md",
            p2p_criteria=["Есть чек-лист", "Файл размещен по указанному пути"],
        ),
    ]

    rendered = _render_practice_block(tasks)

    assert "### Задание 1. Собрать карту решений" in rendered
    assert "### Задача 1." not in rendered
    assert "**Что нужно сделать**" in rendered
    assert "**Что должно получиться**" in rendered
    assert "**Формат сдачи**" in rendered
    assert "**Переход к следующему заданию**" in rendered
    assert "PjM21_Project/part-03/task-01/decision_map.md" in rendered
    assert "используй этот результат как входные данные" in rendered
