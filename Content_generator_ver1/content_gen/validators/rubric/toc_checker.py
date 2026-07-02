"""Проверка оглавления (2.2.1-2.2.4)."""

import json
import re

from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel
from ...models.readme_document import ReadmeDocument, ReadmeSection
from ...utils.logging import safe_print
from .document_utils import section_prose_text, toc_section
from .utils import semantic_similarity as _semantic_similarity


def is_service_heading(h: str) -> bool:
    """Проверяет, является ли заголовок служебным (Аннотация, Содержание и т.п.)."""
    h_low = h.lower()
    return any(x in h_low for x in [
        "аннотация", "содержание", "оглавление",
        "план", "структура документа", "content", "toc", "table of contents"
    ])


def normalize_heading_for_search(h: str) -> str:
    """Нормализует заголовок из TOC для поиска в тексте.
    
    Убирает канонические префиксы типа "Глава 1." и ведущие номера.
    """
    h = h.strip()
    h = re.sub(r'^(Глава|Chapter)\s+\d+\.?\s*', '', h, flags=re.I)
    # Убираем ведущие номера "1.", "2.1." и т.п.
    h = re.sub(r'^\d+(\.\d+)*\.?\s*', '', h)
    return h.strip()


class TOCChecker:
    """Проверяет оглавление проекта."""

    def __init__(self, llm_client=None, embedding_function=None, language: str = "ru", regex_patterns: dict = None):
        """
        Инициализация checker'а.
        
        Args:
            llm_client: LLM клиент для AI-проверок
            embedding_function: Функция для создания эмбеддингов
            language: Язык текстов
            regex_patterns: Словарь с регулярными выражениями для парсинга
        """
        self.llm = llm_client
        self.embedding_function = embedding_function
        self.lang = language
        self.rx_h2 = regex_patterns.get("rx_h2") if regex_patterns else None
        self.rx_h3 = regex_patterns.get("rx_h3") if regex_patterns else None
        self.rx_toc = regex_patterns.get("rx_toc") if regex_patterns else None

    def _ai_check_heading_accuracy(self, headings: list[str], md: str) -> tuple[bool, list[str]]:
        """Гибридная проверка смысловой точности заголовков: SBERT + LLM.

        Сначала проверяет через семантическое сходство (SBERT), затем через LLM при необходимости.
        Возвращает (is_passed, comments), где:
        - is_passed: True если хотя бы один заголовок точный (для SOFT-критерия)
        - comments: список детальных комментариев по каждому заголовку
        """
        try:
            accurate_count = 0
            total_checked = 0
            detailed_comments = []

            # Порог семантического сходства для автоматического прохождения (смягчен)
            SEMANTIC_THRESHOLD = 0.18  # Если сходство >= 0.18, считаем заголовок точным без LLM
            # Порог для дополнительной LLM-проверки (если сходство между 0.10 и 0.18)
            LLM_CHECK_THRESHOLD = 0.10  # Снижен для большей толерантности

            # Whitelist общих заголовков, которые допустимы независимо от длины раздела
            GENERIC_HEADINGS = [
                "введение", "инструкция", "теория", "основные понятия",
                "обзор", "заключение", "выводы", "практика",
                "задачи", "примеры", "дополнительно", "общая информация"
            ]

            # Проверяем до 5 заголовков из оглавления
            for heading in headings[:5]:
                heading = heading.strip()
                if not heading:
                    continue

                # Пропускаем служебные заголовки (не проверяем их)
                if is_service_heading(heading):
                    safe_print(f"      [2.2.4] ⏭️ Заголовок '{heading[:50]}' служебный, пропускаем", flush=True)
                    continue

                # Нормализуем заголовок для поиска (убираем "Глава 1." и ведущие номера).
                normalized_heading = normalize_heading_for_search(heading)

                # Находим соответствующий раздел по нормализованному заголовку
                # Сначала пробуем точное совпадение с нормализованным заголовком
                section_match = re.search(rf'^###\s+{re.escape(normalized_heading)}\s*$', md, re.M | re.I)
                if not section_match:
                    section_match = re.search(rf'^##\s+{re.escape(normalized_heading)}\s*$', md, re.M | re.I)

                # Если не нашли по нормализованному, пробуем по оригинальному заголовку (с номером)
                if not section_match:
                    # Ищем оригинальный заголовок с номером (например, "Глава 2. Теория")
                    heading_escaped_orig = re.escape(heading.strip())
                    heading_clean_orig = heading_escaped_orig.replace(r'\ ', r'\s+')
                    section_match = re.search(rf'^###\s+{heading_clean_orig}', md, re.M | re.I)
                    if not section_match:
                        section_match = re.search(rf'^##\s+{heading_clean_orig}', md, re.M | re.I)

                # Если не нашли точное совпадение, пробуем более гибкий поиск (частичное совпадение)
                if not section_match:
                    # Экранируем специальные символы нормализованного заголовка
                    heading_escaped = re.escape(normalized_heading.strip())
                    # re.escape экранирует пробелы как \ , заменяем их на гибкий паттерн \s+
                    heading_clean = heading_escaped.replace(r'\ ', r'\s+')
                    section_match = re.search(rf'^###\s+{heading_clean}', md, re.M | re.I)
                    if not section_match:
                        section_match = re.search(rf'^##\s+{heading_clean}', md, re.M | re.I)

                # Если все еще не нашли, пробуем найти по ключевым словам
                if not section_match:
                    # Извлекаем ключевые слова из нормализованного заголовка (первые 2-3 слова)
                    heading_words = normalized_heading.split()[:3]
                    if len(heading_words) >= 2:
                        # Ищем заголовок, который содержит эти ключевые слова
                        pattern = rf'^##\s+.*?{re.escape(" ".join(heading_words[:2]))}.*?$'
                        section_match = re.search(pattern, md, re.M | re.I)

                    # Если не нашли по первым словам нормализованного, пробуем по оригинальному
                    if not section_match and len(heading_words) >= 2:
                        # Извлекаем ключевые слова из оригинального заголовка (после номера)
                        orig_words = heading.strip().split()
                        if len(orig_words) >= 3 and orig_words[0].lower() in ['глава', 'chapter']:
                            orig_words = orig_words[2:]  # Пропускаем "Глава" и номер
                        if len(orig_words) >= 2:
                            pattern = rf'^##\s+.*?{re.escape(" ".join(orig_words[:2]))}.*?$'
                            section_match = re.search(pattern, md, re.M | re.I)

                if not section_match:
                    safe_print(f"      [2.2.4] ⚠️ Раздел по заголовку '{heading[:50]}' не найден в документе", flush=True)
                    # Раздел не найден - не считаем это ошибкой, просто пропускаем (не увеличиваем счетчики)
                    detailed_comments.append(f"Заголовок \"{heading}\": раздел не найден в документе")
                    continue

                section_start = section_match.end()

                # Пропускаем пустые строки и пробелы после заголовка, чтобы начать с реального контента
                # Ищем первую непустую строку после заголовка
                content_start = section_start
                # Пропускаем пробелы, табы и переносы строк
                while content_start < len(md) and md[content_start] in ['\n', '\r', ' ', '\t']:
                    content_start += 1

                # Проверяем, что после пропуска пустых строк есть контент
                # Если сразу идет следующий заголовок уровня 2 (##) или уровня 1 (#), раздел пустой
                next_chars = md[content_start:content_start+3] if content_start < len(md) else ""
                if next_chars == "##" or (next_chars.startswith("#") and md[content_start:content_start+2] == "# "):
                    safe_print(f"      [2.2.4] ⚠️ DEBUG: После заголовка '{heading[:50]}' сразу идет следующий заголовок", flush=True)
                    section_content = ""
                else:
                    # Ищем следующий заголовок уровня 2 (##) - это граница раздела
                    # Важно: ищем "\n##" чтобы не захватить подзаголовки уровня 3 (###)
                    # Но начинаем поиск не с section_start, а с content_start, чтобы не пропустить контент
                    section_end = md.find("\n##", content_start)

                    # Если не нашли заголовок уровня 2, ищем заголовок уровня 1 (#)
                    if section_end == -1:
                        section_end = md.find("\n# ", content_start)

                    # Если ничего не найдено, берем до конца документа
                    if section_end == -1:
                        section_end = len(md)

                    # Убеждаемся, что section_end больше content_start
                    if section_end <= content_start:
                        # Если section_end равен или меньше content_start, значит раздел действительно пустой
                        safe_print(f"      [2.2.4] ⚠️ DEBUG: section_end ({section_end}) <= content_start ({content_start}), раздел пустой", flush=True)
                        section_content = ""
                    else:
                        # Берём весь блок раздела для полного анализа
                        section_content = md[content_start:section_end].strip()

                # Проверяем, что раздел не пустой
                if not section_content:
                    safe_print(f"      [2.2.4] ⚠️ Заголовок '{heading[:50]}': раздел пустой, пропускаем проверку", flush=True)
                    # Раздел пустой - не считаем это ошибкой, просто пропускаем (не увеличиваем счетчики)
                    detailed_comments.append(f"Заголовок \"{heading}\": раздел пустой, нет содержания")
                    continue

                # Проверка для общих заголовков (whitelist) - считаем точными независимо от длины раздела
                section_length = len(section_content)
                if any(g in normalized_heading.lower() for g in GENERIC_HEADINGS):
                    safe_print(f"      [2.2.4] ✅ Заголовок '{heading[:50]}': общий заголовок из whitelist, считаем точным (раздел: {section_length} символов)", flush=True)
                    heading_accurate = True
                    total_checked += 1
                    accurate_count += 1
                    continue

                # Проверяем, что заголовок не пустой
                if not heading or not heading.strip():
                    safe_print("      [2.2.4] ⚠️ Заголовок пустой, пропускаем проверку", flush=True)
                    continue

                # ШАГ 1: Проверка через семантическое сходство (SBERT)
                semantic_score = 0.0
                if self.embedding_function:
                    try:
                        # Используем полное содержание раздела для более точной проверки
                        # Если раздел очень длинный (>2000 символов), берем первые 2000 символов для производительности
                        # но это все равно значительно больше, чем 500 символов
                        content_for_sbert = section_content[:2000] if len(section_content) > 2000 else section_content

                        # Дополнительная проверка: убеждаемся, что оба текста не пустые
                        if not content_for_sbert or not heading.strip():
                            safe_print("      [2.2.4] ⚠️ Заголовок или содержание пустые, пропускаем SBERT", flush=True)
                            semantic_score = 0.0
                        else:
                            semantic_score = _semantic_similarity(
                                heading.strip(),
                                content_for_sbert,
                                lang=self.lang,
                                embedding_function=self.embedding_function
                            )
                            safe_print(f"      [2.2.4] Заголовок '{heading[:50]}': семантическое сходство = {semantic_score:.3f} (проверено {len(content_for_sbert)} символов из {len(section_content)})", flush=True)
                    except Exception as e:
                        safe_print(f"      [2.2.4] Ошибка SBERT для '{heading[:50]}': {e}", flush=True)
                        semantic_score = 0.0

                # ШАГ 2: Принятие решения на основе семантического сходства
                heading_accurate = None
                llm_reason = None

                if semantic_score >= SEMANTIC_THRESHOLD:
                    # Высокое сходство — заголовок точный, пропускаем LLM-проверку
                    heading_accurate = True
                    total_checked += 1
                    accurate_count += 1
                    safe_print(f"      [2.2.4] ✅ Заголовок '{heading[:50]}': высокое семантическое сходство ({semantic_score:.3f} >= {SEMANTIC_THRESHOLD})", flush=True)
                    continue
                elif semantic_score >= LLM_CHECK_THRESHOLD and self.llm:
                    # Среднее сходство — дополнительная проверка через LLM
                    pass  # Продолжаем к LLM-проверке ниже
                elif semantic_score < LLM_CHECK_THRESHOLD:
                    # Низкое сходство — заголовок неточный
                    heading_accurate = False
                    total_checked += 1
                    accurate_count += 0  # Низкое сходство = неточный заголовок
                    detailed_comments.append(f"Заголовок \"{heading}\": низкое семантическое сходство ({semantic_score:.3f})")
                    safe_print(f"      [2.2.4] ❌ Заголовок '{heading[:50]}': низкое семантическое сходство ({semantic_score:.3f} < {LLM_CHECK_THRESHOLD})", flush=True)
                    continue

                # ШАГ 3: Дополнительная LLM-проверка (если семантическое сходство среднее или LLM доступен)
                if not self.llm:
                    # Если LLM недоступен и семантическое сходство низкое — считаем неточным
                    if semantic_score < SEMANTIC_THRESHOLD:
                        heading_accurate = False
                    else:
                        heading_accurate = True
                    total_checked += 1
                    if heading_accurate:
                        accurate_count += 1
                    continue

                prompt = f"""
Проверь, точно ли заголовок отражает содержание раздела.

Заголовок:
{heading}

Полное содержание раздела (сравнивается ВСЯ часть целиком):
{section_content}

Семантическое сходство (SBERT, проверено {len(section_content)} символов): {semantic_score:.3f}

КРИТЕРИИ ПРОВЕРКИ (БУДЬ МАКСИМАЛЬНО ТОЛЕРАНТНЫМ И ГИБКИМ):
- Заголовок сравнивается со ВСЕЙ частью целиком, а не с отдельными фрагментами.
- Заголовок должен отражать ОСНОВНУЮ тему раздела (не обязательно все детали, достаточно общей тематики).
- Заголовок может быть общим (например, "Введение", "Общая информация", "Основные понятия", "Теоретические основы") - это ВСЕГДА допустимо для учебных материалов.
- Заголовок считается точным, если он отражает хотя бы 30% содержания раздела (снижен порог с 50%).
- Основные понятия и термины из содержания раздела должны ХОТЯ БЫ ЧАСТИЧНО соответствовать заголовку (даже косвенная связь допустима).
- Заголовок считается НЕТОЧНЫМ ТОЛЬКО если он ЯВНО вводит в заблуждение или ПОЛНОСТЬЮ не имеет отношения к содержанию (менее 20% соответствия, снижен порог с 30%).
- Общие заголовки типа "Основные понятия", "Теоретические основы", "Практические аспекты", "Введение", "Общая информация" ВСЕГДА допустимы и считаются точными.
- Даже если заголовок очень общий, но раздел относится к той же тематической области - заголовок считается точным.

ВАЖНО: Будь максимально толерантным и гибким:
- Общие заголовки допустимы и предпочтительны для учебных материалов.
- Если семантическое сходство >= 0.15, заголовок скорее всего точный.
- Принимай решение на основе ВСЕГО содержания раздела, а не отдельных фрагментов.
- Считай заголовок точным, если он хотя бы косвенно связан с содержанием раздела.
- Только явно несоответствующие заголовки (полностью другая тема) должны считаться неточными.

Верни только JSON:
{{"accurate": true/false, "reason": "краткое объяснение на 1–2 предложения"}}
""".strip()

                response = self.llm.complete(
                    system="Ты эксперт по анализу образовательных текстов. Будь максимально толерантным и гибким при оценке соответствия заголовков содержанию. Общие заголовки допустимы и предпочтительны.",
                    user=prompt,
                    response_format="json_object",
                    temperature=0.2,
                )

                json_start = response.find("{")
                json_end = response.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    data = json.loads(response[json_start:json_end])
                    heading_accurate = data.get("accurate", False)
                    llm_reason = data.get("reason", "")

                    if heading_accurate:
                        safe_print(f"      [2.2.4] ✅ Заголовок '{heading[:50]}': LLM подтвердил точность", flush=True)
                    else:
                        safe_print(f"      [2.2.4] ❌ Заголовок '{heading[:50]}': LLM определил как неточный", flush=True)
                        if llm_reason:
                            detailed_comments.append(f"Заголовок \"{heading}\": LLM считает неточным: {llm_reason}")
                        else:
                            detailed_comments.append(f"Заголовок \"{heading}\": LLM считает неточным")
                else:
                    # не смогли распарсить JSON — используем семантическое сходство как fallback
                    heading_accurate = semantic_score >= LLM_CHECK_THRESHOLD
                    if heading_accurate:
                        safe_print(f"      [2.2.4] ✅ Заголовок '{heading[:50]}': fallback на семантическое сходство ({semantic_score:.3f})", flush=True)
                    else:
                        safe_print(f"      [2.2.4] ❌ Заголовок '{heading[:50]}': fallback - низкое семантическое сходство ({semantic_score:.3f})", flush=True)
                        detailed_comments.append(f"Заголовок \"{heading}\": низкое семантическое сходство ({semantic_score:.3f})")

                total_checked += 1
                if heading_accurate:
                    accurate_count += 1

            # Смягченное правило для SOFT-критерия: провал только если нет ни одного точного заголовка
            if total_checked == 0:
                safe_print("      [2.2.4] ⚠️ Не удалось проверить ни один заголовок", flush=True)
                return False, ["Не удалось проверить соответствие заголовков содержанию"]

            if accurate_count == 0:
                safe_print(f"      [2.2.4] ❌ Итого: {accurate_count} из {total_checked} заголовков точны", flush=True)
                return False, detailed_comments if detailed_comments else ["Ни один из проверенных заголовков не отражает содержание разделов"]

            # Хотя бы один заголовок точный - критерий пройден (для SOFT)
            safe_print(f"      [2.2.4] ✅ Итого: {accurate_count} из {total_checked} заголовков точны", flush=True)

            # Формируем финальный комментарий
            final_comments = []
            if accurate_count < total_checked:
                final_comments.append(f"Часть заголовков можно уточнить: точных {accurate_count} из {total_checked}")
            # Добавляем детальные комментарии по неточным заголовкам
            final_comments.extend(detailed_comments)

            return True, final_comments

        except Exception as e:
            safe_print(f"      [2.2.4] Ошибка при проверке заголовков: {e}", flush=True)
            # На всякий случай не падаем, а считаем критерий не пройденным
            return False, [f"Ошибка при проверке заголовков: {str(e)}"]

    def check(self, md: str) -> list[CriteriaItem]:
        """2.2: Проверка оглавления (2.2.1-2.2.4)."""
        items = []

        toc_match = self.rx_toc.search(md) if self.rx_toc else None
        if not toc_match:
            # Если нет оглавления, все критерии = 0
            for sub_id in ["2.2.1", "2.2.2", "2.2.3", "2.2.4"]:
                items.append(CriteriaItem(
                    id=sub_id,
                    title=f"Проверка оглавления ({sub_id})",
                    description="Требуется оглавление",
                    check_method=CheckMethod.SCRIPT,
                    score=0,
                    comments=["Нет раздела «Содержание/Оглавление»"],
                    parent_id="2.2"
                ))
            return items

        # Извлекаем блок оглавления
        toc_start = toc_match.end()
        toc_end = md.find("\n## ", toc_start)
        if toc_end == -1:
            toc_end = len(md)
        toc_block = md[toc_start:toc_end]

        # 2.2.1: Проверка структуры уровней
        # Проверяем наличие глав и подразделов
        # Поддерживаем оба формата: маркированный список (-) и нумерованный (1.)
        has_chapter1 = bool(re.search(r'Глава\s+1|глава\s+1', toc_block, re.I))
        has_chapter2 = bool(re.search(r'Глава\s+2|глава\s+2', toc_block, re.I))
        has_chapter3 = bool(re.search(r'Глава\s+3|глава\s+3', toc_block, re.I))
        # Подразделы: поддерживаем и маркированный (-), и нумерованный (1.) для верхнего уровня
        has_top_level = bool(re.search(r'^\s*[-*]\s+\[|^\s*\d+\.\s+\[', toc_block, re.M))
        has_subsections = bool(re.search(r'^\s+[-*]', toc_block, re.M))  # Подразделы с отступом

        if has_chapter1 and has_chapter2 and has_chapter3 and has_top_level and has_subsections:
            items.append(CriteriaItem(
                id="2.2.1",
                title="Проверка структуры уровней",
                description="Оглавление содержит два уровня: Главы и подразделы",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.2"
            ))
        else:
            missing = []
            if not has_chapter1:
                missing.append("Глава 1")
            if not has_chapter2:
                missing.append("Глава 2")
            if not has_chapter3:
                missing.append("Глава 3")
            if not has_top_level:
                missing.append("верхний уровень оглавления")
            if not has_subsections:
                missing.append("подразделы")

            items.append(CriteriaItem(
                id="2.2.1",
                title="Проверка структуры уровней",
                description="Оглавление содержит два уровня: Главы и подразделы",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=[f"Отсутствуют: {', '.join(missing)}"],
                parent_id="2.2"
            ))

        # 2.2.2: Проверка корректности Markdown-ссылок
        links = re.findall(r'\[([^\]]+)\]\(#([^\)]+)\)', toc_block)
        valid_links = 0
        invalid_links = []

        for link_text, anchor in links:
            # Проверяем, что якорь соответствует заголовку в тексте
            # Нормализуем якорь (убираем дефисы, приводим к нижнему регистру)
            anchor_normalized = re.sub(r'[-\s]+', '-', anchor.lower().strip('-'))

            # Ищем заголовок в тексте
            h2_pattern = re.compile(rf'^##\s+{re.escape(link_text)}', re.M | re.I)
            h3_pattern = re.compile(rf'^###\s+{re.escape(link_text)}', re.M | re.I)

            if h2_pattern.search(md) or h3_pattern.search(md):
                valid_links += 1
            else:
                invalid_links.append(f"{link_text} → {anchor}")

        if len(links) > 0 and len(invalid_links) == 0:
            items.append(CriteriaItem(
                id="2.2.2",
                title="Проверка корректности Markdown-ссылок",
                description="Все ссылки в оглавлении корректны и ведут на существующие заголовки",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.2",
                details={"total_links": len(links), "valid_links": valid_links}
            ))
        else:
            items.append(CriteriaItem(
                id="2.2.2",
                title="Проверка корректности Markdown-ссылок",
                description="Все ссылки в оглавлении корректны и ведут на существующие заголовки",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=[f"Найдено {len(invalid_links)} невалидных ссылок из {len(links)}"] if links else ["Нет ссылок в оглавлении"],
                parent_id="2.2",
                details={"invalid_links": invalid_links[:5]}  # Первые 5 для детализации
            ))

        # 2.2.3: Проверка согласованности названий
        toc_links = re.findall(r'\[([^\]]+)\]\(#', toc_block)
        mismatches = []

        for link_text in toc_links:
            # Ищем заголовок в тексте (без учета регистра и дефисов)
            link_normalized = re.sub(r'[-\s]+', ' ', link_text.lower())
            found = False

            if self.rx_h2:
                for h2_match in self.rx_h2.finditer(md):
                    h2_text = re.sub(r'[-\s]+', ' ', h2_match.group(1).lower())
                    if link_normalized in h2_text or h2_text in link_normalized:
                        found = True
                        break

            if not found and self.rx_h3:
                for h3_match in self.rx_h3.finditer(md):
                    h3_text = re.sub(r'[-\s]+', ' ', h3_match.group(1).lower())
                    if link_normalized in h3_text or h3_text in link_normalized:
                        found = True
                        break

            if not found:
                mismatches.append(link_text)

        if len(mismatches) == 0:
            items.append(CriteriaItem(
                id="2.2.3",
                title="Проверка согласованности названий",
                description="Названия в оглавлении совпадают с реальными заголовками",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.2"
            ))
        else:
            items.append(CriteriaItem(
                id="2.2.3",
                title="Проверка согласованности названий",
                description="Названия в оглавлении совпадают с реальными заголовками",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=[f"Несовпадения: {', '.join(mismatches[:3])}"],
                parent_id="2.2",
                details={"mismatches": mismatches}
            ))

        # 2.2.4: Проверка смысловой точности заголовков (ИИ)
        if toc_links:
            # Проверяем первые несколько заголовков
            sample_links = toc_links[:5]
            hybrid_check, detailed_comments = self._ai_check_heading_accuracy(sample_links, md)

            # Определяем метод проверки: HYBRID если есть и SBERT и LLM, иначе AI_AGENT или SBERT
            check_method = CheckMethod.HYBRID if (self.embedding_function and self.llm) else (CheckMethod.AI_AGENT if self.llm else CheckMethod.SBERT)

            items.append(CriteriaItem(
                id="2.2.4",
                title="Проверка смысловой точности заголовков",
                description="Заголовки отражают содержание разделов (SBERT + LLM)",
                check_method=check_method,
                score=1 if hybrid_check else 0,
                comments=detailed_comments if detailed_comments else ([] if hybrid_check else ["Некоторые заголовки не отражают содержание разделов"]),
                parent_id="2.2",
                strictness=StrictnessLevel.SOFT  # Редакторская проверка, не блокирует прохождение
            ))
        else:
            items.append(CriteriaItem(
                id="2.2.4",
                title="Проверка смысловой точности заголовков",
                description="Заголовки отражают содержание разделов",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["ИИ-агент недоступен для проверки"],
                parent_id="2.2",
                strictness=StrictnessLevel.SOFT  # Редакторская проверка, не блокирует прохождение
            ))

        return items

    def check_document(self, document: ReadmeDocument) -> list[CriteriaItem]:
        """2.2: Проверка оглавления из typed README document."""
        toc = toc_section(document)
        if toc is None:
            return self._toc_failure_items()

        toc_block = section_prose_text(toc)
        links = re.findall(r'\[([^\]]+)\]\(#([^\)]+)\)', toc_block)
        toc_links = [text for text, _anchor in links]
        heading_sections = [
            section
            for section in document.sections
            for section in section.flatten()
            if section.level in {2, 3} and section.metadata.get("section_kind") != "toc"
        ]
        heading_by_normalized = {
            self._normalize_title(section.title): section
            for section in heading_sections
        }
        heading_titles = set(heading_by_normalized)
        items: list[CriteriaItem] = []

        has_chapter1 = document.chapter_section(1, language=self.lang) is not None
        has_chapter2 = document.chapter_section(2, language=self.lang) is not None
        has_chapter3 = document.chapter_section(3, language=self.lang) is not None
        has_top_level = any(line.startswith(("- ", "* ")) or re.match(r"^\d+\.\s+\[", line) for line in toc_block.splitlines())
        has_subsections = any(re.match(r"^\s+[-*]\s+\[", line) for line in toc_block.splitlines())

        if has_chapter1 and has_chapter2 and has_chapter3 and has_top_level and has_subsections:
            items.append(CriteriaItem(
                id="2.2.1",
                title="Проверка структуры уровней",
                description="Оглавление содержит два уровня: Главы и подразделы",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.2",
            ))
        else:
            missing = []
            if not has_chapter1:
                missing.append("Глава 1")
            if not has_chapter2:
                missing.append("Глава 2")
            if not has_chapter3:
                missing.append("Глава 3")
            if not has_top_level:
                missing.append("верхний уровень оглавления")
            if not has_subsections:
                missing.append("подразделы")
            items.append(CriteriaItem(
                id="2.2.1",
                title="Проверка структуры уровней",
                description="Оглавление содержит два уровня: Главы и подразделы",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=[f"Отсутствуют: {', '.join(missing)}"],
                parent_id="2.2",
            ))

        invalid_links = []
        valid_links = 0
        for link_text, anchor in links:
            normalized_link = self._normalize_title(link_text)
            normalized_anchor = anchor.strip().casefold()
            section = heading_by_normalized.get(normalized_link)
            if section and ReadmeDocument.slugify(section.title) == normalized_anchor:
                valid_links += 1
            elif section:
                invalid_links.append(f"{link_text} → {anchor}")
            else:
                invalid_links.append(f"{link_text} → {anchor}")

        if links and not invalid_links:
            items.append(CriteriaItem(
                id="2.2.2",
                title="Проверка корректности Markdown-ссылок",
                description="Все ссылки в оглавлении корректны и ведут на существующие заголовки",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.2",
                details={"total_links": len(links), "valid_links": valid_links},
            ))
        else:
            items.append(CriteriaItem(
                id="2.2.2",
                title="Проверка корректности Markdown-ссылок",
                description="Все ссылки в оглавлении корректны и ведут на существующие заголовки",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=[f"Найдено {len(invalid_links)} невалидных ссылок из {len(links)}"] if links else ["Нет ссылок в оглавлении"],
                parent_id="2.2",
                details={"invalid_links": invalid_links[:5]},
            ))

        mismatches = [
            link_text
            for link_text in toc_links
            if self._normalize_title(link_text) not in heading_titles
        ]
        items.append(CriteriaItem(
            id="2.2.3",
            title="Проверка согласованности названий",
            description="Названия в оглавлении совпадают с реальными заголовками",
            check_method=CheckMethod.SCRIPT,
            score=1 if not mismatches else 0,
            comments=[] if not mismatches else [f"Несовпадения: {', '.join(mismatches[:3])}"],
            parent_id="2.2",
            details={} if not mismatches else {"mismatches": mismatches},
        ))

        if toc_links:
            hybrid_check, detailed_comments = self._check_heading_accuracy_sections(toc_links[:5], heading_by_normalized)
            check_method = CheckMethod.HYBRID if (self.embedding_function and self.llm) else (
                CheckMethod.AI_AGENT if self.llm else CheckMethod.SBERT
            )
            items.append(CriteriaItem(
                id="2.2.4",
                title="Проверка смысловой точности заголовков",
                description="Заголовки отражают содержание разделов (SBERT + LLM)",
                check_method=check_method,
                score=1 if hybrid_check else 0,
                comments=detailed_comments if detailed_comments else ([] if hybrid_check else ["Некоторые заголовки не отражают содержание разделов"]),
                parent_id="2.2",
                strictness=StrictnessLevel.SOFT,
            ))
        else:
            items.append(CriteriaItem(
                id="2.2.4",
                title="Проверка смысловой точности заголовков",
                description="Заголовки отражают содержание разделов",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["ИИ-агент недоступен для проверки"],
                parent_id="2.2",
                strictness=StrictnessLevel.SOFT,
            ))

        return items

    @staticmethod
    def _normalize_title(title: str) -> str:
        """Normalize TOC and real heading titles for typed comparison."""
        return re.sub(r"[-\s]+", " ", (title or "").casefold()).strip()

    @staticmethod
    def _toc_failure_items() -> list[CriteriaItem]:
        """Return standard failures when the typed TOC section is absent."""
        return [
            CriteriaItem(
                id=sub_id,
                title=f"Проверка оглавления ({sub_id})",
                description="Требуется оглавление",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=["Нет раздела «Содержание/Оглавление»"],
                parent_id="2.2",
            )
            for sub_id in ["2.2.1", "2.2.2", "2.2.3", "2.2.4"]
        ]

    def _check_heading_accuracy_sections(
        self,
        headings: list[str],
        sections_by_title: dict[str, ReadmeSection],
    ) -> tuple[bool, list[str]]:
        """Check TOC heading accuracy using typed section bodies."""
        accurate_count = 0
        total_checked = 0
        comments: list[str] = []
        generic_headings = [
            "введение", "инструкция", "теория", "основные понятия", "обзор",
            "заключение", "выводы", "практика", "задачи", "примеры",
            "дополнительно", "общая информация",
        ]

        for heading in headings:
            normalized = self._normalize_title(heading)
            if is_service_heading(heading):
                continue
            section = sections_by_title.get(normalized)
            if section is None:
                comments.append(f"Заголовок \"{heading}\": раздел не найден в документе")
                continue
            body = section_prose_text(section)
            if not body:
                comments.append(f"Заголовок \"{heading}\": раздел пустой, нет содержания")
                continue
            total_checked += 1
            normalized_heading = normalize_heading_for_search(heading).lower()
            if any(generic in normalized_heading for generic in generic_headings):
                accurate_count += 1
                continue
            score = _semantic_similarity(
                heading.strip(),
                body[:2000],
                lang=self.lang,
                embedding_function=self.embedding_function,
            )
            if score >= 0.10:
                accurate_count += 1
            else:
                comments.append(f"Заголовок \"{heading}\": низкое семантическое сходство ({score:.3f})")

        if total_checked == 0:
            return False, comments or ["Не удалось проверить соответствие заголовков содержанию"]
        if accurate_count == 0:
            return False, comments or ["Ни один из проверенных заголовков не отражает содержание разделов"]
        if accurate_count < total_checked:
            return True, [f"Часть заголовков можно уточнить: точных {accurate_count} из {total_checked}", *comments]
        return True, comments

