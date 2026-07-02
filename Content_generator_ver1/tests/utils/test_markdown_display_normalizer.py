import pytest

from content_gen.utils.markdown_display_normalizer import (
    normalize_flattened_markdown_tables,
    normalize_flattened_mermaid_fences,
    normalize_example_blocks,
    normalize_markdown_display_blocks,
    strip_protected_block_instruction_leaks,
)


def test_normalize_flattened_mermaid_fence_restores_graph_lines():
    md = (
        "<div>```mermaid "
        '%%{init:{"theme":"dark"}}%% %%{init:{"theme":"dark"}}%% '
        "flowchart TD A[Сбор данных] --> B[Проверка качества] "
        "B --> C[Планирование действий] C --> D[Реализация] "
        "```<p>caption</p></div>"
    )

    normalized = normalize_flattened_mermaid_fences(md)

    assert "```mermaid\n" in normalized
    assert normalized.count("%%{init:") == 0
    assert '"theme":"dark"' not in normalized
    assert "flowchart TD\n" in normalized
    assert "\n    A[Сбор данных] --> B[Проверка качества]\n" in normalized
    assert "\n    B --> C[Планирование действий]\n" in normalized
    assert "<div>\n```mermaid" in normalized
    assert "\n```" in normalized


def test_normalize_mermaid_removes_model_visual_styling():
    md = """```mermaid
%%{init: {"theme":"dark","themeVariables":{"primaryColor":"#0f1419"}}}%%
flowchart TD
    A[Клиент] --> B[Сервер]
    classDef dark fill:#0f1419,color:#0f1419,stroke:#0f1419
    class A,B dark
    style B fill:#000,color:#000
    linkStyle 0 stroke:#000,color:#000
```"""

    normalized = normalize_markdown_display_blocks(md)

    assert "```mermaid\nflowchart TD" in normalized
    assert "%%{init:" not in normalized
    assert "classDef" not in normalized
    assert "class A" not in normalized
    assert "style B" not in normalized
    assert "linkStyle" not in normalized
    assert "#0f1419" not in normalized
    assert "\n    A[Клиент] --> B[Сервер]\n" in normalized


def test_normalize_mermaid_splits_unlabeled_node_statement_chains():
    md = """<div>```mermaid
flowchart TD
    E --> F F --> G
```</div>"""

    normalized = normalize_flattened_mermaid_fences(md)

    assert "<div>\n```mermaid" in normalized
    assert "\n    E --> F\n" in normalized
    assert "\n    F --> G\n" in normalized


def test_normalize_mermaid_repairs_dotted_cyrillic_edge_labels():
    md = """```mermaid
flowchart TD
    A[Клиент обновляет состояние] B -. контроль .-> H[Диагностика]
```"""

    normalized = normalize_flattened_mermaid_fences(md)

    assert "\n    A[Клиент обновляет состояние]\n" in normalized
    assert "\n    B -.->|контроль| H[Диагностика]\n" in normalized


def test_normalize_mermaid_repairs_unicode_arrows_and_flat_labeled_edges():
    md = """```mermaid
flowchart TD A[Риск выявлен] → B{Что он затрагивает?} B -->|Срок| C[Добавить резерв времени] B →|Ресурс| E[Скорректировать загрузку] C —> H[Диаграмма Ганта]
```"""

    normalized = normalize_flattened_mermaid_fences(md)

    assert "→" not in normalized
    assert "—>" not in normalized
    assert "\n    A[Риск выявлен] --> B{Что он затрагивает?}\n" in normalized
    assert "\n    B -->|Срок| C[Добавить резерв времени]\n" in normalized
    assert "\n    B -->|Ресурс| E[Скорректировать загрузку]\n" in normalized
    assert "\n    C --> H[Диаграмма Ганта]\n" in normalized


def test_normalize_mermaid_repairs_flattened_sequence_diagram():
    md = """```mermaid
sequenceDiagram Клиент participant S as Сервер C->>S: Отправка запроса с данными S->>S: Проверка формата и логики alt Запрос корректен S-->>C: Ответ 2xx с JSON else Ошибка на сервере S-->>C: Ответ 5xx end
```"""

    normalized = normalize_flattened_mermaid_fences(md)

    assert "\nsequenceDiagram\n" in normalized
    assert "\n    participant C as Клиент\n" in normalized
    assert "\n    participant S as Сервер\n" in normalized
    assert "\n    C->>S: Отправка запроса с данными\n" in normalized
    assert "\n    S->>S: Проверка формата и логики\n" in normalized
    assert "\n    alt Запрос корректен\n" in normalized
    assert "\n    S-->>C: Ответ 2xx с JSON\n" in normalized
    assert "\n    else Ошибка на сервере\n" in normalized
    assert "\n    S-->>C: Ответ 5xx\n" in normalized
    assert "\n    end\n" in normalized
    assert "participant S as Сервер C->>S" not in normalized


def test_normalize_markdown_display_blocks_removes_stray_caption_dot():
    md = (
        "*Структурирует обмен по слоям.*\n\n"
        ". **Клиент** — это часть системы, которая отправляет запросы."
    )

    normalized = normalize_markdown_display_blocks(md)

    assert "\n\n**Клиент** — это часть системы" in normalized
    assert ". **Клиент**" not in normalized


