"""
Тесты для модуля protected_blocks.
"""

from content_gen.utils.protected_blocks import (
    BlockInfo,
    _normalize_mermaid_block,
    _validate_block_content,
    protect_blocks,
    restore_blocks,
)


class TestProtectBlocks:
    """Тесты для функции protect_blocks."""

    def test_protect_mermaid_block(self):
        """Тест защиты mermaid-блока."""
        md = """
# Заголовок

```mermaid
flowchart TD
    A --> B
    B --> C
```
"""
        protected, blocks = protect_blocks(md)

        assert len(blocks) == 1
        assert blocks[0].block_type == "mermaid"
        assert "```mermaid" in blocks[0].content
        assert "[[[BLOCK_0]]]" in protected
        assert "```mermaid" not in protected or "<!-- PROTECTED_BLOCK" in protected

    def test_protect_code_block(self):
        """Тест защиты code-блока."""
        md = """
# Заголовок

```python
def hello():
    print("Hello")
```
"""
        protected, blocks = protect_blocks(md)

        assert len(blocks) == 1
        assert blocks[0].block_type == "code"
        assert "```python" in blocks[0].content
        assert "[[[BLOCK_0]]]" in protected

    def test_protect_formula_block(self):
        """Тест защиты формулы."""
        md = """
# Заголовок

$$E = mc^2$$
"""
        protected, blocks = protect_blocks(md)

        assert len(blocks) == 1
        assert blocks[0].block_type == "formula"
        assert "$$E = mc^2$$" in blocks[0].content
        assert "[[[BLOCK_0]]]" in protected
        assert "$$E = mc^2$$" not in protected

    def test_protect_multiple_blocks(self):
        """Тест защиты нескольких блоков."""
        md = """
# Заголовок

$$E = mc^2$$

```mermaid
graph TD
    A --> B
```

```python
print("Hello")
```
"""
        protected, blocks = protect_blocks(md)

        assert len(blocks) == 3
        assert blocks[0].block_type == "formula"
        assert blocks[1].block_type == "mermaid"
        assert blocks[2].block_type == "code"
        assert "[[[BLOCK_0]]]" in protected
        assert "[[[BLOCK_1]]]" in protected
        assert "[[[BLOCK_2]]]" in protected

    def test_protect_no_blocks(self):
        """Тест защиты текста без блоков."""
        md = """
# Заголовок

Простой текст без блоков.
"""
        protected, blocks = protect_blocks(md)

        assert len(blocks) == 0
        assert protected == md

    def test_protect_markdown_table(self):
        """Тест защиты markdown-таблицы."""
        md = """
# Заголовок

| Риск | Вероятность |
| --- | --- |
| Срыв срока | высокая |
"""
        protected, blocks = protect_blocks(md)

        assert len(blocks) == 1
        assert blocks[0].block_type == "table"
        assert "| Риск | Вероятность |" in blocks[0].content
        assert "| Риск | Вероятность |" not in protected
        assert "[[[BLOCK_0]]]" in protected


class TestRestoreBlocks:
    """Тесты для функции restore_blocks."""

    def test_restore_single_block(self):
        """Тест восстановления одного блока."""
        blocks = [
            BlockInfo(id=0, block_type="mermaid", content="```mermaid\nflowchart TD\n    A --> B\n```")
        ]
        md_with_markers = """
# Заголовок

<!-- PROTECTED_BLOCK id=0 type=mermaid preview="flowchart TD A --> B" -->
[[[BLOCK_0]]]
"""
        restored = restore_blocks(md_with_markers, blocks)

        assert "```mermaid" in restored
        assert "[[[BLOCK_0]]]" not in restored
        assert "<!-- PROTECTED_BLOCK" not in restored

    def test_restore_multiple_blocks(self):
        """Тест восстановления нескольких блоков."""
        blocks = [
            BlockInfo(id=0, block_type="formula", content="$$E = mc^2$$"),
            BlockInfo(id=1, block_type="mermaid", content="```mermaid\ngraph TD\n    A --> B\n```"),
        ]
        md_with_markers = """
# Заголовок

$$E = mc^2$$

<!-- PROTECTED_BLOCK id=0 type=formula preview="E = mc^2" -->
[[[BLOCK_0]]]

<!-- PROTECTED_BLOCK id=1 type=mermaid preview="graph TD A --> B" -->
[[[BLOCK_1]]]
"""
        restored = restore_blocks(md_with_markers, blocks)

        assert "$$E = mc^2$$" in restored
        assert "```mermaid" in restored
        assert "[[[BLOCK_0]]]" not in restored
        assert "[[[BLOCK_1]]]" not in restored

    def test_restore_deleted_block(self):
        """Тест восстановления с удалённым блоком (маркер отсутствует)."""
        blocks = [
            BlockInfo(id=0, block_type="formula", content="$$E = mc^2$$"),
            BlockInfo(id=1, block_type="mermaid", content="```mermaid\ngraph TD\n    A --> B\n```"),
        ]
        md_with_markers = """
# Заголовок

<!-- PROTECTED_BLOCK id=0 type=formula preview="E = mc^2" -->
[[[BLOCK_0]]]

<!-- Блок 1 был удалён агентом -->
"""
        restored = restore_blocks(md_with_markers, blocks)

        assert "$$E = mc^2$$" in restored
        assert "```mermaid" not in restored  # Блок 1 не восстановлен, т.к. маркер отсутствует

    def test_restore_invalid_index(self):
        """Тест восстановления с некорректным индексом."""
        blocks = [
            BlockInfo(id=0, block_type="formula", content="$$E = mc^2$$"),
        ]
        md_with_markers = """
# Заголовок

[[[BLOCK_5]]]  # Несуществующий индекс
"""
        restored = restore_blocks(md_with_markers, blocks)

        # Маркер должен быть удалён, т.к. индекс некорректный
        assert "[[[BLOCK_5]]]" not in restored


