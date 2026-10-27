"""Перевод набора файлов — один проход по описи.

Отдельно от :mod:`archive`, потому что это другая работа: тот знает про zip и про опасные
имена, а здесь перебираются файлы и зовётся модель. Разделение держится на том, что сюда
приходят уже проверенные байты.

Главное свойство прохода — он доходит до конца. Один упавший файл не стоит всего прогона:
он остаётся исходным, причина уходит в отчёт, перебор продолжается. Поэтому ``except`` здесь
широкий, и это не небрежность: упасть может что угодно от шлюза модели до незнакомой кодировки,
а правильный ответ один и тот же — оставить файл как есть и записать причину.

Исходов четыре, и путать их нельзя:

* **переведён** — обработчик вернул другие байты;
* **пропущен** — не брались: формат без обработчика, уже не русский текст, снятая галочка;
* **ошибка** — брались и не смогли;
* **без изменений** — обработчик вернул те же байты. У кода это значит, что перевод не прошёл
  проверку неизменности и файл сохранён от порчи; у остальных форматов — что переводить было
  нечего.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Iterable

from content_factory.generation.agents.base.llm_client import LLMClientProtocol
from content_factory.translation.archive import ArchiveMember, InventoryEntry
from content_factory.translation.engine import translate_units
from content_factory.translation.registry import handler_for
from content_factory.translation.report import FAILED, SKIPPED, TRANSLATED, UNCHANGED, FileOutcome

SOURCE_LANGUAGE = "ru"

_REFUSED = "обработчик вернул файл нетронутым: перевод не прошёл проверку целостности"
_SAME = "перевод совпал с исходником"
_NOTHING = "переводить было нечего"
_UNTICKED = "снят методистом"


def translate_files(
    llm_client: LLMClientProtocol,
    members: list[ArchiveMember],
    entries: list[InventoryEntry],
    *,
    target_language: str,
    only: Iterable[str] | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[dict[str, bytes], list[FileOutcome]]:
    """Переводит отмеченные файлы и возвращает ``путь → новые байты`` и список исходов.

    ``only`` ограничивает прогон путями, которые методист оставил отмеченными. Снятое не
    стоит ни запроса, ни денег, поэтому проверка идёт до обращения к модели.
    """
    chosen = None if only is None else set(only)
    by_path = {member.path: member for member in members}
    translations: dict[str, bytes] = {}
    outcomes: list[FileOutcome] = []

    for entry in entries:
        if not entry.selected:
            outcomes.append(_skip(entry, entry.skip_reason or _NOTHING))
            continue
        if chosen is not None and entry.path not in chosen:
            outcomes.append(_skip(entry, _UNTICKED))
            continue

        member = by_path.get(entry.path)
        if member is None:  # опись и архив разошлись — считаем файл отсутствующим
            outcomes.append(_skip(entry, "файла нет в архиве"))
            continue

        if progress_callback is not None:
            progress_callback(f"перевод {entry.path}")
        outcome, payload = _translate_one(llm_client, member, target_language)
        outcomes.append(outcome)
        if payload is not None:
            translations[member.path] = payload

    return translations, outcomes


def _translate_one(
    llm_client: LLMClientProtocol, member: ArchiveMember, target_language: str
) -> tuple[FileOutcome, bytes | None]:
    extension = posixpath.splitext(member.path)[1].lower()
    handler = handler_for(extension)
    if handler is None:  # опись такое не отмечает, но путь остаётся честным
        return _skip_path(member.path, "формат не переводится"), None

    try:
        units = handler.extract(member.content)
        if not units:
            return FileOutcome(member.path, UNCHANGED, 0, _NOTHING), None
        if target_language == SOURCE_LANGUAGE:
            # Перевод всегда с русского, поэтому ru → ru это не работа. Модель не зовём.
            return FileOutcome(member.path, UNCHANGED, len(units), _NOTHING), None

        translated = translate_units(llm_client, units, target_language)
        payload = handler.apply(member.content, translated)
    except Exception as exc:  # noqa: BLE001 — любой отказ оставляет файл исходным
        return FileOutcome(member.path, FAILED, 0, str(exc) or exc.__class__.__name__), None

    if payload == member.content:
        # Те же байты значат разное. Отказаться от перевода умеет только обработчик,
        # который разбирает результат заново; у остальных это просто совпадение текстов,
        # и называть его проверкой целостности — врать в отчёте.
        reason = _REFUSED if getattr(handler, "verifies_integrity", False) else _SAME
        return FileOutcome(member.path, UNCHANGED, len(units), reason), None
    return FileOutcome(member.path, TRANSLATED, len(units), ""), payload


def _skip(entry: InventoryEntry, detail: str) -> FileOutcome:
    return FileOutcome(path=entry.path, status=SKIPPED, units=0, detail=detail)


def _skip_path(path: str, detail: str) -> FileOutcome:
    return FileOutcome(path=path, status=SKIPPED, units=0, detail=detail)
