"""
Проверки теории (критерии 2.4.x) после генерации.
Проверяет HARD критерии и может триггерить локальную Regeneration.
"""

from dataclasses import dataclass

from ..config.thresholds import THRESHOLDS
from ..models.schemas import TheoryPart
from ..utils.text_analysis import count_words, has_term_definitions, readability_index
from .messages import theory_section_label


@dataclass
class TheoryCheckIssue:
    """Проблема в части теории."""
    part_index: int
    part_title: str
    criterion_id: str
    severity: str  # "hard" или "soft"
    message: str
    fixable: bool


@dataclass
class TheoryChecksResult:
    """Результат проверки теории."""
    passed: bool
    hard_issues: list[TheoryCheckIssue]
    soft_issues: list[TheoryCheckIssue]
    all_issues: list[TheoryCheckIssue]


class TheoryChecks:
    """
    Проверка теории по критериям 2.4.x.
    
    Проверяет:
    - 2.4.1: Количество частей (2-5)
    - 2.4.3: Длина частей (100-300 слов)
    - 2.4.4: Определения терминов (желательны, но не блокируют flow)
    - 2.4.6: Наличие "Пример" и "Вопросы к практике"
    - 2.4.7: Читабельность (10-25 для русского)
    """

    def __init__(self, language: str = "ru"):
        self.language = language

    def check(self, parts: list[TheoryPart]) -> TheoryChecksResult:
        """
        Проверяет части теории по критериям.
        
        Args:
            parts: Список частей теории
            
        Returns:
            TheoryChecksResult
        """
        hard_issues = []
        soft_issues = []

        # Проверка 2.4.1: Количество частей
        lo, hi = THRESHOLDS["theory_parts"]
        if len(parts) < lo or len(parts) > hi:
            hard_issues.append(TheoryCheckIssue(
                part_index=-1,
                part_title="",
                criterion_id="2.4.1",
                severity="hard",
                message=f"Количество теоретических разделов: {len(parts)} (ожидается {lo}-{hi})",
                fixable=True
            ))

        # Проверка каждой части
        for idx, part in enumerate(parts, 1):
            # Проверка 2.4.3: Длина части
            lo_words, hi_words = THRESHOLDS["theory_words_per_part"]
            hi_words = max(hi_words, int(hi_words * 1.2))
            words = count_words(part.body, self.language)
            if words < lo_words or words > hi_words:
                hard_issues.append(TheoryCheckIssue(
                    part_index=idx,
                    part_title=part.title,
                    criterion_id="2.4.3",
                    severity="hard",
                    message=f"{theory_section_label(idx, part.title)}: длина {words} слов (ожидается {lo_words}-{hi_words})",
                    fixable=True
                ))

            # Проверка 2.4.4: Определения терминов.
            # Для flow это soft-критерий: отсутствие явных определений ухудшает качество,
            # но не должно останавливать генерацию целого README.
            has_defs, patterns_found = has_term_definitions(part.body, self.language, min_definitions=1, require_bold=True)
            if not has_defs:
                # Проверяем, есть ли определения без жирного выделения
                has_defs_no_bold, patterns_no_bold = has_term_definitions(part.body, self.language, min_definitions=1, require_bold=False)
                if has_defs_no_bold:
                    soft_issues.append(TheoryCheckIssue(
                        part_index=idx,
                        part_title=part.title,
                        criterion_id="2.4.4",
                        severity="soft",
                        message=(
                            f"{theory_section_label(idx, part.title)}: найдено {len(patterns_no_bold)} "
                            "определение(й), но термины не выделены жирным (**термин**)."
                        ),
                        fixable=True
                    ))
                else:
                    soft_issues.append(TheoryCheckIssue(
                        part_index=idx,
                        part_title=part.title,
                        criterion_id="2.4.4",
                        severity="soft",
                        message=f"{theory_section_label(idx, part.title)}: не найдено явных определений терминов.",
                        fixable=True
                    ))

            # Проверка 2.4.6: Пример и вопросы
            if not part.example or len(part.example.strip()) < 10:
                hard_issues.append(TheoryCheckIssue(
                    part_index=idx,
                    part_title=part.title,
                    criterion_id="2.4.6",
                    severity="hard",
                    message=f"{theory_section_label(idx, part.title)}: отсутствует или слишком короткий пример",
                    fixable=True
                ))

            if not part.bridge_questions or len(part.bridge_questions) < 1:
                hard_issues.append(TheoryCheckIssue(
                    part_index=idx,
                    part_title=part.title,
                    criterion_id="2.4.6",
                    severity="hard",
                    message=f"{theory_section_label(idx, part.title)}: отсутствуют вопросы к практике",
                    fixable=True
                ))

            # Проверка 2.4.7: Читабельность (soft)
            readability = readability_index(part.body, self.language)
            if readability < 10 or readability > 25:
                soft_issues.append(TheoryCheckIssue(
                    part_index=idx,
                    part_title=part.title,
                    criterion_id="2.4.7",
                    severity="soft",
                    message=f"{theory_section_label(idx, part.title)}: читабельность {readability:.1f} (рекомендуется 10-25)",
                    fixable=True
                ))

        all_issues = hard_issues + soft_issues
        passed = len(hard_issues) == 0

        return TheoryChecksResult(
            passed=passed,
            hard_issues=hard_issues,
            soft_issues=soft_issues,
            all_issues=all_issues
        )
