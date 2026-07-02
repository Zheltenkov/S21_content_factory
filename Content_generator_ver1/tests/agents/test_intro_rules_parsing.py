import re

from content_gen.agents.intro_rules import (
    IntroRulesAgent,
    _ensure_instruction_keywords,
    _ensure_instruction_word_range,
    _ensure_intro_word_range,
    _format_static_instruction_markdown,
    _remove_annotation_overlap_sentences,
    _strip_generic_intro_sentences,
    _trim_instruction_to_limit,
)
from content_gen.utils.text_analysis import count_words


def _make_agent() -> IntroRulesAgent:
    agent = object.__new__(IntroRulesAgent)
    agent.INTRO_HEADINGS = IntroRulesAgent.INTRO_HEADINGS
    agent.INSTRUCTION_HEADINGS = IntroRulesAgent.INSTRUCTION_HEADINGS
    agent.rx_h3 = re.compile(r"^###\s+(.+?)\s*$", re.M)
    return agent


def test_split_intro_instruction_accepts_alternative_headings():
    agent = _make_agent()
    markdown = (
        "### Контекст проекта\n\n"
        "Это вводный текст проекта.\n\n"
        "### Требования\n\n"
        "**Контекст и ограничения проекта**\n\n"
        "Это инструкция."
    )

    intro, instruction = IntroRulesAgent._split_intro_instruction(agent, markdown)

    assert "вводный текст" in intro.lower()
    assert "контекст и ограничения проекта" in instruction.lower()


def test_split_intro_instruction_falls_back_to_static_instruction_blocks():
    agent = _make_agent()
    markdown = (
        "### Введение\n\n"
        "Это вводный текст.\n\n"
        "**Контекст и ограничения проекта**\n\n"
        "Это уже инструкция без отдельного h3."
    )

    intro, instruction = IntroRulesAgent._split_intro_instruction(agent, markdown)

    assert "вводный текст" in intro.lower()
    assert "это уже инструкция" in instruction.lower()


def test_remove_annotation_overlap_sentences_drops_repeated_opening():
    annotation = (
        "Проект помогает разобраться в инфраструктуре проекта и выбрать подходящие решения для размещения системы."
    )
    intro = (
        "Проект помогает разобраться в инфраструктуре проекта и выбрать подходящие решения для размещения системы. "
        "В реальной задаче это важно, когда команда должна запустить сервис без лишних рисков."
    )

    cleaned = _remove_annotation_overlap_sentences(intro, annotation)

    assert "запустить сервис без лишних рисков" in cleaned.lower()
    assert cleaned.lower().count("инфраструктуре проекта") <= 1


def test_remove_annotation_overlap_sentences_drops_semantic_overlap_not_only_first_phrase():
    annotation = (
        "Проект помогает выявлять проектные риски, оценивать их влияние и выбирать меры реагирования."
    )
    intro = (
        "В работе project manager риск появляется задолго до дедлайна. "
        "Ты научишься выявлять проектные риски, оценивать их влияние и выбирать меры реагирования. "
        "В реальной задаче это нужно, чтобы команда заранее договорилась, какие угрозы обрабатывать первыми."
    )

    cleaned = _remove_annotation_overlap_sentences(intro, annotation)

    assert "выявлять проектные риски" not in cleaned.lower()
    assert "угрозы обрабатывать первыми" in cleaned.lower()


def test_strip_generic_intro_sentences_removes_program_admin_noise():
    intro = (
        "Проект направлен на развитие навыков и станет важным вкладом в твою карьеру. "
        "В реальной задаче это используется, когда нужно согласовать требования к сервису и ограничения по срокам."
    )

    cleaned = _strip_generic_intro_sentences(intro)

    assert "карьер" not in cleaned.lower()
    assert "согласовать требования" in cleaned.lower()


def test_format_static_instruction_markdown_restores_blocks_and_bullets():
    text = (
        "Эта инструкция задаёт **общие правила работы с проектом** и **не описывает конкретные шаги по решению задач**. "
        "**Контекст и ограничения проекта** — **Требования к окружению:** проект выполняется в среде обучения. "
        "— **Исходные данные:** есть доступ к репозиторию. "
        "**Как учиться в проекте** Важно обсуждать и проверять решения."
    )

    formatted = _format_static_instruction_markdown(text)

    assert "\n\n**Контекст и ограничения проекта**\n\n" in formatted
    assert "\n\n— **Требования к окружению:**" in formatted
    assert "\n\n**Как учиться в проекте**\n\n" in formatted


def test_trim_instruction_to_limit_removes_optional_disclaimer_first():
    context_text = " ".join(["команда"] * 95)
    learning_text = " ".join(["проверяет"] * 95)
    disclaimer = " ".join(["дисклеймер"] * 80)
    instruction = (
        "**Контекст и ограничения проекта**\n\n"
        f"{context_text}\n\n"
        "**Как учиться в проекте**\n\n"
        f"{learning_text}\n\n"
        "**Дисклеймер**\n\n"
        f"{disclaimer}"
    )

    trimmed = _trim_instruction_to_limit(instruction, "ru")

    assert count_words(trimmed, "ru") <= 250
    assert "Контекст и ограничения проекта" in trimmed
    assert "Как учиться в проекте" in trimmed
    assert "Дисклеймер" not in trimmed


def test_ensure_instruction_keywords_inserts_literal_rubric_markers():
    instruction = (
        "**Контекст и ограничения проекта**\n\n"
        "Работай с материалами проекта и фиксируй выводы.\n\n"
        "**Как учиться в проекте**\n\n"
        "Проверяй решения через обсуждение."
    )

    fixed = _ensure_instruction_keywords(instruction, required_tools=[])

    low = fixed.lower()
    assert "обязательно" in low
    assert "допускается" in low
    assert "запрещено" in low
    assert "контекст и ограничения проекта" in low


def test_ensure_intro_word_range_pads_validator_boundary_case():
    intro = " ".join(["слово"] * 79)

    fixed = _ensure_intro_word_range(intro, "ru")

    assert count_words(fixed, "ru") >= 80
    assert "в реальной задаче" in fixed.lower()


def test_ensure_instruction_word_range_preserves_keywords_and_upper_limit():
    instruction = (
        "**Контекст и ограничения проекта**\n\n"
        + " ".join(["материалы"] * 140)
        + "\n\n**Как учиться в проекте**\n\n"
        + " ".join(["проверяй"] * 140)
    )

    fixed = _ensure_instruction_word_range(instruction, required_tools=["Markdown"], language="ru")
    low = fixed.lower()

    assert count_words(fixed, "ru") <= 250
    assert "обязательно" in low
    assert "допускается" in low
    assert "запрещено" in low
