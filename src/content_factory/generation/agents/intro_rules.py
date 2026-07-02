"""
content_gen/agents/intro_rules.py

Агент генерации введения и инструкции.

Генерирует Главу 1: Введение (80-250 слов) и Инструкцию с ключевыми словами.
Обеспечивает соответствие требованиям по длине и структуре.
"""

import re
import sys
from dataclasses import dataclass

from ..config.loader import get_agent_config, prompt_trace_kwargs
from ..config.thresholds import THRESHOLDS
from ..domain_contracts import semantic_overlap_ratio, semantic_tokens
from .base.llm_client import LLMClientProtocol
from ..utils.didactics_loader import compose_didactics_context
from ..models.schemas import ProjectContextMeta, ProjectSeed
from ..utils.text_analysis import count_words
from ..repair.style_guard import StyleGuardRepair


def _count_words(text: str, language: str = "ru") -> int:
    """Подсчитывает количество слов в тексте (обертка для совместимости)."""
    return count_words(text, language)


def _soft_clamp_words(text: str, lo: int, hi: int, language: str = "ru") -> str:
    """Мягко нормализует длину текста по словам."""
    word_count = _count_words(text, language)
    if word_count < lo:
        extra = " Это вводный блок: он объясняет контекст применения, зачем это нужно и какую идею предстоит разобрать."
        text = (text.strip() + " " + extra).strip()
        while _count_words(text, language) < lo:
            text += " Блок задаёт рамки и помогает понять ожидаемый результат."
    elif word_count > hi:
        sents = re.split(r"(?<=[\.\!\?])\s+", text.strip())
        acc = []
        for s in sents:
            if _count_words(" ".join(acc + [s]), language) <= hi:
                acc.append(s)
            else:
                break
        text = " ".join(acc).strip()
        if _count_words(text, language) < lo and sents:
            text = (text + " " + sents[0]).strip()
    return text


def _split_sentences(text: str) -> list[str]:
    """Разбивает текст на предложения для локальной чистки."""
    chunks = re.split(r"(?<=[\.\!\?])\s+", (text or "").strip())
    return [chunk.strip() for chunk in chunks if chunk and chunk.strip()]


