import re

from content_factory.generation.models.criteria_models import CheckMethod, StrictnessLevel
from content_factory.generation.validators.rubric.chapter2_checker import Chapter2Checker


def _checker() -> Chapter2Checker:
    return Chapter2Checker(
        language="ru",
        regex_patterns={"rx_theory_part": re.compile(r"^###\s+2\.\d+\.\s+.+$", re.M)},
    )


def _prose(topic: str) -> str:
    sentence = (
        f"**{topic}** — это рабочий элемент планирования, который помогает команде "
        "связать цель, ограничения, зависимости, дорожную карту и ожидаемый результат. "
        "В учебном проекте студент анализирует исходные факты, выделяет решения, "
        "уточняет порядок работ и объясняет, как выбранная структура помогает команде "
        "контролировать сроки, риски и следующий шаг. "
    )
    return sentence * 3


def test_theory_volume_counts_prose_without_tables_or_mermaid() -> None:
    table = "\n".join(
        [
            "| Очень длинная колонка | Еще одна длинная колонка |",
            "| --- | --- |",
            "| " + "данные " * 80 + " | " + "пояснение " * 80 + " |",
        ]
    )
    diagram = "```mermaid\nflowchart TD\nA[Очень длинная подпись] --> B[Еще одна подпись]\n```"
    ch2 = f"""
### 2.1. Бэклог
{_prose("Бэклог")}
{table}
{diagram}
**Пример:** Внешний пример не входит в лимит основной теории.

### 2.2. Дорожная карта
{_prose("Дорожная карта")}
{table}

### 2.3. Диаграмма Ганта
{_prose("Диаграмма Ганта")}
{diagram}
"""

    items = _checker().check(ch2, learning_outcomes=["умеет строить дорожную карту и диаграмму ганта"])

    volume_item = next(item for item in items if item.id == "2.4.3")
    lo_item = next(item for item in items if item.id == "2.4.5")
    assert volume_item.score == 1
    assert lo_item.score == 1
    assert lo_item.details["coverage_percent"] >= 50


def test_learning_outcome_coverage_reports_missing_evidence_without_llm() -> None:
    ch2 = f"""
### 2.1. Бэклог
{_prose("Бэклог")}

### 2.2. Дорожная карта
{_prose("Дорожная карта")}

### 2.3. Диаграмма Ганта
{_prose("Диаграмма Ганта")}
"""

    items = _checker().check(ch2, learning_outcomes=["умеет настраивать Kubernetes deployment и Helm chart"])

    lo_item = next(item for item in items if item.id == "2.4.5")
    assert lo_item.score == 0
    assert lo_item.details["missing"]
    assert "Недостаточно evidence по образовательным результатам" in lo_item.comments[0]


def test_learning_outcome_coverage_is_optional_without_context() -> None:
    ch2 = f"""
### 2.1. Бэклог
{_prose("Бэклог")}

### 2.2. Дорожная карта
{_prose("Дорожная карта")}

### 2.3. Диаграмма Ганта
{_prose("Диаграмма Ганта")}
"""

    items = _checker().check(ch2, learning_outcomes=[])

    lo_item = next(item for item in items if item.id == "2.4.5")
    assert lo_item.score == 1
    assert lo_item.title == "Проверка соответствия образовательным результатам"
    assert lo_item.comments == []
    assert lo_item.details["mode"] == "skipped"


def test_2_4_6_ignores_primerno_adverb() -> None:
    """«примерно/применять» больше не считаются примером (границы слова, не подстрока)."""
    ch2 = (
        "### 2.1. Первый раздел\n"
        "Мы действуем примерно так же, применяя знания на практике команды.\n\n"
        "### 2.2. Второй раздел\n"
        "Здесь примерно описан процесс без конкретной ситуации для студента.\n\n"
        "### 2.3. Третий раздел\n"
        "Ещё один абзац, где всё примерно понятно и применимо в проекте.\n"
    )
    items = _checker().check(ch2)

    example_item = next(item for item in items if item.id == "2.4.6")
    assert example_item.score == 0
    assert example_item.check_method == CheckMethod.SCRIPT
    assert example_item.strictness == StrictnessLevel.SOFT


def test_2_4_6_detects_real_example_word() -> None:
    ch2 = (
        "### 2.1. Первый раздел\n"
        "**Пример:** команда сверяет решение с требованиями заказчика.\n\n"
        "### 2.2. Второй раздел\n"
        "Разберём кейс: аналитик уточняет риски и фиксирует выводы проекта.\n\n"
        "### 2.3. Третий раздел\n"
        "Рассмотрим ситуацию, когда план меняется в середине спринта.\n"
    )
    items = _checker().check(ch2)

    example_item = next(item for item in items if item.id == "2.4.6")
    assert example_item.score == 1


_LOW_READABILITY = (
    "Институционализированное функционирование квазигосударственных организаций "
    "характеризуется гипертрофированной бюрократизацией многоуровневой "
    "административно-иерархической субординацией участников образовательного процесса "
    "без промежуточной пунктуационной сегментации усложняющей интерпретацию содержания"
)


def test_2_4_7_fails_on_low_readability() -> None:
    """Тавтологичная нормировка убрана: сложный текст теперь честно проваливает 2.4.7."""
    body = _LOW_READABILITY + "."
    ch2 = "\n\n".join(f"### 2.{i}. Раздел {i}\n{body}" for i in (1, 2, 3))

    items = _checker().check(ch2)

    readability_item = next(item for item in items if item.id == "2.4.7")
    assert readability_item.score == 0
    assert readability_item.check_method == CheckMethod.SCRIPT
    assert readability_item.strictness == StrictnessLevel.SOFT
    assert readability_item.details["avg_readability"] < 45


def test_readability_item_band_logic() -> None:
    checker = _checker()
    assert checker._readability_item([60.0, 55.0], []).score == 1
    assert checker._readability_item([30.0], [(1, 30.0)]).score == 0
    assert checker._readability_item([], []).score == 0
