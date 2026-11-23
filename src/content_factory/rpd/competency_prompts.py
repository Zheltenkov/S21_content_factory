"""Prompt construction for the bounded competency matcher, designer and critic agents."""

from __future__ import annotations

import json

from .competency_contracts import (
    CompetencyDraft,
    CompetencyEvidence,
    ProgramCompetencyPolicy,
    SourceCompetencyItem,
)
from .professional_standards import ProfessionalStandardRecord
from .up_models import UpPlan


def matcher_prompt(
    up: UpPlan,
    candidates: list[ProfessionalStandardRecord],
) -> tuple[str, str]:
    """Build a closed-world ranking prompt over catalog records already retrieved by code."""
    system = (
        "Ты сопоставляешь учебный план с кандидатами из локального снимка официального реестра "
        "профессиональных стандартов. Выбирай только standard_code и коды функций, присутствующие "
        "во входных данных. Это рекомендации для методиста, а не автоматическое нормативное утверждение. "
        "Не добавляй стандарты, должности, функции или коды из памяти."
    )
    payload = [candidate.model_dump(mode="json") for candidate in candidates]
    user = (
        f"Программа: {up.title or 'не указана'}\n"
        f"Модули: {'; '.join(module.title for module in up.modules) or 'не указаны'}\n"
        f"Навыки: {'; '.join(skill for module in up.modules for row in module.rows for skill in row.skills) or 'не указаны'}\n"
        f"Результаты: {'; '.join(up.all_outcomes()[:40]) or 'не указаны'}\n\n"
        "Кандидаты каталога:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Верни не более пяти наиболее релевантных кандидатов. Если содержательной связи нет, верни пустой список."
    )
    return system, user


def designer_prompt(
    up: UpPlan,
    policy: ProgramCompetencyPolicy,
    source_items: list[SourceCompetencyItem],
    evidence: list[CompetencyEvidence],
    *,
    repair_issues: list[str] | None = None,
) -> tuple[str, str]:
    """Build a schema-first competence design prompt with an explicit evidence allow-list."""
    family_name = "профессиональные" if policy.family == "ПК" else "общие/общеобразовательные"
    system = (
        f"Ты проектировщик образовательных результатов. Сгруппируй исходные элементы в {family_name} "
        f"компетенции семейства {policy.family}. Не присваивай номера: локальные коды назначит программа. "
        "Каждая компетенция должна описывать наблюдаемую способность, а не перечислять названия навыков. "
        "covered_source_ids, evidence_refs и assessment_refs могут содержать только идентификаторы из входа. "
        "Профессиональный стандарт допустим только через предоставленные evidence_refs. "
        "Не придумывай нормативные источники, должности, ОТФ, ТФ, квалификационные уровни или формы оценки. "
        "Для каждой компетенции обязательно заполни baseline, qualitative_change, after_training и assessment. "
        "baseline описывает осторожно предполагаемый старт: отдельные действия по образцу без устойчивого "
        "алгоритма; не утверждай, что проводилась входная диагностика. qualitative_change формулируй как "
        "переход от стартового поведения к наблюдаемому результату после обучения. assessment кратко "
        "описывает выбранное средство оценки, но его assessment_refs обязаны ссылаться на assessments "
        "покрытых исходных элементов."
    )
    repair = (
        "\nЗамечания предыдущей проверки, которые необходимо исправить:\n- " + "\n- ".join(repair_issues)
        if repair_issues
        else ""
    )
    user = (
        f"Программа: {up.title or 'не указана'}\n"
        f"Тип: {policy.program_type.value}; семейство: {policy.family}\n"
        f"Допустимо не более {policy.max_competencies} компетенций.\n\n"
        f"Исходные элементы:\n{json.dumps([item.model_dump(mode='json') for item in source_items], ensure_ascii=False, indent=2)}\n\n"
        f"Разрешенные доказательства:\n{json.dumps([item.model_dump(mode='json') for item in evidence], ensure_ascii=False, indent=2)}"
        f"{repair}\n\n"
        "Покрой каждый source_id хотя бы одной компетенцией. Все смысловые поля обязательны. Для assessment_refs "
        "выбери один или несколько assessment_id из покрытых source_id; не предлагай экзамены, тесты или защиты, "
        "которых нет среди assessments входа."
    )
    return system, user


def critic_prompt(
    policy: ProgramCompetencyPolicy,
    source_items: list[SourceCompetencyItem],
    evidence: list[CompetencyEvidence],
    drafts: list[CompetencyDraft],
) -> tuple[str, str]:
    """Build an independent groundedness and coverage review prompt."""
    system = (
        "Ты независимый методист-критик. Проверь набор компетенций на полноту покрытия исходных навыков, "
        "наблюдаемость формулировок, смысловые дубли и доказательность. Отдельно проверь, что baseline не "
        "выдаёт предположение за результат входной диагностики, qualitative_change описывает реальный переход, "
        "а assessment_refs относятся к заданиям покрытых исходных элементов. Любая ссылка вне разрешённого "
        "списка является unsupported. Не исправляй текст и не добавляй новые источники — верни только "
        "структурированный результат проверки."
    )
    user = (
        f"Тип программы: {policy.program_type.value}; семейство: {policy.family}.\n"
        f"Исходные элементы:\n{json.dumps([item.model_dump(mode='json') for item in source_items], ensure_ascii=False, indent=2)}\n\n"
        f"Разрешенные доказательства:\n{json.dumps([item.model_dump(mode='json') for item in evidence], ensure_ascii=False, indent=2)}\n\n"
        f"Черновик:\n{json.dumps([item.model_dump(mode='json') for item in drafts], ensure_ascii=False, indent=2)}"
    )
    return system, user


__all__ = ["critic_prompt", "designer_prompt", "matcher_prompt"]