class TestValidateBlockContent:
    """Тесты для функции _validate_block_content."""

    def test_validate_valid_mermaid(self):
        """Тест валидации корректного mermaid-блока."""
        block = "```mermaid\nflowchart TD\n    A --> B\n```"
        assert _validate_block_content(block, "mermaid") is True

    def test_validate_invalid_mermaid(self):
        """Тест валидации некорректного mermaid-блока."""
        block = "```mermaid\n```"  # Пустой блок
        assert _validate_block_content(block, "mermaid") is False

    def test_validate_valid_formula(self):
        """Тест валидации корректной формулы."""
        block = "$$E = mc^2$$"
        assert _validate_block_content(block, "formula") is True

    def test_validate_invalid_formula(self):
        """Тест валидации некорректной формулы."""
        block = "E = mc^2"  # Без $$
        assert _validate_block_content(block, "formula") is False

    def test_validate_valid_code(self):
        """Тест валидации корректного code-блока."""
        block = "```python\nprint('Hello')\n```"
        assert _validate_block_content(block, "code") is True

    def test_validate_valid_table(self):
        """Тест валидации корректной markdown-таблицы."""
        block = "| Риск | Вероятность |\n| --- | --- |\n| A | B |"
        assert _validate_block_content(block, "table") is True


class TestNormalizeMermaidBlock:
    """Тесты для функции _normalize_mermaid_block."""

    def test_normalize_mermaid_with_spaces(self):
        """Тест нормализации mermaid с проблемами пробелов."""
        block = "```mermaid\nflowchart TD\n    A-->B[Поддержка]\n```"
        normalized = _normalize_mermaid_block(block)

        assert "A --> B[Поддержка]" in normalized or "A --> B" in normalized
        assert "```mermaid" in normalized
        assert "```" in normalized

    def test_normalize_mermaid_with_label(self):
        """Тест нормализации mermaid с метками на стрелках."""
        block = "```mermaid\nflowchart TD\n    A -->|label|B\n```"
        normalized = _normalize_mermaid_block(block)

        assert "A -->|label| B" in normalized or "A -->|label|B" in normalized


class TestRoundTrip:
    """Тесты полного цикла protect -> restore."""

    def test_round_trip_simple(self):
        """Тест полного цикла для простого случая."""
        original = """
# Заголовок

$$E = mc^2$$

Текст между блоками.

```mermaid
flowchart TD
    A --> B
```
"""
        protected, blocks = protect_blocks(original)
        restored = restore_blocks(protected, blocks)

        # Проверяем, что все блоки восстановлены
        assert "$$E = mc^2$$" in restored
        assert "```mermaid" in restored
        assert "flowchart TD" in restored

    def test_round_trip_complex(self):
        """Тест полного цикла для сложного случая."""
        original = """
# Заголовок

$$C = E + B + Q$$

```python
def calculate():
    return 42
```

```mermaid
graph TD
    Start --> Process
    Process --> End
```

$$T = T_0 \\times (1 + \\frac{B - B_0}{B_0})$$
"""
        protected, blocks = protect_blocks(original)
        restored = restore_blocks(protected, blocks)

        # Проверяем количество блоков
        assert len(blocks) == 4
        # Проверяем, что все блоки восстановлены
        assert "$$C = E + B + Q$$" in restored
        assert "```python" in restored
        assert "```mermaid" in restored
        assert "$$T = T_0" in restored