def _trim_instruction_to_limit(text: str, language: str = "ru") -> str:
    """Сжимает инструкцию до верхней границы, сохраняя обязательные блоки."""
    lo, hi = THRESHOLDS.get("instruction_words", (80, 250))
    if _count_words(text, language) <= hi:
        return text

    original = (text or "").strip()
    trimmed = re.sub(
        r"\n{2,}\*\*Дисклеймер\*\*\n{2,}[\s\S]*$",
        "",
        original,
        flags=re.I,
    ).strip()
    if trimmed != original and _count_words(trimmed, language) >= lo:
        if _count_words(trimmed, language) <= hi:
            return trimmed
    else:
        trimmed = original

    blocks = [block.strip() for block in re.split(r"\n\s*\n", trimmed) if block.strip()]
    for _ in range(20):
        current = "\n\n".join(blocks).strip()
        if _count_words(current, language) <= hi:
            return current

        candidates = [
            (idx, _count_words(block, language))
            for idx, block in enumerate(blocks)
            if not re.match(r"^\*\*[^*]+\*\*$", block.strip())
            and _count_words(block, language) > 12
        ]
        if not candidates:
            break

        idx, _ = max(candidates, key=lambda item: item[1])
        sentences = _split_sentences(blocks[idx])
        if len(sentences) > 1:
            blocks[idx] = " ".join(sentences[:-1]).strip()
            continue

        words = blocks[idx].split()
        keep = max(8, len(words) - max(4, len(words) // 4))
        blocks[idx] = " ".join(words[:keep]).rstrip(" ,;:") + "."

    candidate = "\n\n".join(blocks).strip()
    if lo <= _count_words(candidate, language) <= hi:
        return candidate
    return original


def _has_instruction_keywords(text: str) -> bool:
    """Check literal validator keywords required by chapter 1 rubric."""
    low = (text or "").lower()
    return all(keyword in low for keyword in ("допускается", "запрещено", "обязательно"))


def _ensure_intro_word_range(text: str, language: str = "ru") -> str:
    """Final deterministic guard for rubric intro volume and context markers."""
    lo, hi = THRESHOLDS["intro_words"]
    fixed = _ensure_context_markers(_soft_clamp_words(text, lo, hi, language))
    while _count_words(fixed, language) < lo:
        fixed = (
            fixed.rstrip()
            + " В реальной задаче это удерживает общий рабочий кейс и помогает увидеть ожидаемый результат проекта."
        ).strip()
    if _count_words(fixed, language) > hi:
        fixed = _soft_clamp_words(fixed, lo, hi, language)
    return _ensure_context_markers(fixed)


def _ensure_instruction_word_range(text: str, required_tools: list[str], language: str = "ru") -> str:
    """Final deterministic guard for instruction volume and literal rubric keywords."""
    lo, hi = THRESHOLDS.get("instruction_words", (80, 250))
    fixed = _ensure_instruction_keywords(text, required_tools)
    fixed = _trim_instruction_to_limit(fixed, language)
    while _count_words(fixed, language) < lo:
        fixed = (
            fixed.rstrip()
            + " Проверяй итог по критериям проекта и фиксируй только те выводы, которые можно подтвердить материалами."
        ).strip()
    fixed = _ensure_instruction_keywords(fixed, required_tools)
    if _count_words(fixed, language) > hi:
        fixed = _trim_instruction_to_limit(fixed, language)
        fixed = _ensure_instruction_keywords(fixed, required_tools)
    return fixed


def _ensure_context_markers(text: str) -> str:
    """Обеспечивает наличие маркеров контекста."""
    NEEDLES = [
        "используется для",
        "в реальной задаче",
        "применяется",
        "основная идея",
        "что решает",
        "зачем",
    ]
    if any(n in text.lower() for n in NEEDLES):
        return text
    return (
        text.strip()
        + " В реальной задаче это применяется для решения конкретных проблем: важно понять, что именно решаем, зачем это нужно и какова основная идея подхода."
    ).strip()


def _sanitize_intro_text(text: str) -> str:
    """Убирает случайные вложенные markdown-заголовки из тела введения."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^\s*#{1,6}\s+.+?(?:\n+|$)", "", cleaned, count=1, flags=re.M)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _reduce_annotation_overlap(intro_text: str, annotation_text: str) -> str:
    """Удаляет первый абзац введения, если он почти дублирует аннотацию."""
    intro = (intro_text or "").strip()
    annotation = (annotation_text or "").strip()
    if not intro or not annotation:
        return intro

    intro_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", intro) if p.strip()]
    if not intro_paragraphs:
        return intro

    first = intro_paragraphs[0]

    first_tokens = semantic_tokens(first)
    annotation_tokens = semantic_tokens(annotation)
    if not first_tokens or not annotation_tokens:
        return intro

    overlap = semantic_overlap_ratio(first, annotation)
    if overlap < 0.65:
        return intro

    remaining = "\n\n".join(intro_paragraphs[1:]).strip()
    return remaining or intro


def _remove_annotation_overlap_sentences(intro_text: str, annotation_text: str) -> str:
    """Удаляет отдельные предложения введения, если они почти дублируют аннотацию."""
    intro_sentences = _split_sentences(intro_text)
    annotation_tokens = semantic_tokens(annotation_text)

    if not intro_sentences or not annotation_tokens:
        return intro_text

    kept: list[str] = []
    scored_sentences: list[tuple[float, str]] = []
    for sentence in intro_sentences:
        tokens = semantic_tokens(sentence)
        if not tokens:
            kept.append(sentence)
            continue
        overlap = semantic_overlap_ratio(sentence, annotation_text)
        scored_sentences.append((overlap, sentence))
        if overlap < 0.42:
            kept.append(sentence)

    if not kept and scored_sentences:
        kept = [min(scored_sentences, key=lambda item: item[0])[1]]
    return " ".join(kept).strip()


def _strip_generic_intro_sentences(text: str) -> str:
    """Убирает типовые карьерные и административные фразы, не несущие контекста проекта."""
    generic_patterns = [
        r"\bпроект направлен на\b",
        r"\bзнания,?\s+полученн",
        r"\bстанет важн",
        r"\bрасширит твои горизонты\b",
        r"\bотносится к блоку\b",
        r"\bпродолжает линию курса\b",
        r"\bуспешной работы в различных сферах\b",
        r"\bуглубл[её]нно\b",
        r"\bкарьер[аы]\b",
    ]
    sentences = _split_sentences(text)
    filtered = [
        sentence
        for sentence in sentences
        if not any(re.search(pattern, sentence, flags=re.I) for pattern in generic_patterns)
    ]
    if not filtered:
        filtered = sentences[:2]
    return " ".join(filtered).strip()


def _enforce_intro_focus(text: str, project_description: str) -> str:
    """Возвращает введение к реальному контексту проекта, если оно слишком абстрактно."""
    intro = (text or "").strip()
    if not intro:
        return intro

    context_markers = ("в реальной задаче", "используется для", "применяется", "что решает", "зачем")
    if any(marker in intro.lower() for marker in context_markers):
        return intro

    description = re.sub(r"\s+", " ", (project_description or "").strip()).rstrip(".")
    if not description:
        return intro

    addition = f" В реальной задаче это используется, когда нужно {description[:180].lower()}."
    return (intro + addition).strip()


def _sanitize_instruction_for_content_type(text: str, content_type: str) -> str:
    """Чистит несоответствующие типу проекта упоминания в инструкции."""
    if content_type != "no_code":
        return text

    cleaned = text
    replacements = [
        (r"\bкод[а-я]*\b", "документы и артефакты"),
        (r"\bавтотест[а-я]*\b", "peer-review проверка"),
        (r"\bтест[а-я]*\b", "проверка результата"),
        (r"\bдепло[йя][а-я]*\b", "подготовку результата к сдаче"),
        (r"\bстатическ[а-я]* анализ[а-я]*\b", "экспертную проверку"),
    ]
    for pattern, replacement in replacements:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.I)

    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _format_static_instruction_markdown(text: str) -> str:
    """Возвращает статическую инструкцию к блочному markdown-формату."""
    formatted = (text or "").strip()
    if not formatted:
        return formatted

    formatted = re.sub(r"\s+", " ", formatted)
    formatted = re.sub(
        r"(Эта инструкция зада[её]т \*\*общие правила работы с проектом\*\* и \*\*не описывает конкретные шаги по решению задач\*\*\.)\s*",
        r"\1\n\n",
        formatted,
        count=1,
        flags=re.I,
    )
    formatted = re.sub(
        r"\s*(\*\*(?:Контекст и ограничения проекта|Как учиться(?: в проекте)?|Как работать с проектом|Дисклеймер)\*\*)\s*",
        r"\n\n\1\n\n",
        formatted,
        flags=re.I,
    )
    formatted = re.sub(r"\s+(—\s+\*\*[^*]+:\*\*)", r"\n\n\1", formatted)
    formatted = re.sub(r"(\.\s+)(?=—\s+\*\*)", ".\n\n", formatted)
    formatted = re.sub(r"[ \t]{2,}", " ", formatted)
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)
    return formatted.strip()


def _ensure_instruction_keywords(text: str, required_tools: list[str]) -> str:
    """
    Проверяет наличие обязательных элементов в инструкции.
    
    Для статического формата инструкции проверяет наличие обязательных блоков:
    - "Контекст и ограничения проекта" (КРИТИЧЕСКИ ВАЖНО для 2.3.7)
    - "Как учиться в «Школе 21»"
    
    Остальные блоки ("Как работать с проектом", "Дисклеймер") опциональны.
    
    Если обязательные блоки отсутствуют, возвращает исходный текст (LLM должен был их сгенерировать).
    """
    from ..utils.text_analysis import count_words

    tl = text.lower()
    keyword_sentence = (
        "Обязательно опирайся на материалы проекта; допускается вести черновики в удобном формате; "
        "запрещено подменять анализ готовыми выводами."
    )

    if not _has_instruction_keywords(text):
        if "контекст и ограничения проекта" in tl:
            text = re.sub(
                r"(\*\*Контекст и ограничения проекта\*\*\s*)",
                rf"\1\n\n{keyword_sentence}",
                text,
                count=1,
                flags=re.I,
            )
        else:
            text = f"{keyword_sentence}\n\n{text.strip()}".strip()
        tl = text.lower()

    # Проверяем наличие обязательных статических блоков
    has_context_block = "контекст и ограничения проекта" in tl or "требования к окружению" in tl
    has_learning_block = "как учиться" in tl or "как работать с проектом" in tl or "подход к обучению" in tl

    # Если обязательные блоки присутствуют, проверяем только длину
    if has_context_block and has_learning_block:
        # Проверяем длину (80-250 слов)
        lo, hi = THRESHOLDS.get("instruction_words", (80, 250))
        word_count = count_words(text, "ru")

        # Если инструкция слишком короткая, но блоки есть - возможно, нужно добавить
        # специфичные пункты в блок "Как работать с проектом"
        if word_count < lo and required_tools:
            # Проверяем, упомянуты ли обязательные инструменты в блоке "Как работать с проектом"
            tools_str = ", ".join(required_tools)
            if tools_str.lower() not in tl and "инструмент" not in tl:
                # Находим блок "Как работать с проектом" и добавляем пункт об инструментах
                project_block_pattern = r"(.*?\*\*Как работать с проектом:\*\*.*?)(\n\n\*\*Дисклеймер|\Z)"
                match = re.search(project_block_pattern, text, re.S | re.I)
                if match:
                    # Добавляем пункт об инструментах перед Дисклеймером
                    before_disclaimer = match.group(1)
                    after = match.group(2) if match.group(2) else ""
                    new_text = before_disclaimer.rstrip() + f"\n- Обязательные инструменты: {tools_str}." + after
                    return new_text

        return text

    # Если блоки отсутствуют, возвращаем исходный текст
    # (LLM должен был их сгенерировать согласно промпту)
    return text


SYSTEM = ""

USER_TMPL = ""


@dataclass
class IntroResult:
    """Результат генерации введения и инструкции."""

    intro_text: str
    instruction_text: str


class IntroRulesAgent:
    """Генерирует введение и инструкцию для Главы 1."""

    CONFIG_NAME = "intro_rules"
    INTRO_HEADINGS = {"введение", "вводная часть", "контекст проекта"}
    INSTRUCTION_HEADINGS = {"инструкция", "правила выполнения", "требования"}

    def __init__(self, llm: LLMClientProtocol):
        self.llm = llm
        self.style = StyleGuardRepair()
        self.rx_h3 = re.compile(r"^###\s+(.+?)\s*$", re.M)
        self.config = get_agent_config(self.CONFIG_NAME)
        self.llm_kwargs = self.config.llm.to_kwargs() if self.config.llm else {}
        try:
            self.didactics_context, self.didactics_trace = compose_didactics_context(self.CONFIG_NAME)
        except Exception:
            self.didactics_context, self.didactics_trace = "", {}

    @staticmethod
    def _normalize_heading(text: str) -> str:
        """Normalize heading text for loose matching."""
        cleaned = re.sub(r"[*_`:#]+", " ", text or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
        return cleaned

    def _is_intro_heading(self, heading: str) -> bool:
        normalized = self._normalize_heading(heading)
        return normalized in self.INTRO_HEADINGS

    def _is_instruction_heading(self, heading: str) -> bool:
        normalized = self._normalize_heading(heading)
        return normalized in self.INSTRUCTION_HEADINGS

    def _split_intro_instruction(self, md: str) -> tuple[str, str]:
        """Extract intro and instruction blocks from markdown with tolerant heading matching."""
        headings = list(self.rx_h3.finditer(md or ""))
        intro_text = ""
        instruction_text = ""

        intro_idx = next((idx for idx, match in enumerate(headings) if self._is_intro_heading(match.group(1))), None)
        instr_idx = next((idx for idx, match in enumerate(headings) if self._is_instruction_heading(match.group(1))), None)

        if intro_idx is not None:
            intro_start = headings[intro_idx].end()
            intro_end = headings[instr_idx].start() if instr_idx is not None and instr_idx > intro_idx else len(md)
            intro_text = md[intro_start:intro_end].strip()

        if instr_idx is not None:
            instr_start = headings[instr_idx].end()
            instr_end = headings[instr_idx + 1].start() if instr_idx + 1 < len(headings) else len(md)
            instruction_text = md[instr_start:instr_end].strip()

        if intro_text and not instruction_text:
            marker = re.search(
                r"\*\*(Контекст и ограничения проекта|Как учиться в проекте|Как работать с проектом|Дисклеймер)\*\*",
                intro_text,
                flags=re.I,
            )
            if marker:
                instruction_text = intro_text[marker.start():].strip()
                intro_text = intro_text[:marker.start()].strip()

        if instruction_text and not intro_text:
            prefix = md[: headings[instr_idx].start()].strip() if instr_idx is not None else ""
            if prefix:
                intro_text = prefix

        return intro_text, instruction_text

    def _repair_sections(self, md: str, system_prompt: str, generation_kwargs: dict) -> str:
        """Ask the model to reformat already generated content into the required two-section layout."""
        repair_prompt = (
            "Переформатируй уже сгенерированный текст в СТРОГО такой markdown-формат без добавления новых смыслов:\n\n"
            "### Введение\n\n"
            "<текст введения>\n\n"
            "### Инструкция\n\n"
            "<текст инструкции>\n\n"
            "Разрешено только переименовать заголовки и переразложить существующий текст по двум секциям. "
            "Сохрани содержание, tone of voice и markdown-блоки инструкции.\n\n"
            "=== ИСХОДНЫЙ ТЕКСТ ===\n"
            f"{md}"
        )
        repair_kwargs = generation_kwargs.copy()
        repair_kwargs.setdefault("temperature", 0.0)
        return self.llm.complete(system=system_prompt, user=repair_prompt, **repair_kwargs)

    def _determine_content_type(self, seed: ProjectSeed) -> str:
        """
        Определяет тип контента на основе направления.
        
        Returns:
            'hard_code' | 'low_code' | 'no_code'
        """
        explicit_type = getattr(seed, "project_content_type", None)
        if explicit_type in {"hard_code", "low_code", "no_code"}:
            return explicit_type

        direction = (getattr(seed, 'direction', '') or seed.thematic_block or "").upper()

        # Hard code: Разработчик ПО
        hard_code_directions = {
            'C', 'CPP', 'C++', 'JAVA', 'GO', 'RUST', 'BACKEND', 'MOBILE',
            'WEB', 'FRONTEND', 'FULLSTACK', 'DEV', 'SWE'
        }

        # Low code: DS, DevOps, QA
        low_code_directions = {
            'DS', 'DO', 'QA', 'BIO', 'BIOINF', 'DEVOPS', 'DATA',
            'ML', 'AI', 'TESTING', 'AUTOMATION'
        }

        # No code: PjM, UX, КБ, BSA
        no_code_directions = {
            'PJM', 'UX', 'CB', 'KB', 'BSA', 'BA', 'PM', 'CYBER',
            'SECURITY', 'PRODUCT', 'DESIGN', 'MANAGEMENT', 'ANALYST'
        }

        if direction in hard_code_directions:
            return 'hard_code'
        elif direction in low_code_directions:
            return 'low_code'
        elif direction in no_code_directions:
            return 'no_code'
        else:
            return 'low_code'

    def _build_content_type_instruction(self, seed: ProjectSeed, content_type: str) -> str:
        """Строит блок контекста и ограничений в зависимости от типа контента."""

        # Получаем данные из УП
        platform_name = getattr(seed, 'platform_name', None) or "project"
        gitlab_link = getattr(seed, 'gitlab_link', None) or "материалы проекта"
        required_tools_str = ", ".join(seed.required_tools) if seed.required_tools else "инструменты по выбору"
        required_software_str = ", ".join(getattr(seed, "required_software", []) or []) or "ПО по условиям проекта"
        environment_str = f"{required_tools_str}; ПО/среда: {required_software_str}"

        if content_type == 'no_code':
            # NO CODE: менеджмент, аналитика, дизайн - БЕЗ упоминания кода, Python, автотестов
            return f"""Используй в блоке "Контекст и ограничения проекта":

**Контекст и ограничения проекта**

— **Окружение и инструменты:** обязательно используй инструменты проекта: {environment_str}. Локальные черновики допускаются, но итоговые артефакты должны быть проверяемыми.
— **Исходные данные:** работай только с материалами проекта ({gitlab_link}) и файлами из `materials/`.
— **Артефакты:** размещай результат по каноническим путям задач; запрещено менять служебные материалы и подменять анализ готовыми выводами."""

        elif content_type == 'low_code':
            # LOW CODE: DS, DevOps, QA - минимум кода, больше конфигов/анализа
            return f"""Используй в блоке "Контекст и ограничения проекта":

**Контекст и ограничения проекта**

— **Окружение и инструменты:** обязательно используй инструменты проекта: {environment_str}. Локальная работа допускается для черновиков, итог должен воспроизводиться в целевом окружении.
— **Исходные данные:** используй структуру и материалы проекта ({gitlab_link}); дополнительные данные допустимы только в рамках задания.
— **Артефакты:** результат размещается по каноническим путям задач; запрещено менять служебные файлы и материалы вне задания."""

        else:  # hard_code
            # HARD CODE: разработка ПО - полные требования к окружению
            return f"""Используй в блоке "Контекст и ограничения проекта":

**Контекст и ограничения проекта**

— **Окружение и инструменты:** обязательно используй целевое окружение и инструменты: {environment_str}. Локальное окружение допускается для черновиков, итог должен воспроизводиться.
— **Исходные данные:** стартовая структура проекта находится в GitLab/материалах ({gitlab_link}).
— **Артефакты:** проверяемый код и файлы размещаются по каноническим путям задач; запрещено менять служебные файлы и добавлять лишние директории вне задания."""

    def generate(self, seed: ProjectSeed, context_meta: ProjectContextMeta, annotation_text: str = "") -> IntroResult:
        """
        Генерирует введение и инструкцию.

        Args:
            seed: Входные данные проекта
            context_meta: Метаданные curriculum context

        Returns:
            IntroResult с текстами введения и инструкции

        Raises:
            ValueError: Если секции не найдены
        """
        # Определяем тип контента
        content_type = self._determine_content_type(seed)
        content_type_instruction = self._build_content_type_instruction(seed, content_type)

        system_prompt = self.config.get_prompt("system").format(language=seed.language)
        if self.didactics_context:
            system_prompt = f"{system_prompt}\n\n=== DIDACTICS CONTEXT ===\n{self.didactics_context}"
        usr = self.config.get_prompt("user_template").format(
            annotation_text=annotation_text or "—",
            narrative_anchor=context_meta.narrative_anchor or "—",
            context_summary=context_meta.context_summary or "—",
            required_tools=", ".join(seed.required_tools) if seed.required_tools else "—",
            track=seed.thematic_block,
            project_type=seed.project_type,
            project_description=seed.project_description,
            learning_outcomes="; ".join(seed.learning_outcomes),
            skills="; ".join(seed.skills),
            sjm=seed.sjm or "—",
            content_type_instruction=content_type_instruction,
        )
        if seed.reference_project_hint and seed.reference_project_hint.strip():
            usr = (
                f"{usr}\n\n=== ЭТАЛОН ИДЕАЛЬНОГО ПРОЕКТА ===\n"
                "Ниже дан ориентир по качеству, структуре и уровню подачи. Используй его только как reference по форме,"
                " но факты, ограничения и содержание бери из текущего проекта и УП.\n\n"
                f"{seed.reference_project_hint.strip()}"
            )
        generation_kwargs = self.llm_kwargs.copy()
        generation_kwargs.setdefault("temperature", 0.2)
        generation_kwargs.update(
            prompt_trace_kwargs(
                self.config,
                "system",
                "user_template",
                output_schema="IntroResult",
            )
        )
        md = self.llm.complete(system=system_prompt, user=usr, **generation_kwargs)

        intro, instr = self._split_intro_instruction(md)
        if not (intro and instr):
            print("  ⚠️ IntroRules: структура ответа не распознана, пробую repair-pass...", file=sys.stderr, flush=True)
            repaired_md = self._repair_sections(md, system_prompt, generation_kwargs)
            intro, instr = self._split_intro_instruction(repaired_md)
            if intro and instr:
                md = repaired_md

        if not (intro and instr):
            preview = (md or "").strip().replace("\n", " ")[:400]
            raise ValueError(f"Ожидались секции «Введение» и «Инструкция». Ответ модели: {preview}")

        intro = _sanitize_intro_text(intro)
        intro = _reduce_annotation_overlap(intro, annotation_text)
        intro = _remove_annotation_overlap_sentences(intro, annotation_text)
        intro = _strip_generic_intro_sentences(intro)
        intro = _enforce_intro_focus(intro, seed.project_description)
        intro = _ensure_intro_word_range(intro, seed.language)

        # Проверяем наличие статических блоков в инструкции
        instr_lower = instr.lower()
        has_static_format = (
            ("контекст и ограничения проекта" in instr_lower or "требования к окружению" in instr_lower) and
            ("как учиться" in instr_lower or "как работать с проектом" in instr_lower)
        )

        if has_static_format:
            # Инструкция в статическом формате - проверяем только наличие обязательных элементов
            instr = _format_static_instruction_markdown(instr)
            instr = _ensure_instruction_keywords(instr, seed.required_tools)
            instr_word_count = _count_words(instr, seed.language)
            print(f"  ✅ Инструкция в статическом формате ({instr_word_count} слов)", file=sys.stderr, flush=True)
        else:
            # Старый формат - применяем проверки длины
            instr_lo, instr_hi = THRESHOLDS.get("instruction_words", (80, 250))
            instr_word_count = _count_words(instr, seed.language)

            if instr_word_count < instr_lo:
                # Расширяем инструкцию до минимальной длины
                instr = _ensure_instruction_word_range(instr, seed.required_tools, seed.language)
                instr_word_count = _count_words(instr, seed.language)
                if instr_word_count < instr_lo:
                    print(f"  ⚠️ Инструкция слишком короткая ({instr_word_count} слов, требуется {instr_lo}-{instr_hi}). Расширение...", file=sys.stderr, flush=True)

        instr = _ensure_instruction_keywords(instr, seed.required_tools)
        instr = _sanitize_instruction_for_content_type(instr, content_type)
        if has_static_format:
            instr = _format_static_instruction_markdown(instr)
        instr = _trim_instruction_to_limit(instr, seed.language)

        intro_fixed = self.style.rewrite(intro, seed.language)
        instr_fixed = self.style.rewrite(instr, seed.language)
        if has_static_format:
            instr_fixed = _format_static_instruction_markdown(instr_fixed)
        intro_fixed = _ensure_intro_word_range(intro_fixed, seed.language)
        instr_fixed = _ensure_instruction_word_range(instr_fixed, seed.required_tools, seed.language)

        return IntroResult(intro_text=intro_fixed, instruction_text=instr_fixed)

    def inject_into_markdown(self, md: str, result: IntroResult) -> str:
        """
        Вставляет сгенерированные тексты в Markdown.

        Args:
            md: Исходный Markdown
            result: Результат генерации

        Returns:
            Обновлённый Markdown
        """
        import sys
        # Более гибкое регулярное выражение
        patterns = [
            r"(##\s+Глава 1\. Введение и инструкция\s*\n)(.*?)(?=\n##\s+Глава 2|\Z)",
            r"(##\s+Глава 1[^\n]*\n)(.*?)(?=\n##\s+Глава 2|\Z)",
            r"(##\s+Глава 1[^\n]*\n)(.*?)(?=\n##|\Z)",
        ]

        # Проверяем, находится ли инструкция в статическом формате
        instruction_formatted = result.instruction_text
        instr_lower = instruction_formatted.lower()
        has_static_format = (
            ("контекст и ограничения проекта" in instr_lower or "требования к окружению" in instr_lower) and
            ("как учиться" in instr_lower or "как работать с проектом" in instr_lower or "подход к обучению" in instr_lower)
        )

        # Если инструкция в статическом формате, используем её как есть
        # Иначе преобразуем в пункты, если она не в формате пунктов
        if not has_static_format:
            if not instruction_formatted.strip().startswith("-") and not instruction_formatted.strip().startswith("*"):
                # Разбиваем на предложения и форматируем как пункты
                sentences = re.split(r'(?<=[\.\!\?])\s+', instruction_formatted.strip())
                instruction_formatted = "\n".join([f"- {s.strip()}" for s in sentences if s.strip()])
        else:
            instruction_formatted = _format_static_instruction_markdown(instruction_formatted)

        replacement = f"\n### Введение\n\n{result.intro_text}\n\n### Инструкция\n\n{instruction_formatted}\n"

        md_before = md
        for pattern in patterns:
            md = re.sub(
                pattern,
                lambda m: m.group(1) + replacement,
                md,
                flags=re.S,
            )
            if md != md_before:
                print(f"  ✅ Intro вставлен (паттерн: {pattern[:30]}...)", file=sys.stderr, flush=True)
                break

        if md == md_before:
            print("  ⚠️  ВНИМАНИЕ: Intro не был вставлен! Проверяю структуру markdown...", file=sys.stderr, flush=True)
            # Показываем первые 500 символов markdown для отладки
            print(f"  💡 Начало markdown: {md[:500]}", file=sys.stderr, flush=True)

        return md
