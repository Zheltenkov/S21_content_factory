"""
content_gen/agents/translation_refiner.py

Агенты для улучшения перевода по читаемости и стилю и комбинирования двух версий.

- TranslationRefinerAgent: переписывает уже переведённый текст для лучшей читаемости
  и стиля (для всех языков; для английского — American English, более простые конструкции).
- TranslationCombinerAgent: принимает дословную и «улучшенную» версии и выдаёт
  комбинированный вариант, сохраняя полноту и улучшая ясность.
"""

import sys
import re

from .base.llm_client import LLMClientProtocol
from ..utils.translation_languages import get_translation_language_profile

REFINER_SYSTEM = """Ты — редактор переводов технических документов.
Твоя задача — переписать уже переведённый текст на целевом языке для лучшей читаемости и стиля, БЕЗ изменения смысла и структуры документа.

КРИТИЧЕСКИ ВАЖНО:
- Сохрани ВСЮ структуру Markdown: заголовки (# ## ###), списки, таблицы (| ... |), ссылки, изображения.
- НЕ изменяй и НЕ удаляй маркеры [[[BLOCK_N]]] и HTML-комментарии <!-- PROTECTED_BLOCK id=N ... -->.
- НЕ меняй количество глав, параграфов, пунктов списков.
- Переводи только формулировки предложений: делай их проще и яснее, сохраняя смысл.
- Письменность: {script_instruction}.

Для английского языка:
- Используй American English (разговорный, простые конструкции).
- Избегай тяжёлых формальных «британских» оборотов; предпочитай короткие предложения.

Для остальных языков:
- Улучшай читаемость: более естественный порядок слов, простые конструкции, без излишней формальности.
- Сохраняй тон «на ты» и дружелюбность.

Язык текста: {target_language}."""

REFINER_USER = """Перепиши следующий переведённый README для лучшей читаемости и стиля.
НЕ меняй структуру (заголовки, таблицы, маркеры [[[BLOCK_N]]]). Меняй только формулировки в абзацах и списках.

Исходный перевод:
{markdown}

Верни тот же документ с улучшенными формулировками. Начни сразу с первого заголовка. Сохрани все маркеры [[[BLOCK_N]]] и таблицы без изменений."""


COMBINER_SYSTEM = """Ты — редактор, который объединяет две версии перевода одного документа.
У тебя есть:
1) Дословная версия — максимально близка к оригиналу, иногда тяжеловесна.
2) Улучшенная версия — переписана для читаемости, может что-то упростить.

Твоя задача: выдать ОДИН итоговый документ, который:
- Сохраняет полноту содержания (ничего не терять из дословной версии).
- Использует более простые и ясные формулировки там, где улучшенная версия лучше.
- Сохраняет структуру Markdown и все маркеры [[[BLOCK_N]]] и <!-- PROTECTED_BLOCK --> без изменений.
- Для английского: American English, читаемо и без излишней формальности.
- Соблюдает письменность целевого языка: {script_instruction}.

Выбирай по каждому фрагменту: оставить дословный вариант, взять улучшенный или слегка скомбинировать (ясность + полнота).

Язык: {target_language}."""

COMBINER_USER = """Объедини две версии перевода в один итоговый документ.

ВЕРСИЯ 1 (дословный перевод):
---
{literal_markdown}
---

ВЕРСИЯ 2 (улучшенная для читаемости):
---
{refined_markdown}
---

Требования:
- Выдай один полный документ: полнота как в версии 1, ясность как в версии 2 где это уместно.
- Сохрани всю структуру (заголовки, таблицы, списки). Не изменяй маркеры [[[BLOCK_N]]] и комментарии PROTECTED_BLOCK.
- Соблюдай письменность целевого языка: {script_instruction}.
- Начни с первого заголовка, без вводных фраз."""


