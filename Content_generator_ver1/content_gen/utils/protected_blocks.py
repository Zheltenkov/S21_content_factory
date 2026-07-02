"""
content_gen/utils/protected_blocks.py

Модуль для защиты блоков кода, формул и диаграмм при перегенерации README.

Заменяет защищённые блоки на маркеры перед отправкой в LLM,
чтобы предотвратить их случайное изменение или удаление.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class BlockInfo:
    """Информация о защищённом блоке (код, формула, диаграмма)."""

    id: int
    block_type: str  # "code" | "mermaid" | "formula" | "table"
    content: str  # исходный текст блока (с ``` или $$)


# Шаблон маркера, который видит LLM
BLOCK_MARKER_TEMPLATE = "[[[BLOCK_{id}]]]"

# Кодовый блок в Markdown: ```lang\n...\n```
# Важно: используем нежадный квантификатор и учитываем возможные пробелы/табы перед ```
CODE_BLOCK_RE = re.compile(
    r"```(?P<header>[^\n]*)\n(?P<body>.*?)```",
    re.DOTALL | re.MULTILINE,
)

# Блочная формула: $$ ... $$
# Важно: используем нежадный квантификатор и учитываем возможные пробелы/переносы строк
FORMULA_BLOCK_RE = re.compile(
    r"\$\$(?P<body>.*?)\$\$",
    re.DOTALL,
)

# Markdown-таблица: минимум две pipe-строки, среди них separator вида | --- | --- |
TABLE_BLOCK_RE = re.compile(
    r"(?P<table>(?:^[ \t]*\|.*\|\s*\n?){2,})",
    re.MULTILINE,
)

TABLE_SEPARATOR_RE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$"
)

# Inline формулы: $...$ (но не $$...$$)
INLINE_FORMULA_RE = re.compile(
    r"(?<!\$)\$(?!\$)(?P<body>[^$\n]+?)\$(?!\$)",
    re.MULTILINE,
)


def _fix_latex_body(body: str) -> str:
    """
    Исправляем самые частые опечатки в формулах.
    Делаем это очень точечно, только в пределах $$...$$ или $...$.

    Args:
        body: Тело формулы (без $ или $$)

    Returns:
        Исправленное тело формулы
    """
    # x_1^,  x_k^)  x_2^  ->  x_1^*, x_k^*), x_2^*
    body = re.sub(r'(x_[0-9k])\^\s*(?=[,\s)])', r'\1^*', body)

    # Исправляем неправильный синтаксис \text{...}{\text{...}} -> \text{...}_{\text{...}}
    # Это исправляет случаи типа \text{Accuracy}{\text{full}} -> \text{Accuracy}_{\text{full}}
    body = re.sub(
        r'\\text\{([^}]+)\}\{\\text\{([^}]+)\}\}',
        r'\\text{\1}_{\\text{\2}}',
        body
    )

    # Исправляем \text{...}{...} (без \text внутри) -> \text{...}_{...}
    # Например: \text{Accuracy}{full} -> \text{Accuracy}_{full}
    # Но только если это не часть более сложной конструкции
    body = re.sub(
        r'\\text\{([^}]+)\}\{([^}]+)\}(?!\})',
        r'\\text{\1}_{\2}',
        body
    )

    # при желании можно чинить множество c фигурными скобками
    # {1, 2, ..., N} -> \{1, 2, ..., N\}
    body = re.sub(
        r'(?<!\\)\{1,\s*2,\s*\.{3},\s*N\}(?!\})',
        r'\\{1, 2, ..., N\\}',
        body,
    )

    return body


def fix_common_latex_issues_in_md(md: str) -> str:
    """
    Прогоняет весь Markdown, но меняет только содержимое внутри $$...$$ и $...$.
    
    ВАЖНО: Исправляет только реальные ошибки, не трогает корректные формулы.

    Args:
        md: Исходный Markdown текст

    Returns:
        Markdown с исправленными формулами
    """
    def _fix_block_match(m: re.Match) -> str:
        body = m.group('body') or ''
        original_body = body

        # Применяем исправления только если они действительно нужны
        fixed = _fix_latex_body(body)

        # Если тело формулы не изменилось, возвращаем оригинал без изменений
        if fixed == original_body:
            return m.group(0)  # Возвращаем оригинальную формулу целиком

        # Только если были реальные исправления, возвращаем исправленную версию
        return f"$${fixed}$$"

    def _fix_inline_match(m: re.Match) -> str:
        body = m.group('body') or ''
        original_body = body

        # Для inline формул применяем те же исправления
        fixed = _fix_latex_body(body)

        # Если тело формулы не изменилось, возвращаем оригинал без изменений
        if fixed == original_body:
            return m.group(0)

        # Только если были реальные исправления, возвращаем исправленную версию
        return f"${fixed}$"

    # Сначала обрабатываем блочные формулы
    md = FORMULA_BLOCK_RE.sub(_fix_block_match, md)
    # Затем обрабатываем inline формулы
    md = INLINE_FORMULA_RE.sub(_fix_inline_match, md)

    return md


def _make_preview(text: str, max_len: int = 80) -> str:
    """
    Делаем однострочное превью содержимого блока,
    чтобы LLM мог по нему идентифицировать нужную формулу/диаграмму.

    Args:
        text: Текст блока
        max_len: Максимальная длина превью

    Returns:
        Однострочное превью текста
    """
    one_line = " ".join(text.strip().split())
    one_line = one_line.replace('"', "'")  # чтобы не ломать атрибут preview="..."
    if len(one_line) > max_len:
        return one_line[: max_len - 1] + "…"
    return one_line


def protect_blocks(
    md: str,
    *,
    protect_code: bool = True,
    protect_mermaid: bool = True,
    protect_formulas: bool = True,
    protect_tables: bool = True,
) -> tuple[str, list[BlockInfo]]:
    """
    Находит в Markdown выбранные защищаемые блоки:
    - ```кодовые блоки``` и ```mermaid``` (управляются отдельно)
    - блочные формулы $$ ... $$
    - markdown-таблицы

    Заменяет их на:
      <!-- PROTECTED_BLOCK id=N type=... preview="..." -->
      [[[BLOCK_N]]]

    Args:
        md: Исходный Markdown текст
        protect_code: Защищать обычные fenced code-блоки.
        protect_mermaid: Защищать fenced mermaid-диаграммы.
        protect_formulas: Защищать блочные LaTeX-формулы.
        protect_tables: Защищать Markdown-таблицы.

    Returns:
        Tuple[защищённый Markdown с маркерами, список BlockInfo с оригинальным содержимым]
    """
    blocks: list[BlockInfo] = []
    result_parts: list[str] = []
    pos = 0
    length = len(md)

    while pos < length:
        code_match = CODE_BLOCK_RE.search(md, pos) if (protect_code or protect_mermaid) else None
        formula_match = FORMULA_BLOCK_RE.search(md, pos) if protect_formulas else None
        table_match = _find_next_table(md, pos) if protect_tables else None

        # выбираем ближайшее совпадение
        candidates = [m for m in (code_match, formula_match, table_match) if m]
        if not candidates:
            result_parts.append(md[pos:])
            break

        match = min(candidates, key=lambda m: m.start())

        # добираем текст до найденного блока
        if match.start() > pos:
            result_parts.append(md[pos : match.start()])

        # определяем тип блока и контент
        if match.re is CODE_BLOCK_RE:
            header = match.group("header") or ""
            body = match.group("body") or ""
            block_type = "mermaid" if "mermaid" in header.lower() else "code"
            full_block = md[match.start() : match.end()]
            if (block_type == "mermaid" and not protect_mermaid) or (
                block_type == "code" and not protect_code
            ):
                result_parts.append(full_block)
                pos = match.end()
                continue
        elif match.re is FORMULA_BLOCK_RE:
            # FORMULA_BLOCK_RE
            body = match.group("body") or ""
            block_type = "formula"
            full_block = md[match.start() : match.end()]
        else:
            body = match.group("table") or match.group(0)
            block_type = "table"
            full_block = md[match.start() : match.end()]

        block_id = len(blocks)
        blocks.append(BlockInfo(id=block_id, block_type=block_type, content=full_block))

        if block_type == "table":
            row_count = len([line for line in body.splitlines() if line.strip()])
            preview = f"markdown table rows={row_count}"
        else:
            preview = _make_preview(body)
        marker = BLOCK_MARKER_TEMPLATE.format(id=block_id)

        comment = (
            f'<!-- PROTECTED_BLOCK id={block_id} '
            f'type={block_type} preview="{preview}" -->'
        )

        # блок занимает свои собственные строки
        # Сохраняем переносы строк до и после блока для правильного восстановления
        replacement = f"\n{comment}\n{marker}\n"

        result_parts.append(replacement)
        pos = match.end()

    protected_md = "".join(result_parts)
    return protected_md, blocks


def _find_next_table(md: str, pos: int) -> re.Match | None:
    """Find the next markdown table after pos, skipping pipe text without separator."""
    search_pos = pos
    while True:
        match = TABLE_BLOCK_RE.search(md, search_pos)
        if not match:
            return None
        table = match.group("table") or ""
        lines = [line.rstrip() for line in table.splitlines() if line.strip()]
        has_separator = any(TABLE_SEPARATOR_RE.match(line) for line in lines[:3])
        if has_separator:
            return match
        search_pos = max(match.end(), search_pos + 1)


# Маркер, который мы ищем при восстановлении
BLOCK_PLACEHOLDER_RE = re.compile(r"\[\[\[BLOCK_(\d+)\]\]\]")


def _normalize_mermaid_block(block_content: str) -> str:
    """
    Нормализует Mermaid-блок, исправляя распространённые проблемы с форматированием.
    
    Args:
        block_content: Содержимое блока (с ```mermaid и ```)
        
    Returns:
        Нормализованный блок
    """
    # Извлекаем содержимое между ```mermaid и ```
    mermaid_match = re.search(
        r'```mermaid\s*\n(.*?)\n```',
        block_content,
        re.DOTALL | re.IGNORECASE
    )
    if not mermaid_match:
        return block_content

    code = mermaid_match.group(1)

    # Исправляем распространённые проблемы:
    # 1. Убираем лишние пробелы вокруг стрелок (E --> F -> E --> F)
    code = re.sub(r'(\w+)\s*-->\s*(\w+)(\[[^\]]+\])', r'\1 --> \2\3', code)
    code = re.sub(r'(\w+)\s*-->\s*\|([^|]+)\|\s*(\w+)', r'\1 -->|\2| \3', code)

    # 2. Убеждаемся, что каждая строка заканчивается корректно
    lines = code.split('\n')
    normalized_lines = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Если строка содержит стрелку без пробелов, добавляем пробелы
        if '-->' in line and not re.search(r'\s-->\s', line):
            line = re.sub(r'(\w+)(-->)(\w+)', r'\1 \2 \3', line)
        normalized_lines.append(line)

    code = '\n'.join(normalized_lines)

    # Восстанавливаем блок
    return f"```mermaid\n{code}\n```"


def _validate_block_content(block_content: str, block_type: str) -> bool:
    """
    Валидирует содержимое восстановленного блока.
    
    Args:
        block_content: Содержимое блока
        block_type: Тип блока ("mermaid", "code", "formula")
        
    Returns:
        True если блок валиден, False иначе
    """
    if not block_content or not block_content.strip():
        return False

    if block_type == "mermaid":
        # Проверяем, что это валидный mermaid-блок
        if not block_content.strip().startswith("```mermaid"):
            return False
        if not block_content.strip().endswith("```"):
            return False
        # Проверяем наличие базового синтаксиса mermaid
        content = block_content.replace("```mermaid", "").replace("```", "").strip()
        if not content:
            return False
        # Проверяем наличие ключевых слов mermaid
        mermaid_keywords = ["flowchart", "graph", "sequenceDiagram", "stateDiagram", "classDiagram"]
        if not any(keyword in content for keyword in mermaid_keywords):
            return False

    elif block_type == "formula":
        # Проверяем, что формула обёрнута в $$
        if not block_content.strip().startswith("$$"):
            return False
        if not block_content.strip().endswith("$$"):
            return False

    elif block_type == "code":
        # Проверяем, что это валидный code-блок
        if not block_content.strip().startswith("```"):
            return False
        if not block_content.strip().endswith("```"):
            return False

    elif block_type == "table":
        lines = [line.strip() for line in block_content.splitlines() if line.strip()]
        if len(lines) < 2:
            return False
        if not any(TABLE_SEPARATOR_RE.match(line) for line in lines[:3]):
            return False
        if not all("|" in line for line in lines):
            return False

    return True


def restore_blocks(md_with_markers: str, blocks: list[BlockInfo]) -> str:
    """
    Заменяет маркеры [[[BLOCK_i]]] на исходные блоки.

    ВАЖНО:
    - Если какой-то маркер был удалён агентом (например, по просьбе «удалить формулу»),
      соответствующий блок НЕ вставляется назад → считаем, что он удалён.
    - Удаляет HTML-комментарии PROTECTED_BLOCK после восстановления блоков.
    - Нормализует Mermaid-блоки для исправления проблем с форматированием.
    - Валидирует восстановленные блоки.

    Args:
        md_with_markers: Markdown текст с маркерами [[[BLOCK_N]]]
        blocks: Список BlockInfo с оригинальным содержимым блоков

    Returns:
        Markdown текст с восстановленными блоками
    """
    missing_blocks = []  # Для логирования отсутствующих блоков

    def repl(match: re.Match) -> str:
        try:
            idx = int(match.group(1))
            if 0 <= idx < len(blocks):
                block_info = blocks[idx]
                block_content = block_info.content

                # Валидируем блок перед восстановлением
                if not _validate_block_content(block_content, block_info.block_type):
                    # Если блок невалиден, логируем и оставляем маркер
                    missing_blocks.append((idx, block_info.block_type, "invalid_content"))
                    return match.group(0)

                # Нормализуем Mermaid-блоки для исправления проблем с форматированием
                if block_info.block_type == "mermaid":
                    block_content = _normalize_mermaid_block(block_content)
                    # Повторная валидация после нормализации
                    if not _validate_block_content(block_content, block_info.block_type):
                        missing_blocks.append((idx, block_info.block_type, "normalization_failed"))
                        return match.group(0)

                return block_content
            else:
                # Индекс вне диапазона
                missing_blocks.append((idx, "unknown", "index_out_of_range"))
                return match.group(0)
        except (ValueError, IndexError) as e:
            # Ошибка при парсинге индекса или доступе к блоку
            missing_blocks.append((match.group(1), "unknown", f"error: {str(e)}"))
            return match.group(0)

    # Заменяем маркеры на блоки, сохраняя контекст вокруг них
    restored = BLOCK_PLACEHOLDER_RE.sub(repl, md_with_markers)

    # Логируем проблемы с восстановлением (если есть)
    if missing_blocks:
        import logging
        logger = logging.getLogger(__name__)
        for idx, block_type, reason in missing_blocks:
            logger.warning(
                f"Не удалось восстановить блок {idx} (тип: {block_type}, причина: {reason})"
            )

    # Удаляем оставшиеся HTML-комментарии PROTECTED_BLOCK (они больше не нужны)
    # Это может произойти, если LLM удалил маркер, но оставил комментарий
    # Удаляем только те комментарии, которые стоят отдельно (не перед восстановленными блоками)
    restored = re.sub(
        r'<!--\s*PROTECTED_BLOCK\s+id=\d+\s+type=\w+\s+preview="[^"]*"\s*-->\s*\n?(?!```)',
        '',
        restored,
        flags=re.IGNORECASE | re.MULTILINE
    )

    # Удаляем оставшиеся маркеры, которые не были восстановлены (на случай, если их формат был изменён)
    restored = re.sub(r'\[\[\[BLOCK_\d+\]\]\]\s*\n?', '', restored)

    # Нормализуем множественные пустые строки (более 2 подряд)
    restored = re.sub(r'\n{3,}', '\n\n', restored)

    return restored
