from __future__ import annotations

from content_gen.agents.base.llm_client import LLMClientProtocol
from content_gen.agents.translator import TranslatorAgent
from content_gen.models.schemas import ProjectSeed
from content_gen.utils.protected_blocks import protect_blocks


class RecordingTranslationLLM(LLMClientProtocol):
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict[str, str]] = []

    def complete(self, system: str, user: str, response_format=None, **kwargs) -> str:
        self.calls.append({"system": system, "user": user})
        return self.response


def _seed() -> ProjectSeed:
    return ProjectSeed(
        language="ru",
        project_type="individual",
        direction="PjM",
        thematic_block="Блок",
        audience_level="base",
        project_description="Описание",
        sjm="Сюжет",
        learning_outcomes=[],
        skills=[],
        required_tools=[],
    )


def test_translation_protection_can_leave_tables_and_formulas_editable() -> None:
    markdown = """# Проект

| Риск | Вероятность |
| --- | --- |
| Срыв срока | высокая |

$$R = P \\cdot I \\quad \\text{Ожидаемый риск}$$

```mermaid
flowchart TD
    A --> B
```
"""

    protected, blocks = protect_blocks(
        markdown,
        protect_code=True,
        protect_mermaid=True,
        protect_formulas=False,
        protect_tables=False,
    )

    assert "| Риск | Вероятность |" in protected
    assert "$$R = P \\cdot I" in protected
    assert "[[[BLOCK_0]]]" in protected
    assert blocks[0].block_type == "mermaid"


def test_kyrgyz_translation_prompt_requires_cyrillic_and_exposes_tables_formulas() -> None:
    original = """# Проект

| Риск | Вероятность |
| --- | --- |
| Срыв срока | высокая |

$$R = P \\cdot I \\quad \\text{Ожидаемый риск}$$

```mermaid
flowchart TD
    A --> B
```
"""
    translated = """# Долбоор

| Тобокелдик | Ыктымалдык |
| --- | --- |
| Мөөнөт үзүлүшү | жогору |

$$R = P \\cdot I \\quad \\text{Күтүлгөн тобокелдик}$$

```mermaid
flowchart TD
A --> B
```
"""
    llm = RecordingTranslationLLM(translated)
    result = TranslatorAgent(llm).translate(original, "kg", _seed(), strict=True)

    prompt = llm.calls[0]["user"]
    system = llm.calls[0]["system"]
    assert "кыргызской кириллицей" in system
    assert "кыргызской кириллицей" in prompt
    assert "| Риск | Вероятность |" in prompt
    assert "$$R = P \\cdot I" in prompt
    assert "```mermaid" not in prompt
    assert "| Тобокелдик | Ыктымалдык |" in result
    assert "Ожидаемый риск" not in result


def test_translation_script_validator_flags_wrong_script() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))

    latin_kyrgyz = (
        "Bul dokumenttoghu tekst kyrgyz tilinde bolush kerek, birok al latin "
        "transliteratsiyasy menen jazylgan jana oquuchu uchun tushunuksuz."
    )
    assert agent._validate_script_coverage(latin_kyrgyz, "kg")
    assert not agent._validate_script_coverage(
        "Бул документ кыргыз тилинде жазылган, README жана API терминдери гана өзгөрбөйт.",
        "kg",
    )
    assert agent._validate_script_coverage(
        "Бу ҳужжат ҳали ҳам кириллда қолган ва ўзбек лотинига ўтмаган.",
        "uz",
    )


def test_translation_script_validator_allows_programming_io_tokens_for_tajik() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))
    translated = """# Exam_07_03. Ҳазфи фосилаҳои зиёдатӣ

| Майдон | Қимат |
| ------ | ------ |
| Директория барои ҳал | src/ |
| Файли ҳал | main.c |
| Маълумоти воридшаванда | Ҷараёни стандартии ворид stdin |
| Маълумоти баромад | Ҷараёни стандартии баромад stdout |

Барномае навис, ки фосилаҳои зиёдатиро тоза мекунад ва натиҷаро ба stdout мебарорад.
Вуруд аз stdin хонда мешавад. Намуна: 1&nbsp;&nbsp;&nbsp;2&nbsp;&nbsp;3.
"""

    assert agent._validate_script_coverage(translated, "tg") == []


