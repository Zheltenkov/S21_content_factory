"""Категории разделов ДПО-шаблона «повышение квалификации» — спека из комментариев методиста.

Определяет, ОТКУДА берётся содержимое каждого раздела при сборке РПД:
- ``UP_*``       — из УП (учебный план, график, модули, компетенции) — ядро ценности;
- ``GENERATE``   — проза, генерируется LLM прямо в РПД (актуальность длинно, цели поверхностно, паспорт);
- ``BOILERPLATE``— фиксированный текст ФОРМЫ, держим как в шаблоне (нормативка, документ, инвалиды);
- ``PLATFORM``   — нужен скрин задания с платформы/LMS → авто НЕ закрывается, помечаем;
- ``GAP``        — специфично для программы, методист впишет (оценочные материалы, разработчики).

Правила — из комментариев к `Шаблон_ДПО.docx` (#8 актуальность длинно, #10 цели поверхностно,
#11 компетенции через профстандарт, #12 учебный план — ядро, #17 оценочные — только после платформы).
"""

from __future__ import annotations

from enum import StrEnum


class Cat(StrEnum):
    CONTAINER = "container"
    UP_PLAN = "up_plan"
    UP_GRAFIK = "up_grafik"
    UP_MODULES = "up_modules"
    UP_COMP = "up_comp"
    GENERATE = "generate"
    BOILERPLATE = "boilerplate"
    PLATFORM = "platform"
    GAP = "gap"


def category_for(number: str, title: str) -> Cat:
    """Категория раздела по номеру/заголовку (порядок проверок важен из-за пересечений слов)."""
    del number
    t = title.lower()

    if "оформления учебного модуля" in t:  # 3.3.1 — данные УП + обязательный скрин по каждому модулю
        return Cat.UP_MODULES
    if "пример" in t or "скрин" in t:  # 4.4 — скрин с платформы
        return Cat.PLATFORM
    if "актуальн" in t:  # 1.3 — генерим длинно (#8)
        return Cat.GENERATE
    if "паспорт" in t and "оцен" in t:  # 4.3 паспорт оценочных материалов — ВАЖНО (#15)
        return Cat.GAP
    if "паспорт" in t:  # 1.1 паспорт программы — генерим (опц., вузы удаляют #7)
        return Cat.GENERATE
    if "характеристика" in t and "проф" in t:  # 1.5 — опц. (#9)
        return Cat.GENERATE
    if "цель" in t or "цели" in t or "задач" in t:  # 1.4 / 2.1 цели — поверхностно (#10)
        return Cat.GENERATE
    if "компетенц" in t:  # 2.1 компетенции — из профстандарта/УП (#11)
        return Cat.UP_COMP
    if "учебный план" in t:  # 3.1 — ядро (#12)
        return Cat.UP_PLAN
    if "календарн" in t:  # 3.2 (#13)
        return Cat.UP_GRAFIK
    if "рабочие программы" in t or "модул" in t:  # 3.3 модули
        return Cat.UP_MODULES
    if "нормативн" in t or "инвалид" in t or "документ" in t or "форма обучения" in t:
        return Cat.BOILERPLATE
    if (
        "текущий контроль" in t
        or "итоговая аттест" in t
        or "оценочн" in t
        or "разработчик" in t
        or "кадров" in t
        or "материально" in t
        or "учебно-методич" in t
    ):
        return Cat.GAP
    return Cat.BOILERPLATE  # по умолчанию держим текст шаблона, ничего не затираем
