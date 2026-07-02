from content_gen.agents.regeneration import RegenerationAgent


def test_detect_target_scope_prefers_specific_task():
    scope = RegenerationAgent._detect_target_scope(
        "Перепиши задачу 2: нужно исправить цель и сделать артефакт p2p-проверяемым."
    )

    assert scope == "Задача 2"


def test_detect_target_scope_understands_intro_sections():
    assert RegenerationAgent._detect_target_scope("Нужно сократить аннотацию и переписать введение.") == "Аннотация"
    assert RegenerationAgent._detect_target_scope("Исправь введение: сейчас оно дублирует верх README.") == "Введение"
