"""
content_gen/agents/title_annotation.py

Агент генерации заголовка и аннотации.

Генерирует H1 заголовок (1-3 слова) и аннотацию (220-520 символов).
Использует curriculum context для выравнивания с предыдущими проектами.
"""

import re
import sys
from dataclasses import dataclass

from ..config.loader import get_agent_config, prompt_trace_kwargs
from ..config.thresholds import THRESHOLDS
from .base.llm_client import LLMClientProtocol
from ..utils.didactics_loader import compose_didactics_context
from ..models.schemas import Annotation, ProjectContextMeta, ProjectSeed


def _split_sentences(text: str) -> list[str]:
    """Split text into short sentence-like chunks."""
    chunks = re.split(r"(?<=[\.\!\?])\s+", (text or "").strip())
    return [chunk.strip() for chunk in chunks if chunk and chunk.strip()]


def _sanitize_annotation_text(text: str, hi: int | None = None) -> str:
    """Keep annotation compact, teaser-like and free from README structure noise."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^\s*#+\s+.+?(?:\n+|$)", "", cleaned, flags=re.M)
    cleaned = re.sub(r"[*_`]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    forbidden_patterns = (
        r"\bглава\s+\d+\b",
        r"\bоглавлен",
        r"\bсодержани",
        r"\breadme\b",
        r"\bинструкц",
        r"\bраздел\s+\d+\b",
        r"\bструктур",
        r"\bшаг\s+\d+\b",
        r"\bпрактическ\w*\s+блок\b",
    )

    sentences = _split_sentences(cleaned)
    filtered = [
        sentence
        for sentence in sentences
        if not any(re.search(pattern, sentence.lower()) for pattern in forbidden_patterns)
    ]
    if not filtered:
        filtered = sentences

    filtered = filtered[:4]

    if hi:
        compact: list[str] = []
        for sentence in filtered:
            candidate = " ".join(compact + [sentence]).strip()
            if compact and len(candidate) > hi:
                break
            compact.append(sentence)
        if compact:
            filtered = compact

    return " ".join(filtered).strip()


GENERIC_TITLE_PHRASES = {
    "анализ",
    "план",
    "план работ",
    "планирование",
    "практика",
    "проект",
    "рабочий план",
    "работа",
    "работа с проектом",
    "разработка проекта",
    "управление проектом",
    "учебный проект",
}


def _normalize_title_key(title: str) -> str:
    """Normalize title for generic-title detection."""
    cleaned = re.sub(r"[^\w\sА-Яа-яЁё-]+", " ", title or "", flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _is_generic_project_title(title: str) -> bool:
    """Return True when H1 is too generic to anchor a concrete learning project."""
    key = _normalize_title_key(title)
    if not key:
        return True
    if key in GENERIC_TITLE_PHRASES:
        return True

    tokens = key.split()
    generic_tokens = {"анализ", "план", "работ", "работа", "работы", "проект", "проектом", "планирование"}
    return len(tokens) <= 2 and all(token in generic_tokens for token in tokens)


def _derive_specific_title_from_seed(seed: ProjectSeed) -> str:
    """Build a deterministic fallback title from project semantics when LLM returns a generic H1."""
    source = " ".join(
        [
            seed.title_seed or "",
            seed.project_description or "",
            " ".join(seed.learning_outcomes or []),
            " ".join(seed.skills or []),
        ]
    ).lower()

    if any(marker in source for marker in ("спринт", "sprint")):
        return "Планирование спринта"
    if any(marker in source for marker in ("дорожн", "roadmap", "роадмап")):
        return "Дорожная карта"
    if any(marker in source for marker in ("бэклог", "backlog")):
        return "Карта бэклога"
    if any(marker in source for marker in ("риск", "risk")):
        return "Карта рисков"
    if any(marker in source for marker in ("бюджет", "смет", "cost", "затрат")):
        return "Бюджет проекта"
    if any(marker in source for marker in ("коммуникац", "переписк", "письм", "заказчик")):
        return "Рабочая коммуникация"
    if any(marker in source for marker in ("презентац", "выступлен", "pitch")):
        return "Структура выступления"
    if any(marker in source for marker in ("требован", "requirement")):
        return "Карта требований"

    title_seed = (seed.title_seed or "").strip()
    if title_seed and not _is_generic_project_title(title_seed) and len(title_seed.split()) <= 3:
        return title_seed
    return "Рабочий артефакт"


@dataclass
class TitleAnnotation:
    """Результат генерации заголовка и аннотации."""

    title: str
    annotation: Annotation


class TitleAnnotationAgent:
    """Генерирует H1 и аннотацию проекта."""

    CONFIG_NAME = "title_annotation"

    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm
        self.rx_h1 = re.compile(r"^#\s+(.+)$", re.M)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        try:
            self.didactics_context, self.didactics_trace = compose_didactics_context(self.CONFIG_NAME)
        except Exception:
            self.didactics_context, self.didactics_trace = "", {}

    def _regenerate_annotation(self, title: str, original_annotation: str, seed: ProjectSeed, target_length: int) -> str:
        """
        Перегенерирует аннотацию для соответствия целевой длине.

        Args:
            title: Заголовок проекта
            original_annotation: Исходная аннотация
            seed: Входные данные проекта
            target_length: Целевая длина в символах

        Returns:
            Перегенерированная аннотация
        """
        system_prompt = self.config.get_prompt("system_base").format(language=seed.language)
        if self.didactics_context:
            system_prompt = f"{system_prompt}\n\n=== DIDACTICS CONTEXT ===\n{self.didactics_context}"
        direction = "расширь" if len(original_annotation) < target_length else "сократи"

        # Извлекаем ключевые слова из названия
        title_words = set(re.findall(r"[А-Яа-яЁёA-Za-z]+", title.lower()))
        stop_words = {"это", "для", "проект", "проекта", "проекту", "проектом", "проекте", "проекты", "проектов", "проектам", "проектами", "проектах"}
        keywords = [w for w in title_words if w not in stop_words and len(w) > 3]
        keywords_str = ", ".join(keywords[:5]) if keywords else "ключевые слова из названия"

        user = self.config.get_prompt("regenerate_annotation").format(
            direction=direction,
            target_length=target_length,
            title=title,
            original_annotation=original_annotation,
            keywords_str=keywords_str or "ключевые слова из названия",
            seed=seed,
            seed_learning_outcomes="; ".join(seed.learning_outcomes) if seed.learning_outcomes else "—",
            seed_skills="; ".join(seed.skills) if seed.skills else "—",
        )

        llm_kwargs = self.llm_kwargs.copy()
        llm_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system_base",
                "regenerate_annotation",
                output_schema="Annotation.text",
            )
        )
        regenerated = self.llm.complete(system=system_prompt, user=user, **llm_kwargs)
        return re.sub(r"\s+", " ", regenerated.strip())

    def _regenerate_title(self, original_title: str, seed: ProjectSeed, context_meta: ProjectContextMeta) -> str:
        """
        Перефразирует заголовок, если он содержит больше 3 слов.

        Args:
            original_title: Исходный заголовок
            seed: Входные данные проекта
            context_meta: Метаданные curriculum context

        Returns:
            Перефразированный заголовок (1-3 слова)
        """
        system_prompt = self.config.get_prompt("system_base").format(language=seed.language)
        if self.didactics_context:
            system_prompt = f"{system_prompt}\n\n=== DIDACTICS CONTEXT ===\n{self.didactics_context}"
        user = self.config.get_prompt("regenerate_title").format(
            original_title=original_title,
            thematic_block=seed.thematic_block,
            project_type=seed.project_type,
            project_description=seed.project_description,
            title_seed=seed.title_seed or "—",
        )

        regen_kwargs = self.llm_kwargs.copy()
        regen_kwargs.setdefault("temperature", 0.2)
        regen_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system_base",
                "regenerate_title",
                output_schema="TitleAnnotation.title",
            )
        )
        regenerated = self.llm.complete(system=system_prompt, user=user, **regen_kwargs)
        # Убираем возможные символы # и лишние пробелы
        regenerated = re.sub(r"^#+\s*", "", regenerated.strip())
        return regenerated.strip()

    def _postprocess(
        self,
        md: str,
        seed: ProjectSeed | None = None,
        context_meta: ProjectContextMeta | None = None,
    ) -> TitleAnnotation:
        """
        Постобработка сгенерированного Markdown.

        Args:
            md: Сгенерированный Markdown
            seed: Входные данные проекта (для перегенерации при необходимости)
            context_meta: Метаданные curriculum context для перегенерации заголовка при необходимости

        Returns:
            TitleAnnotation с заголовком и аннотацией

        Raises:
            ValueError: Если H1 не найден
        """
        m = self.rx_h1.search(md.strip())
        if not m:
            raise ValueError("H1 не найден")
        title = m.group(1).strip()

        # Проверяем количество слов в заголовке (должно быть 1-3 слова)
        title_words = len(title.split())
        if title_words > 3:
            if seed and context_meta:
                print(f"  ⚠️ Заголовок содержит {title_words} слов (требуется 1–3). Перефразирование...", file=sys.stderr, flush=True)
                title = self._regenerate_title(title, seed, context_meta)
                title_words = len(title.split())
                # Повторная проверка после перефразирования
                if title_words > 3:
                    # Если перефразирование не помогло, обрезаем до первых 3 слов
                    words = title.split()[:3]
                    title = " ".join(words)
                    title_words = len(title.split())
                    print(f"  ⚠️ Перефразирование не помогло, обрезано до 3 слов: '{title}'", file=sys.stderr, flush=True)
                else:
                    print(f"  ✅ Перефразировано: '{title}' ({title_words} слов)", file=sys.stderr, flush=True)
            else:
                # Если нет seed/context, просто обрезаем до первых 3 слов
                words = title.split()[:3]
                title = " ".join(words)
                print(f"  ⚠️ Заголовок обрезан до 3 слов: '{title}'", file=sys.stderr, flush=True)

        if seed and _is_generic_project_title(title):
            print(f"  ⚠️ Заголовок слишком общий: '{title}'. Перефразирование...", file=sys.stderr, flush=True)
            if context_meta:
                title = self._regenerate_title(title, seed, context_meta)
            if _is_generic_project_title(title) or len(title.split()) > 3:
                title = _derive_specific_title_from_seed(seed)
            if len(title.split()) > 3:
                title = " ".join(title.split()[:3])
            print(f"  ✅ Заголовок уточнён: '{title}'", file=sys.stderr, flush=True)

        after = md[m.end() :].lstrip()
        annotation = after.split("\n## ", 1)[0].strip()

        lo, hi = THRESHOLDS["annotation_chars"]
        txt = _sanitize_annotation_text(annotation, hi=hi)

        # Проверяем наличие трех обязательных компонентов
        def _check_annotation_components(text: str) -> tuple[bool, list[str]]:
            """
            Проверяет наличие трех компонентов в аннотации:
            1. Назначение (зачем проект: ценность)
            2. Что внутри (содержание и формат)
            3. Ожидаемый результат
            
            Returns:
                (has_all_components, missing_components)
            """
            text_lower = text.lower()
            missing = []

            # Проверка 1: Назначение (ценность, зачем, для чего, проблема, решает)
            purpose_indicators = [
                "ценность", "зачем", "для чего", "проблем", "решает",
                "необходим", "важен", "позволяет", "помогает", "направлен",
                "нужен", "важен для", "чтобы", "понадобится", "поможет"
            ]
            has_purpose = any(indicator in text_lower for indicator in purpose_indicators)
            if not has_purpose:
                missing.append("назначение (зачем проект: ценность)")

            # Проверка 2: Что внутри (содержание, формат, деятельность, работа)
            content_indicators = [
                "содержит", "включает", "формат", "деятельность", "работа",
                "задачи", "этапы", "процесс", "выполнишь", "освоишь",
                "изучишь", "применишь", "создашь", "темы", "действия",
                "инструмент", "подход", "метод", "практик", "разбер"
            ]
            has_content = any(indicator in text_lower for indicator in content_indicators)
            if not has_content:
                missing.append("что внутри (содержание и формат)")

            # Проверка 3: Ожидаемый результат (результат, получишь, итог, продукт, навык)
            result_indicators = [
                "результат", "получишь", "итог", "продукт", "навык",
                "освоишь", "создашь", "разработаешь", "получишь опыт",
                "сможешь", "научишься", "артефакт", "подготовишь",
                "сформируешь", "соберёшь", "соберешь"
            ]
            has_result = any(indicator in text_lower for indicator in result_indicators)
            if not has_result:
                missing.append("ожидаемый результат")

            return len(missing) == 0, missing

        # Проверяем компоненты
        has_all_components, missing_components = _check_annotation_components(txt)
        sentence_count = len(_split_sentences(txt))

        # Проверяем семантическое сходство с названием (используем SBERT если доступен)
        from ..embeddings import create_embedding_function
        from ..validators.rubric import _semantic_similarity

        # Пытаемся создать embedding function для SBERT
        embedding_func = None
        try:
            embedding_func = create_embedding_function()
        except Exception:
            pass  # Используем fallback на bag-of-words

        similarity = _semantic_similarity(title, txt, seed.language if seed else "ru", embedding_func)
        similarity_threshold = 0.25
        has_good_similarity = similarity >= similarity_threshold

        # Проверяем наличие ключевых слов из названия.
        # Требование адаптивное: для коротких заголовков достаточно 1-2 совпадений,
        # иначе мы зря уходим в несколько дорогих перегенераций.
        title_words = set(re.findall(r"[А-Яа-яЁёA-Za-z]+", title.lower()))
        annotation_words = set(re.findall(r"[А-Яа-яЁёA-Za-z]+", txt.lower()))
        common_words = title_words.intersection(annotation_words)
        # Фильтруем стоп-слова
        stop_words = {"это", "для", "проект", "проекта", "проекту", "проектом", "проекте", "проекты", "проектов", "проектам", "проектами", "проектах", "в", "на", "с", "и", "или", "а", "но", "как", "что", "то", "из", "от", "до", "по", "при", "без", "над", "под", "за", "перед", "после", "между", "среди", "около", "вокруг", "внутри", "вне", "через", "про", "ради", "благодаря", "вопреки", "согласно", "вместо", "кроме", "сверх", "вроде", "подобно", "наподобие", "вследствие", "ввиду", "вслед", "навстречу", "наперекор", "наперерез", "наперехват", "наперегонки", "наперебой", "наперевес"}
        title_keywords = {w for w in title_words if w not in stop_words and len(w) > 2}
        common_words = {w for w in common_words if w not in stop_words and len(w) > 2}
        required_keyword_hits = min(2, len(title_keywords)) if title_keywords else 0
        has_keywords = len(common_words) >= required_keyword_hits if required_keyword_hits else True

        # Проверяем длину, компоненты и семантическое сходство
        needs_regeneration = False
        regeneration_reason = ""

        if len(txt) < lo:
            needs_regeneration = True
            regeneration_reason = f"слишком короткая ({len(txt)} символов, требуется {lo}-{hi})"
        elif len(txt) > hi:
            needs_regeneration = True
            regeneration_reason = f"слишком длинная ({len(txt)} символов, требуется {lo}-{hi})"
        elif sentence_count < 2 or sentence_count > 4:
            needs_regeneration = True
            regeneration_reason = f"неподходящее количество предложений ({sentence_count}, требуется 2-4)"
        elif not has_all_components:
            needs_regeneration = True
            regeneration_reason = f"отсутствуют компоненты: {', '.join(missing_components)}"
        elif not has_good_similarity or not has_keywords:
            needs_regeneration = True
            reasons = []
            if not has_good_similarity:
                reasons.append(f"низкое семантическое сходство ({similarity:.2f} < {similarity_threshold})")
            if not has_keywords:
                reasons.append(
                    f"недостаточно ключевых слов из названия "
                    f"(найдено: {len(common_words)}, требуется минимум {required_keyword_hits})"
                )
            regeneration_reason = " или ".join(reasons)

        if needs_regeneration and seed:
            print(f"  ⚠️ Аннотация: {regeneration_reason}. Перегенерация...", file=sys.stderr, flush=True)
            target_length = (lo + hi) // 2  # Контрольная точка ~500

            # Перегенерируем с явным указанием на отсутствующие компоненты
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                txt = self._regenerate_annotation(title, txt, seed, target_length)
                txt = _sanitize_annotation_text(txt, hi=hi)

                # Повторная проверка компонентов и семантического сходства после перегенерации
                has_all_after, missing_after = _check_annotation_components(txt)
                sentence_count_after = len(_split_sentences(txt))
                similarity_after = _semantic_similarity(title, txt, seed.language, embedding_func)
                title_words_after = set(re.findall(r"[А-Яа-яЁёA-Za-z]+", title.lower()))
                annotation_words_after = set(re.findall(r"[А-Яа-яЁёA-Za-z]+", txt.lower()))
                common_words_after = title_words_after.intersection(annotation_words_after)
                title_keywords_after = {w for w in title_words_after if w not in stop_words and len(w) > 2}
                common_words_after = {w for w in common_words_after if w not in stop_words and len(w) > 2}
                required_hits_after = min(2, len(title_keywords_after)) if title_keywords_after else 0

                if (
                    has_all_after
                    and 2 <= sentence_count_after <= 4
                    and similarity_after >= similarity_threshold
                    and (
                    len(common_words_after) >= required_hits_after if required_hits_after else True
                    )
                ):
                    print(f"  ✅ Перегенерировано: {len(txt)} символов, все компоненты присутствуют", file=sys.stderr, flush=True)
                    break
                else:
                    if attempt < max_attempts:
                        issues = []
                        if not has_all_after:
                            issues.append(f"отсутствуют компоненты: {', '.join(missing_after)}")
                        if sentence_count_after < 2 or sentence_count_after > 4:
                            issues.append(f"неподходящее количество предложений ({sentence_count_after})")
                        if similarity_after < similarity_threshold:
                            issues.append(f"низкое семантическое сходство ({similarity_after:.2f})")
                        if required_hits_after and len(common_words_after) < required_hits_after:
                            issues.append(
                                f"недостаточно ключевых слов ({len(common_words_after)} из {required_hits_after})"
                            )
                        print(f"  ⚠️ Попытка {attempt}/{max_attempts}: все еще проблемы ({'; '.join(issues)}). Повторная перегенерация...", file=sys.stderr, flush=True)
                    else:
                        print(f"  ⚠️ После {max_attempts} попыток перегенерации все еще есть проблемы, используем текущую версию", file=sys.stderr, flush=True)

            print(f"  ✅ Перегенерировано: {len(txt)} символов", file=sys.stderr, flush=True)

            # Финальная проверка длины (на случай если перегенерация не помогла)
            if len(txt) > hi:
                # Обрезаем до последнего полного предложения в пределах лимита
                truncated = txt[:hi]
                last_sentence_end = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
                if last_sentence_end > lo:
                    txt = truncated[:last_sentence_end + 1]
            txt = _sanitize_annotation_text(txt, hi=hi)

        return TitleAnnotation(title=title, annotation=Annotation(text=txt, chars=len(txt)))

    def generate(self, seed: ProjectSeed, context_meta: ProjectContextMeta) -> TitleAnnotation:
        """
        Генерирует заголовок и аннотацию.

        Args:
            seed: Входные данные проекта
            context_meta: Метаданные curriculum context

        Returns:
            TitleAnnotation
        """
        system_prompt = self.config.get_prompt("system_base").format(language=seed.language)
        if self.didactics_context:
            system_prompt = f"{system_prompt}\n\n=== DIDACTICS CONTEXT ===\n{self.didactics_context}"

        usr = self.config.get_prompt("user_template").format(
            title_seed=seed.title_seed or "—",
            track=seed.thematic_block,
            project_type=seed.project_type,
            required_tools=", ".join(seed.required_tools) if seed.required_tools else "—",
            project_description=seed.project_description,
            learning_outcomes="; ".join(seed.learning_outcomes),
            skills="; ".join(seed.skills),
            narrative_anchor=context_meta.narrative_anchor or "—",
            context_summary=context_meta.context_summary or "—",
        )
        generation_kwargs = self.llm_kwargs.copy()
        generation_kwargs.setdefault("temperature", 0.2)
        generation_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system_base",
                "user_template",
                output_schema="TitleAnnotation",
            )
        )
        md = self.llm.complete(system=system_prompt, user=usr, **generation_kwargs)
        return self._postprocess(md, seed=seed, context_meta=context_meta)
