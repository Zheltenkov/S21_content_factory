"""
content_gen/agents/content_editor.py

Агент-редактор для устранения дублирования контента и улучшения связности теории.

Проверяет и устраняет:
1. Дублирование между таблицами и диаграммами
2. Слабую связность между разделами (низкий cosine similarity)
3. Отсутствие мостиков между частями теории
4. Несогласованность терминологии
5. Отсутствие логических переходов
"""

import re

from ..config.loader import get_agent_config, prompt_trace_kwargs
from .base.llm_client import LLMClientProtocol
from ..models.readme_document import ReadmeDocument, ReadmeSection
from ..models.schemas import ProjectSeed, TheoryPart
from ..utils.markdown_block_contract import MarkdownBlockContract
from ..utils.markdown_display_normalizer import normalize_markdown_display_blocks
from ..utils.logging import safe_print

SYSTEM = ""
EDIT_TMPL = ""
COHERENCE_TMPL = ""


class ContentEditorAgent:
    """
    Агент для редактирования контента теории.
    
    Разделен на два режима:
    - edit_theory_parts: локальная чистка (дубли, терминология, мостики между частями)
    - ensure_global_coherence: SBERT-когерентность и переходы между главами 1-3
    """

    CONFIG_NAME = "content_editor"

    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        self.block_contract = MarkdownBlockContract()

    def _has_table(self, text: str) -> bool:
        """Проверяет наличие таблицы в тексте."""
        # Проверяем Markdown таблицы
        table_pattern = r'\|[^\n]+\|\s*\n\|[-\s:]+\|\s*\n(\|[^\n]+\|\s*\n)+'
        return bool(re.search(table_pattern, text))

    def _has_mermaid_diagram(self, text: str) -> bool:
        """Проверяет наличие Mermaid диаграммы в тексте."""
        mermaid_pattern = r'```mermaid[\s\S]*?```'
        return bool(re.search(mermaid_pattern, text))

    def _has_duplication(self, text: str) -> bool:
        """Проверяет наличие дублирования между таблицами и диаграммами."""
        has_table = self._has_table(text)
        has_diagram = self._has_mermaid_diagram(text)

        # Если есть и таблица, и диаграмма, возможно дублирование
        if has_table and has_diagram:
            # Проверяем, описывают ли они одну и ту же концепцию
            # Простая эвристика: если в тексте упоминаются одни и те же ключевые слова
            # около таблицы и диаграммы, возможно дублирование
            return True

        return False

    def edit_part(self, part: TheoryPart, seed: ProjectSeed) -> TheoryPart:
        """
        Редактирует часть теории, устраняя дублирование.
        
        Args:
            part: Часть теории для редактирования
            seed: Входные данные проекта
        
        Returns:
            Отредактированная часть теории
        """
        normalized_body = normalize_markdown_display_blocks(part.body)
        part = TheoryPart(
            title=part.title,
            body=normalized_body,
            example=part.example,
            bridge_questions=part.bridge_questions,
            covers_outcomes=part.covers_outcomes,
            references=part.references.copy() if part.references else [],
        )

        # Проверяем, есть ли дублирование
        if not self._has_duplication(part.body):
            return part

        safe_print(f"  ✏️ Редактирование части '{part.title[:50]}...' для устранения дублирования", flush=True)

        try:
            system_prompt = self.config.get_prompt("system").format(language=seed.language)
            protected_body, blocks = self.block_contract.protect(part.body)
            user_prompt = self.config.get_prompt("edit_template").format(
                title=part.title,
                body=protected_body
            )
            user_prompt += self.block_contract.protection_instruction(blocks)

            llm_kwargs = self.llm_kwargs.copy()
            llm_kwargs.setdefault("temperature", 0.1)
            llm_kwargs.update(prompt_trace_kwargs(self.config, "system", "edit_template", output_schema="TheoryPart.body"))
            edited_body = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                **llm_kwargs,
            )

            edited_body = self.block_contract.restore(edited_body.strip(), blocks)
            contract_issues = self.block_contract.validate(edited_body)
            if contract_issues:
                safe_print(f"  ⚠️ Редактирование нарушило MarkdownBlockContract: {contract_issues}", flush=True)
                return part

            # Проверяем, что результат не пустой
            if not edited_body or len(edited_body) < 50:
                safe_print("  ⚠️ Редактирование вернуло слишком короткий текст, оставляем оригинал", flush=True)
                return part

            safe_print("  ✅ Дублирование устранено", flush=True)

            return TheoryPart(
                title=part.title,
                body=edited_body,
                example=part.example,
                bridge_questions=part.bridge_questions,
                covers_outcomes=part.covers_outcomes,
                references=part.references.copy() if part.references else []
            )

        except Exception as e:
            import traceback
            safe_print(f"  ⚠️ Ошибка при редактировании: {str(e)}", flush=True)
            safe_print(f"     Детали: {traceback.format_exc()[:500]}", flush=True)
            return part

    def improve_coherence(self, part1: TheoryPart, part2: TheoryPart, seed: ProjectSeed, previous_parts: list[TheoryPart] = None) -> TheoryPart:
        """
        Улучшает связность между двумя частями теории.
        
        Args:
            part1: Предыдущая часть теории
            part2: Текущая часть теории (будет улучшена)
            seed: Входные данные проекта
            previous_parts: Все предыдущие части (для контекста)
        
        Returns:
            Улучшенная часть 2 с мостиками и связностью
        """
        safe_print(f"  🔗 Улучшение связности между '{part1.title[:30]}...' и '{part2.title[:30]}...'", flush=True)

        try:
            # Формируем контекст предыдущих частей
            previous_context = ""
            if previous_parts:
                prev_titles = [p.title for p in previous_parts[-2:]]  # Последние 2 части
                if prev_titles:
                    previous_context = "\n**Контекст предыдущих частей:**\n" + "\n".join(f"- {t}" for t in prev_titles)

            part1_body = normalize_markdown_display_blocks(part1.body)
            part2_body, blocks = self.block_contract.protect(normalize_markdown_display_blocks(part2.body))
            system_prompt = self.config.get_prompt("system").format(language=seed.language)
            user_prompt = self.config.get_prompt("coherence_template").format(
                part1_title=part1.title,
                part1_body=part1_body[:1000],  # Первые 1000 символов для контекста
                part2_title=part2.title,
                part2_body=part2_body,
                previous_context=previous_context
            )
            user_prompt += self.block_contract.protection_instruction(blocks)

            llm_kwargs = self.llm_kwargs.copy()
            llm_kwargs.setdefault("temperature", 0.2)
            llm_kwargs.update(
                prompt_trace_kwargs(self.config, "system", "coherence_template", output_schema="TheoryPart.body")
            )
            improved_body = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                **llm_kwargs,
            )

            improved_body = self.block_contract.restore(improved_body.strip(), blocks)
            contract_issues = self.block_contract.validate(improved_body)
            if contract_issues:
                safe_print(f"  ⚠️ Улучшение связности нарушило MarkdownBlockContract: {contract_issues}", flush=True)
                return part2

            # Проверяем, что результат не пустой
            if not improved_body or len(improved_body) < 50:
                safe_print("  ⚠️ Улучшение связности вернуло слишком короткий текст, оставляем оригинал", flush=True)
                return part2

            safe_print("  ✅ Связность улучшена", flush=True)

            return TheoryPart(
                title=part2.title,
                body=improved_body,
                example=part2.example,
                bridge_questions=part2.bridge_questions,
                covers_outcomes=part2.covers_outcomes,
                references=part2.references.copy() if part2.references else []
            )

        except Exception as e:
            import traceback
            safe_print(f"  ⚠️ Ошибка при улучшении связности: {str(e)}", flush=True)
            safe_print(f"     Детали: {traceback.format_exc()[:500]}", flush=True)
            return part2

    def edit_theory_parts(self, parts: list[TheoryPart], seed: ProjectSeed) -> list[TheoryPart]:
        """
        Локальная чистка частей теории: дубли, терминология, мостики между частями.
        
        Режим 1: Редактирование отдельных частей (Фаза 2 - после генерации теории).
        
        Args:
            parts: Список частей теории
            seed: Входные данные проекта
        
        Returns:
            Список отредактированных частей теории
        """
        return self.edit_parts(parts, seed)

    def edit_parts(self, parts: list[TheoryPart], seed: ProjectSeed) -> list[TheoryPart]:
        """
        Редактирует все части теории, устраняя дублирование и улучшая связность.
        
        Args:
            parts: Список частей теории
            seed: Входные данные проекта
        
        Returns:
            Список отредактированных частей теории
        """
        if not parts:
            return []

        safe_print(f"  ✏️ Редактирование {len(parts)} частей теории...", flush=True)
        safe_print("     - Устранение дублирования между таблицами и диаграммами", flush=True)
        safe_print("     - Улучшение связности между частями (мостики, терминология, переходы)", flush=True)

        # Шаг 1: Устраняем дублирование в каждой части
        edited_parts = []
        for i, part in enumerate(parts, 1):
            safe_print(f"  ✏️ Редактирование части {i}/{len(parts)}: {part.title[:50]}...", flush=True)
            edited_part = self.edit_part(part, seed)
            edited_parts.append(edited_part)

        # Шаг 2: Улучшаем связность между частями
        if len(edited_parts) > 1:
            safe_print("  🔗 Улучшение связности между частями...", flush=True)
            improved_parts = [edited_parts[0]]  # Первая часть остается без изменений

            for i in range(1, len(edited_parts)):
                previous_parts = edited_parts[:i]
                improved_part = self.improve_coherence(
                    part1=edited_parts[i-1],
                    part2=edited_parts[i],
                    seed=seed,
                    previous_parts=previous_parts
                )
                improved_parts.append(improved_part)

            edited_parts = improved_parts

        safe_print("  ✅ Редактирование завершено", flush=True)

        return edited_parts

    def ensure_global_coherence(self, md: str, seed: ProjectSeed) -> str:
        """
        SBERT-когерентность и переходы между главами 1-3.
        
        Режим 2: Глобальная связность (Фаза 4 - после сборки всех глав).
        
        Args:
            md: Полный Markdown документ
            seed: Входные данные проекта
        
        Returns:
            Улучшенный Markdown с мостиками между главами
        """
        return self.improve_chapter_coherence(md, seed)

    def ensure_global_coherence_document(
        self,
        document: ReadmeDocument,
        seed: ProjectSeed,
    ) -> ReadmeDocument:
        """Improve chapter bridges on a typed README document."""
        return self.improve_chapter_coherence_document(document, seed)

    def improve_chapter_coherence_document(
        self,
        document: ReadmeDocument,
        seed: ProjectSeed,
    ) -> ReadmeDocument:
        """Add chapter bridges using typed chapter sections instead of regex slices."""
        result = document.model_copy(deep=True)
        chapters = {
            1: result.chapter_section(1, language=seed.language),
            2: result.chapter_section(2, language=seed.language),
            3: result.chapter_section(3, language=seed.language),
        }
        if not all(chapters.values()):
            safe_print("  ⚠️ Не удалось извлечь все главы для улучшения связности", flush=True)
            return document

        names = self._chapter_names(seed.language)
        transitions = ((1, 2, names["ch1"], names["ch2"]), (2, 3, names["ch2"], names["ch3"]))
        for previous_number, current_number, previous_name, current_name in transitions:
            previous_section = chapters[previous_number]
            current_section = chapters[current_number]
            if previous_section is None or current_section is None:
                continue
            previous_content = self._section_content(previous_section)
            current_content = self._section_content(current_section)
            safe_print(f"  🔗 Улучшение связности: {previous_name} → {current_name}", flush=True)
            improved_body = self._add_chapter_bridge(
                previous_chapter=previous_name,
                previous_content=previous_content[-800:] if len(previous_content) > 800 else previous_content,
                current_chapter=current_name,
                current_content=current_content,
                seed=seed,
            )
            if not improved_body:
                continue
            improved_body = self._strip_leading_markdown_heading(improved_body)
            improved_body = normalize_markdown_display_blocks(improved_body)
            result, replaced = result.with_replaced_chapter_body(
                current_number,
                improved_body,
                language=seed.language,
            )
            if replaced:
                chapters[current_number] = result.chapter_section(current_number, language=seed.language)
        return result

    @staticmethod
    def _chapter_names(language: str) -> dict[str, str]:
        """Return localized chapter names used in bridge prompts."""
        chapter_names = {
            "ru": {
                "ch1": "Глава 1. Введение и инструкция",
                "ch2": "Глава 2. Теория",
                "ch3": "Глава 3. Практика",
            },
            "en": {
                "ch1": "Chapter 1. Introduction & Guidelines",
                "ch2": "Chapter 2. Theory",
                "ch3": "Chapter 3. Practice",
            },
            "kg": {
                "ch1": "1-Бөлүм. Киришүү жана эрежелер",
                "ch2": "2-Бөлүм. Теория",
                "ch3": "3-Бөлүм. Практика",
            },
            "ky": {
                "ch1": "1-Бөлүм. Киришүү жана эрежелер",
                "ch2": "2-Бөлүм. Теория",
                "ch3": "3-Бөлүм. Практика",
            },
        }
        return chapter_names.get((language or "ru").casefold(), chapter_names["ru"])

    @staticmethod
    def _section_content(section: ReadmeSection) -> str:
        """Render a section body and descendants without the section heading."""
        blocks: list[str] = []
        body = (section.body or "").strip()
        if body:
            blocks.append(body)
        for child in section.children:
            rendered = child.to_markdown().strip()
            if rendered:
                blocks.append(rendered)
        return "\n\n".join(blocks).strip()

    @staticmethod
    def _strip_leading_markdown_heading(text: str) -> str:
        """Drop an accidental leading heading from model output before replacing chapter body."""
        cleaned = (text or "").lstrip()
        if cleaned.startswith("##"):
            lines = cleaned.split("\n")
            if lines and lines[0].startswith("##"):
                return "\n".join(lines[1:]).lstrip()
        return cleaned

    def improve_chapter_coherence(self, md: str, seed: ProjectSeed) -> str:
        """
        Улучшает связность между главами (Глава 1, Глава 2, Глава 3).
        
        Args:
            md: Полный Markdown документ
            seed: Входные данные проекта
        
        Returns:
            Улучшенный Markdown с мостиками между главами
        """
        import re

        # Извлекаем главы (поддерживаем разные языки)
        lang_patterns = {
            "ru": {
                "ch1": r"(##\s+Глава\s+1[^\n]*\n)(.*?)(?=\n##\s+Глава\s+2|\Z)",
                "ch2": r"(##\s+Глава\s+2[^\n]*\n)(.*?)(?=\n##\s+Глава\s+3|\Z)",
                "ch3": r"(##\s+Глава\s+3[^\n]*\n)(.*?)(?=\n##\s+Бонус|\Z)",
            },
            "en": {
                "ch1": r"(##\s+Chapter\s+1[^\n]*\n)(.*?)(?=\n##\s+Chapter\s+2|\Z)",
                "ch2": r"(##\s+Chapter\s+2[^\n]*\n)(.*?)(?=\n##\s+Chapter\s+3|\Z)",
                "ch3": r"(##\s+Chapter\s+3[^\n]*\n)(.*?)(?=\n##\s+Bonus|\Z)",
            },
            "kg": {
                "ch1": r"(##\s+1-Бөлүм[^\n]*\n)(.*?)(?=\n##\s+2-Бөлүм|\Z)",
                "ch2": r"(##\s+2-Бөлүм[^\n]*\n)(.*?)(?=\n##\s+3-Бөлүм|\Z)",
                "ch3": r"(##\s+3-Бөлүм[^\n]*\n)(.*?)(?=\n##\s+Бонус|\Z)",
            },
        }

        patterns = lang_patterns.get(seed.language, lang_patterns["ru"])
        ch1_match = re.search(patterns["ch1"], md, flags=re.S | re.IGNORECASE)
        ch2_match = re.search(patterns["ch2"], md, flags=re.S | re.IGNORECASE)
        ch3_match = re.search(patterns["ch3"], md, flags=re.S | re.IGNORECASE)

        if not (ch1_match and ch2_match and ch3_match):
            safe_print("  ⚠️ Не удалось извлечь все главы для улучшения связности", flush=True)
            return md

        ch1_content = ch1_match.group(2) if ch1_match else ""
        ch2_content = ch2_match.group(2) if ch2_match else ""
        ch3_content = ch3_match.group(2) if ch3_match else ""

        # Определяем названия глав в зависимости от языка
        chapter_names = {
            "ru": {
                "ch1": "Глава 1. Введение и инструкция",
                "ch2": "Глава 2. Теория",
                "ch3": "Глава 3. Практика",
            },
            "en": {
                "ch1": "Chapter 1. Introduction & Guidelines",
                "ch2": "Chapter 2. Theory",
                "ch3": "Chapter 3. Practice",
            },
            "kg": {
                "ch1": "1-Бөлүм. Киришүү жана эрежелер",
                "ch2": "2-Бөлүм. Теория",
                "ch3": "3-Бөлүм. Практика",
            },
        }
        names = chapter_names.get(seed.language, chapter_names["ru"])

        # Улучшаем переход от Главы 1 к Главе 2
        if ch1_match and ch2_match:
            safe_print(f"  🔗 Улучшение связности: {names['ch1']} → {names['ch2']}", flush=True)
            improved_ch2 = self._add_chapter_bridge(
                previous_chapter=names["ch1"],
                previous_content=ch1_content[-800:] if len(ch1_content) > 800 else ch1_content,  # Последние 800 символов для контекста
                current_chapter=names["ch2"],
                current_content=ch2_content,
                seed=seed
            )
            if improved_ch2:
                # Убеждаемся, что improved_ch2 не начинается с заголовка
                improved_ch2 = improved_ch2.lstrip()
                if improved_ch2.startswith('##'):
                    # Удаляем заголовок, если он есть
                    lines = improved_ch2.split('\n')
                    if lines[0].startswith('##'):
                        improved_ch2 = '\n'.join(lines[1:]).lstrip()
                improved_ch2 = normalize_markdown_display_blocks(improved_ch2)
                md = md.replace(ch2_match.group(0), ch2_match.group(1) + improved_ch2)

        # Улучшаем переход от Главы 2 к Главе 3
        if ch2_match and ch3_match:
            safe_print(f"  🔗 Улучшение связности: {names['ch2']} → {names['ch3']}", flush=True)
            improved_ch3 = self._add_chapter_bridge(
                previous_chapter=names["ch2"],
                previous_content=ch2_content[-800:] if len(ch2_content) > 800 else ch2_content,  # Последние 800 символов для контекста
                current_chapter=names["ch3"],
                current_content=ch3_content,
                seed=seed
            )
            if improved_ch3:
                # Убеждаемся, что improved_ch3 не начинается с заголовка
                improved_ch3 = improved_ch3.lstrip()
                if improved_ch3.startswith('##'):
                    # Удаляем заголовок, если он есть
                    lines = improved_ch3.split('\n')
                    if lines[0].startswith('##'):
                        improved_ch3 = '\n'.join(lines[1:]).lstrip()
                improved_ch3 = normalize_markdown_display_blocks(improved_ch3)
                md = md.replace(ch3_match.group(0), ch3_match.group(1) + improved_ch3)

        # Финальная проверка: удаляем дублирующиеся заголовки глав
        md = self._remove_duplicate_chapter_headers(md, seed.language)
        md = normalize_markdown_display_blocks(md)

        return md

    def _remove_duplicate_chapter_headers(self, md: str, language: str) -> str:
        """
        Удаляет дублирующиеся заголовки глав (например, два "## Глава 2. Теория" подряд).
        
        Args:
            md: Markdown текст
            language: Язык документа
            
        Returns:
            Очищенный Markdown текст
        """
        import re

        # Паттерны для заголовков глав в зависимости от языка
        chapter_header_patterns = {
            "ru": r'^##\s+(Глава\s+\d+[^\n]+)$',
            "en": r'^##\s+(Chapter\s+\d+[^\n]+)$',
            "kg": r'^##\s+(\d+-Бөлүм[^\n]+)$',
        }

        pattern = chapter_header_patterns.get(language, chapter_header_patterns["ru"])
        lines = md.split('\n')
        cleaned_lines = []
        prev_header = None

        for line in lines:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                current_header = match.group(1).strip()
                # Если это тот же заголовок, что и предыдущий, пропускаем его
                if prev_header and prev_header.lower() == current_header.lower():
                    continue
                prev_header = current_header
            else:
                # Сбрасываем prev_header, если это не заголовок главы
                prev_header = None
            cleaned_lines.append(line)

        return '\n'.join(cleaned_lines)

    def _add_chapter_bridge(self, previous_chapter: str, previous_content: str, current_chapter: str, current_content: str, seed: ProjectSeed) -> str | None:
        """
        Добавляет мостик между главами.
        
        Args:
            previous_chapter: Название предыдущей главы
            previous_content: Содержимое предыдущей главы (для контекста)
            current_chapter: Название текущей главы
            current_content: Содержимое текущей главы
            seed: Входные данные проекта
        
        Returns:
            Улучшенное содержимое текущей главы с мостиком
        """
        try:
            system_prompt = self.config.get_prompt("system").format(language=seed.language)
            user_prompt = f"""Нужен небольшой мостик между главами (1-2 предложения).

**Предыдущая глава: {previous_chapter}**
{previous_content[:800]}

**Начало текущей главы ({current_chapter}):**
{current_content[:400]}

ТРЕБОВАНИЯ:
- Верни ТОЛЬКО мостик (1-2 предложения), без заголовков и списков.
- Не используй клише и канцеляризмы.
- Упомяни ключевые термины из предыдущей главы и покажи, как они переходят в текущую.
- Не повторяй содержимое главы 3, не вставляй подзаголовки.
"""
            llm_kwargs = self.llm_kwargs.copy()
            llm_kwargs.setdefault("temperature", 0.2)
            llm_kwargs.update(prompt_trace_kwargs(self.config, "system", output_schema="chapter_bridge"))
            bridge_text = self.llm.complete(
                system=system_prompt,
                user=user_prompt,
                **llm_kwargs,
            ).strip()

            bridge_text = re.sub(r'^\s*(Текущая|Current)[^\n]*\n+', '', bridge_text, flags=re.I)
            bridge_text = re.sub(r'^##\s+[^\n]+\n+', '', bridge_text, flags=re.M)
            bridge_text = re.sub(r'^\s*[#\-\*]+\s*', '', bridge_text)
            bridge_text = bridge_text.strip()

            if not bridge_text:
                return None

            current_body = current_content.lstrip()
            combined = f"{bridge_text}\n\n{current_body}"
            safe_print("  ✅ Мостик добавлен", flush=True)
            return combined

        except Exception as e:
            import traceback
            safe_print(f"  ⚠️ Ошибка при добавлении мостика: {str(e)}", flush=True)
            safe_print(f"     Детали: {traceback.format_exc()[:500]}", flush=True)
            return None
