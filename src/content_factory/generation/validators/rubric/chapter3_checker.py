"""Проверка Главы 3: Практика (2.5.1-2.5.7)."""

import re
from math import sqrt
from typing import Any

from ...config.banned_phrases import BAD_GOAL_PATTERNS, BANNED_BY_LANG
from ...config.active_goals import has_active_goal_verb
from ...config.thresholds import THRESHOLDS
from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel
from ...models.readme_document import ReadmeDocument
from ...utils.logging import safe_print
from ...utils.text_analysis import clean_markdown_prose_for_counting, count_words
from ..messages import practice_task_label
from .document_utils import chapter_prose_text, practice_task_sections, task_block_from_section
from .utils import TaskBlock, bag, cosine, tokens


class Chapter3Checker:
    """Проверяет Главу 3 (практика)."""

    EXPECTED_RESULT_LABELS = (
        "Что должно получиться",
        "Ожидаемый результат",
        "Результат",
        "Итог",
        "Артефакт",
    )

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
        self.rx_task = regex_patterns.get("rx_task") if regex_patterns else None
        self.rx_bad_goals = BAD_GOAL_PATTERNS.get(language, BAD_GOAL_PATTERNS.get("ru", []))
        self.rx_directives = BANNED_BY_LANG.get(language, BANNED_BY_LANG.get("ru", []))

    @staticmethod
    def _has_active_goal_verb(goal_text: str) -> bool:
        return has_active_goal_verb(goal_text)

    @staticmethod
    def _has_deterministic_p2p_signal(
        task_text: str,
        artifact_paths: list[str] | None = None,
        criteria_items: list[str] | None = None,
    ) -> bool:
        checklist_items = list(criteria_items or [])
        if not checklist_items:
            criteria_match = re.search(
                r"\*\*(?:Что должно получиться|Критерии проверки.*?):?\*\*\s*\n(.*?)(?=\n\*\*|\n###|\n##|\Z)",
                task_text,
                flags=re.S | re.I,
            )
            if not criteria_match:
                return False

            criteria_block = criteria_match.group(1)
            checklist_items = [
                re.sub(r"^[-*]\s*\[[ x]?\]\s*", "", line.strip())
                for line in criteria_block.splitlines()
                if re.match(r"^\s*[-*]\s*(?:\[[ x]?\]\s*)?.+", line)
            ]
        checklist_items = [item for item in checklist_items if len(item) >= 10]
        if len(checklist_items) < 3:
            return False

        observable_signals = [
            "содержит", "указан", "указаны", "есть", "присутствует", "присутствуют",
            "размещен", "размещён", "путь", "файл", "раздел", "схема", "таблица",
            "перечислен", "перечислены", "учтен", "учтены", "обоснован", "обоснование",
            "предоставлен", "предоставлены", "соответствует", "оформлен", "оформлена",
            "зафиксирован", "зафиксированы", "добавлен", "добавлены", "подсчитан",
            "подсчитаны", "объяснение", "рекомендации", "вывод", "этап", "категори",
            "статья расходов", "тариф", "трудозатрат", "человеко-час",
        ]
        observable_items = [
            item for item in checklist_items
            if any(signal in item.lower() for signal in observable_signals)
            or bool(
                re.search(
                    r"\b(минимум|кажд[а-я]+|в документе|в файле|в таблице|на схеме|по указанному пути|нет)\b",
                    item.lower(),
                )
            )
        ]
        has_location = bool(artifact_paths) or bool(
            re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+", task_text)
        )
        return len(observable_items) >= 2 and has_location

    @staticmethod
    def _has_label(task_text: str, label: str) -> bool:
        return bool(re.search(rf"\*\*{re.escape(label)}:?\*\*", task_text, flags=re.I))

    @staticmethod
    def _extract_label_block(task_text: str, label: str) -> str:
        match = re.search(
            rf"\*\*{re.escape(label)}:?\*\*\s*(.+?)(?=\n\*\*|\n###|\n##|\Z)",
            task_text,
            flags=re.S | re.I,
        )
        return match.group(1).strip() if match else ""

    @classmethod
    def _extract_action_field(cls, task_text: str, label: str) -> str:
        action = cls._extract_label_block(task_text, "Что нужно сделать")
        labels = ["Ситуация", "Исходные данные", "Цель", "Подход"]
        other_labels = "|".join(re.escape(item) for item in labels if item.casefold() != label.casefold())
        match = re.search(
            rf"(?:^|\n)\s*{re.escape(label)}:\s*(.+?)(?=\n\s*(?:{other_labels}):|\Z)",
            action,
            flags=re.S | re.I,
        )
        return match.group(1).strip() if match else ""

    @classmethod
    def _has_expected_result_label(cls, task_text: str) -> bool:
        return any(cls._has_label(task_text, label) for label in cls.EXPECTED_RESULT_LABELS)

    @classmethod
    def _extract_expected_result_block(cls, task_text: str) -> str:
        for label in cls.EXPECTED_RESULT_LABELS:
            block = cls._extract_label_block(task_text, label)
            if block:
                return block
        return ""

    @classmethod
    def _extract_goal_text(cls, task_text: str) -> str:
        return cls._extract_action_field(task_text, "Цель")

    @classmethod
    def _extract_approach_text(cls, task_text: str) -> str:
        return cls._extract_action_field(task_text, "Подход")

    @classmethod
    def _has_expected_result_text(
        cls,
        task_text: str,
        artifact_paths: list[str] | None = None,
        expected_result: str = "",
    ) -> bool:
        result = expected_result or cls._extract_expected_result_block(task_text)
        has_artifact = bool(re.search(r"\b(файл|документ|таблиц|схем|артефакт|отчет|отчёт|README|Markdown)\b", result, re.I))
        has_location = bool(artifact_paths) or bool(re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./{}:\-]+", result))
        return bool(result.strip()) and (has_artifact or has_location)

    @staticmethod
    def _has_task_situation(task_text: str, situation: str = "") -> bool:
        situation_text = (situation or Chapter3Checker._extract_action_field(task_text, "Ситуация")).strip().lower()
        if not situation_text:
            return False
        if len(situation_text) < 25:
            return False
        actor_signals = ["ты", "команда", "заказчик", "клиент", "коллега", "ревьюер", "проект"]
        tension_signals = [
            "нужно", "проблем", "риск", "срок", "дедлайн", "ошиб", "конфликт",
            "неяс", "задерж", "не понима", "не хватает", "важно", "необходимо",
            "обсуждени", "утверждени", "согласован", "зависит", "иначе", "затруднит",
        ]
        if any(signal in situation_text for signal in actor_signals) and any(
            signal in situation_text for signal in tension_signals
        ):
            return True
        return len(situation_text) >= 60

    def check(
        self,
        ch3_content: str,
        ch2_content: str,
        task_blocks: list[TaskBlock] | None = None,
    ) -> list[CriteriaItem]:
        """2.5: Проверка Главы 3 (2.5.1-2.5.7)."""
        items = []

        if not ch3_content:
            # Используем правильные названия критериев даже когда глава 3 отсутствует
            titles = {
                "2.5.1": "Проверка количества заданий",
                "2.5.2": "Проверка структуры задания",
                "2.5.3": "Проверка формулировки цели",
                "2.5.4": "Проверка корректности подсказки выполнения",
                "2.5.5": "Проверка формулировки ожидаемого результата",
                "2.5.6": "Проверка p2p-проверяемости артефактов",
                "2.5.7": "Проверка связи с теоретическим блоком"
            }
            descriptions = {
                "2.5.1": "Допустимый диапазон — 3–8 заданий",
                "2.5.2": "В каждом задании есть канонические блоки: Что нужно сделать, Что должно получиться, Формат сдачи",
                "2.5.3": "Цель в активной форме: «разработать», «настроить», «проверить»",
                "2.5.4": "Подход ≤150 слов, без директив, 2–6 пунктов",
                "2.5.5": "Результат описан конкретно с указанием артефакта и локации",
                "2.5.6": "Артефакт описан так, чтобы другой участник мог провести проверку",
                "2.5.7": "Ключевые понятия из главы 2 встречаются в главе 3"
            }
            for sub_id in ["2.5.1", "2.5.2", "2.5.3", "2.5.4", "2.5.5", "2.5.6", "2.5.7"]:
                items.append(CriteriaItem(
                    id=sub_id,
                    title=titles.get(sub_id, f"Проверка Главы 3 ({sub_id})"),
                    description=descriptions.get(sub_id, "Требуется Глава 3"),
                    check_method=CheckMethod.SCRIPT,
                    score=0,
                    comments=["Нет Главы 3"],
                    parent_id="2.5"
                ))
            return items

        # 2.5.1: Проверка количества заданий
        task_blocks = task_blocks if task_blocks is not None else self._split_tasks(ch3_content)
        n_tasks = len(task_blocks)
        lo, hi = THRESHOLDS["practice_tasks_range"]

        if lo <= n_tasks <= hi:
            items.append(CriteriaItem(
                id="2.5.1",
                title="Проверка количества заданий",
                description=f"Допустимый диапазон — {lo}–{hi} заданий",
                check_method=CheckMethod.SCRIPT,
                score=1,
                comments=[],
                parent_id="2.5"
            ))
        else:
            items.append(CriteriaItem(
                id="2.5.1",
                title="Проверка количества заданий",
                description=f"Допустимый диапазон — {lo}–{hi} заданий",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=[f"Количество практических заданий: {n_tasks} (ожидалось {lo}–{hi})"],
                parent_id="2.5"
            ))

        # 2.5.2: Проверка структуры задания (ИИ)
        structure_issues = []
        for i, task in enumerate(task_blocks, 1):
            task_text = task.body

            canonical = (
                (task.has_action_block or self._has_label(task_text, "Что нужно сделать"))
                and (task.has_expected_result_block or self._has_expected_result_label(task_text))
                and (task.has_submission_block or self._has_label(task_text, "Формат сдачи"))
                and self._has_task_situation(task_text, task.situation)
            )

            if not canonical:
                missing = []
                label_flags = {
                    "Что нужно сделать": task.has_action_block,
                    "Что должно получиться": task.has_expected_result_block,
                    "Формат сдачи": task.has_submission_block,
                }
                for label, typed_present in label_flags.items():
                    label_present = self._has_expected_result_label(task_text) if label == "Что должно получиться" else self._has_label(task_text, label)
                    if not typed_present and not label_present:
                        missing.append(label)
                if not self._has_task_situation(task_text, task.situation):
                    missing.append("Ситуация")
                structure_issues.append(f"{practice_task_label(i, task.title)}: отсутствуют блоки: {', '.join(missing)}")

        if len(structure_issues) == 0:
            items.append(CriteriaItem(
                id="2.5.2",
                title="Проверка структуры задания",
                description="В каждом задании есть канонические блоки: Что нужно сделать, Что должно получиться, Формат сдачи",
                check_method=CheckMethod.AI_AGENT,
                score=1,
                comments=[],
                parent_id="2.5"
            ))
        else:
            items.append(CriteriaItem(
                id="2.5.2",
                title="Проверка структуры задания",
                description="В каждом задании есть канонические блоки: Что нужно сделать, Что должно получиться, Формат сдачи",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=structure_issues[:5],
                parent_id="2.5",
                details={"issues": structure_issues}
            ))

        # 2.5.3: Проверка формулировки цели (ИИ)
        goal_issues = []
        for i, task in enumerate(task_blocks, 1):
            task_text = task.body

            goal_text = task.goal or self._extract_goal_text(task_text)
            if goal_text:
                # Проверяем на плохие цели
                has_bad_goal = any(re.search(p, goal_text, flags=re.I) for p in self.rx_bad_goals)
                # Проверяем на активные глаголы
                has_active_verb = self._has_active_goal_verb(goal_text)

                if has_bad_goal:
                    goal_issues.append(f"{practice_task_label(i, task.title)}: цель содержит запрещенные слова («изучить/ознакомиться/посмотреть»)")  # HARD
                if not has_active_verb:
                    goal_issues.append(f"{practice_task_label(i, task.title)}: цель не содержит явного активного глагола")  # SOFT
            else:
                goal_issues.append(f"{practice_task_label(i, task.title)}: цель не найдена")  # HARD

        has_hard_issues = any("запрещенные слова" in issue or "не найдена" in issue for issue in goal_issues)

        if len(goal_issues) == 0:
            items.append(CriteriaItem(
                id="2.5.3",
                title="Проверка формулировки цели",
                description="Цель в активной форме: «разработать», «настроить», «проверить»",
                check_method=CheckMethod.AI_AGENT,
                score=1,
                comments=[],
                parent_id="2.5"
            ))
        else:
            items.append(CriteriaItem(
                id="2.5.3",
                title="Проверка формулировки цели",
                description="Цель в активной форме: «разработать», «настроить», «проверить»",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=goal_issues[:5],
                parent_id="2.5",
                details={"issues": goal_issues},
                strictness=StrictnessLevel.HARD if has_hard_issues else StrictnessLevel.SOFT
            ))

        # 2.5.4: Проверка корректности подсказки выполнения (ИИ)
        approach_issues = []
        for i, task in enumerate(task_blocks, 1):
            task_text = task.body

            approach_text = task.approach or self._extract_approach_text(task_text)
            if approach_text:
                w = count_words(approach_text, self.lang)

                # Проверяем длину
                if w > 150:
                    approach_issues.append(f"{practice_task_label(i, task.title)}: подход {w} слов (>150)")

                # Проверяем директивы
                has_directives = any(re.search(p, approach_text, flags=re.I) for p in self.rx_directives)
                if has_directives:
                    approach_issues.append(f"{practice_task_label(i, task.title)}: подход содержит директивы")

                # Проверяем количество пунктов
                list_items = re.findall(r'^[\s]*[-*+]\s+', approach_text, re.MULTILINE)
                numbered_items = re.findall(r'^[\s]*\d+[\.)]\s+', approach_text, re.MULTILINE)
                total_items = len(list_items) + len(numbered_items)

                if total_items > 0 and not (2 <= total_items <= 6):
                    approach_issues.append(f"{practice_task_label(i, task.title)}: подход содержит {total_items} пунктов (ожидалось 2–6)")
            else:
                approach_issues.append(f"{practice_task_label(i, task.title)}: подход не найден")

        has_hard_issues = any("директивы" in issue for issue in approach_issues)

        if len(approach_issues) == 0:
            items.append(CriteriaItem(
                id="2.5.4",
                title="Проверка корректности подсказки выполнения",
                description="Подход ≤150 слов, без директив, 2–6 пунктов",
                check_method=CheckMethod.AI_AGENT,
                score=1,
                comments=[],
                parent_id="2.5"
            ))
        else:
            items.append(CriteriaItem(
                id="2.5.4",
                title="Проверка корректности подсказки выполнения",
                description="Подход ≤150 слов, без директив, 2–6 пунктов",
                check_method=CheckMethod.AI_AGENT,
                score=0,
                comments=approach_issues[:5],
                parent_id="2.5",
                details={"issues": approach_issues},
                strictness=StrictnessLevel.HARD if has_hard_issues else StrictnessLevel.SOFT
            ))

        # 2.5.5: Проверка формулировки ожидаемого результата (ИИ)
        result_issues = []
        if self.llm:
            for i, task in enumerate(task_blocks, 1):
                task_text = task.body

                if self._has_expected_result_text(task_text, task.artifact_paths, task.expected_result):
                    continue

                # Используем LLM-агента для проверки ожидаемого результата
                check_result = self._ai_check_expected_result(task_text)

                if not check_result["is_valid"]:
                    issue_msg = f"{practice_task_label(i, task.title)}: {check_result['reason']}"
                    result_issues.append(issue_msg)

            if len(result_issues) == 0:
                items.append(CriteriaItem(
                    id="2.5.5",
                    title="Проверка формулировки ожидаемого результата",
                    description="Результат описан конкретно с указанием артефакта и локации",
                    check_method=CheckMethod.AI_AGENT,
                    score=1,
                    comments=[],
                    parent_id="2.5"
                ))
            else:
                items.append(CriteriaItem(
                    id="2.5.5",
                    title="Проверка формулировки ожидаемого результата",
                    description="Результат описан конкретно с указанием артефакта и локации",
                    check_method=CheckMethod.AI_AGENT,
                    score=0,
                    comments=result_issues[:5],
                    parent_id="2.5",
                    details={"issues": result_issues}
                ))
        else:
            for i, task in enumerate(task_blocks, 1):
                if not self._has_expected_result_text(task.body, task.artifact_paths, task.expected_result):
                    result_issues.append(f"{practice_task_label(i, task.title)}: ожидаемый результат не найден или не содержит артефакт/локацию")
            items.append(CriteriaItem(
                id="2.5.5",
                title="Проверка формулировки ожидаемого результата",
                description="Результат описан конкретно с указанием артефакта и локации",
                check_method=CheckMethod.SCRIPT,
                score=1 if not result_issues else 0,
                comments=[] if not result_issues else result_issues[:5],
                parent_id="2.5"
            ))

        # 2.5.6: Проверка p2p-проверяемости артефактов
        p2p_issues = []
        p2p_check_method = CheckMethod.AI_AGENT if self.llm else CheckMethod.SCRIPT
        for i, task in enumerate(task_blocks, 1):
            task_text = task.body

            has_p2p_signal = self._has_deterministic_p2p_signal(
                task_text,
                task.artifact_paths,
                task.criteria_items,
            )
            if not has_p2p_signal and self.llm:
                has_p2p_signal = self._ai_check_p2p_verifiability(task_text)

            if not has_p2p_signal:
                p2p_issues.append(f"{practice_task_label(i, task.title)}: артефакт не является p2p-проверяемым")

        if len(p2p_issues) == 0:
            items.append(CriteriaItem(
                id="2.5.6",
                title="Проверка p2p-проверяемости артефактов",
                description="Артефакт описан так, чтобы другой участник мог провести проверку",
                check_method=p2p_check_method,
                score=1,
                comments=[],
                parent_id="2.5"
            ))
        else:
            items.append(CriteriaItem(
                id="2.5.6",
                title="Проверка p2p-проверяемости артефактов",
                description="Артефакт описан так, чтобы другой участник мог провести проверку",
                check_method=p2p_check_method,
                score=0,
                comments=p2p_issues[:5],
                parent_id="2.5",
                details={"issues": p2p_issues}
            ))

        # 2.5.7: Проверка связи с теоретическим блоком
        ch2_text = ch2_content if ch2_content else ""
        tasks = task_blocks

        if not ch2_text.strip():
            items.append(CriteriaItem(
                id="2.5.7",
                title="Проверка связи с теоретическим блоком",
                description="Ключевые понятия из главы 2 встречаются в главе 3",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=["Глава 2 не найдена"],
                parent_id="2.5"
            ))
        elif not tasks:
            items.append(CriteriaItem(
                id="2.5.7",
                title="Проверка связи с теоретическим блоком",
                description="Ключевые понятия из главы 2 встречаются в главе 3",
                check_method=CheckMethod.SCRIPT,
                score=0,
                comments=["Практические задания в главе 3 не найдены"],
                parent_id="2.5"
            ))
        else:
            # 1) Пытаемся через SBERT
            if self.embedding_function:
                avg_sim, scores = self._theory_practice_similarity(ch2_text, tasks)
                threshold = THRESHOLDS.get("theory_practice_sbert_threshold", 0.40)
                term_overlap, matched_terms = self._theory_practice_term_overlap(ch2_text, tasks)
                ok = avg_sim >= threshold or (
                    avg_sim >= max(0.25, threshold - 0.12) and term_overlap >= 0.35
                )

                items.append(CriteriaItem(
                    id="2.5.7",
                    title="Проверка связи с теоретическим блоком",
                    description=f"В задачах используются понятия и технологии из теоретического блока (SBERT-score теории ↔ задач ≥ {threshold})",
                    check_method=CheckMethod.SBERT,
                    score=1 if ok else 0,
                    comments=[] if ok else [
                        f"Низкая семантическая связь теории и задач: средний score {avg_sim:.2f} (< {threshold})"
                    ],
                    parent_id="2.5",
                    details={
                        "average_similarity": avg_sim,
                        "threshold": threshold,
                        "task_scores": scores,
                        "term_overlap": term_overlap,
                        "matched_terms": matched_terms,
                        "tasks": [t.title for t in tasks],
                    },
                    strictness=StrictnessLevel.SOFT
                ))
            # 2) Если SBERT недоступен, но есть LLM — спрашиваем его
            elif self.llm:
                ok, comments = self._ai_check_theory_practice_connection(ch2_text, ch3_content)
                items.append(CriteriaItem(
                    id="2.5.7",
                    title="Проверка связи с теоретическим блоком",
                    description="Ключевые понятия из главы 2 встречаются в главе 3",
                    check_method=CheckMethod.AI_AGENT,
                    score=1 if ok else 0,
                    comments=comments,
                    parent_id="2.5",
                    strictness=StrictnessLevel.SOFT
                ))
            # 3) Полный fallback: bag-of-words
            else:
                overlap, matched_terms = self._theory_practice_term_overlap(ch2_text, tasks)
                overlap_threshold = (
                    0.35 if matched_terms else THRESHOLDS.get("theory_practice_overlap_threshold", 0.10)
                )

                items.append(CriteriaItem(
                    id="2.5.7",
                    title="Проверка связи с теоретическим блоком",
                    description="Ключевые понятия из главы 2 встречаются в главе 3",
                    check_method=CheckMethod.SCRIPT,
                    score=1 if overlap >= overlap_threshold else 0,
                    comments=[] if overlap >= overlap_threshold else [
                        f"Низкое пересечение терминов теории и практики (overlap: {overlap:.2f})"
                    ],
                    parent_id="2.5",
                    details={"term_overlap": overlap, "matched_terms": matched_terms},
                    strictness=StrictnessLevel.SOFT
                ))

        return items

    def check_document(self, document: ReadmeDocument) -> list[CriteriaItem]:
        """2.5: Проверка Главы 3 из typed README document."""
        task_blocks = [
            task_block_from_section(section)
            for section in practice_task_sections(document, language=self.lang)
        ]
        return self.check(
            chapter_prose_text(document, 3, language=self.lang),
            chapter_prose_text(document, 2, language=self.lang),
            task_blocks=task_blocks,
        )

    def _ai_check_expected_result(self, task_text: str) -> dict[str, Any]:
        """ИИ-проверка формулировки ожидаемого результата."""
        default_result = {
            "is_valid": False,
            "has_block": False,
            "has_artifact": False,
            "has_location": False,
            "reason": "Ожидаемый результат не найден"
        }

        if not self.llm:
            return default_result

        try:
            prompt = f"""Проверь текст задачи на наличие и качество формулировки ожидаемого результата.

Текст задачи:
{task_text[:1500]}

КРИТЕРИИ ПРОВЕРКИ:
1. Наличие блока результата. Валидные названия: "**Что должно получиться**", "**Ожидаемый результат:**", "Результат:", "Итог:", "Артефакт:".
2. Указание на артефакт - должно быть четко указано, ЧТО должно быть создано/получено:
   - Тип артефакта (файл, отчет, код, скриншот, программа, скрипт, документ и т.д.)
   - Название или описание артефакта
3. Указание на локацию - должно быть указано, ГДЕ найти результат:
   - Канонический путь к файлу или артефакту (например: "ProjectName/part-03/task-01/plan.md")
   - Рабочая папка, репозиторий или директория
   - Консольный вывод
   - Отчет или документ

ПРИМЕРЫ ПРАВИЛЬНОГО ОПИСАНИЯ:
- "**Что должно получиться** Файл `ProjectName/part-03/task-01/README.md` содержит итоговый артефакт и критерии проверки."
- "**Ожидаемый результат:** Файл `ProjectName/part-03/task-01/README.md` содержит описание проекта и инструкции по запуску."
- "**Ожидаемый результат:** В консоли выводится таблица с результатами вычислений. Файл с кодом находится по пути `ProjectName/src/main.py`."
- "**Ожидаемый результат:** Создан скриншот работы программы, сохраненный в файле `ProjectName/screenshots/result.png`."

ПРИМЕРЫ НЕПРАВИЛЬНОГО ОПИСАНИЯ:
- "**Ожидаемый результат:** Результат работы" - нет указания на артефакт и локацию
- "**Ожидаемый результат:** Создай файл" - нет локации и конкретики
- "**Ожидаемый результат:** Код готов" - нет указания на файл и путь

Верни только JSON:
{{
    "has_block": true/false,
    "has_artifact": true/false,
    "has_location": true/false,
    "is_valid": true/false,
    "reason": "краткое объяснение (если is_valid=false, укажи что именно отсутствует)"
}}"""

            response = self.llm.complete(
                system="Ты эксперт по анализу образовательных текстов. Твоя задача - проверить формулировку ожидаемого результата в задаче.",
                user=prompt,
                response_format="json_object",
                temperature=0.1
            )

            import json
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                return {
                    "is_valid": data.get("is_valid", False),
                    "has_block": data.get("has_block", False),
                    "has_artifact": data.get("has_artifact", False),
                    "has_location": data.get("has_location", False),
                    "reason": data.get("reason", "Не удалось определить причину")
                }
        except Exception as e:
            safe_print(f"        ⚠️ Ошибка ИИ-проверки ожидаемого результата: {str(e)}", flush=True)
            default_result["reason"] = f"Ошибка при проверке: {str(e)}"
            return default_result

        return default_result

    def _ai_check_p2p_verifiability(self, task_text: str) -> bool:
        """ИИ-проверка p2p-проверяемости."""
        if not self.llm:
            return False

        try:
            prompt = f"""Проверь, описан ли ожидаемый результат так, чтобы другой участник мог проверить артефакт БЕЗ участия автора.

Текст задачи:
{task_text[:800]}

КРИТЕРИИ ПРОВЕРКИ:
- Артефакт должен быть описан с четкими критериями успешности (что должно быть в артефакте, какие элементы, какие характеристики)
- Формулировки должны исключать двусмысленность - должно быть ясно, что именно нужно проверить
- Критерии должны быть основаны на наблюдаемых признаках (можно увидеть/проверить/измерить)
- Должно быть указано, ЧТО должно быть в артефакте и ГДЕ его найти

ПРИМЕР ПРАВИЛЬНОГО (p2p-проверяемо):
"Файл README.md содержит описание проекта, список использованных технологий и инструкции по запуску. В файле должны быть разделы: Описание, Технологии, Установка, Использование. Каждый раздел должен содержать минимум 2-3 предложения. Файл размещен по пути ProjectName/part-03/task-01/README.md"

ПРИМЕР НЕПРАВИЛЬНОГО (не p2p-проверяемо):
"Создай файл README.md" - нет критериев проверки, нет локации
"Результат работы" - слишком общо, нет конкретных критериев

Критерии p2p-проверяемости:
- Указаны четкие критерии успешности
- Формулировки исключают двусмысленность
- Критерии основаны на наблюдаемых признаках
- Если есть вариативность, заданы рамки

Верни только JSON:
{{"is_p2p_verifiable": true/false, "reason": "краткое объяснение"}}"""

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
                return data.get("is_p2p_verifiable", False)
        except:
            pass

        return False

    def _ai_check_theory_practice_connection(self, ch2_text: str, ch3_text: str) -> tuple[bool, list[str]]:
        """ИИ-проверка связи между теорией и практикой."""
        if not self.llm:
            return False, ["ИИ-агент недоступен"]

        try:
            prompt = f"""Проверь, используются ли ключевые понятия и технологии из теоретического блока (Глава 2) в практических задачах (Глава 3).

ТЕОРИЯ (Глава 2):
{ch2_text[:1500]}

ПРАКТИКА (Глава 3):
{ch3_text[:1500]}

КРИТЕРИИ ПРОВЕРКИ:
- В задачах должны использоваться понятия, упомянутые в теории
- Технологии и инструменты из теории должны применяться в задачах
- Задачи должны логически вытекать из теоретического материала

Верни только JSON:
{{"has_connection": true/false, "reason": "краткое объяснение", "issues": ["проблема1", "проблема2"]}}"""

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
                has_connection = data.get("has_connection", False)
                issues = data.get("issues", [])
                return has_connection, issues
        except Exception as e:
            safe_print(f"[RUBRIC] AI check theory-practice failed: {e}", flush=True)

        return False, ["Ошибка при проверке ИИ-агентом"]

    def _split_tasks(self, ch3_text: str) -> list[TaskBlock]:
        """Возвращает список задач из главы 3."""
        if not ch3_text.strip():
            return []

        # Режем по заголовкам задач
        raw = re.split(r'(?=^###\s+Задани(?:е|я)\s+\d+\.?)', ch3_text, flags=re.M)
        blocks: list[TaskBlock] = []

        for chunk in raw:
            chunk = chunk.strip()
            if not chunk.startswith("###"):
                continue

            header_match = re.match(r'^###\s+(.+)$', chunk, flags=re.M)
            if not header_match:
                continue

            title = header_match.group(1).strip()
            body = chunk[header_match.end():].strip()
            blocks.append(TaskBlock(title=title, body=body))

        return blocks

    def _embed_many(self, texts: list[str]) -> list[list[float]]:
        """Обёртка над embedding_function."""
        if not self.embedding_function:
            return []
        try:
            return self.embedding_function(texts)
        except Exception as e:
            safe_print(f"[RUBRIC] Embedding failed: {e}", flush=True)
            return []

    def _cosine_sim(self, a: list[float], b: list[float]) -> float:
        """Вычисляет cosine similarity между двумя векторами."""
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = sqrt(sum(x * x for x in a))
        nb = sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _theory_practice_similarity(self, ch2_text: str, tasks: list[TaskBlock]) -> tuple[float, list[float]]:
        """Считает средний SBERT-score между теорией (глава 2) и каждой задачей (глава 3)."""
        if not tasks or not ch2_text.strip():
            return 0.0, []

        reference_text = clean_markdown_prose_for_counting(ch2_text) or ch2_text
        task_texts = [f"{t.title}\n{t.body}" for t in tasks]
        return self._compute_pairwise_similarities(reference_text, task_texts, use_batch_embedding=True)

    def _extract_theory_terms(self, ch2_text: str) -> list[str]:
        """Извлекает проверяемые термины из заголовков и определений теории."""
        raw_terms: list[str] = []
        raw_terms.extend(
            re.findall(r"^###\s+(?:2\.\d+\.?\s*)?(.+?)\s*$", ch2_text, flags=re.M | re.I)
        )
        raw_terms.extend(
            re.findall(
                r"\*\*([^*\n]{3,80})\*\*\s*(?:[—\-–]\s*(?:это|представляет|означает)|является|представляет)",
                ch2_text,
                flags=re.I,
            )
        )

        terms: list[str] = []
        seen: set[str] = set()
        generic = {"вопросы к практике", "пример", "глава", "теоретический блок"}
        for term in raw_terms:
            cleaned = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", term or "").strip(" .:-—")
            cleaned = re.sub(r"\s+", " ", cleaned)
            key = cleaned.lower()
            if not cleaned or key in generic or len(tokens(cleaned, self.lang)) == 0:
                continue
            if key not in seen:
                seen.add(key)
                terms.append(cleaned)
        return terms[:12]

    def _theory_practice_term_overlap(self, ch2_text: str, tasks: list[TaskBlock]) -> tuple[float, list[str]]:
        """Проверяет, что практика явно использует ключевые термины теории."""
        terms = self._extract_theory_terms(ch2_text)
        practice_text = " ".join(f"{task.title} {task.body}" for task in tasks)
        practice_tokens = set(tokens(practice_text, self.lang))
        matched: list[str] = []

        for term in terms:
            term_tokens = tokens(term, self.lang)
            if not term_tokens:
                continue
            common = len(set(term_tokens) & practice_tokens)
            if common / len(set(term_tokens)) >= 0.5:
                matched.append(term)

        if terms:
            return len(matched) / len(terms), matched

        theory_tokens = set(tokens(clean_markdown_prose_for_counting(ch2_text), self.lang))
        common = len(theory_tokens & practice_tokens)
        total_unique = len(theory_tokens | practice_tokens)
        return (common / total_unique if total_unique > 0 else 0.0), []

    def _compute_pairwise_similarities(
        self,
        reference_text: str,
        texts: list[str],
        use_batch_embedding: bool = True
    ) -> tuple[float, list[float]]:
        """Вычисляет similarity между референсным текстом и списком текстов."""
        if not texts or not reference_text.strip():
            return 0.0, []

        # Пытаемся использовать batch embedding для оптимизации
        if use_batch_embedding and self.embedding_function:
            embeddings = self._embed_many([reference_text] + texts)
            if embeddings is not None:
                try:
                    embeddings_len = len(embeddings)
                except (TypeError, ValueError):
                    embeddings_len = 0

                if embeddings_len >= len(texts) + 1:
                    v_ref = embeddings[0]
                    scores: list[float] = []
                    for i in range(len(texts)):
                        v_text = embeddings[i + 1]
                        sim = self._cosine_sim(v_ref, v_text)
                        scores.append(sim)

                    avg = sum(scores) / len(scores) if scores else 0.0
                    return avg, scores

        # Fallback: вычисляем similarity для каждого текста отдельно через bag-of-words
        scores: list[float] = []
        for text in texts:
            va = bag(tokens(reference_text, self.lang))
            vb = bag(tokens(text, self.lang))
            sim = cosine(va, vb)
            scores.append(sim)

        avg = sum(scores) / len(scores) if scores else 0.0
        return avg, scores