class TranslationRefinerAgent:
    """Переписывает перевод для читаемости и стиля (все языки)."""

    def __init__(self, llm_client: LLMClientProtocol):
        self.llm = llm_client
        self._max_chars = 12000

    @staticmethod
    def _strip_preface(text: str) -> str:
        """Удаляет служебные префиксы, которые модель иногда добавляет в ответ."""
        cleaned = text.strip()
        for prefix in (
            "Вот итоговый документ:\n\n",
            "Итоговый документ:\n\n",
            "Улучшенный перевод:\n\n",
        ):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                break
        return cleaned

    def _refine_chunk(self, system: str, chunk: str) -> str:
        """Рефайнит один chunk и возвращает исходный chunk при ошибке."""
        user = REFINER_USER.format(markdown=chunk)
        try:
            refined = self.llm.complete(system=system, user=user, temperature=0.3)
            return self._strip_preface(refined)
        except Exception as e:
            print(f"  ⚠️ Ошибка рефайнера chunk: {e}", file=sys.stderr, flush=True)
            return chunk

    def _split_markdown_chunks(self, markdown: str, max_chars: int) -> list[str]:
        """
        Делит markdown на куски для рефайнера.
        Приоритет: деление по заголовкам H2, fallback — по абзацам.
        """
        text = (markdown or "").strip()
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]

        # Сохраняем заголовки в split как отдельные элементы
        sections = re.split(r"(^##\s+.+$)", text, flags=re.MULTILINE)
        chunks: list[str] = []
        current = ""

        for part in sections:
            if not part:
                continue

            # Очень длинный блок делим на абзацы
            if len(part) > max_chars:
                if current:
                    chunks.append(current.strip())
                    current = ""

                paragraphs = [p for p in re.split(r"\n\n+", part) if p.strip()]
                para_chunk = ""
                for para in paragraphs:
                    candidate = f"{para_chunk}\n\n{para}".strip() if para_chunk else para
                    if len(candidate) > max_chars and para_chunk:
                        chunks.append(para_chunk.strip())
                        para_chunk = para
                    elif len(candidate) > max_chars:
                        # Даже один абзац слишком длинный — режем грубо
                        for i in range(0, len(para), max_chars):
                            chunks.append(para[i:i + max_chars].strip())
                        para_chunk = ""
                    else:
                        para_chunk = candidate

                if para_chunk:
                    chunks.append(para_chunk.strip())
                continue

            candidate = f"{current}{part}"
            if len(candidate) > max_chars and current:
                chunks.append(current.strip())
                current = part
            else:
                current = candidate

        if current.strip():
            chunks.append(current.strip())

        return [c for c in chunks if c]

    def refine(self, translated_markdown: str, target_language: str) -> str:
        """
        Улучшает уже переведённый текст по читаемости и стилю.

        Args:
            translated_markdown: Готовый перевод (с восстановленными блоками).
            target_language: Код целевого языка (en, kg, uz, tg и т.д.).

        Returns:
            Тот же документ с переписанными формулировками.
        """
        profile = get_translation_language_profile(target_language)
        lang_name = profile.prompt_label

        system = REFINER_SYSTEM.format(
            target_language=lang_name,
            script_instruction=profile.script_instruction,
        )
        try:
            print("  📝 Рефайнер: улучшение перевода для читаемости...", file=sys.stderr, flush=True)
            if len(translated_markdown) <= self._max_chars:
                refined = self._refine_chunk(system, translated_markdown)
            else:
                chunks = self._split_markdown_chunks(translated_markdown, self._max_chars)
                print(
                    f"  ⚠️ Рефайнер: длинный документ ({len(translated_markdown)} символов), "
                    f"обрабатываю по частям: {len(chunks)}",
                    file=sys.stderr,
                    flush=True,
                )
                refined_parts = []
                for idx, chunk in enumerate(chunks, 1):
                    print(
                        f"  🧩 Рефайнер chunk {idx}/{len(chunks)} ({len(chunk)} символов)",
                        file=sys.stderr,
                        flush=True,
                    )
                    refined_parts.append(self._refine_chunk(system, chunk))
                refined = "\n\n".join(refined_parts).strip()

            print("  ✅ Рефайнер завершён", file=sys.stderr, flush=True)
            return refined
        except Exception as e:
            print(f"  ⚠️ Ошибка рефайнера: {e}", file=sys.stderr, flush=True)
            return translated_markdown


class TranslationCombinerAgent:
    """Комбинирует дословную и улучшенную версии перевода в один вариант."""

    def __init__(self, llm_client: LLMClientProtocol):
        self.llm = llm_client

    def combine(
        self,
        literal_markdown: str,
        refined_markdown: str,
        target_language: str,
    ) -> str:
        """
        Объединяет две версии перевода: выбирает лучшие фрагменты по полноте и ясности.

        Args:
            literal_markdown: Дословный перевод.
            refined_markdown: Версия после рефайнера.
            target_language: Код целевого языка.

        Returns:
            Один итоговый документ.
        """
        profile = get_translation_language_profile(target_language)
        lang_name = profile.prompt_label

        # Ограничиваем размер для промпта (можно разбить по главам при необходимости)
        max_chars = 28000
        if len(literal_markdown) <= max_chars and len(refined_markdown) <= max_chars:
            system = COMBINER_SYSTEM.format(
                target_language=lang_name,
                script_instruction=profile.script_instruction,
            )
            user = COMBINER_USER.format(
                literal_markdown=literal_markdown,
                refined_markdown=refined_markdown,
                script_instruction=profile.script_instruction,
            )
            try:
                print("  🔀 Комбайнер: объединение двух версий...", file=sys.stderr, flush=True)
                combined = self.llm.complete(system=system, user=user, temperature=0.2)
                combined = combined.strip()
                for prefix in ("Итоговый документ:\n\n", "Вот объединённый перевод:\n\n", "Объединённый перевод:\n\n"):
                    if combined.startswith(prefix):
                        combined = combined[len(prefix):].strip()
                        break
                print("  ✅ Комбайнер завершён", file=sys.stderr, flush=True)
                return combined
            except Exception as e:
                print(f"  ⚠️ Ошибка комбайнера: {e}", file=sys.stderr, flush=True)
                return refined_markdown
        # Слишком длинный документ — возвращаем улучшенную версию как есть
        print("  ⚠️ Документ слишком длинный для комбайнера, возвращаем улучшенную версию", file=sys.stderr, flush=True)
        return refined_markdown
