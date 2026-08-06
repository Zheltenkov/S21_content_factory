"""Консилиум из 3 LLM-агентов для осмысленной сборки РПД (template-agnostic).

Заменяет хардкод-классификацию (`dpo_spec.category_for`) на понимание:
1. **Аналитик шаблона** — классифицирует разделы ЛЮБОГО ДПО-шаблона по роли (что за содержание нужно).
2. **Интерпретатор УП** — под нужды шаблона пишет краткое ТЗ на каждый прозаический раздел (что по
   смыслу взять из УП), не делая лишней работы.
3. **Сборщик** — пишет официальный текст каждого прозаического раздела по ТЗ и данным УП.

Значения УП-таблиц (часы, модули, компетенции) сюда НЕ идут — их переносит детерминированный код
(без домыслов). Консилиум решает МАРШРУТ и пишет ПРОЗУ; цифры/факты не выдумываются.

Все три вызова — батчевые (один запрос на стадию), это же снимает latency 14 последовательных вызовов.
Best-effort: при сбое любой стадии сборка откатывается на детерминированный `category_for`.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from .prose import _up_context
from .up_models import UpPlan

PROMPT_VERSION = "rpd-consilium-v1"

#: Дисковый кэш результата консилиума: повторная сборка того же УП+шаблона — бесплатно.
_CACHE_DIR = Path(tempfile.gettempdir()) / "rpd_consilium_cache"

#: Роли = категории раздела (совпадают со значениями dpo_spec.Cat, чтобы сборка их поняла).
_ROLES: dict[str, str] = {
    "up_plan": "учебный план — таблица модулей с часами и формой контроля",
    "up_grafik": "календарный учебный график (по периодам)",
    "up_modules": "рабочие программы / содержание учебных модулей",
    "up_comp": "профессиональные компетенции (таблица)",
    "generate": "прозаический раздел, который надо НАПИСАТЬ: цель и задачи, актуальность, паспорт/аннотация, характеристика профессиональной деятельности",
    "boilerplate": "стандартный неизменный текст формы ДПО: нормативно-правовые основания, выдаваемый документ, форма обучения, условия для инвалидов, оценка качества",
    "platform": "требует скриншот задания с платформы/LMS: пример оформления модуля, пример оценки",
    "gap": "заполняет методист вручную: оценочные материалы, разработчики, организационно-педагогические условия, обеспечение, кадры",
}
_PROSE_ROLES = {"generate", "gap", "platform"}


def section_key(number: str, title: str) -> str:
    return f"{number} {title}".strip()


def run_consilium(
    sections: list[dict[str, str]], up: UpPlan, client: Any
) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str]]:
    """Возвращает (роли, тексты прозы, флаги валидатора). Пустое → откат на category_for.

    Кэшируется на диске по хешу (prompt_version + разделы + УП): повторная сборка бесплатна.
    """
    if client is None:
        return {}, {}, {}
    key = _cache_key(sections, up)
    cached = _cache_read(key)
    if cached is not None:
        return cached

    roles = _classify_sections(sections, client)  # Агент 1
    if not roles:
        return {}, {}, {}
    prose = [
        {"key": section_key(s["number"], s["title"]), "role": roles.get(section_key(s["number"], s["title"]), "")}
        for s in sections
        if roles.get(section_key(s["number"], s["title"]), "") in _PROSE_ROLES
    ]
    texts: dict[str, list[str]] = {}
    flags: dict[str, str] = {}
    if prose:
        briefs = _interpret_up(prose, up, client)  # Агент 2
        texts = _compose_prose(prose, briefs, up, client)  # Агент 3
        flags = _validate_prose(texts, up, client)  # Агент 4 (критик)
    result = (roles, texts, flags)
    _cache_write(key, result)
    return result


def _classify_sections(sections: list[dict[str, str]], client: Any) -> dict[str, str]:
    """Агент 1: роль каждого раздела шаблона (template-agnostic)."""
    roles_desc = "\n".join(f"{key}: {desc}" for key, desc in _ROLES.items())
    listing = "\n".join(f"- {section_key(s['number'], s['title'])}" for s in sections)
    system = "Ты аналитик структуры ДПО-шаблонов рабочей программы дисциплины. Классифицируй каждый раздел по роли. Отвечай строго JSON без пояснений."
    user = (
        f"Возможные роли:\n{roles_desc}\n\nРазделы шаблона:\n{listing}\n\n"
        'Верни JSON вида {"<раздел>": "<роль>"} — по одной роли на каждый раздел из списка.'
    )
    data = _json_call(system, user, client, role="rpd_classify", stage="classify")
    return {str(key): str(value) for key, value in data.items() if value in _ROLES}


def _interpret_up(prose: list[dict[str, str]], up: UpPlan, client: Any) -> dict[str, str]:
    """Агент 2: краткое ТЗ на каждый прозаический раздел по смыслу УП."""
    items = "\n".join(f"- [{s['role']}] {s['key']}" for s in prose)
    system = "Ты методист ДПО. Для каждого раздела напиши краткое ТЗ (одно предложение): что в нём написать ПО СМЫСЛУ этой программы. Опирайся только на данные программы, без выдумок. Строго JSON."
    user = f"{_up_context(up)}\n\nРазделы (роль | раздел):\n{items}\n\nВерни JSON {{\"<раздел>\": \"<краткое ТЗ>\"}}."
    return {
        str(key): str(value)
        for key, value in _json_call(system, user, client, role="rpd_interpret", stage="interpret").items()
    }


def _compose_prose(prose: list[dict[str, str]], briefs: dict[str, str], up: UpPlan, client: Any) -> dict[str, list[str]]:
    """Агент 3: официальный русский текст каждого прозаического раздела по ТЗ и данным УП."""
    items = "\n".join(f"- {s['key']} :: {briefs.get(s['key'], '')}" for s in prose)
    system = (
        "Ты методист ДПО. Напиши официальный текст каждого раздела на грамотном русском по его ТЗ и данным "
        "программы. Без выдуманных фактов, без английских слов кроме терминов и имён собственных. Строго JSON."
    )
    user = (
        f"{_up_context(up)}\n\nРазделы и ТЗ:\n{items}\n\n"
        'Верни JSON {"<раздел>": "<текст 1–3 абзаца, абзацы разделяй \\n>"}.'
    )
    data = _json_call(system, user, client, role="rpd_prose", stage="compose")
    return {
        str(key): [line.strip() for line in str(value).split("\n") if line.strip()]
        for key, value in data.items()
    }


def _validate_prose(texts: dict[str, list[str]], up: UpPlan, client: Any) -> dict[str, str]:
    """Агент 4 (критик): проверяет прозу на русский / привязку к УП / отсутствие выдумок.

    Возвращает замечания ТОЛЬКО по проблемным разделам (корректные не включает)."""
    if not texts:
        return {}
    items = "\n".join(f"- {key}: {' '.join(paras)[:400]}" for key, paras in texts.items())
    system = (
        "Ты редактор-контролёр ДПО. Проверь тексты разделов по критериям: (а) грамотный русский без "
        "лишнего английского, (б) привязка к данным программы, (в) отсутствие выдуманных фактов. Для "
        "ПРОБЛЕМНЫХ разделов верни короткое замечание. Строго JSON {\"<раздел>\": \"<замечание>\"} — "
        "только проблемные; корректные не включай."
    )
    user = f"{_up_context(up)}\n\nТексты разделов:\n{items}\n\nВерни JSON с замечаниями только по проблемным разделам."
    return {
        str(key): str(value)
        for key, value in _json_call(system, user, client, role="rpd_validate", stage="validate").items()
        if str(value).strip()
    }


def _cache_key(sections: list[dict[str, str]], up: UpPlan) -> str:
    payload = json.dumps(
        {"v": PROMPT_VERSION, "sections": sections, "up": up.model_dump()},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_read(key: str) -> tuple[dict[str, str], dict[str, list[str]], dict[str, str]] | None:
    path = _CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (dict(data["roles"]), dict(data["texts"]), dict(data["flags"]))
    except (OSError, ValueError, KeyError):
        return None


def _cache_write(key: str, result: tuple[dict[str, str], dict[str, list[str]], dict[str, str]]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        roles, texts, flags = result
        (_CACHE_DIR / f"{key}.json").write_text(
            json.dumps({"roles": roles, "texts": texts, "flags": flags}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # кэш best-effort


def _json_call(system: str, user: str, client: Any, *, role: str, stage: str) -> dict[str, Any]:
    try:
        raw = str(
            client.complete(
                system,
                user,
                llm_role=role,
                prompt_version=f"{PROMPT_VERSION}:{stage}",
            )
        )
        parsed = json.loads(_extract_json(raw))
    except Exception:  # noqa: BLE001 - консилиум best-effort; при сбое сборка откатится
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text
