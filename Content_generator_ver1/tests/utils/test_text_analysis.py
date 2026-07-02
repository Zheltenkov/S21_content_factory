from content_gen.utils.text_analysis import clean_markdown_prose_for_counting, count_prose_words


def test_prose_counter_keeps_text_after_visual_caption() -> None:
    md = (
        "*Таблица 1. Каркас плана* . Их недостаточно просто собрать вместе: "
        "нужно сопоставить сроки, зависимости и ожидаемый результат."
    )

    cleaned = clean_markdown_prose_for_counting(md)

    assert "Таблица" not in cleaned
    assert "Их недостаточно" in cleaned
    assert count_prose_words(md, "ru") >= 10
