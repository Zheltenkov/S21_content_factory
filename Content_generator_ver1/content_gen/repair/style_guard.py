"""
content_gen/repair/style_guard.py

Deterministic style lint and repair.

Проверяет markdown на соответствие Tone of Voice:
- Обращение на «ты»
- Отсутствие директив («нажми/кликни»)
- Отсутствие оценочных ярлыков
- Автоматически перефразирует нарушения
"""

import re
from dataclasses import dataclass

from ..config.banned_phrases import BAD_GOAL_PATTERNS, BANNED_BY_LANG
from ..config.loader import get_agent_config
from ..models.readme_document import ReadmeDocument


@dataclass
class LintIssue:
    """Проблема стиля."""

    kind: str
    span: tuple[int, int]
    excerpt: str
    suggestion: str | None = None


class StyleGuardRepair:
    """Проверяет стиль и автоматически перефразирует нарушения без LLM-вызовов."""

    CONFIG_NAME = "style_guard"

    def __init__(self):
        self.config = get_agent_config(self.CONFIG_NAME)
        options = self.config.options or {}
        self.enforce_directives = options.get("enforce_directives", True)
        self.enforce_cta = options.get("enforce_cta", True)
        self.enforce_pronouns = options.get("enforce_pronouns", True)
        self.enforce_eval_labels = options.get("enforce_eval_labels", True)
        self.auto_fix_quotes = options.get("auto_fix_quotes", True)

        self.cta_rx = re.compile(
            r"\b(успей|только сегодня|последние места|скидка|горящее предложение)\b", re.I
        )
        self.brand_rx = re.compile(r"\bшкола\s*21\b", re.I)
        # Оценочные ярлыки, которые нужно избегать
        self.eval_labels_rx = re.compile(
            r"\b(правильно|неправильно|плохо|хорошо|верно|неверно)\b", re.I
        )

    def _auto_rephrase_directive(self, text: str, language: str) -> str:
        """
        Автоматически перефразирует директивы.

        Args:
            text: Исходный текст
            language: Язык

        Returns:
            Текст с перефразированными директивами
        """
        rules = {
            "ru": [
                (r"\bнажм(и|ите)\b", "используется по необходимости"),
                (r"\bкликн(и|ите)\b", "при необходимости через интерфейс"),
                (r"\bперейд(и|ите)\b", "переход возможен при необходимости"),
                (r"\bввед(и|ите)\b", "ввод данных выполняется участником"),
                (r"\bоткрой(те)?\b", "используется доступ к материалам"),
                (r"\bвыбери(те)?\b", "выбор остаётся на твоё усмотрение"),
                (r"\bзапусти(те)?\b", "запуск инструмента допускается"),
                (r"\bскач(ай|айте)\b", "материалы доступны при необходимости"),
            ],
            "en": [
                (r"\bclick\b", "use the interface if needed"),
                (r"\bpress\b", "use the interface if needed"),
                (r"\bgo to\b", "navigation is possible if needed"),
                (r"\benter\b", "data input is up to you"),
                (r"\bopen\b", "access materials when needed"),
                (r"\bselect\b", "selection is up to you"),
                (r"\brun\b", "running the tool is allowed"),
                (r"\bdownload\b", "materials are available if needed"),
            ],
            "ky": [],
        }
        out = text
        for rx, repl in rules.get(language, []):
            out = re.sub(rx, repl, out, flags=re.I)
        return out

    def _auto_rephrase_goals(self, text: str, language: str) -> str:
        """
        Перефразирует цели-антипаттерны.

        Args:
            text: Исходный текст
            language: Язык

        Returns:
            Текст с перефразированными целями
        """
        bads = BAD_GOAL_PATTERNS.get(language, [])
        out = text
        for pat in bads:
            out = re.sub(pat, "разработать и описать подход к", out, flags=re.I)
        return out

    def _remove_eval_labels(self, text: str, language: str) -> str:
        """
        Удаляет или заменяет оценочные ярлыки.

        Args:
            text: Исходный текст
            language: Язык

        Returns:
            Текст без оценочных ярлыков
        """
        replacements = {
            "ru": {
                "правильно": "корректно",
                "неправильно": "некорректно",
                "плохо": "не рекомендуется",
                "хорошо": "рекомендуется",
                "верно": "корректно",
                "неверно": "некорректно",
            },
            "en": {
                "correctly": "appropriately",
                "incorrectly": "inappropriately",
                "bad": "not recommended",
                "good": "recommended",
                "right": "appropriate",
                "wrong": "inappropriate",
            },
            "ky": {},
        }
        out = text
        repl_dict = replacements.get(language, {})

        # Заменяем оценочные ярлыки на нейтральные формулировки
        for old, new in repl_dict.items():
            out = re.sub(rf"\b{re.escape(old)}\b", new, out, flags=re.I)

        # Если остались необработанные, просто удаляем их из контекста
        # (это для случаев, когда слово в другом падеже или форме)
        if self.eval_labels_rx.search(out):
            # Более мягкая замена - убираем только в оценочном контексте
            out = re.sub(
                r"\b(правильно|неправильно|плохо|хорошо|верно|неверно)\b",
                "",
                out,
                flags=re.I,
            )
            # Убираем двойные пробелы после удаления
            out = re.sub(r"\s+", " ", out)

        return out

    def _fix_pronouns(self, text: str, language: str) -> str:
        """
        Заменяет обращение на «вы» на «ты» для соответствия Tone of Voice.

        Args:
            text: Исходный текст
            language: Язык

        Returns:
            Текст с исправленными местоимениями
        """
        if language != "ru":
            return text

        # Заменяем формы "вы" на "ты" (но не в контексте других слов)
        replacements = {
            r"\bвы\b": "ты",
            r"\bвас\b": "тебя",
            r"\bвам\b": "тебе",
            r"\bваш\b": "твой",
            r"\bваша\b": "твоя",
            r"\bваше\b": "твое",
            r"\bваши\b": "твои",
        }

        out = text
        for pattern, replacement in replacements.items():
            # Заменяем только если это отдельное слово (не часть другого слова)
            out = re.sub(pattern, replacement, out, flags=re.I)

        return out

    def lint(self, text: str, language: str) -> list[LintIssue]:
        """
        Находит проблемы стиля.

        Args:
            text: Текст для проверки
            language: Язык

        Returns:
            Список найденных проблем
        """
        issues: list[LintIssue] = []
        for pat in BANNED_BY_LANG.get(language, []):
            for m in re.finditer(pat, text, flags=re.I):
                s, e = m.span()
                issues.append(LintIssue(kind="directive", span=(s, e), excerpt=text[s:e]))

        if self.enforce_cta:
            for m in self.cta_rx.finditer(text):
                s, e = m.span()
                issues.append(LintIssue(kind="cta", span=(s, e), excerpt=text[s:e]))

        for pat in BAD_GOAL_PATTERNS.get(language, []):
            for m in re.finditer(pat, text, flags=re.I):
                s, e = m.span()
                issues.append(
                    LintIssue(
                        kind="goal_antipattern",
                        span=(s, e),
                        excerpt=text[s:e],
                        suggestion="заменить на действие+результат",
                    )
                )

        # Проверка оценочных ярлыков
        if self.enforce_eval_labels:
            for m in self.eval_labels_rx.finditer(text):
                s, e = m.span()
                issues.append(
                    LintIssue(
                        kind="eval_label",
                        span=(s, e),
                        excerpt=text[s:e],
                        suggestion="избегай оценочных ярлыков, используй нейтральные формулировки",
                    )
                )

        return issues

    def lint_document(self, document: ReadmeDocument, language: str) -> list[LintIssue]:
        """Lint a typed README while keeping Markdown as an output boundary."""
        return self.lint(document.to_markdown(), language)

    def _fix_quotes(self, text: str, language: str) -> str:
        """
        Заменяет прямые кавычки на кавычки-елочки для русского языка.

        Args:
            text: Исходный текст
            language: Язык

        Returns:
            Текст с исправленными кавычками
        """
        if language != "ru":
            return text

        # Заменяем прямые двойные кавычки на елочки
        # Простая эвристика: открывающая " -> «, закрывающая " -> »
        # Учитываем, что в Markdown могут быть кавычки в коде, которые не нужно менять
        # Заменяем только вне блоков кода

        # Разбиваем на части: код блоки и обычный текст
        parts = []
        in_code_block = False
        current_part = ""

        i = 0
        while i < len(text):
            if text[i:i+3] == "```":
                if current_part:
                    parts.append(("text", current_part))
                    current_part = ""
                # Находим конец блока кода
                end = text.find("```", i+3)
                if end != -1:
                    parts.append(("code", text[i:end+3]))
                    i = end + 3
                    continue
                else:
                    current_part += text[i]
                    i += 1
            else:
                current_part += text[i]
                i += 1

        if current_part:
            parts.append(("text", current_part))

        # Обрабатываем только текстовые части, учитывая inline code
        result_parts = []
        for part_type, part_text in parts:
            if part_type == "code":
                result_parts.append(part_text)
            else:
                # Обрабатываем inline code в текстовых частях
                inline_code_pattern = re.compile(r'`[^`]+`')
                inline_parts = []
                inline_last_end = 0
                for m in inline_code_pattern.finditer(part_text):
                    if m.start() > inline_last_end:
                        inline_parts.append(("text", part_text[inline_last_end:m.start()]))
                    inline_parts.append(("code", m.group(0)))
                    inline_last_end = m.end()
                if inline_last_end < len(part_text):
                    inline_parts.append(("text", part_text[inline_last_end:]))

                # Заменяем кавычки только в текстовых частях
                fixed_text = ""
                for inline_type, inline_text in inline_parts:
                    if inline_type == "code":
                        fixed_text += inline_text
                    else:
                        # Заменяем прямые кавычки на елочки
                        # Улучшенная эвристика: учитываем контекст
                        quote_count = 0
                        for i, char in enumerate(inline_text):
                            if char == '"':
                                # Проверяем контекст: если перед кавычкой пробел или начало строки - открывающая
                                # Если после кавычки пробел, точка, запятая или конец строки - закрывающая
                                prev_char = inline_text[i-1] if i > 0 else ' '
                                next_char = inline_text[i+1] if i < len(inline_text)-1 else ' '

                                if quote_count % 2 == 0:
                                    # Открывающая кавычка: обычно после пробела или в начале
                                    if prev_char in ' \n\t' or i == 0:
                                        fixed_text += "«"
                                    else:
                                        fixed_text += "«"
                                else:
                                    # Закрывающая кавычка: обычно перед пробелом, точкой, запятой или в конце
                                    if next_char in ' \n\t.,;:!?)' or i == len(inline_text)-1:
                                        fixed_text += "»"
                                    else:
                                        fixed_text += "»"
                                quote_count += 1
                            else:
                                fixed_text += char

                result_parts.append(fixed_text)

        return "".join(result_parts)

    def rewrite(self, text: str, language: str) -> str:
        """
        Переписывает текст, убирая нарушения стиля.

        Args:
            text: Исходный текст
            language: Язык

        Returns:
            Исправленный текст
        """
        t = text
        if self.enforce_directives:
            t = self._auto_rephrase_directive(t, language)
        if self.enforce_directives:
            t = self._auto_rephrase_goals(t, language)
        if self.enforce_eval_labels:
            t = self._remove_eval_labels(t, language)
        if self.enforce_pronouns:
            t = self._fix_pronouns(t, language)
        if self.auto_fix_quotes:
            t = self._fix_quotes(t, language)
        return t

    def rewrite_document(self, document: ReadmeDocument, language: str) -> ReadmeDocument:
        """Rewrite style and return a typed README document for the next pipeline step."""
        rewritten = self.rewrite(document.to_markdown(), language)
        return ReadmeDocument.from_markdown(rewritten, fallback_title=document.title)


# Backward-compatible alias for older imports. New code should use StyleGuardRepair.
StyleGuardAgent = StyleGuardRepair
