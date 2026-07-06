"""Критерии целостности (N.1–N.5) для RubricScorer.

Оборачивает детерминированные сигналы из `validators.integrity_signals` в
`CriteriaItem`. Структурная ось v2: ловит слитые таблицы, дословные повторы,
диаграммы не по теме, оборванные кавычки, множественные id проекта.

Rollout advisory-first: все критерии помечены `StrictnessLevel.SOFT`, поэтому
провал становится предупреждением (`policy.apply_rubric_warning_policy`), а не
блокирует пайплайн. После калибровки порогов на корпусе часть критериев
промотируется в HARD отдельным решением (тогда сработает гейт, см. gate.py).
"""

from __future__ import annotations

from ...models.criteria_models import CheckMethod, CriteriaItem, StrictnessLevel
from ...models.readme_document import ReadmeDocument
from ..integrity_signals import IntegritySignal, all_integrity_signals


class IntegrityChecker:
    """Проверяет целостность README (N.1–N.5) поверх сырого markdown."""

    def check(self, md: str) -> list[CriteriaItem]:
        return [self._to_item(signal) for signal in all_integrity_signals(md)]

    def check_document(self, document: ReadmeDocument) -> list[CriteriaItem]:
        return self.check(document.to_markdown())

    @staticmethod
    def _to_item(signal: IntegritySignal) -> CriteriaItem:
        return CriteriaItem(
            id=signal.id,
            title=signal.title,
            description=signal.title,
            check_method=CheckMethod.SCRIPT,
            score=1 if signal.passed else 0,
            comments=list(signal.comments),
            parent_id="N",
            strictness=StrictnessLevel.SOFT,
            details=dict(signal.details) if signal.details else None,
        )