def test_normalize_mermaid_repairs_missing_closing_fence_before_caption():
    md = (
        "Текст перед диаграммой. ```mermaid\n"
        "flowchart TD\n"
        "A[Старт] --> B[Проверка]\n"
        "B --> C{Готово?}\n"
        "C -- да --> D[Финиш]\n"
        "<p style='text-align:center;font-style:italic;'>Алгоритм проверки</p> </div></div> "
        "**Контекст предыдущих частей:**\n"
        "- Первая часть"
    )

    normalized = normalize_markdown_display_blocks(md)

    assert "Текст перед диаграммой.\n\n```mermaid\n" in normalized
    assert "\n    A[Старт] --> B[Проверка]\n" in normalized
    assert "\n```\n\n*Алгоритм проверки*\n\n**Контекст предыдущих частей:**" in normalized
    assert "<p" not in normalized
    assert "</div>" not in normalized


@pytest.mark.parametrize("boundary", ["Ситуация", "Цель", "Входные данные"])
def test_normalize_mermaid_repairs_missing_closing_fence_before_extended_boundaries(boundary):
    md = (
        "```mermaid\n"
        "flowchart TD\n"
        "A[Старт] --> B[Проверка]\n"
        f"**{boundary}:**\n"
        "Текст следующего блока."
    )

    normalized = normalize_markdown_display_blocks(md)

    assert "```mermaid\nflowchart TD\n    A[Старт] --> B[Проверка]\n```\n" in normalized
    assert f"**{boundary}:**\nТекст следующего блока." in normalized


def test_normalize_flattened_markdown_table_restores_rows_and_caption():
    md = (
        "Ниже показаны категории задач. "
        "| Тип задачи | Описание | Примеры | "
        "|--------------------|----------|----------| "
        "| Аналитические | Работа с исходными данными | Разбор интервью | "
        "| Коммуникационные | Подготовка сообщений | Письмо команде | "
        "*Таблица 1. Категории задач*"
    )

    normalized = normalize_flattened_markdown_tables(md)

    assert "Ниже показаны категории задач.\n\n| Тип задачи | Описание | Примеры |" in normalized
    assert "\n|--------------------|----------|----------|\n" in normalized
    assert "\n| Аналитические | Работа с исходными данными | Разбор интервью |\n" in normalized
    assert "\n\n*Таблица 1. Категории задач*" in normalized


def test_normalize_markdown_display_blocks_keeps_tables_outside_fenced_code_only():
    md = (
        "```text\n"
        "| A | B | |---|---| | 1 | 2 |\n"
        "```\n\n"
        "Таблица: | A | B | |---|---| | 1 | 2 |"
    )

    normalized = normalize_markdown_display_blocks(md)

    assert "```text\n| A | B | |---|---| | 1 | 2 |\n```" in normalized
    assert "Таблица:\n\n| A | B |\n|---|---|\n| 1 | 2 |" in normalized


def test_normalize_example_blocks_separates_inline_example_marker():
    md = (
        "Для этого проекта важно разобраться с дорожной картой без лишнего тумана. "
        "Пример: В 2023 году команда связала задачи с релизными целями."
    )

    normalized = normalize_example_blocks(md)

    assert "тумана.\n\n**Пример:** В 2023" in normalized


def test_normalize_example_blocks_keeps_code_fences_unchanged():
    md = "```text\nПример: это часть кода\n```\n\nТекст.\nПример: это пример теории."

    normalized = normalize_markdown_display_blocks(md)

    assert "```text\nПример: это часть кода\n```" in normalized
    assert "Текст.\n\n**Пример:** это пример теории." in normalized


def test_strip_protected_block_instruction_leaks_removes_internal_regeneration_prompt_text():
    md = (
        "Таблица выше задает рамку оценки.\n\n"
        "и комментарии PROTECTED_BLOCK . Это защищённые таблицы, диаграммы, формулы или код. "
        "КРИТИЧЕСКИ ВАЖНО: - Сохрани все маркеры [[[BLOCK_0]]] без изменений. "
        "- Сохрани комментарии PROTECTED_BLOCK без изменений. "
        "Для этого проекта важно понять чёрных лебедей и план реакции.\n"
    )

    normalized = strip_protected_block_instruction_leaks(md)

    assert "PROTECTED_BLOCK" not in normalized
    assert "[[[BLOCK_0]]]" not in normalized
    assert "КРИТИЧЕСКИ ВАЖНО" not in normalized
    assert "Таблица выше задает рамку оценки." in normalized
    assert "Для этого проекта важно понять чёрных лебедей" in normalized


def test_normalize_markdown_display_blocks_keeps_unresolved_protected_markers_for_validation():
    md = 'Текст\n\n<!-- PROTECTED_BLOCK id=0 type=mermaid preview="flowchart" -->\n[[[BLOCK_0]]]\n'

    normalized = normalize_markdown_display_blocks(md)

    assert "PROTECTED_BLOCK" in normalized
    assert "[[[BLOCK_0]]]" in normalized
