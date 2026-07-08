"""Конфигурация дидактической оси (жюри моделей).

Все пороги — провизорны, помечены как калибруемые (следующий слайс: авто-промоушен).
Модели жюри задаются через Polza (OpenRouter-подобный шлюз, отдаёт разные семейства по
имени). Список переопределяется env `DIDACTIC_JURY_MODELS` (через запятую), чтобы не
зашивать каталог Polza в код. Недоступный джурор деградирует в mock-фолбэк — панель не
разваливается.
"""

from __future__ import annotations

import os

# Провайдер жюри: единый шлюз Polza, разные модели по имени.
JURY_PROVIDER = "polza"

# Дефолтное жюри: разные семейства для расхождения линз (анти-моно-bias).
# Переопределяется env; имена должны существовать в каталоге Polza.
DEFAULT_JURY_MODELS: tuple[str, ...] = (
    "openai/gpt-5.4",
    "google/gemini-3.1-pro",
    "deepseek/deepseek-v4",
)

# Роли структурированной дискуссии на спорном дименшене (разные модели).
DEFAULT_DEBATE_ROLES: dict[str, str] = {
    "critic": "deepseek/deepseek-v4",
    "defender": "google/gemini-3.1-pro",
    "judge": "openai/gpt-5.4",
}

# Пороги дидактической оси (калибруются).
ABSTAIN_CONFIDENCE = 0.55   # ниже — эскалация/на человека (jury_split)
DIDACTIC_FLOOR = 3.0        # дименшен ниже — major issue + эскалация
DEBATE_ON_ESCALATE = True
DEBATE_ROUNDS = 1

# Обрезка README в промпте джурора (стоимость/лимиты контекста).
JUROR_MD_CHARS = 11000
DEBATE_MD_CHARS = 8000


def resolve_jury_models() -> list[str]:
    """Список моделей жюри: env `DIDACTIC_JURY_MODELS` или дефолт."""
    raw = os.getenv("DIDACTIC_JURY_MODELS", "").strip()
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if models:
            return models
    return list(DEFAULT_JURY_MODELS)
