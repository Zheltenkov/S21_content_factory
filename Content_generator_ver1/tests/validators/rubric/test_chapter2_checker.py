import re

from content_gen.validators.rubric.chapter2_checker import Chapter2Checker


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
