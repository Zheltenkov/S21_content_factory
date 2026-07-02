"""
InputAgent - нормализация README.md.

Удаляет HTML теги, нормализует markdown, извлекает структуру документа.
"""

import re
from typing import Any

from ..models import NormalizedReadme


class InputAgent:
    """Агент для нормализации README и извлечения структуры."""

    def normalize(self, readme_text: str) -> NormalizedReadme:
        """
        Нормализует README текст и извлекает структуру.
        
        Args:
            readme_text: Исходный текст README
            
        Returns:
            NormalizedReadme с очищенным текстом и структурой
        """
        # Удаление HTML тегов
        text = self._remove_html_tags(readme_text)

        # Нормализация markdown
        text = self._normalize_markdown(text)

        # Извлечение структуры
        structure = self._extract_structure(text)

        # Извлечение глав
        chapters = self._extract_chapters(text)

        return NormalizedReadme(
            raw_text=text,
            structure=structure,
            chapters=chapters
        )

    def _remove_html_tags(self, text: str) -> str:
        """Удаляет HTML теги из текста."""
        # Удаляем HTML теги, но сохраняем содержимое
        text = re.sub(r'<[^>]+>', '', text)
        return text

    def _normalize_markdown(self, text: str) -> str:
        """Нормализует markdown разметку."""
        # Удаляем лишние пробелы
        text = re.sub(r'\n{3,}', '\n\n', text)
        # Нормализуем пробелы в начале строк
        lines = text.split('\n')
        normalized_lines = []
        for line in lines:
            # Сохраняем отступы для списков и кода
            if line.strip().startswith(('-', '*', '+', '1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.')):
                normalized_lines.append(line)
            elif line.strip().startswith('#'):
                normalized_lines.append(line.strip())
            else:
                normalized_lines.append(line.rstrip())
        return '\n'.join(normalized_lines)

    def _extract_structure(self, text: str) -> dict[str, Any]:
        """Извлекает структуру документа (заголовки, списки)."""
        structure = {
            "headings": [],
            "sections": [],
            "lists": []
        }

        lines = text.split('\n')
        current_section = None

        for i, line in enumerate(lines):
            # Заголовки
            if line.strip().startswith('#'):
                level = len(line) - len(line.lstrip('#'))
                title = line.lstrip('#').strip()
                heading = {
                    "level": level,
                    "title": title,
                    "line": i + 1
                }
                structure["headings"].append(heading)

                # Начало новой секции
                if level <= 2:  # h1 или h2
                    if current_section:
                        structure["sections"].append(current_section)
                    current_section = {
                        "title": title,
                        "level": level,
                        "start_line": i + 1,
                        "content": []
                    }

            # Списки
            if line.strip().startswith(('-', '*', '+', '1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.')):
                if current_section:
                    current_section["content"].append(line.strip())

        # Добавляем последнюю секцию
        if current_section:
            structure["sections"].append(current_section)

        return structure

    def _extract_chapters(self, text: str) -> dict[int, str]:
        """Извлекает содержимое глав по номерам."""
        chapters = {}

        # Паттерн для глав: "## Глава 1", "## Глава 2", "## Chapter 1", etc.
        chapter_pattern = re.compile(
            r'^##\s+(?:Глава|Chapter|Chapter\s+\d+|Глава\s+\d+)\s*(\d+)',
            re.IGNORECASE | re.MULTILINE
        )

        # Разбиваем текст по главам
        parts = re.split(r'^##\s+(?:Глава|Chapter)\s*(\d+)', text, flags=re.IGNORECASE | re.MULTILINE)

        # Первая часть - до первой главы
        if len(parts) > 1:
            # parts[0] - текст до первой главы
            # parts[1], parts[2] - номер главы и её содержимое
            # parts[3], parts[4] - следующая глава и т.д.
            for i in range(1, len(parts), 2):
                if i + 1 < len(parts):
                    chapter_num = int(parts[i])
                    chapter_content = parts[i + 1].strip()
                    chapters[chapter_num] = chapter_content

        # Если не найдено глав по паттерну, ищем по структуре заголовков
        if not chapters:
            lines = text.split('\n')
            current_chapter = None
            current_content = []

            for line in lines:
                # Ищем заголовки вида "## Глава 1" или просто "## 1"
                match = re.match(r'^##\s+(?:Глава\s*)?(\d+)', line, re.IGNORECASE)
                if match:
                    if current_chapter is not None:
                        chapters[current_chapter] = '\n'.join(current_content).strip()
                    current_chapter = int(match.group(1))
                    current_content = []
                elif current_chapter is not None:
                    current_content.append(line)

            # Добавляем последнюю главу
            if current_chapter is not None:
                chapters[current_chapter] = '\n'.join(current_content).strip()

        return chapters

