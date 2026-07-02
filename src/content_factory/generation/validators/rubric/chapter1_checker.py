"""Проверка Главы 1: Введение и инструкция (2.3.1-2.3.7)."""

import re

from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel
from ...models.readme_document import ReadmeDocument
from ...utils.logging import safe_print
from ...utils.text_analysis import count_words
from .document_utils import section_prose_text


class Chapter1Checker:
    """Проверяет Главу 1 (введение и инструкция)."""

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
        self.rx_h3 = regex_patterns.get("rx_h3") if regex_patterns else None
        self.rx_directives = regex_patterns.get("rx_directives", []) if regex_patterns else []

    def check(self, ch1_content: str) -> list[CriteriaItem]:
        """2.3: Проверка Главы 1 (2.3.1-2.3.7)."""
        items = []

        if not ch1_content:
            for sub_id in ["2.3.1", "2.3.2", "2.3.3", "2.3.4", "2.3.5", "2.3.6", "2.3.7"]:
                items.append(CriteriaItem(
                    id=sub_id,
                    title=f"Проверка Главы 1 ({sub_id})",
                    description="Требуется Глава 1",
                    check_method=CheckMethod.AI_AGENT,
                    score=0,
                    comments=["Нет Главы 1"],
                    parent_id="2.3"
                ))
            return items

        # 2.3.1: Проверка структуры подразделов (ИИ)
        h3_matches = list(self.rx_h3.finditer(ch1_content)) if self.rx_h3 else []
        intro_blocks = []
        instruction_blocks = []

        for h3 in h3_matches:
            h3_text = h3.group(1).lower()
            # Вводная часть
            if any(kw in h3_text for kw in ["введение", "вводная часть", "контекст проекта", "контекст", "описание проекта", "цель", "о проекте"]):
                intro_blocks.append(h3)
            # Инструкция
            elif any(kw in h3_text for kw in ["инструкция", "правила выполнения", "требования", "рекомендации", "как работать с проектом"]):
                instruction_blocks.append(h3)

        if intro_blocks and instruction_blocks:
            # проверяем порядок: вводная должна быть раньше инструкции
            has_correct_order = intro_blocks[0].start() < instruction_blocks[0].start()

            if not has_correct_order:
                items.append(CriteriaItem(
                    id="2.3.1",
                    title="Проверка структуры подразделов",
                    description="Два подраздела: вводная часть и инструкция",
                    check_method=CheckMethod.AI_AGENT,
                    score=0,
                    comments=["Инструкция располагается раньше вводной части, нарушен ожидаемый порядок"],
                    parent_id="2.3",
                ))
            else:
                items.append(CriteriaItem(
                    id="2.3.1",
                    title="Проверка структуры подразделов",
                    description="Два подраздела: вводная часть и инструкция",
                    check_method=CheckMethod.AI_AGENT,
                    score=1,
                    comments=[],
                    parent_id="2.3",
                ))
        else:
            items.append(CriteriaItem(
                id="2.3.1",
                title="Проверка структуры подразделов",
                description="Два подраздела: вводная часть и инструкция",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=[f"Найдено: вводных блоков={len(intro_blocks)}, инструкций={len(instruction_blocks)}"],
                parent_id="2.3",
            ))

        # 2.3.2: Проверка длины текста вводной части
        intro_text = ""
        if intro_blocks:
            intro_start = intro_blocks[0].end()
            intro_end = ch1_content.find("\n###", intro_start)
            if intro_end == -1:
                intro_end = len(ch1_content)
            intro_text = ch1_content[intro_start:intro_end]

        if intro_text:
            w = count_words(intro_text, self.lang)
            lo, hi = 80, 250

            if lo <= w <= hi:
                items.append(CriteriaItem(
                    id="2.3.2",
                    title="Проверка длины текста вводной части",
                    description=f"Корректный диапазон — {lo}–{hi} слов",
                    check_method=CheckMethod.SCRIPT,
                    score=1,
                    comments=[],
                    parent_id="2.3"
                ))
            else:
                items.append(CriteriaItem(
                    id="2.3.2",
                    title="Проверка длины текста вводной части",
                    description=f"Корректный диапазон — {lo}–{hi} слов",
                    check_method=CheckMethod.SCRIPT,
                    score=0,
                    comments=[f"Введение {w} слов (ожидалось {lo}–{hi})"],
                    parent_id="2.3"
                ))
        else:
            items.append(CriteriaItem(
                id="2.3.2",
                title="Проверка длины текста вводной части",
                description="Корректный диапазон — 80–250 слов",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=["Вводная часть не найдена"],
                parent_id="2.3"
            ))

        # 2.3.3: Проверка формата вводной части (ИИ)
        if intro_text and self.llm:
            ai_check = self._ai_check_intro_format(intro_text)
            items.append(CriteriaItem(
                id="2.3.3",
                title="Проверка формата вводной части",
                description="Наличие маркеров применения и цели проекта",
                check_method=CheckMethod.AI_AGENT,
                score=1 if ai_check else 0,
                comments=[] if ai_check else ["Во введении нет маркеров применения/цели"],
                parent_id="2.3"
            ))
        elif intro_text:
            # Fallback на скриптовую проверку
            low = intro_text.lower()
            has_markers = any(k in low for k in [
                "используется для", "применяется в", "решает задачу", "в реальной ситуации",
                "цель проекта", "зачем", "основная идея", "что решает"
            ])

            items.append(CriteriaItem(
                id="2.3.3",
                title="Проверка формата вводной части",
                description="Наличие маркеров применения и цели проекта",
                check_method=CheckMethod.SCRIPT,
                score=1 if has_markers else 0,
                comments=[] if has_markers else ["Во введении нет маркеров применения/цели"],
                parent_id="2.3",
                strictness=StrictnessLevel.SOFT  # Рекомендация, не блокирует прохождение
            ))
        else:
            items.append(CriteriaItem(
                id="2.3.3",
                title="Проверка формата вводной части",
                description="Наличие маркеров применения и цели проекта",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["Вводная часть не найдена"],
                parent_id="2.3",
                strictness=StrictnessLevel.SOFT  # Рекомендация, не блокирует прохождение
            ))

        # 2.3.4: Проверка длины текста инструкции
        instruction_text = ""
        if instruction_blocks:
            instr_start = instruction_blocks[0].end()
            instr_end = ch1_content.find("\n###", instr_start)
            if instr_end == -1:
                instr_end = len(ch1_content)
            instruction_text = ch1_content[instr_start:instr_end]

            # КРИТИЧЕСКИ ВАЖНО: Убираем заголовки подразделов перед подсчетом слов
            # Заголовки типа "**Контекст и ограничения проекта**", "**Как учиться в «Школе 21»:**" не должны считаться
            # Убираем markdown-заголовки (жирный текст на отдельной строке)
            instruction_text = re.sub(r'^\*\*[^\*]+\*\*\s*:?\s*\n', '', instruction_text, flags=re.MULTILINE)
            instruction_text = re.sub(r'\n\*\*[^\*]+\*\*\s*:?\s*\n', '\n', instruction_text, flags=re.MULTILINE)
            # Убираем markdown-разметку для жирного текста в заголовках (оставляем только текст)
            instruction_text = re.sub(r'\*\*([^\*]+)\*\*', r'\1', instruction_text)
            # Убираем пустые строки и лишние пробелы
            instruction_text = re.sub(r'\n\s*\n\s*\n+', '\n\n', instruction_text)
            instruction_text = instruction_text.strip()

        if instruction_text:
            w = count_words(instruction_text, self.lang)
            safe_print(f"      [2.3.4] Длина инструкции: {w} слов (диапазон: 80-250)", flush=True)
            lo, hi = 80, 250

            if lo <= w <= hi:
                items.append(CriteriaItem(
                    id="2.3.4",
                    title="Проверка длины текста инструкции",
                    description=f"Корректный диапазон — {lo}–{hi} слов",
                    check_method=CheckMethod.SCRIPT,
                    score=1,
                    comments=[],
                    parent_id="2.3"
                ))
            else:
                items.append(CriteriaItem(
                    id="2.3.4",
                    title="Проверка длины текста инструкции",
                    description=f"Корректный диапазон — {lo}–{hi} слов",
                    check_method=CheckMethod.SCRIPT,
                    score=0,
                    comments=[f"Инструкция {w} слов (ожидалось {lo}–{hi})"],
                    parent_id="2.3"
                ))
        else:
            items.append(CriteriaItem(
                id="2.3.4",
                title="Проверка длины текста инструкции",
                description="Корректный диапазон — 80–250 слов",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=["Инструкция не найдена"],
                parent_id="2.3"
            ))

        # 2.3.5: Проверка формата инструкции (ИИ)
        # Смешанный случай: директивы - HARD, ключевые слова - SOFT
        if instruction_text and self.llm:
            ai_check = self._ai_check_instruction_format(instruction_text)
            # Проверяем директивы отдельно для более точных комментариев
            has_directives = any(
                p.search(instruction_text) if hasattr(p, 'search') else re.search(p, instruction_text, flags=re.I)
                for p in self.rx_directives
            )
            comments_list = []
            if not ai_check:
                if has_directives:
                    comments_list.append("Обнаружены директивы (нарушение методологии)")  # HARD
                else:
                    comments_list.append("Отсутствуют ключевые слова «допускается/запрещено/обязательно»")  # SOFT

            items.append(CriteriaItem(
                id="2.3.5",
                title="Проверка формата инструкции",
                description="Инструкция без директив, с фразами «допускается», «запрещено», «обязательно»",
                check_method=CheckMethod.AI_AGENT,
                score=1 if ai_check else 0,
                comments=comments_list if comments_list else [],
                parent_id="2.3",
                strictness=StrictnessLevel.HARD if has_directives else StrictnessLevel.SOFT  # Директивы - HARD, остальное - SOFT
            ))
        elif instruction_text:
            # Fallback на скриптовую проверку
            low = instruction_text.lower()
            has_keywords = all(k in low for k in ["допускается", "запрещено", "обязательно"])
            has_directives = any(re.search(p, instruction_text, flags=re.I) for p in self.rx_directives)

            comments_list = []
            if not (has_keywords and not has_directives):
                if has_directives:
                    comments_list.append("Обнаружены директивы (нарушение методологии)")  # HARD
                if not has_keywords:
                    comments_list.append("Отсутствуют ключевые слова «допускается/запрещено/обязательно»")  # SOFT

            items.append(CriteriaItem(
                id="2.3.5",
                title="Проверка формата инструкции",
                description="Инструкция без директив, с фразами «допускается», «запрещено», «обязательно»",
                check_method=CheckMethod.SCRIPT,
                score=1 if (has_keywords and not has_directives) else 0,
                comments=comments_list,
                parent_id="2.3",
                strictness=StrictnessLevel.HARD if has_directives else StrictnessLevel.SOFT  # Директивы - HARD, остальное - SOFT
            ))
        else:
            items.append(CriteriaItem(
                id="2.3.5",
                title="Проверка формата инструкции",
                description="Инструкция без директив, с фразами «допускается», «запрещено», «обязательно»",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["Инструкция не найдена"],
                parent_id="2.3",
                strictness=StrictnessLevel.HARD  # Отсутствие инструкции - блокер
            ))

        # 2.3.6: Проверка автономности инструкции (ИИ)
        if instruction_text and self.llm:
            ai_check = self._ai_check_instruction_autonomy(instruction_text)
            items.append(CriteriaItem(
                id="2.3.6",
                title="Проверка автономности инструкции",
                description="Инструкция описывает общие правила, а не шаги из практики",
                check_method=CheckMethod.AI_AGENT,
                score=1 if ai_check else 0,
                comments=[] if ai_check else ["Инструкция дублирует шаги из практической части"],
                parent_id="2.3"
            ))
        else:
            items.append(CriteriaItem(
                id="2.3.6",
                title="Проверка автономности инструкции",
                description="Инструкция описывает общие правила, а не шаги из практики",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["ИИ-агент недоступен для проверки"],
                parent_id="2.3"
            ))

        # 2.3.7: Проверка наличия контекстных ограничений (ИИ)
        has_constraints_script = self._has_contextual_constraints_script(instruction_text)
        if instruction_text and self.llm:
            ai_check = has_constraints_script or self._ai_check_contextual_constraints(instruction_text)
            items.append(CriteriaItem(
                id="2.3.7",
                title="Проверка наличия контекстных ограничений",
                description="Наличие требований к окружению, версиям ПО, исходным данным",
                check_method=CheckMethod.AI_AGENT,
                score=1 if ai_check else 0,
                comments=[] if ai_check else ["В инструкции отсутствуют контекстные ограничения"],
                parent_id="2.3"
            ))
        else:
            items.append(CriteriaItem(
                id="2.3.7",
                title="Проверка наличия контекстных ограничений",
                description="Наличие требований к окружению, версиям ПО, исходным данным",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=["ИИ-агент недоступен для проверки"],
                parent_id="2.3"
            ))

        return items

    def check_document(self, document: ReadmeDocument) -> list[CriteriaItem]:
        """2.3: Проверка Главы 1 из typed README document."""
        chapter = document.chapter_section(1, language=self.lang)
        if chapter is None:
            return self.check("")
        child_blocks = [
            f"### {child.title}\n\n{section_prose_text(child)}".strip()
            for child in chapter.children
            if child.title.strip()
        ]
        typed_content = "\n\n".join(block for block in child_blocks if block.strip())
        if not typed_content:
            typed_content = section_prose_text(chapter)
        return self.check(typed_content)

    @staticmethod
    def _has_contextual_constraints_script(instruction_text: str) -> bool:
        """Детерминированная проверка наличия контекстных ограничений."""
        low = (instruction_text or "").lower()
        if not low.strip():
            return False

        environment_markers = [
            "требования к окружению", "среде обучения", "окружении", "инструмент", "зависимост",
            "верс", "локальн", "кампус",
        ]
        source_markers = [
            "исходные данные", "на старте", "доступ к репозиторию", "репозитор", "шаблон",
            "материал", "данные",
        ]
        structure_markers = [
            "структура артефактов", "структура репозитория", "размещ", "по пути", "папк",
            "markdown", "таблиц", "схем", "презентац",
        ]
        check_markers = [
            "правила сдачи", "проверка выполняется", "peer-to-peer", "p2p", "чек-лист",
            "обязательно", "допускается", "запрещено",
        ]

        groups = [
            environment_markers,
            source_markers,
            structure_markers,
            check_markers,
        ]
        matched_groups = sum(1 for group in groups if any(marker in low for marker in group))
        return matched_groups >= 3

    def _ai_check_intro_format(self, intro_text: str) -> bool:
        """ИИ-проверка формата вводной части."""
        if not self.llm:
            return False

        try:
            prompt = f"""Проверь, содержит ли вводная часть маркеры применения и цели проекта:
- «используется для», «применяется в», «решает задачу»
- «в реальной ситуации», «цель проекта», «зачем»

Текст:
{intro_text[:500]}

Верни только JSON:
{{"has_markers": true/false}}"""

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
                return data.get("has_markers", False)
        except:
            pass

        return False

    def _ai_check_instruction_format(self, instruction_text: str) -> bool:
        """ИИ-проверка формата инструкции."""
        if not self.llm:
            return False

        try:
            prompt = f"""Проверь инструкцию на соответствие требованиям:
1. Содержит фразы «допускается», «запрещено», «обязательно»
2. НЕ содержит директивных указаний («нажми», «введи», «перейди»)
3. Описывает рамки и условия работы

Текст:
{instruction_text[:500]}

Верни только JSON:
{{"has_keywords": true/false, "no_directives": true/false, "correct_format": true/false}}"""

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
                return data.get("correct_format", False)
        except:
            pass

        return False

    def _ai_check_instruction_autonomy(self, instruction_text: str) -> bool:
        """ИИ-проверка автономности инструкции."""
        if not self.llm:
            return False

        try:
            # Берем весь текст инструкции для проверки (не только первые 500 символов)
            text_sample = instruction_text

            # Проверяем наличие явной подсказки об автономности в начале
            has_autonomy_hint = any(phrase in text_sample.lower() for phrase in [
                "общие правила",
                "не описывает конкретные шаги",
                "не дублирует",
                "правила работы с проектом",
                "не описывает шаги по решению"
            ])

            prompt = f"""Проверь, описывает ли инструкция ОБЩИЕ РАМКИ И ПРАВИЛА для всего проекта, а НЕ конкретные шаги из практических задач.

Полный текст инструкции:
{text_sample}

КРИТИЧЕСКИ ВАЖНО - БУДЬ ТОЛЕРАНТНЫМ:
Инструкция описывает ОБЩИЕ ПРАВИЛА работы с проектом и НЕ описывает конкретные шаги по решению задач.
Если в тексте есть явная подсказка об этом (например, "общие правила", "не описывает конкретные шаги"), то инструкция АВТОНОМНА.

КРИТЕРИИ ПРОВЕРКИ:
- Инструкция должна описывать ОБЩИЕ правила для ВСЕХ задач проекта (условия, ограничения, разрешенные/запрещенные подходы)
- Инструкция НЕ должна содержать КОНКРЕТНЫЕ действия из ОПРЕДЕЛЕННЫХ практических задач (например: "Создай файл README.md для задачи 1", "Напиши код для анализа данных", "Выполни задачу 1", "Проанализируй данные из файла X")
- Инструкция НЕ должна дублировать пошаговые действия из КОНКРЕТНЫХ задач
- Инструкция должна фокусироваться на условиях работы, ограничениях, разрешенных/запрещенных подходах
- Инструкция - это "правила игры", а не "как играть"

ВАЖНО - РАЗЛИЧАЙ:
- ОБЩИЕ ПРАВИЛА (правильно для инструкции):
  * "Весь код должен находиться в папке src/" - это общее правило для всех задач
  * "Обязательно создавай файлы в формате task_N.py" - это общее правило структуры
  * "Запрещено изменять служебные файлы" - это общее ограничение
  * "Допускается использовать любые библиотеки" - это общее разрешение
  
- КОНКРЕТНЫЕ ШАГИ ИЗ ЗАДАЧ (неправильно для инструкции):
  * "Создай файл README.md" - это конкретное действие из задачи
  * "Напиши код для анализа данных из файла data.csv" - это конкретная задача
  * "Выполни задачу 1: создай функцию calculate()" - это конкретный шаг из задачи
  * "Проанализируй данные и построй график" - это конкретное действие из задачи

ПРИМЕРЫ ПРАВИЛЬНОГО (это общие правила, должно быть в инструкции):
- "Допускается использовать разные подходы к решению задач"
- "Запрещено копировать готовые решения без указания источника"
- "Обязательно сохранять проверяемость результата"
- "Весь код должен находиться в папке src/"
- "Для каждого задания создавай отдельный файл формата task_N.py"
- "Использование локального окружения допускается только для черновой работы"

ПРИМЕРЫ НЕПРАВИЛЬНОГО (это шаги из практики, НЕ должно быть в инструкции):
- "Создай файл README.md" (конкретный файл для конкретной задачи)
- "Напиши код на Python для анализа данных" (конкретное действие)
- "Выполни задачу 1" (ссылка на конкретную задачу)
- "Проанализируй данные из файла data.csv" (конкретный файл и действие)
- "Используй библиотеку pandas для обработки данных" (конкретный инструмент для конкретной задачи)

Верни только JSON:
{{"is_autonomous": true/false, "reason": "краткое объяснение"}}"""

            response = self.llm.complete(
                system="Ты эксперт по анализу образовательных текстов. Будь толерантным к общим правилам и не считай их шагами из практики.",
                user=prompt,
                response_format="json_object",
                temperature=0.1
            )

            import json
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                is_autonomous = data.get("is_autonomous", False)

                # Если есть явная подсказка об автономности, считаем инструкцию автономной
                if has_autonomy_hint and not is_autonomous:
                    safe_print("      [2.3.6] Обнаружена подсказка об автономности, но LLM вернул False. Принудительно устанавливаем True.", flush=True)
                    return True

                return is_autonomous
        except Exception as e:
            safe_print(f"      [2.3.6] Ошибка при проверке автономности: {e}", flush=True)
            pass

        return False

    def _ai_check_contextual_constraints(self, instruction_text: str) -> bool:
        """ИИ-проверка наличия контекстных ограничений."""
        if not self.llm:
            return False

        try:
            prompt = f"""Проверь, содержит ли инструкция релевантные контекстные ограничения для выполнения проекта.

Текст:
{instruction_text[:500]}

КРИТЕРИИ ПРОВЕРКИ:
- Инструкция задаёт общие правила работы с проектом, а не конкретные шаги решения задач.
- Указано, с какими исходными данными, материалами или рабочей областью стартует участник.
- Указаны обязательные инструменты, если они заданы в проекте.
- Указаны ограничения, которые действительно важны для типа проекта: формат артефактов, данные, время, роли, окружение или версии ПО.
- Для no_code/управленческих проектов не требуй ОС, версий ПО, автотестов или структуры репозитория, если это не является предметом проекта.
- Репозиторий/GitLab/правила сдачи допустимы только если проект явно работает с Git/GitLab.

ПРИМЕРЫ ПРАВИЛЬНОГО (есть контекстные ограничения):
- "Исходные данные: участник получает заметки встречи и должен оформить рабочий артефакт по заданному шаблону."
- "Обязательные инструменты: Miro и Google Sheets; итоговый файл размещается по указанному пути проекта."
- "Для технического проекта: Python 3.12 и Docker обязательны, результат хранится в рабочей папке проекта."

ПРИМЕР НЕПРАВИЛЬНОГО (нет контекстных ограничений):
- Только общие фразы типа "Допускается использовать разные подходы" без указания исходных данных, инструментов или ограничений

Верни только JSON:
{{"has_constraints": true/false, "reason": "краткое объяснение"}}"""

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
                return data.get("has_constraints", False)
        except:
            pass

        return False

    def _ai_check_chapter1_structure(self, ch1_content: str) -> bool:
        """ИИ-проверка структуры Главы 1.

        Ожидается:
        - есть вводная часть (Введение / Контекст / Описание проекта / Цель),
        - есть инструкция (Инструкция / Требования / Правила выполнения / Рекомендации),
        - вводная часть идёт раньше инструкции.
        """
        if not self.llm:
            return False

        try:
            prompt = f"""
Проверь структуру Главы 1 учебного проекта.

ОЖИДАЕМАЯ СТРУКТУРА:
1. Внутри Главы 1 должен быть подраздел третьего уровня (###), который является вводной частью:
   - возможные варианты заголовка: "Введение", "Вводная часть",
     "Контекст проекта", "Описание проекта", "Цель" и их близкие формулировки.
2. После него должен идти подраздел третьего уровня (###) с инструкцией:
   - возможные варианты заголовка: "Инструкция", "Правила выполнения",
     "Требования", "Рекомендации" и их близкие формулировки.
3. Сначала должна идти вводная часть, затем инструкция (порядок важен).

Текст главы 1 (фрагмент):
{ch1_content[:2000]}

ТВОЯ ЗАДАЧА:
- Определи, есть ли в тексте главы 1 вводная часть и инструкция
  как отдельные подразделы (### ...).
- Проверь, что вводная часть идёт раньше инструкции.
- Оцени, можно ли считать структуру корректной с точки зрения требований выше.

Верни только JSON:
{{
  "has_intro": true/false,
  "has_instruction": true/false,
  "correct_order": true/false,
  "correct_structure": true/false,
  "reason": "краткое объяснение на 1–2 предложения"
}}
""".strip()

            response = self.llm.complete(
                system="Ты эксперт по анализу структуры учебных материалов.",
                user=prompt,
                response_format="json_object",
                temperature=0.1,
            )

            import json
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                # считаем структуру корректной только если все три признака True
                return bool(
                    data.get("has_intro")
                    and data.get("has_instruction")
                    and data.get("correct_order")
                    and data.get("correct_structure")
                )
        except Exception:
            pass

        return False
