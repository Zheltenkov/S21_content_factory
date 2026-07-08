"""Presentation labels for the catalog UI.

Pure string-mapping helpers and their lookup tables, extracted from ``viewer/app.py``
during its decomposition. No database or request state — safe to import anywhere.
"""

from __future__ import annotations

REVIEW_REASON_LABELS = {
    "missing_dimension": "Не указан тип индикатора",
    "missing_block_title": "У блока нет названия",
    "orphan_indicator_row": "Строка не привязана к skill",
    "ambiguous_skill_name": "Нужно уточнить название skill",
    "no_header_rows": "Не найден заголовок блока",
    "ambiguous_block_title": "Нужно уточнить название блока",
    "level_headers_inherited_from_previous_block": "Шкала унаследована от предыдущего блока",
    "skill_name_trimmed": "Название skill было очищено",
    "base_text_without_levels": "Есть текст индикатора без уровней",
    "novel_skill": "Новый skill не найден в каталоге",
    "fuzzy_match_ambiguous": "Нечеткое совпадение с каталогом",
    "low_confidence": "Низкая уверенность",
    "single_source": "Недостаточно подтверждающих источников",
    "council_split": "Модели не согласились между собой",
    "catalog_match_suspicious": "Подозрительный match с каталогом: нужно проверить смысл и группу canonical skill",
    "new_competency_candidate": "Новая competency требует подтверждения",
    "missing_observable_action": "Название не похоже на наблюдаемый навык: нет действия или отглагольного существительного",
    "auto_accept_policy": "Автопринято по policy: уверенность >= 0.95 и согласие жюри = 1.00",
    "composite_decomposed": "Кандидат разбит на атомарные части",
    "non_skill:competency_block": "Это блок программы, а не skill",
    "non_skill:curriculum_section": "Это учебный раздел, а не skill",
    "program_brief_publication_guardrail": "Новый skill из program brief требует методологического подтверждения",
    "needs_review": "Нужна методологическая проверка",
    "cycle_broken": "Цикл в графе был разорван",
    "redundant_transitive": "Ребро признано транзитивно избыточным",
    "bloom_direction": "Возможный спорный порядок по уровню сложности",
    "ai_proposed": "Связь предложена системой и требует проверки",
}
REVIEW_STATUS_LABELS = {
    "open": "Открыто",
    "resolved": "Решено",
    "ignored": "Пропущено",
    "all": "Все",
}
REVIEW_SEVERITY_LABELS = {
    "error": "Ошибка",
    "warning": "Внимание",
    "info": "Инфо",
    "all": "Все",
}
INTAKE_JOB_STATUS_LABELS = {
    "pending": "В очереди",
    "running": "Обрабатывается",
    "succeeded": "Готово",
    "failed": "Ошибка",
}
INTAKE_STAGE_LABELS = {
    "queued": "Постановка в очередь",
    "starting": "Запуск",
    "decompose": "Декомпозиция брифа",
    "draft": "Черновик навыков",
    "atomize": "Атомизация кандидатов",
    "normalize": "Нормализация и дедупликация",
    "resolve": "Сопоставление с каталогом",
    "search": "Поиск evidence по серой зоне",
    "council": "Экспертное жюри",
    "triage": "Финальный триаж",
    "ready_for_review": "Готово к проверке",
    "prerequisites": "Пререквизиты",
    "persist": "Запись в БД",
    "catalog_apply": "Применение в справочник",
    "templates": "Шаблоны УП",
    "plan": "Черновик УП",
    "completed": "Завершено",
    "failed": "Ошибка",
}
REVIEW_ENTITY_LABELS = {
    "skill": "Навык",
    "competency": "Компетенция",
    "indicator_row": "Индикатор",
    "profile": "Профиль",
    "project": "Проект",
    "project_indicator": "Индикатор проекта",
    "prerequisite_edge": "Связь зависимостей",
    "ai_analysis_run": "Запуск анализа",
    "workbook": "Файл",
    "sheet": "Лист",
    "block": "Блок",
}
REVIEW_TEXT_REPLACEMENTS = {
    "program_brief_publication_guardrail": "новый навык из брифа требует методологического подтверждения",
    "catalog_match_suspicious": "подозрительное совпадение с каталогом",
    "missing_observable_action": "нет наблюдаемого действия",
    "fuzzy_match_ambiguous": "неоднозначное похожее совпадение",
    "auto_accept_policy": "автопринято по правилу",
    "novel_skill": "новый навык",
    "low_confidence": "низкая уверенность",
    "single_source": "недостаточно источников",
    "council_split": "жюри не согласилось",
    "needs_review": "нужно проверить",
    "bloom_direction": "возможный спорный порядок по уровню сложности",
    "ai_proposed": "связь предложена системой",
    "prerequisite_edge": "связь зависимостей",
    "edge_key": "код связи",
    "src_id": "исходный навык",
    "dst_id": "следующий навык",
    "edge_label": "связь",
    "confidence": "уверенность",
    "source": "источник",
    "relation_type": "тип связи",
    "soft": "мягкая методическая связь",
    "Резолв против каталога": "Сопоставление с каталогом",
    "Атомарность": "Атомарность",
    "competency": "компетенция",
    "skills": "навыки",
    "atomic": "атомарный",
    "new": "новый",
    "matched": "найдено совпадение",
    "alias": "найден синоним",
    "fuzzy": "похожий вариант",
    "skill": "навык",
}


def review_reason_label(reason_code: str | None) -> str:
    if not reason_code:
        return "Нужна проверка"
    if "," in reason_code:
        labels = [
            REVIEW_REASON_LABELS.get(part.strip(), part.strip().replace("_", " "))
            for part in reason_code.split(",")
            if part.strip()
        ]
        return "; ".join(labels) if labels else "Нужна проверка"
    return REVIEW_REASON_LABELS.get(reason_code, reason_code.replace("_", " "))


def edge_reason_label(value: object | None) -> str:
    """Translate one or several stored edge reason codes into UI labels."""
    if value is None:
        return "—"
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw_items = [str(item) for item in value if item]
    else:
        raw_items = [str(value)]
    labels = [review_reason_label(item.strip()) for item in raw_items if item and item.strip()]
    return ", ".join(labels) if labels else "—"


def review_entity_label(entity_type: str | None) -> str:
    if not entity_type:
        return "Объект"
    return REVIEW_ENTITY_LABELS.get(entity_type, entity_type.replace("_", " "))


def review_source_label(source_ref: str | None) -> str:
    if not source_ref:
        return "Источник не указан"
    source = str(source_ref)
    if source.startswith("brief:"):
        return f"Бриф #{source.split(':', 1)[1]}"
    if source.startswith("intake_accept:"):
        return f"Принятие в справочник #{source.split(':', 1)[1]}"
    return source.replace("_", " ")


def review_text_label(text: object | None) -> str:
    if text is None:
        return "—"
    normalized = str(text)
    for source, replacement in sorted(REVIEW_TEXT_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = normalized.replace(source, replacement)
    return normalized


def review_status_label(status: str | None) -> str:
    if not status:
        return "Не указан"
    return REVIEW_STATUS_LABELS.get(status, status)


def review_severity_label(severity: str | None) -> str:
    if not severity:
        return "Не указано"
    return REVIEW_SEVERITY_LABELS.get(severity, severity)


def intake_job_status_label(status: str | None) -> str:
    if not status:
        return "Неизвестно"
    return INTAKE_JOB_STATUS_LABELS.get(status, status)


def intake_stage_label(stage: str | None) -> str:
    if not stage:
        return "Не указан"
    return INTAKE_STAGE_LABELS.get(stage, stage)
