"""
content_gen/renderers/toc.py

Renderer оглавления.

Извлекает заголовки из markdown и формирует TOC (Table of Contents).
Вставляет оглавление после аннотации в структуре документа.
"""

import re
from dataclasses import dataclass

from ..models.readme_document import ReadmeDocument, ReadmeSection


@dataclass
class TOCResult:
    """Результат генерации оглавления."""

    toc_md: str


class TOCRenderer:
    """Генерирует оглавление по фактическим заголовкам без LLM-вызовов."""

    def build(self, md: str, language: str = "ru") -> TOCResult:
        """
        Строит оглавление из заголовков H2/H3.

        Args:
            md: Markdown документ
            language: Язык проекта

        Returns:
            TOCResult с оглавлением
        """
        lines = md.splitlines()
        items: list[str] = []
        for ln in lines:
            if ln.startswith("## "):
                title = ln[3:].strip()
                if "содержание" in title.lower() or "content" in title.lower() or "мазмун" in title.lower():
                    continue
                anchor = (
                    "#"
                    + re.sub(r"[^\w\- ]+", "", title, flags=re.U).strip().lower().replace(" ", "-")
                )
                items.append(f"- [{title}]({anchor})")
            elif ln.startswith("### "):
                title = ln[4:].strip()
                anchor = (
                    "#"
                    + re.sub(r"[^\w\- ]+", "", title, flags=re.U).strip().lower().replace(" ", "-")
                )
                items.append(f"  - [{title}]({anchor})")
        toc = "\n".join(items) if items else "- (появится после добавления разделов)"
        return TOCResult(toc_md=toc)

    def build_document(self, document: ReadmeDocument, language: str = "ru") -> TOCResult:
        """Build TOC from a typed README tree without reparsing Markdown."""
        items: list[str] = []
        for section in document.sections:
            for item in section.flatten():
                if item.level not in {2, 3}:
                    continue
                title = item.title.strip()
                if self._is_toc_title(title):
                    continue
                indent = "  " if item.level == 3 else ""
                items.append(f"{indent}- [{title}]({self._anchor(title)})")
        toc = "\n".join(items) if items else "- (появится после добавления разделов)"
        return TOCResult(toc_md=toc)

    def inject(self, md: str, toc_md: str) -> str:
        """
        Вставляет оглавление вместо плейсхолдера.

        Args:
            md: Исходный Markdown
            toc_md: Сгенерированное оглавление

        Returns:
            Обновлённый Markdown
        """
        import sys
        md_before = md
        # Более точное регулярное выражение - не жадное, останавливается на следующей строке
        result = re.sub(
            r"(##\s+(Содержание|Content|Мазмун)\s*\n)\s*<!-- TOC_PLACEHOLDER -->\s*\n",
            r"\1" + toc_md + "\n\n",
            md,
            flags=re.MULTILINE,  # Используем MULTILINE вместо DOTALL, чтобы . не захватывал \n
        )
        if len(result) < len(md_before):
            print(f"  ⚠️  TOC.inject: markdown стал короче! Было: {len(md_before)}, Стало: {len(result)}", file=sys.stderr, flush=True)
            print("     Паттерн: (##\\s+(Содержание|Content|Мазмун)\\s*\\n)\\s*<!-- TOC_PLACEHOLDER -->\\s*\\n", file=sys.stderr, flush=True)
        return result

    def inject_document(
        self,
        document: ReadmeDocument,
        toc_md: str,
        language: str = "ru",
    ) -> ReadmeDocument:
        """Insert or replace the TOC section in a typed README document."""
        result = document.model_copy(deep=True)
        toc_section = ReadmeSection(
            title=self._toc_title(language),
            level=2,
            body=(toc_md or "").strip(),
            metadata={"kind": "toc"},
        )
        for index, section in enumerate(result.sections):
            if self._is_toc_title(section.title):
                result.sections[index] = toc_section
                return result
        result.sections.insert(0, toc_section)
        return result

    @staticmethod
    def _anchor(title: str) -> str:
        """Return the GitHub-style anchor format used by Markdown TOC links."""
        return "#" + re.sub(r"[^\w\- ]+", "", title, flags=re.U).strip().lower().replace(" ", "-")

    @staticmethod
    def _is_toc_title(title: str) -> bool:
        """Detect localized TOC section headings."""
        normalized = (title or "").casefold()
        return "содержание" in normalized or "content" in normalized or "мазмун" in normalized

    @staticmethod
    def _toc_title(language: str) -> str:
        """Return localized TOC heading for the current document language."""
        language = (language or "ru").casefold()
        if language == "en":
            return "Content"
        if language in {"ky", "kg"}:
            return "Мазмун"
        return "Содержание"


# Backward-compatible alias for older imports. New code should use TOCRenderer.
TOCAgent = TOCRenderer
