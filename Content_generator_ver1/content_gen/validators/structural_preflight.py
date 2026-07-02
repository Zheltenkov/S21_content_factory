"""
Structural Preflight - быстрая проверка структуры документа после каркаса.
Проверяет HARD критерии 1.x и 2.3.x до генерации теории/практики.
"""

import re
from dataclasses import dataclass

from ..config.thresholds import THRESHOLDS
from ..utils.text_analysis import count_words


@dataclass
class StructuralIssue:
    """Проблема структуры."""
    criterion_id: str
    severity: str  # "hard" или "soft"
    message: str
    fixable: bool  # Можно ли автоматически исправить


@dataclass
class StructuralPreflightResult:
    """Результат проверки структуры."""
    passed: bool
    hard_issues: list[StructuralIssue]
    soft_issues: list[StructuralIssue]
    all_issues: list[StructuralIssue]


class StructuralPreflight:
    """
    Быстрая проверка структуры документа.
    
    Проверяет HARD критерии:
    - 1.1-1.6: H1, аннотация, TOC, Главы 1-3
    - 2.3.1-2.3.4: Введение и Инструкция, длины
    """

    def __init__(self, language: str = "ru"):
        self.language = language

        # Регулярные выражения
        self.rx_h1 = re.compile(r"^#\s+(.+)$", re.M)
        self.rx_toc = re.compile(r"^##\s+(Содержание|Оглавление|Content|Мазмун)\s*$", re.M)
        self.rx_chapter1 = re.compile(r"^##\s+Глава\s+1[^\n]*\n", re.M)
        self.rx_chapter2 = re.compile(r"^##\s+Глава\s+2[^\n]*\n", re.M)
        self.rx_chapter3 = re.compile(r"^##\s+Глава\s+3[^\n]*\n", re.M)
        self.rx_intro = re.compile(
            r"^###\s+(?:Введение|Вводная часть|Контекст проекта)\s*(.+?)(?=^###\s+(?:Инструкция|Правила выполнения|Требования)|\Z)",
            re.S | re.M | re.I,
        )
        self.rx_instr = re.compile(
            r"^###\s+(?:Инструкция|Правила выполнения|Требования)\s*(.+)$",
            re.S | re.M | re.I,
        )

    def check(self, md: str, has_bonus: bool = False) -> StructuralPreflightResult:
        """
        Проверяет структуру документа.
        
        Args:
            md: Markdown документ
            has_bonus: Есть ли бонусные задания
            
        Returns:
            StructuralPreflightResult
        """
        hard_issues = []
        soft_issues = []

        # Проверка 1.1: H1 заголовок
        h1_match = self.rx_h1.search(md)
        if not h1_match:
            hard_issues.append(StructuralIssue(
                criterion_id="1.1",
                severity="hard",
                message="Отсутствует H1 заголовок",
                fixable=False
            ))
        else:
            title = h1_match.group(1).strip()
            # Проверка 1.2: длина заголовка (1-3 слова)
            words = len(title.split())
            if words < 1 or words > 3:
                soft_issues.append(StructuralIssue(
                    criterion_id="1.2",
                    severity="soft",
                    message=f"Заголовок содержит {words} слов (ожидается 1-3)",
                    fixable=True
                ))

        # Проверка 1.3: Аннотация сразу под H1
        if h1_match:
            # Ищем аннотацию после H1 (до TOC или следующего заголовка)
            h1_end = h1_match.end()
            next_header = re.search(r"^##\s+", md[h1_end:], re.M)
            if next_header:
                annotation_text = md[h1_end:h1_end + next_header.start()].strip()
            else:
                annotation_text = md[h1_end:].strip()

            # Убираем пустые строки
            annotation_text = re.sub(r'^\s*\n+', '', annotation_text)

            if not annotation_text or len(annotation_text) < 50:
                hard_issues.append(StructuralIssue(
                    criterion_id="1.3",
                    severity="hard",
                    message="Отсутствует или слишком короткая аннотация под H1",
                    fixable=False
                ))
            else:
                # Проверка 1.4: длина аннотации (300-800 символов)
                lo, hi = THRESHOLDS["annotation_chars"]
                if len(annotation_text) < lo or len(annotation_text) > hi:
                    soft_issues.append(StructuralIssue(
                        criterion_id="1.4",
                        severity="soft",
                        message=f"Длина аннотации {len(annotation_text)} символов (ожидается {lo}-{hi})",
                        fixable=True
                    ))

        # Проверка 1.5: TOC
        toc_match = self.rx_toc.search(md)
        if not toc_match:
            soft_issues.append(StructuralIssue(
                criterion_id="1.5",
                severity="soft",
                message="Отсутствует оглавление (TOC)",
                fixable=True
            ))

        # Проверка 1.6: Главы 1-3
        if not self.rx_chapter1.search(md):
            hard_issues.append(StructuralIssue(
                criterion_id="1.6.1",
                severity="hard",
                message="Отсутствует Глава 1",
                fixable=False
            ))

        if not self.rx_chapter2.search(md):
            hard_issues.append(StructuralIssue(
                criterion_id="1.6.2",
                severity="hard",
                message="Отсутствует Глава 2",
                fixable=False
            ))

        if not self.rx_chapter3.search(md):
            hard_issues.append(StructuralIssue(
                criterion_id="1.6.3",
                severity="hard",
                message="Отсутствует Глава 3",
                fixable=False
            ))

        if not re.search(r"^##\s+(?:Заключение|Итог проекта|Финал проекта|Завершение проекта)\b", md, re.M | re.I):
            soft_issues.append(StructuralIssue(
                criterion_id="1.6.4",
                severity="soft",
                message="Отсутствует финальный раздел завершения текущего проекта",
                fixable=True
            ))

        # Проверка 2.3.1-2.3.4: Введение и Инструкция
        intro_match = self.rx_intro.search(md)
        if not intro_match:
            hard_issues.append(StructuralIssue(
                criterion_id="2.3.1",
                severity="hard",
                message="Отсутствует секция «Введение» в Главе 1",
                fixable=False
            ))
        else:
            intro_text = intro_match.group(1).strip()
            # Проверка 2.3.2: длина введения (80-250 слов)
            lo, hi = THRESHOLDS["intro_words"]
            words_intro = count_words(intro_text, self.language)
            if words_intro < lo or words_intro > hi:
                hard_issues.append(StructuralIssue(
                    criterion_id="2.3.2",
                    severity="hard",
                    message=f"Длина введения {words_intro} слов (ожидается {lo}-{hi})",
                    fixable=True
                ))

        instr_match = self.rx_instr.search(md)
        if not instr_match:
            hard_issues.append(StructuralIssue(
                criterion_id="2.3.3",
                severity="hard",
                message="Отсутствует секция «Инструкция» в Главе 1",
                fixable=False
            ))
        else:
            instr_text = instr_match.group(1).strip()
            # Проверка 2.3.4: ключевые слова в инструкции
            instr_lower = instr_text.lower()
            required_keywords = ["допускается", "запрещено", "обязательно"]
            found_keywords = [kw for kw in required_keywords if kw in instr_lower]
            if len(found_keywords) < 2:
                soft_issues.append(StructuralIssue(
                    criterion_id="2.3.4",
                    severity="soft",
                    message=f"В инструкции найдено только {len(found_keywords)} из 3 ключевых слов (допускается/запрещено/обязательно)",
                    fixable=True
                ))

        all_issues = hard_issues + soft_issues
        passed = len(hard_issues) == 0

        return StructuralPreflightResult(
            passed=passed,
            hard_issues=hard_issues,
            soft_issues=soft_issues,
            all_issues=all_issues
        )
