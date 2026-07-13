"""Evidence cache + grounded search for stage_brief_to_catalog.

Grounded-search side of the brief pipeline: a small ``evidence_query_cache`` table
(TTL-bounded), the live/offline ``search`` that fills it, and ``gather_evidence`` which
fans sub-queries into deduped ``Evidence``. Extracted from ``stage_brief_to_catalog``
as a leaf (imports only sibling leaves + the catalog connection + stdlib); the stage
module re-imports ``search`` (used by gray-zone enrichment) plus the two public
entrypoints ``gather_evidence``/``ensure_evidence_cache_table``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from content_factory.catalog.db import CatalogConnection

from . import config, llm
from .models import Evidence


def _normalize_evidence_query(query: str) -> str:
    return " ".join(query.casefold().replace("ё", "е").split())


def _evidence_cache_key(query: str) -> str:
    return hashlib.sha256(_normalize_evidence_query(query).encode("utf-8")).hexdigest()


def ensure_evidence_cache_table(conn: CatalogConnection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_query_cache (
            cache_key TEXT PRIMARY KEY,
            normalized_query TEXT NOT NULL,
            query TEXT NOT NULL,
            model TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_query_cache_updated ON evidence_query_cache(updated_at)")
    conn.commit()


def _load_cached_search(cache_conn: CatalogConnection | None, query: str) -> list[dict] | None:
    if cache_conn is None:
        return None
    ensure_evidence_cache_table(cache_conn)
    row = cache_conn.execute(
        "SELECT response_json, updated_at FROM evidence_query_cache WHERE cache_key = ? AND model = ?",
        (_evidence_cache_key(query), config.MODEL_SEARCH),
    ).fetchone()
    if not row:
        return None
    try:
        updated_at = datetime.fromisoformat(str(row["updated_at"]))
    except ValueError:
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - updated_at > timedelta(days=config.EVIDENCE_CACHE_TTL_DAYS):
        return None
    try:
        payload = json.loads(str(row["response_json"]))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


def _store_cached_search(cache_conn: CatalogConnection | None, query: str, items: list[dict]) -> None:
    if cache_conn is None:
        return
    ensure_evidence_cache_table(cache_conn)
    now = datetime.now(UTC).isoformat()
    cache_conn.execute(
        """
        INSERT INTO evidence_query_cache(cache_key, normalized_query, query, model, response_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO UPDATE SET
            normalized_query = excluded.normalized_query,
            query = excluded.query,
            model = excluded.model,
            response_json = excluded.response_json,
            updated_at = excluded.updated_at
        """,
        (
            _evidence_cache_key(query),
            _normalize_evidence_query(query),
            query,
            config.MODEL_SEARCH,
            json.dumps(items, ensure_ascii=False),
            now,
            now,
        ),
    )
    cache_conn.commit()


# --------- grounded-поиск -> evidence ---------
def search(query: str, cache_conn: CatalogConnection | None = None) -> list[dict]:
    cached = _load_cached_search(cache_conn, query)
    if cached is not None:
        return cached
    if config.USE_LIVE:
        sys = (
            "Найди подтверждающие источники по навыкам. "
            "Верни компактный JSON-массив объектов {claim, source_type, url, snippet}. "
            "source_type: vacancy|framework|syllabus|other. "
            "snippet должен быть коротким, без длинных цитат."
        )
        try:
            resp = llm.chat(
                config.MODEL_SEARCH,
                [{"role": "system", "content": sys}, {"role": "user", "content": query}],
                max_tokens=config.MODEL_SEARCH_MAX_TOKENS,
            )
            items = json.loads(llm.content(resp))
        except Exception:
            items = []
        cits = llm.citations(resp) if items else []
        for it in items:
            it.setdefault("url", cits[0] if cits else "")
            it.setdefault("snippet", "")
            it.setdefault("retrieved_at", date.today().isoformat())
        _store_cached_search(cache_conn, query, items)
        return cast("list[dict[Any, Any]]", items)
    today = date.today().isoformat()
    DB = {
        "SQL": [("Уверенный SQL: SELECT, JOIN", "vacancy", "https://hh.ru/v/1", "SQL, JOIN, индексы"),
                ("Основы реляционных БД", "framework", "https://esco.ec.europa.eu/rdb", "relational db")],
        "REST": [("Проектирование REST API", "vacancy", "https://hh.ru/v/2", "REST API, HTTP"),
                 ("Принципы REST", "syllabus", "https://roadmap.sh/backend", "REST design")],
        "очеред": [("Работа с очередями сообщений", "vacancy", "https://hh.ru/v/3", "RabbitMQ/Kafka")],
        "Docker": [("Контейнеризация Docker", "syllabus", "https://roadmap.sh/devops", "Dockerfile, образы")],
        "требован": [("Git в командной работе", "vacancy", "https://hh.ru/v/4", "Git, ветки, review")],
        "проблем": [("Discovery: выявление проблем клиента", "framework", "https://example.org/discovery", "JTBD, problem framing")],
        "ai-инструменты в маркетинге": [("AI-маркетинг: генерация креативов, аналитика", "syllabus", "https://example.org/ai-mkt", "AI marketing")],
        "метрики": [("Продуктовые метрики и сегментация", "syllabus", "https://example.org/product-analytics", "product analytics")],
    }
    out = []
    ql = query.lower()
    for key, items in DB.items():
        if key.lower() in ql:
            for claim, st, url, snip in items:
                out.append({"claim": claim, "source_type": st, "url": url, "snippet": snip, "retrieved_at": today})
    _store_cached_search(cache_conn, query, out)
    return out


def gather_evidence(sub_queries: list[str], cache_conn: CatalogConnection | None = None) -> list[Evidence]:
    ev, n = [], 0
    for q in sub_queries:
        for h in search(q, cache_conn=cache_conn):
            n += 1
            ev.append(Evidence(id=f"E{n:02d}", **{k: h[k] for k in ("claim", "source_type", "url", "snippet", "retrieved_at")}))
    # дедуп по (claim,url)
    seen, out = set(), []
    for e in ev:
        k = (e.claim.lower(), e.url)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out
