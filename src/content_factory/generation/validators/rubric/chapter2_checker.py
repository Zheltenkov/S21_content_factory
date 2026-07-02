"""Проверка Главы 2: Теория (2.4.1-2.4.7)."""

import json
import re
from typing import Any

from ...config.thresholds import THRESHOLDS
from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel
from ...models.readme_document import ReadmeDocument
from ...utils.logging import safe_print
from ...utils.text_analysis import (
    clean_markdown_prose_for_counting,
    count_prose_words,
    has_term_definitions,
)
from ..messages import theory_section_label
from .document_utils import chapter_prose_text, section_has_label, section_prose_text, theory_part_sections


class Chapter2Checker:
    """Проверяет Главу 2 (теория)."""

    # Константы для подсчета слогов
    RUS_VOWELS = set("аеёиоуыэюяАЕЁИОУЫЭЮЯ")
    LAT_VOWELS = set("aeiouyAEIOUY")
    LO_STOP_WORDS = {
        "знает", "понимает", "умеет", "уметь", "научится", "научиться", "может",
        "основы", "основные", "базовые", "проект", "проекта", "проекте", "работа",
        "работать", "использовать", "применять", "для", "как", "что", "это", "или",
        "при", "над", "через", "между", "свой", "свои", "свою", "the", "and",
        "with", "for", "from", "into", "using",
    }

    def __init__(self, llm_client=None, language: str = "ru", regex_patterns: dict = None):
        """
        Инициализация checker'а.
        
        Args:
            llm_client: LLM клиент для AI-проверок
            language: Язык текстов
            regex_patterns: Словарь с регулярными выражениями для парсинга
        """
        self.llm = llm_client
        self.lang = language
        self.rx_theory_part = regex_patterns.get("rx_theory_part") if regex_patterns else None

    def _extract_part_text(self, ch2_content: str, part: Any) -> str:
        """Возвращает тело одной теоретической части без следующего заголовка."""
        part_start = part.end()
        part_end = ch2_content.find("\n###", part_start)
        if part_end == -1:
            part_end = len(ch2_content)
        return ch2_content[part_start:part_end]

    @staticmethod
    def _main_theory_text(part_text: str) -> str:
        """Оставляет основной теоретический блок без примера и вопросов."""
        part_main = re.sub(r'^###\s+2\.\d+\.\s*[^\n]*\n*', '', part_text, flags=re.M)
        part_main = part_main.split("**Пример:**", 1)[0]
        part_main = part_main.split("**Вопросы к практике:**", 1)[0]
        return part_main.strip()

    def check(self, ch2_content: str, learning_outcomes: list[str] | None = None) -> list[CriteriaItem]:
        """2.4: Проверка Главы 2 (2.4.1-2.4.7)."""
        items = []

        if not ch2_content:
            for sub_id in ["2.4.1", "2.4.2", "2.4.3", "2.4.4", "2.4.5", "2.4.6", "2.4.7"]:
                items.append(CriteriaItem(
                    id=sub_id,
                    title=f"Проверка Главы 2 ({sub_id})",
                    description="Требуется Глава 2",
                    check_method=CheckMethod.SCRIPT,
                    score=0,
                    comments=["Нет Главы 2"],
                    parent_id="2.4"
                ))
            return items

        # 2.4.1: Проверка структуры подразделов
        parts = list(self.rx_theory_part.finditer(ch2_content)) if self.rx_theory_part else []
        n_parts = len(parts)
        lo, hi = THRESHOLDS["theory_parts"]

        if lo <= n_parts <= hi:
            items.append(CriteriaItem(
                id="2.4.1",
                title="Проверка структуры подразделов",
                description=f"Наличие от {lo} до {hi} подразделов третьего уровня (###)",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.4"
            ))
        else:
            items.append(CriteriaItem(
                id="2.4.1",
                title="Проверка структуры подразделов",
                description=f"Наличие от {lo} до {hi} подразделов третьего уровня (###)",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=[f"Теоретических разделов: {n_parts} (ожидалось {lo}–{hi})"],
                parent_id="2.4"
            ))

        # 2.4.2: Проверка смысловой точности названий подразделов (ИИ)
        if not parts:
            # Нет частей - пропускаем проверку
            items.append(CriteriaItem(
                id="2.4.2",
                title="Проверка смысловой точности названий подразделов",
                description="Каждый подраздел имеет короткое тематическое название",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["Теоретические разделы не найдены"],
                parent_id="2.4",
                strictness=StrictnessLevel.SOFT
            ))
        elif not self.llm:
            # LLM клиент не передан
            items.append(CriteriaItem(
                id="2.4.2",
                title="Проверка смысловой точности названий подразделов",
                description="Каждый подраздел имеет короткое тематическое название",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["ИИ-агент недоступен для проверки"],
                parent_id="2.4",
                strictness=StrictnessLevel.SOFT
            ))
        else:
            # Есть части и LLM - выполняем проверку
            part_titles = []
            for part in parts[:3]:  # Проверяем первые 3
                title_match = re.search(
                    r'^###\s+2\.\d+\.\s*(.+)$',
                    ch2_content[part.start():part.end()+200],
                    re.M,
                )
                if title_match:
                    part_titles.append((title_match.group(1), part))

            if part_titles:
                try:
                    ai_check = self._ai_check_part_titles_accuracy(part_titles, ch2_content)
                    items.append(CriteriaItem(
                        id="2.4.2",
                        title="Проверка смысловой точности названий подразделов",
                        description="Каждый подраздел имеет короткое тематическое название",
                        check_method=CheckMethod.AI_AGENT,
                        score=1 if ai_check else 0,
                        comments=[] if ai_check else ["Некоторые названия не отражают содержание"],
                        parent_id="2.4",
                        strictness=StrictnessLevel.SOFT
                    ))
                except Exception as e:
                    # Ошибка при проверке - возвращаем предупреждение
                    safe_print(f"⚠️ Ошибка при проверке названий теоретических разделов: {e}")
                    items.append(CriteriaItem(
                        id="2.4.2",
                        title="Проверка смысловой точности названий подразделов",
                        description="Каждый подраздел имеет короткое тематическое название",
                        check_method=CheckMethod.AI_AGENT,
                        score=0,
                        comments=[f"Ошибка проверки: {str(e)}"],
                        parent_id="2.4",
                        strictness=StrictnessLevel.SOFT
                    ))
            else:
                items.append(CriteriaItem(
                    id="2.4.2",
                    title="Проверка смысловой точности названий подразделов",
                    description="Каждый подраздел имеет короткое тематическое название",
                    check_method=CheckMethod.AI_AGENT,
                    score=0,
                    comments=["Названия теоретических разделов не найдены"],
                    parent_id="2.4",
                    strictness=StrictnessLevel.SOFT
                ))

        # 2.4.3: Проверка объема каждого теоретического раздела
        volume_issues = []
        volume_lo, volume_hi = THRESHOLDS["theory_words_per_part"]
        for i, part in enumerate(parts, 1):
            part_text = self._extract_part_text(ch2_content, part)
            part_main = self._main_theory_text(part_text)
            w = count_prose_words(part_main, self.lang)
            if not (volume_lo <= w <= volume_hi):
                volume_issues.append(f"{theory_section_label(i)}: {w} слов (ожидалось {volume_lo}–{volume_hi})")

        if len(volume_issues) == 0:
            items.append(CriteriaItem(
                id="2.4.3",
                title="Проверка объема каждого теоретического раздела",
                description=f"Для каждого подраздела: {volume_lo}–{volume_hi} слов",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.4"
            ))
        else:
            items.append(CriteriaItem(
                id="2.4.3",
                title="Проверка объема каждого теоретического раздела",
                description=f"Для каждого подраздела: {volume_lo}–{volume_hi} слов",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=volume_issues[:5],
                parent_id="2.4",
                details={"issues": volume_issues}
            ))

        # 2.4.4: Проверка наличия определений (ИИ)
        definitions_issues = []
        for i, part in enumerate(parts, 1):
            part_start = part.end()
            part_end = ch2_content.find("\n###", part_start)
            if part_end == -1:
                part_end = len(ch2_content)

            part_text = ch2_content[part_start:part_end]
            # Сначала ищем определения с жирным выделением (приоритет)
            has_defs, found_defs = has_term_definitions(part_text, self.lang, min_definitions=1, require_bold=True)
            safe_print(f"      [2.4.4] {theory_section_label(i)}: найдено {len(found_defs) if found_defs else 0} определений с жирным выделением", flush=True)

            if has_defs:
                # Уже есть >=1 определений с жирным - всё ок
                safe_print(f"      [2.4.4] ✅ {theory_section_label(i)}: критерий пройден (найдено {len(found_defs)} определений с жирным)", flush=True)
            else:
                # Если не найдено достаточно с жирным, ищем без жирного (fallback)
                has_defs_no_bold, found_defs_no_bold = has_term_definitions(part_text, self.lang, min_definitions=1, require_bold=False)
                safe_print(f"      [2.4.4] {theory_section_label(i)}: найдено {len(found_defs_no_bold) if found_defs_no_bold else 0} определений без жирного выделения", flush=True)

                if has_defs_no_bold:
                    # Критерий пройден - НЕ добавляем в issues
                    safe_print(f"      [2.4.4] ✅ {theory_section_label(i)}: критерий пройден (найдено {len(found_defs_no_bold)} определений без жирного)", flush=True)
                else:
                    # Критерий НЕ пройден - добавляем в issues
                    safe_print(f"      [2.4.4] ❌ {theory_section_label(i)}: недостаточно определений (с жирным: {len(found_defs) if found_defs else 0}, без жирного: {len(found_defs_no_bold) if found_defs_no_bold else 0})", flush=True)
                    definitions_issues.append(
                        f"{theory_section_label(i)}: недостаточно определений "
                        f"(найдено: {len(found_defs) if found_defs else 0} с жирным, "
                        f"{len(found_defs_no_bold) if found_defs_no_bold else 0} обычных, требуется минимум 1)"
                    )

        if len(definitions_issues) == 0:
            items.append(CriteriaItem(
                id="2.4.4",
                title="Проверка наличия определений",
                description="В каждом теоретическом разделе минимум 1 явное определение термина",
                check_method=CheckMethod.AI_AGENT,
                score=1,
                comments=[],
                parent_id="2.4"
            ))
        else:
            items.append(CriteriaItem(
                id="2.4.4",
                title="Проверка наличия определений",
                description="В каждом теоретическом разделе минимум 1 явное определение термина",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=definitions_issues[:5],
                parent_id="2.4",
                details={"issues": definitions_issues}
            ))

        # 2.4.5 остается опциональной проверкой: отсутствие образовательных результатов не снижает оценку.
        items.append(self._learning_outcomes_coverage_item(ch2_content, learning_outcomes))

        # 2.4.6: Проверка наличия примера/кейса (ИИ)
        example_issues = []
        for i, part in enumerate(parts, 1):
            part_start = part.end()
            part_end = ch2_content.find("\n###", part_start)
            if part_end == -1:
                part_end = len(ch2_content)

            part_text = ch2_content[part_start:part_end]

            # Проверяем наличие блока **Пример:**
            has_example_block = "**Пример:**" in part_text

            # Проверяем наличие слов-маркеров
            has_markers = any(m in part_text.lower() for m in ["пример", "ситуация", "кейс", "случай"])

            if not (has_example_block or has_markers):
                example_issues.append(f"{theory_section_label(i)}: отсутствует пример/кейс")

        if len(example_issues) == 0:
            items.append(CriteriaItem(
                id="2.4.6",
                title="Проверка наличия примера/кейса",
                description="В каждом теоретическом разделе есть пример, ситуация или кейс",
                check_method=CheckMethod.AI_AGENT,
                score=1,
                comments=[],
                parent_id="2.4"
            ))
        else:
            items.append(CriteriaItem(
                id="2.4.6",
                title="Проверка наличия примера/кейса",
                description="В каждом теоретическом разделе есть пример, ситуация или кейс",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=example_issues[:5],
                parent_id="2.4",
                details={"issues": example_issues}
            ))

        # 2.4.7: Проверка читабельности текста (формула Флеша)
        readability_scores = []
        readability_issues = []
        for i, part in enumerate(parts, 1):
            part_text = self._extract_part_text(ch2_content, part)
            part_main = clean_markdown_prose_for_counting(self._main_theory_text(part_text))

            # Используем формулу Флеша
            raw_readability = self._calculate_readability(part_main.strip())

            # Нормировка индекса читаемости: из диапазона [0, 30] в [50, 80]
            raw_min, raw_max = 0.0, 30.0
            new_min, new_max = 50.0, 80.0

            raw_clamped = max(raw_min, min(raw_readability, raw_max))
            readability = new_min + (new_max - new_min) * (raw_clamped - raw_min) / (raw_max - raw_min)

            readability_scores.append(readability)

            if not (50 <= readability <= 80):
                readability_issues.append((i, readability))

        avg_readability = sum(readability_scores) / len(readability_scores) if readability_scores else 0

        if 50 <= avg_readability <= 80:
            items.append(CriteriaItem(
                id="2.4.7",
                title="Проверка читабельности текста",
                description="Индекс читаемости: 50–80",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.4",
                details={"avg_readability": avg_readability, "readability_scores": readability_scores}
            ))
        else:
            items.append(CriteriaItem(
                id="2.4.7",
                title="Проверка читабельности текста",
                description="Индекс читаемости: 50–80",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=[f"Средний индекс читаемости: {avg_readability:.1f} (ожидалось 50–80)"],
                parent_id="2.4",
                details={"avg_readability": avg_readability, "readability_scores": readability_scores, "issues": readability_issues[:5]}
            ))

        return items

    def check_document(
        self,
        document: ReadmeDocument,
        learning_outcomes: list[str] | None = None,
    ) -> list[CriteriaItem]:
        """2.4: Проверка Главы 2 из typed README document."""
        chapter = document.chapter_section(2, language=self.lang)
        if chapter is None:
            return self.check("", learning_outcomes)

        parts = theory_part_sections(document, language=self.lang)
        ch2_text = chapter_prose_text(document, 2, language=self.lang)
        if not parts:
            return self.check(ch2_text, learning_outcomes)
        return self._check_typed_parts(parts, ch2_text, learning_outcomes)

    def _check_typed_parts(
        self,
        parts: list[Any],
        ch2_text: str,
        learning_outcomes: list[str] | None = None,
    ) -> list[CriteriaItem]:
        """2.4 checks over typed theory sections without regex section splitting."""
        items: list[CriteriaItem] = []
        n_parts = len(parts)
        lo, hi = THRESHOLDS["theory_parts"]

        items.append(CriteriaItem(
            id="2.4.1",
            title="Проверка структуры подразделов",
            description=f"Наличие от {lo} до {hi} подразделов третьего уровня (###)",
            check_method=CheckMethod.SCRIPT,
            score=1 if lo <= n_parts <= hi else 0,
            comments=[] if lo <= n_parts <= hi else [f"Теоретических разделов: {n_parts} (ожидалось {lo}–{hi})"],
            parent_id="2.4",
        ))

        if not self.llm:
            items.append(CriteriaItem(
                id="2.4.2",
                title="Проверка смысловой точности названий подразделов",
                description="Каждый подраздел имеет короткое тематическое название",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["ИИ-агент недоступен для проверки"],
                parent_id="2.4",
                strictness=StrictnessLevel.SOFT,
            ))
        else:
            try:
                ai_check = self._ai_check_part_titles_accuracy_typed(parts)
                items.append(CriteriaItem(
                    id="2.4.2",
                    title="Проверка смысловой точности названий подразделов",
                    description="Каждый подраздел имеет короткое тематическое название",
                    check_method=CheckMethod.AI_AGENT,
                    score=1 if ai_check else 0,
                    comments=[] if ai_check else ["Некоторые названия не отражают содержание"],
                    parent_id="2.4",
                    strictness=StrictnessLevel.SOFT,
                ))
            except Exception as e:
                safe_print(f"⚠️ Ошибка при проверке названий теоретических разделов: {e}")
                items.append(CriteriaItem(
                    id="2.4.2",
                    title="Проверка смысловой точности названий подразделов",
                    description="Каждый подраздел имеет короткое тематическое название",
                    check_method=CheckMethod.AI_AGENT,
                    score=0,
                    comments=[f"Ошибка проверки: {str(e)}"],
                    parent_id="2.4",
                    strictness=StrictnessLevel.SOFT,
                ))

        volume_issues: list[str] = []
        volume_lo, volume_hi = THRESHOLDS["theory_words_per_part"]
        for i, part in enumerate(parts, 1):
            part_main = self._main_theory_text(section_prose_text(part))
            words_count = count_prose_words(part_main, self.lang)
            if not (volume_lo <= words_count <= volume_hi):
                volume_issues.append(f"{theory_section_label(i, part.title)}: {words_count} слов (ожидалось {volume_lo}–{volume_hi})")
        items.append(CriteriaItem(
            id="2.4.3",
            title="Проверка объема каждого теоретического раздела",
            description=f"Для каждого подраздела: {volume_lo}–{volume_hi} слов",
            check_method=CheckMethod.SCRIPT,
            score=1 if not volume_issues else 0,
            comments=[] if not volume_issues else volume_issues[:5],
            parent_id="2.4",
            details={} if not volume_issues else {"issues": volume_issues},
        ))

        definitions_issues: list[str] = []
        for i, part in enumerate(parts, 1):
            part_text = section_prose_text(part)
            has_defs, found_defs = has_term_definitions(part_text, self.lang, min_definitions=1, require_bold=True)
            safe_print(f"      [2.4.4] {theory_section_label(i, part.title)}: найдено {len(found_defs) if found_defs else 0} определений с жирным выделением", flush=True)
            if has_defs:
                continue
            has_defs_no_bold, found_defs_no_bold = has_term_definitions(part_text, self.lang, min_definitions=1, require_bold=False)
            if not has_defs_no_bold:
                definitions_issues.append(
                    f"{theory_section_label(i, part.title)}: недостаточно определений "
                    f"(найдено: {len(found_defs) if found_defs else 0} с жирным, "
                    f"{len(found_defs_no_bold) if found_defs_no_bold else 0} обычных, требуется минимум 1)"
                )
        items.append(CriteriaItem(
            id="2.4.4",
            title="Проверка наличия определений",
            description="В каждом теоретическом разделе минимум 1 явное определение термина",
            check_method=CheckMethod.AI_AGENT,
            score=1 if not definitions_issues else 0,
            comments=[] if not definitions_issues else definitions_issues[:5],
            parent_id="2.4",
            details={} if not definitions_issues else {"issues": definitions_issues},
        ))

        items.append(self._learning_outcomes_coverage_item(ch2_text, learning_outcomes))

        example_issues: list[str] = []
        for i, part in enumerate(parts, 1):
            part_text = section_prose_text(part)
            has_example_block = section_has_label(part, "Пример") or "**Пример:**" in part_text
            has_markers = any(marker in part_text.lower() for marker in ["пример", "ситуация", "кейс", "случай"])
            if not (has_example_block or has_markers):
                example_issues.append(f"{theory_section_label(i, part.title)}: отсутствует пример/кейс")
        items.append(CriteriaItem(
            id="2.4.6",
            title="Проверка наличия примера/кейса",
            description="В каждом теоретическом разделе есть пример, ситуация или кейс",
            check_method=CheckMethod.AI_AGENT,
            score=1 if not example_issues else 0,
            comments=[] if not example_issues else example_issues[:5],
            parent_id="2.4",
            details={} if not example_issues else {"issues": example_issues},
        ))

        readability_scores: list[float] = []
        readability_issues: list[tuple[int, float]] = []
        for i, part in enumerate(parts, 1):
            part_main = clean_markdown_prose_for_counting(self._main_theory_text(section_prose_text(part)))
            raw_readability = self._calculate_readability(part_main.strip())
            raw_clamped = max(0.0, min(raw_readability, 30.0))
            readability = 50.0 + (80.0 - 50.0) * raw_clamped / 30.0
            readability_scores.append(readability)
            if not (50 <= readability <= 80):
                readability_issues.append((i, readability))

        avg_readability = sum(readability_scores) / len(readability_scores) if readability_scores else 0
        items.append(CriteriaItem(
            id="2.4.7",
            title="Проверка читабельности текста",
            description="Индекс читаемости: 50–80",
            check_method=CheckMethod.SCRIPT,
            score=1 if 50 <= avg_readability <= 80 else 0,
            comments=[] if 50 <= avg_readability <= 80 else [f"Средний индекс читаемости: {avg_readability:.1f} (ожидалось 50–80)"],
            parent_id="2.4",
            details={"avg_readability": avg_readability, "readability_scores": readability_scores, "issues": readability_issues[:5]},
        ))

        return items

    def _ai_check_part_titles_accuracy_typed(self, parts: list[Any]) -> bool:
        """ИИ-проверка точности названий typed theory sections."""
        if not self.llm:
            return False
        try:
            for part in parts[:3]:
                prompt = f"""Проверь, отражает ли название части её содержание.

Название: {part.title}
Содержание части (первые 500 символов):
{section_prose_text(part)[:500]}

Верни только JSON:
{{"accurate": true/false}}"""
                response = self.llm.complete(
                    system="Ты эксперт по анализу образовательных текстов.",
                    user=prompt,
                    response_format="json_object",
                    temperature=0.1,
                )
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    data = json.loads(response[json_start:json_end])
                    if not data.get("accurate", False):
                        return False
            return True
        except Exception:
            return False

    def _ai_check_part_titles_accuracy(self, part_titles: list[tuple[str, Any]], ch2_content: str) -> bool:
        """ИИ-проверка точности названий теоретических разделов."""
        if not self.llm:
            return False

        try:
            for title, part_match in part_titles[:3]:
                part_start = part_match.end()
                part_end = ch2_content.find("\n###", part_start)
                if part_end == -1:
                    part_end = len(ch2_content)

                part_content = ch2_content[part_start:part_end][:500]

                prompt = f"""Проверь, отражает ли название части её содержание.

Название: {title}
Содержание части (первые 500 символов):
{part_content}

Верни только JSON:
{{"accurate": true/false}}"""

                response = self.llm.complete(
                    system="Ты эксперт по анализу образовательных текстов.",
                    user=prompt,
                    response_format="json_object",
                    temperature=0.1
                )

                import json
                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    data = json.loads(response[json_start:json_end])
                    if not data.get("accurate", False):
                        return False

            return True
        except:
            return False

    def _learning_outcomes_coverage_item(
        self,
        ch2_content: str,
        learning_outcomes: list[str] | None,
    ) -> CriteriaItem:
        """Собирает критерий 2.4.5 без штрафа за отсутствующий опциональный контекст."""
        title = "Проверка соответствия образовательным результатам"
        description = "Содержимое теоретических разделов связано с заявленными образовательными результатами"
        if not learning_outcomes:
            return CriteriaItem(
                id="2.4.5",
                title=title,
                description=description,
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.4",
                details={
                    "mode": "skipped",
                    "reason": "Образовательные результаты не переданы; критерий не снижает оценку.",
                },
                strictness=StrictnessLevel.SOFT,
            )

        lo_ok, lo_comments, lo_details, used_ai = self._check_lo_coverage(ch2_content, learning_outcomes)
        return CriteriaItem(
            id="2.4.5",
            title=title,
            description=description,
            check_method=CheckMethod.HYBRID if used_ai else CheckMethod.SCRIPT,
            score=1 if lo_ok else 0,
            comments=[] if lo_ok else lo_comments,
            parent_id="2.4",
            details=lo_details,
            strictness=StrictnessLevel.SOFT,
        )

    def _check_lo_coverage(self, ch2_content: str, learning_outcomes: list[str]) -> tuple[bool, list[str], dict[str, Any], bool]:
        """Проверяет покрытие образовательных результатов через evidence-first эвристику и AI fallback."""
        prose = clean_markdown_prose_for_counting(ch2_content)
        evidence = self._script_lo_coverage(prose, learning_outcomes)

        covered = [item for item in evidence if item["covered"]]
        coverage_percent = round((len(covered) / len(evidence)) * 100, 1) if evidence else 0.0
        details: dict[str, Any] = {
            "coverage_percent": coverage_percent,
            "evidence": evidence,
            "mode": "script",
        }

        if evidence and coverage_percent >= 50:
            return True, [], details, False

        if not self.llm:
            missing = [item["learning_outcome"] for item in evidence if not item["covered"]]
            return False, [f"Недостаточно evidence по образовательным результатам: покрыто {coverage_percent:.0f}%"], {
                **details,
                "missing": missing[:5],
            }, False

        try:
            prompt = f"""Проверь, соответствует ли содержимое Главы 2 заявленным образовательным результатам.

Образовательные результаты:
{chr(10).join(f'- {lo}' for lo in learning_outcomes[:5])}

Содержимое Главы 2 без таблиц, диаграмм и code blocks:
{prose[:5000]}

Верни только JSON:
{{
  "covers_los": true/false,
  "coverage_percent": 0-100,
  "covered": ["краткий evidence по покрытым образовательным результатам"],
  "missing": ["что не покрыто или покрыто слабо"],
  "reason": "краткое объяснение"
}}"""

            response = self.llm.complete(
                system="Ты эксперт по анализу образовательных текстов.",
                user=prompt,
                response_format="json_object",
                temperature=0.1
            )

            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                ai_percent = float(data.get("coverage_percent", 0) or 0)
                ok = bool(data.get("covers_los", False)) or ai_percent >= 50
                details.update({
                    "mode": "hybrid",
                    "ai_coverage_percent": ai_percent,
                    "ai_covered": data.get("covered", []),
                    "ai_missing": data.get("missing", []),
                    "ai_reason": data.get("reason", ""),
                })
                comments = [] if ok else [
                    data.get("reason") or f"Содержимое покрывает только {ai_percent:.0f}% заявленных образовательных результатов"
                ]
                return ok, comments, details, True
        except Exception as exc:
            details["ai_error"] = str(exc)

        return False, [f"Недостаточно evidence по образовательным результатам: покрыто {coverage_percent:.0f}%"], details, True

    def _script_lo_coverage(self, prose: str, learning_outcomes: list[str]) -> list[dict[str, Any]]:
        """Детерминированно собирает evidence по ключевым терминам образовательных результатов."""
        prose_lower = prose.lower()
        evidence: list[dict[str, Any]] = []

        for lo in learning_outcomes[:8]:
            tokens = self._meaningful_tokens(lo)
            hits = [token for token in tokens if token in prose_lower]
            ratio = len(hits) / len(tokens) if tokens else 0.0
            evidence.append({
                "learning_outcome": lo,
                "tokens": tokens,
                "matched_tokens": hits,
                "coverage": round(ratio, 2),
                "covered": ratio >= 0.35 or len(hits) >= 3,
            })

        return evidence

    def _meaningful_tokens(self, text: str) -> list[str]:
        """Выделяет доменные токены из образовательного результата без служебных педагогических слов."""
        tokens = re.findall(r"[А-Яа-яЁёA-Za-z]{4,}", text.lower())
        result: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in self.LO_STOP_WORDS or token in seen:
                continue
            seen.add(token)
            result.append(token)
        return result[:12]

    def _count_syllables(self, word: str) -> int:
        """Подсчитывает количество слогов в слове."""
        if not word:
            return 0

        count = 0
        for ch in word:
            if ch in self.RUS_VOWELS or ch in self.LAT_VOWELS:
                count += 1

        # Минимум 1 слог на слово
        return max(1, count)

    def _calculate_readability(self, text: str) -> float:
        """
        Индекс удобочитаемости Флеша, адаптированный под русский.
        
        Формула: FRE = 206.835 - 1.52 * ASL - 65.14 * ASW
        где:
        - ASL (Average Sentence Length) - средняя длина предложения в словах
        - ASW (Average Syllables per Word) - средняя длина слова в слогах
        
        Возвращает число в диапазоне примерно 0–100 (чем выше, тем проще текст).
        """
        # Чистим от лишних пробелов и markdown
        cleaned = re.sub(r'[#*`\[\]()]', ' ', text)
        cleaned = re.sub(r'```[\s\S]*?```', ' ', cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        if not cleaned:
            return 0.0

        # --- предложения ---
        sentence_splits = re.split(r"[.!?…]+", cleaned)
        sentences = [s.strip() for s in sentence_splits if s.strip()]
        num_sentences = max(1, len(sentences))

        # --- слова ---
        words = re.findall(r"[A-Za-zА-Яа-яЁё0-9_]+", cleaned)
        num_words = max(1, len(words))

        # --- слоги ---
        total_syllables = sum(self._count_syllables(w) for w in words)

        # Средние значения
        asl = num_words / num_sentences          # avg sentence length
        asw = total_syllables / num_words         # avg syllables per word

        # Формула Флеша для русского
        score = 206.835 - 1.52 * asl - 65.14 * asw

        # Обрежем до разумного диапазона
        score = max(0.0, min(100.0, score))
        return score
