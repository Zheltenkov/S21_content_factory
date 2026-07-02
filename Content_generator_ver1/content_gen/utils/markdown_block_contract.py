"""Contract for safe markdown editing around fenced blocks and formulas."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from .markdown_display_normalizer import normalize_markdown_display_blocks
from .protected_blocks import BlockInfo, protect_blocks, restore_blocks

logger = logging.getLogger("content_gen.utils.markdown_block_contract")

MarkdownEditFn = Callable[[str], str]


class MarkdownBlockContract:
    """Protect fenced blocks before LLM editing and validate them after restore."""

    def protect(
        self,
        markdown: str,
        *,
        protect_code: bool = True,
        protect_mermaid: bool = True,
        protect_formulas: bool = True,
        protect_tables: bool = True,
    ) -> tuple[str, list[BlockInfo]]:
        """Normalize and hide fenced code, mermaid and block formulas from editors."""
        normalized = normalize_markdown_display_blocks(markdown or "")
        return protect_blocks(
            normalized,
            protect_code=protect_code,
            protect_mermaid=protect_mermaid,
            protect_formulas=protect_formulas,
            protect_tables=protect_tables,
        )

    def restore(self, markdown: str, blocks: list[BlockInfo]) -> str:
        """Restore protected blocks and run display-block normalization."""
        restored = restore_blocks(markdown or "", blocks)
        return normalize_markdown_display_blocks(restored)

    def edit(
        self,
        markdown: str,
        edit_fn: MarkdownEditFn,
        *,
        min_chars: int = 1,
        fallback_original: bool = True,
    ) -> str:
        """Run a safe edit cycle and optionally fall back to the original markdown."""
        original = normalize_markdown_display_blocks(markdown or "")
        protected, blocks = self.protect(original)
        try:
            edited = (edit_fn(protected) or "").strip()
            restored = self.restore(edited, blocks)
            issues = self.validate(restored)
            if len(restored.strip()) < min_chars:
                issues.append("edited markdown is shorter than min_chars")
            if issues:
                logger.warning("MarkdownBlockContract validation issues: %s", issues)
                return original if fallback_original else restored
            return restored
        except Exception as exc:  # noqa: BLE001
            logger.warning("MarkdownBlockContract edit failed: %s", exc)
            if fallback_original:
                return original
            raise

    @staticmethod
    def protection_instruction(
        blocks: list[BlockInfo],
        *,
        allow_display_block_edit: bool = False,
    ) -> str:
        """Instruction appended to editor prompts when placeholders are present."""
        if not blocks:
            return ""
        if allow_display_block_edit:
            return (
                "\n\nВАЖНО: маркеры вида [[[BLOCK_0]]] и комментарии PROTECTED_BLOCK "
                "обозначают защищённые кодовые блоки или формулы. "
                "Сохрани эти маркеры без изменений. Mermaid-диаграммы и Markdown-таблицы "
                "можно редактировать только если это прямо требуется инструкцией."
            )
        return (
            "\n\nВАЖНО: маркеры вида [[[BLOCK_0]]] и комментарии PROTECTED_BLOCK "
            "обозначают защищённые таблицы, диаграммы, формулы или код. "
            "Сохрани эти маркеры без изменений; реальные fenced blocks редактировать нельзя."
        )

    @staticmethod
    def validate(markdown: str) -> list[str]:
        """Detect unresolved placeholders and malformed fenced-block boundaries."""
        issues: list[str] = []
        text = markdown or ""
        if re.search(r"\[\[\[BLOCK_\d+\]\]\]", text):
            issues.append("unresolved protected block placeholder")
        if re.search(r"<!--\s*PROTECTED_BLOCK\b", text, flags=re.I):
            issues.append("unresolved protected block comment")

        fence_count = len(re.findall(r"^```", text, flags=re.M))
        if fence_count % 2:
            issues.append("unbalanced fenced code blocks")

        flattened_mermaid = re.search(
            r"^\s*(?:flowchart|graph)[ \t]+\w+[ \t]+[A-Za-z0-9_]+\[",
            text,
            flags=re.I | re.M,
        )
        if flattened_mermaid:
            issues.append("possible flattened mermaid block")
        return issues
