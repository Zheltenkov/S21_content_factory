"""Тесты для Section4Checker."""

import re

from content_gen.validators.rubric.section4_checker import Section4Checker


def test_script_check_tov_accepts_clear_second_person_tone():
    checker = Section4Checker(
        llm_client=None,
        regex_patterns={
            "rx_directives": [re.compile(r"нажми|кликни|перейди|введи|скачай|открой|выбери|запусти", re.I)],
            "rx_marketing": [],
        },
    )

    text = (
        "Ты работаешь над проектом вместе с командой. "
        "Тебе важно спокойно проверить результат и обсудить его с ревьюером. "
        "Текст объясняет задачу простым языком и не давит на читателя."
    )

    assert checker._script_check_tov(text, checker.rx_directives) is True


def test_script_check_tov_rejects_directive_heavy_text():
    checker = Section4Checker(
        llm_client=None,
        regex_patterns={
            "rx_directives": [re.compile(r"нажми|кликни|перейди|введи|скачай|открой|выбери|запусти", re.I)],
            "rx_marketing": [],
        },
    )

    text = "Нажми кнопку, открой окно и выбери режим проверки."

    assert checker._script_check_tov(text, checker.rx_directives) is False
