"""Диалектные различия SQLite→Postgres для каталога.

Каталожный код написан под sqlite3 (`?`-плейсхолдеры, доступ к строкам по имени как
`sqlite3.Row`). Здесь — переводчик плейсхолдеров и адаптер строк, чтобы тот же SQL работал
на psycopg. Точечные различия (`INSERT OR REPLACE`, `PRAGMA`, `rowid`) сюда НЕ входят —
они правятся в конкретных запросах при переводе роутеров (следующие слайсы).
"""

from __future__ import annotations

import re

_PRAGMA_RE = re.compile(r"^\s*PRAGMA\b", re.IGNORECASE)
_GROUP_CONCAT_RE = re.compile(r"GROUP_CONCAT\s*\(\s*DISTINCT\s+([^)]+?)\s*\)", re.IGNORECASE)
_LIKE_TOKEN_RE = re.compile(r"\bLIKE\b", re.IGNORECASE)
_CURRENT_TS_RE = re.compile(r"\bCURRENT_TIMESTAMP\b", re.IGNORECASE)
# SQLite stores CURRENT_TIMESTAMP as 'YYYY-MM-DD HH:MM:SS' text; catalog timestamp columns are
# text. Mirror that exact shape so date parsers on the read side stay happy.
_PG_NOW_TEXT = "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"
_INSERT_OR_IGNORE_RE = re.compile(r"^(\s*)INSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_INSERT_INTO_RE = re.compile(r"^\s*INSERT\s+INTO\b", re.IGNORECASE)
_RETURNING_RE = re.compile(r"\bRETURNING\b", re.IGNORECASE)
_ON_CONFLICT_RE = re.compile(r"\bON\s+CONFLICT\b", re.IGNORECASE)


def is_pragma(sql: str) -> bool:
    """SQLite `PRAGMA ...` — в PG не применяется (no-op)."""
    return bool(_PRAGMA_RE.match(sql))


def translate_group_concat(sql: str) -> str:
    """`GROUP_CONCAT(DISTINCT expr)` → `string_agg(DISTINCT expr::text, ',')` (Postgres).

    Каталог использует только DISTINCT-форму с дефолтным разделителем ','. `::text` безопасен
    и для текстовых, и для числовых колонок (совпадает с sqlite-выводом строки)."""
    return _GROUP_CONCAT_RE.sub(r"string_agg(DISTINCT \1::text, ',')", sql)


def translate_like(sql: str) -> str:
    """`LIKE` → `ILIKE` (Postgres) вне строковых литералов.

    SQLite `LIKE` регистронезависим для ASCII; PG `LIKE` — регистрозависим. `ILIKE` даёт
    паритет с sqlite для пользовательского поиска. Литералы-теги (`'brief:%'`) уже в нижнем
    регистре, поэтому ILIKE на них ничего не меняет."""
    result: list[str] = []
    segment: list[str] = []
    quote: str | None = None
    for ch in sql:
        if quote is not None:
            result.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            result.append(_LIKE_TOKEN_RE.sub("ILIKE", "".join(segment)))
            segment = []
            result.append(ch)
            quote = ch
        else:
            segment.append(ch)
    result.append(_LIKE_TOKEN_RE.sub("ILIKE", "".join(segment)))
    return "".join(result)


def translate_current_timestamp(sql: str) -> str:
    """Bare `CURRENT_TIMESTAMP` (outside string literals) → PG text-now expression.

    SQLite treats `CURRENT_TIMESTAMP` in DEFAULTs and runtime `INSERT/UPDATE` values as a text
    timestamp; the catalog columns are `text`. On PG the keyword is a `timestamptz` and won't
    implicitly cast into `text`, so runtime writes that pass it literally fail — replace it."""
    result: list[str] = []
    segment: list[str] = []
    quote: str | None = None
    for ch in sql:
        if quote is not None:
            result.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            result.append(_CURRENT_TS_RE.sub(_PG_NOW_TEXT, "".join(segment)))
            segment = []
            result.append(ch)
            quote = ch
        else:
            segment.append(ch)
    result.append(_CURRENT_TS_RE.sub(_PG_NOW_TEXT, "".join(segment)))
    return "".join(result)


def adapt_write_sql(sql: str, *, add_returning: bool = True) -> tuple[str, bool]:
    """Адаптировать write-SQL под PG. Возвращает (sql, wants_returning_id).

    - `INSERT OR IGNORE INTO` → `INSERT INTO … ON CONFLICT DO NOTHING`.
    - Одиночный `INSERT INTO` без `RETURNING` и без `ON CONFLICT` → добавить `RETURNING id`
      (эмуляция sqlite `lastrowid`). Для bulk (`executemany`) `add_returning=False` — lastrowid
      там не нужен, а `RETURNING` сломал бы batch.

    `RETURNING id` НЕ добавляется к `ON CONFLICT`-запросам: они бывают над таблицами без
    колонки `id` (`evidence_query_cache` — PK `cache_key`), и это upsert'ы по уникальному
    ключу, которым lastrowid не нужен. Прочие диалект-вставки (`OR REPLACE`) — вручную.
    """
    core = sql.rstrip().rstrip(";").rstrip()
    if _INSERT_OR_IGNORE_RE.match(core):
        core = _INSERT_OR_IGNORE_RE.sub(r"\1INSERT INTO", core, count=1)
        if not _ON_CONFLICT_RE.search(core):
            core = f"{core} ON CONFLICT DO NOTHING"

    is_insert = bool(_INSERT_INTO_RE.match(core))
    has_conflict = bool(_ON_CONFLICT_RE.search(core))
    wants_returning = add_returning and is_insert and not _RETURNING_RE.search(core) and not has_conflict
    if wants_returning:
        core = f"{core} RETURNING id"
    return core, wants_returning


def translate_placeholders(sql: str) -> str:
    """Заменить `?`-плейсхолдеры на `%s` (paramstyle qmark→pyformat) вне строковых литералов.

    Учитывает одинарные/двойные кавычки; `''`/`""` (SQLite-экранирование) корректно
    переключают состояние. `%` внутри SQL не трогаем — psycopg trebует `%%` только если
    реально нужен литерал `%`, что в каталожных запросах не встречается.
    """
    out: list[str] = []
    quote: str | None = None
    for ch in sql:
        if quote is not None:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)
