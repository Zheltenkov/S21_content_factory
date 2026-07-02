from content_gen.utils.markdown_regeneration_guard import remove_adjacent_rewritten_paragraph_duplicates


OLD_PARAGRAPH = (
    "В предыдущей главе ты уже задал рабочий подход: смотришь на задачу как на кейс, "
    "держишь материалы в едином стиле и выбираешь формулировки, которые проще проверить. "
    "Теперь этот же принцип помогает перейти к теории границ ответственности: сначала "
    "разбираем, где заканчивается frontend и начинается backend, чтобы дальше не смешивать "
    "роли и не терять логику решения."
)

NEW_PARAGRAPH = (
    "В предыдущей главе ты уже выбрал рабочий подход: смотришь на задачу как на кейс, "
    "держишь материалы в едином стиле и выбираешь формулировки, которые проще проверить. "
    "Теперь этот же принцип помогает перейти к теории границ ответственности: сначала "
    "разбираем, где заканчивается frontend и начинается backend, чтобы дальше не смешивать "
    "роли и не терять логику решения."
)


def test_remove_adjacent_rewritten_paragraph_duplicates_keeps_new_inserted_before_old() -> None:
    original = f"# README\n\n## Глава 2. Теоретический блок\n\n{OLD_PARAGRAPH}\n\n### 2.1. Граница"
    regenerated = (
        f"# README\n\n## Глава 2. Теоретический блок\n\n{NEW_PARAGRAPH}\n\n"
        f"{OLD_PARAGRAPH}\n\n### 2.1. Граница"
    )

    result = remove_adjacent_rewritten_paragraph_duplicates(original, regenerated)

    assert NEW_PARAGRAPH in result
    assert OLD_PARAGRAPH not in result
    assert "### 2.1. Граница" in result


def test_remove_adjacent_rewritten_paragraph_duplicates_keeps_new_inserted_after_old() -> None:
    original = f"# README\n\n## Глава 2. Теоретический блок\n\n{OLD_PARAGRAPH}\n\n### 2.1. Граница"
    regenerated = (
        f"# README\n\n## Глава 2. Теоретический блок\n\n{OLD_PARAGRAPH}\n\n"
        f"{NEW_PARAGRAPH}\n\n### 2.1. Граница"
    )

    result = remove_adjacent_rewritten_paragraph_duplicates(original, regenerated)

    assert NEW_PARAGRAPH in result
    assert OLD_PARAGRAPH not in result
    assert "### 2.1. Граница" in result


def test_remove_adjacent_rewritten_paragraph_duplicates_does_not_touch_lists() -> None:
    original = "# README\n\n- Проверить одно\n- Проверить два"
    regenerated = "# README\n\n- Проверить одно\n- Проверить два\n\n- Проверить одно\n- Проверить три"

    assert remove_adjacent_rewritten_paragraph_duplicates(original, regenerated) == regenerated
