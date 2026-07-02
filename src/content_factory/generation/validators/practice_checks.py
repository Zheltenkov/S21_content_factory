"""
Проверки практики (критерии 2.5.x) после генерации.
Проверяет HARD критерии и может триггерить локальную Regeneration.
"""

import re
from dataclasses import dataclass

from ..config.active_goals import has_active_goal_verb
from ..config.thresholds import THRESHOLDS
from ..models.schemas import PracticeTask
from ..practice_contract import find_non_raw_material_issues, task_uses_previous_artifact
from ..utils.text_analysis import count_words
from .messages import practice_task_label


@dataclass
class PracticeCheckIssue:
    """Проблема в задаче."""
    task_index: int
    task_title: str
    criterion_id: str
    severity: str  # "hard" или "soft"
    message: str
    fixable: bool


@dataclass
class PracticeChecksResult:
    """Результат проверки практики."""
    passed: bool
    hard_issues: list[PracticeCheckIssue]
    soft_issues: list[PracticeCheckIssue]
    all_issues: list[PracticeCheckIssue]


class PracticeChecks:
    """
    Проверка практики по критериям 2.5.x.
    
    Проверяет:
    - 2.5.1: Количество задач (3-8)
    - 2.5.2: Наличие 5 блоков в каждой задаче
    - 2.5.3: Цель в активной форме
    - 2.5.4: Корректность и компактность блока "Подход"
    - 2.5.5: Входные данные и локация результата
    - 2.5.6: P2P-проверяемость
    - 2.5.7: Связь с LO и теорией
    """

    def __init__(
        self,
        language: str = "ru",
        expected_tasks: int = None,
        *,
        check_task_count: bool = True,
    ):
        self.language = language
        self.expected_tasks = expected_tasks
        self.check_task_count = check_task_count

    @staticmethod
    def _looks_like_path(text: str) -> bool:
        return bool(re.search(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+", text or ""))

    @staticmethod
    def _has_active_goal(goal: str) -> bool:
        return has_active_goal_verb(goal)

    @staticmethod
    def _has_banned_goal(goal: str) -> bool:
        return bool(re.search(r"\b(изучи|изучить|ознакомься|ознакомиться|посмотри|посмотреть|рассмотри|рассмотреть|пойми|понять)\b", goal or "", flags=re.I))

    @staticmethod
    def _is_observable_p2p_criterion(text: str) -> bool:
        signals = [
            "содержит", "указан", "указаны", "есть", "присутствует", "присутствуют",
            "нет", "совпадает", "заполнен", "заполнены", "описан", "описаны",
            "размещен", "размещён", "добавлен", "добавлены", "прикреплен", "прикреплён",
            "оформлен", "оформлена", "перечислен", "перечислены", "зафиксирован",
            "зафиксированы", "учтен", "учтены", "обоснован", "обоснование",
            "путь", "файл", "раздел", "схема", "таблица", "презентация",
            "по указанному пути", "в документе", "в таблице", "на схеме", "минимум",
        ]
        normalized = (text or "").strip().lower()
        return len(normalized) >= 12 and any(signal in normalized for signal in signals)

    @staticmethod
    def _looks_like_situation(text: str) -> bool:
        normalized = (text or "").strip().lower()
        if len(normalized) < 25:
            return False
        actor_signals = ["ты", "команда", "заказчик", "клиент", "коллега", "ревьюер", "проект"]
        tension_signals = [
            "нужно", "необходимо", "проблем", "риск", "срок", "дедлайн", "ошиб",
            "конфликт", "неяс", "задерж", "не понима", "не хватает", "важно",
            "обсуждени", "согласован", "утверждени", "иначе", "зависит",
        ]
        has_actor = any(signal in normalized for signal in actor_signals)
        has_tension = any(signal in normalized for signal in tension_signals)
        return (has_actor and has_tension) or len(normalized) >= 60

    def check(self, tasks: list[PracticeTask]) -> PracticeChecksResult:
        """
        Проверяет задачи по критериям.
        
        Args:
            tasks: Список задач
            
        Returns:
            PracticeChecksResult
        """
        hard_issues = []
        soft_issues = []

        if self.check_task_count:
            # Проверка 2.5.1: Количество задач
            lo, hi = THRESHOLDS["practice_tasks_range"]
            if len(tasks) < lo or len(tasks) > hi:
                hard_issues.append(PracticeCheckIssue(
                    task_index=-1,
                    task_title="",
                    criterion_id="2.5.1",
                    severity="hard",
                    message=f"Количество практических заданий: {len(tasks)} (ожидается {lo}-{hi})",
                    fixable=True
                ))

            # Если указано ожидаемое количество, проверяем точное соответствие
            if self.expected_tasks and len(tasks) != self.expected_tasks:
                hard_issues.append(PracticeCheckIssue(
                    task_index=-1,
                    task_title="",
                    criterion_id="2.5.1",
                    severity="hard",
                    message=f"Количество практических заданий: {len(tasks)} (ожидалось {self.expected_tasks})",
                    fixable=True
                ))

        # Проверка каждой задачи
        for idx, task in enumerate(tasks, 1):
            task_label = practice_task_label(idx, task.title)
            previous_task = tasks[idx - 2] if idx > 1 else None

            # Проверка 2.5.2: Наличие 5 блоков
            has_situation = bool(getattr(task, "situation", "") and task.situation.strip())
            has_input = bool(task.input_data and task.input_data.strip())
            has_goal = bool(task.goal and task.goal.strip())
            has_approach = bool(task.approach_bullets and len(task.approach_bullets) > 0)
            has_artifact = bool(task.expected_artifact and task.expected_artifact.strip())

            missing_blocks = []
            if not has_situation:
                missing_blocks.append("Ситуация")
            if not has_input:
                missing_blocks.append("Входные данные")
            if not has_goal:
                missing_blocks.append("Цель")
            if not has_approach:
                missing_blocks.append("Подход")
            if not has_artifact:
                missing_blocks.append("Ожидаемый результат")

            if missing_blocks:
                hard_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.2",
                    severity="hard",
                    message=f"{task_label}: отсутствуют блоки: {', '.join(missing_blocks)}",
                    fixable=True
                ))
            elif not self._looks_like_situation(task.situation):
                soft_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.2",
                    severity="soft",
                    message=f"{task_label}: блок «Ситуация» слишком общий и не задаёт рабочее напряжение",
                    fixable=True
                ))

            if not getattr(task, "constraints_or_risk", "").strip():
                soft_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.2",
                    severity="soft",
                    message=f"{task_label}: нет явного блока «Ограничение / риск», задание выглядит слишком линейным",
                    fixable=True
                ))

            non_raw_materials = find_non_raw_material_issues(task.input_data)
            if non_raw_materials:
                hard_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.materials",
                    severity="hard",
                    message=(
                        f"{task_label}: входные материалы похожи на готовый результат "
                        f"студента, а не на сырые данные: {', '.join(non_raw_materials)}"
                    ),
                    fixable=True
                ))

            if previous_task and not task_uses_previous_artifact(task, previous_task):
                hard_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.dependency",
                    severity="hard",
                    message=(
                        f"{task_label}: не использует артефакт предыдущего задания "
                        f"`{previous_task.artifact_location}` как входные данные"
                    ),
                    fixable=True
                ))

            # Проверка 2.5.3: Цель в активной форме
            if has_goal:
                if self._has_banned_goal(task.goal):
                    hard_issues.append(PracticeCheckIssue(
                        task_index=idx,
                        task_title=task.title,
                        criterion_id="2.5.3",
                        severity="hard",
                        message=f"{task_label}: цель содержит запрещённую пассивную формулировку",
                        fixable=True
                    ))
                elif not self._has_active_goal(task.goal):
                    soft_issues.append(PracticeCheckIssue(
                        task_index=idx,
                        task_title=task.title,
                        criterion_id="2.5.3",
                        severity="soft",
                        message=f"{task_label}: цель не выглядит как активное действие с результатом",
                        fixable=True
                    ))

            # Проверка 2.5.4: Длина и структура подхода (≤150 слов, 2-6 пунктов)
            if has_approach:
                approach_text = " ".join(task.approach_bullets)
                approach_words = count_words(approach_text, self.language)
                max_words = THRESHOLDS["approach_words_max"]
                if approach_words > max_words:
                    hard_issues.append(PracticeCheckIssue(
                        task_index=idx,
                        task_title=task.title,
                        criterion_id="2.5.4",
                        severity="hard",
                        message=f"{task_label}: подход содержит {approach_words} слов (максимум {max_words})",
                        fixable=True
                    ))
                if not (2 <= len(task.approach_bullets) <= 6):
                    hard_issues.append(PracticeCheckIssue(
                        task_index=idx,
                        task_title=task.title,
                        criterion_id="2.5.4",
                        severity="hard",
                        message=f"{task_label}: в подходе {len(task.approach_bullets)} пунктов (ожидается 2-6)",
                        fixable=True
                    ))

            # Проверка 2.5.5: Входные данные и локация результата
            if has_input and len(task.input_data.strip()) < 10:
                hard_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.5",
                    severity="hard",
                    message=f"{task_label}: входные данные слишком короткие или неявные",
                    fixable=True
                ))

            artifact_text = task.expected_artifact.strip() if has_artifact else ""
            artifact_location = (task.artifact_location or "").strip()
            has_location = self._looks_like_path(artifact_text) or self._looks_like_path(artifact_location)

            if has_artifact and len(artifact_text) < 10:
                hard_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.5",
                    severity="hard",
                    message=f"{task_label}: локация результата не указана или неявная",
                    fixable=True
                ))
            elif has_artifact and not has_location:
                hard_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.5",
                    severity="hard",
                    message=f"{task_label}: в ожидаемом результате нет явного пути к артефакту",
                    fixable=True
                ))

            # Проверка 2.5.6: P2P-проверяемость
            criteria = [criterion.strip() for criterion in (task.p2p_criteria or []) if criterion.strip()]
            if len(criteria) < 3:
                hard_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.6",
                    severity="hard",
                    message=f"{task_label}: недостаточно критериев P2P-проверки (найдено {len(criteria)}, ожидается минимум 3)",
                    fixable=True
                ))
            else:
                observable_count = sum(1 for criterion in criteria if self._is_observable_p2p_criterion(criterion))
                if observable_count < 2:
                    soft_issues.append(PracticeCheckIssue(
                        task_index=idx,
                        task_title=task.title,
                        criterion_id="2.5.6",
                        severity="soft",
                        message=f"{task_label}: критерии P2P выглядят слишком общими и плохо проверяемыми",
                        fixable=True
                    ))

            if not getattr(task, "covered_outcomes", None):
                soft_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.7",
                    severity="soft",
                    message=f"{task_label}: задание не привязано к конкретному LO текущего проекта",
                    fixable=True
                ))
            if not getattr(task, "theory_support", None):
                soft_issues.append(PracticeCheckIssue(
                    task_index=idx,
                    task_title=task.title,
                    criterion_id="2.5.7",
                    severity="soft",
                    message=f"{task_label}: задание не ссылается на конкретные темы из теории",
                    fixable=True
                ))

        all_issues = hard_issues + soft_issues
        passed = len(hard_issues) == 0

        return PracticeChecksResult(
            passed=passed,
            hard_issues=hard_issues,
            soft_issues=soft_issues,
            all_issues=all_issues
        )