def test_cleanup_keeps_leading_exam_identifier_at_heading_start() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))
    original = """# Exam_04_01. Биномиальные коэффициенты

## Задание

Текст.
"""
    translated = """# Коэффитсиентҳои биномиалӣ Exam_04_01

## Вазифа

Матн.
"""

    cleaned = agent._cleanup_translation(translated, original)

    assert cleaned.startswith("# Exam_04_01. Коэффитсиентҳои биномиалӣ")


def test_cleanup_prepends_missing_project_identifier_to_heading() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))
    original = """# D01T01: Знакомство с Linux и Git-системой

## Chapter I

Текст.
"""
    translated = """# Шиносоӣ бо Linux ва Git-система

## Боби I

Матн.
"""

    cleaned = agent._cleanup_translation(translated, original)

    assert cleaned.startswith("# D01T01: Шиносоӣ бо Linux ва Git-система")


def test_cleanup_keeps_numeric_identifier_at_heading_start() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))
    original = """# 04_01. Биномиальные коэффициенты

## 1.1. Рекомендации

Текст.
"""
    translated = """# Коэффитсиентҳои биномиалӣ 04_01

## Тавсияҳо 1.1

Матн.
"""

    cleaned = agent._cleanup_translation(translated, original)

    assert cleaned.startswith("# 04_01. Коэффитсиентҳои биномиалӣ")
    assert "## 1.1. Тавсияҳо" in cleaned


def test_language_coverage_preserves_markdown_link_labels_for_toc() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))
    original = """# D01T01: Знакомство с Linux и Git-системой

## Contents

1. [Введение](#введение)
2. [Chapter I](#chapter-i)
    2.1. [Level 1. Room 1](#level-1-room-1)
3. [Quest 1. Clone](#quest-1-clone)
"""
    translated = """# D01T01: Linux жана Git системасы менен таанышуу

## Мазмуну

1. [Киришүү](#введение)
2. [I бөлүм](#chapter-i)
    2.1. [1-деңгээл. 1-бөлмө](#level-1-room-1)
3. [1-тапшырма. Клондоо](#quest-1-clone)
"""

    assert agent._extract_text_content(original).count("Введение") == 1
    assert agent._extract_text_content(translated).count("Киришүү") == 1
    assert agent._validate_language_coverage(original, translated) == []


def test_language_coverage_still_flags_untranslated_toc_labels() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))
    original = """# D01T01: Знакомство с Linux и Git-системой

## Contents

1. [Введение](#введение)
2. [Chapter I](#chapter-i)
    2.1. [Level 1. Room 1](#level-1-room-1)
3. [Quest 1. Clone](#quest-1-clone)
"""
    translated = """# D01T01: Linux жана Git системасы менен таанышуу

## Мазмуну

1. [Введение](#введение)
2. [Chapter I](#chapter-i)
    2.1. [Level 1. Room 1](#level-1-room-1)
3. [Quest 1. Clone](#quest-1-clone)
"""

    untranslated = agent._validate_language_coverage(original, translated)

    assert untranslated
    assert untranslated[0][0] == 1
    assert "Мазмуну" in untranslated[0][1]


def test_language_coverage_ignores_technical_numeric_output_sections() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))
    original = """# Проект

#### Все значения записываются с точностью до 7 знаков после запятой

Результат:

-3.1415927 | 0.0919997 | - | 0.1013212<br/>
-2.9883442 | 0.1007029 | - | 0.1119796<br/>
...............e.r.r.o.r.......................................................

`src/data/door_data.txt`

***LOADING...***
"""
    translated = """# Лоиҳа

#### Ҳамаи қиматҳо бо дақиқии то 7 рақам пас аз вергул навишта мешаванд

Натиҷа:

-3.1415927 | 0.0919997 | - | 0.1013212<br/>
-2.9883442 | 0.1007029 | - | 0.1119796<br/>
...............e.r.r.o.r.......................................................

`src/data/door_data.txt`

***LOADING...***
"""

    assert agent._validate_language_coverage(original, translated) == []


def test_language_coverage_still_flags_untranslated_prose_section() -> None:
    agent = TranslatorAgent(RecordingTranslationLLM(""))
    original = """# Проект

## Введение

Это задание поможет тебе познакомиться с терминалом, командами и системой Git.
Сначала ты разберешь структуру проекта, затем выполнишь последовательность шагов.
"""
    translated = """# Лоиҳа

## Муқаддима

Это задание поможет тебе познакомиться с терминалом, командами и системой Git.
Сначала ты разберешь структуру проекта, затем выполнишь последовательность шагов.
"""

    untranslated = agent._validate_language_coverage(original, translated)

    assert untranslated
    assert untranslated[0][0] == 1
