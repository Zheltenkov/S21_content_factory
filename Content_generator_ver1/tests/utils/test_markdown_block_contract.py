from content_gen.utils.markdown_block_contract import MarkdownBlockContract


def test_markdown_block_contract_hides_fenced_blocks_from_editor_and_restores_them() -> None:
    contract = MarkdownBlockContract()
    original = """Текст до.

```mermaid
flowchart TD
    A[Сбор данных] --> B[Проверка]
```

Текст после.
"""

    def edit_fn(protected: str) -> str:
        assert "```mermaid" not in protected
        return protected.replace("Текст после.", "Обновленный текст после.")

    edited = contract.edit(original, edit_fn, min_chars=20)

    assert "```mermaid" in edited
    assert "flowchart TD" in edited
    assert "Обновленный текст после." in edited
    assert "[[[BLOCK_" not in edited


def test_markdown_block_contract_detects_flattened_mermaid() -> None:
    contract = MarkdownBlockContract()
    broken = """```mermaid
flowchart TD A[Сбор данных] --> B[Проверка]
```"""

    assert "possible flattened mermaid block" in contract.validate(broken)


def test_markdown_block_contract_accepts_multiline_mermaid() -> None:
    contract = MarkdownBlockContract()
    valid = """```mermaid
flowchart TD
    A[Сбор данных] --> B[Проверка]
```"""

    assert contract.validate(valid) == []


def test_markdown_block_contract_hides_tables_from_editor_and_restores_them() -> None:
    contract = MarkdownBlockContract()
    original = """До таблицы.

| Тип | Значение |
| --- | --- |
| A | 1 |

После таблицы.
"""

    def edit_fn(protected: str) -> str:
        assert "| Тип |" not in protected
        return protected.replace("До таблицы.", "Перед таблицей.")

    edited = contract.edit(original, edit_fn, min_chars=20)

    assert "| Тип | Значение |" in edited
    assert "Перед таблицей." in edited
    assert "[[[BLOCK_" not in edited
