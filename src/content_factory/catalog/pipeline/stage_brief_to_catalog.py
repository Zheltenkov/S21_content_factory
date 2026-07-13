"""Стадия 1->2: бриф -> навыки-кандидаты справочника.

Новый порядок экономит внешний поиск: сначала делаем draft skills из брифа,
резолвим их против канона, затем запускаем grounded-поиск только для серой зоны.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from content_factory.catalog.db import CatalogConnection

from . import config, language, llm, stage_atomize, stage_normalize
from .brief_bloom_workload import (
    _extract_workload_from_text,
    _normalized_spec,
    extract_workload_from_text,  # noqa: F401 — re-export: viewer calls stage_brief_to_catalog.extract_workload_from_text
    normalize_bloom,
)
from .brief_coverage import (
    _build_coverage_audit,
    _reclassify_program_artifacts,
    build_coverage_audit,  # noqa: F401 — re-export: viewer calls stage_brief_to_catalog.build_coverage_audit
    is_catalog_match_safe,
)
from .brief_mock_spec import (
    _mock_spec_from_brief,
    _short_topic_label,
    _topic_to_mock_skill_name,
)
from .catalog_repo import CatalogRepo
from .models import Evidence, IndicatorSpec, SkillCandidate
from .skill_names import canonicalize_skill_name, has_observable_action

RUSSIAN_OUTPUT_RULE = (
    "Все поля name, group, coverage_area, rationale и тексты индикаторов пиши на русском языке. "
    "Сохраняй на английском только общепринятые технические термины и аббревиатуры: MVP, API, REST, SQL, CI/CD, LLM, SLA, Git, Docker, OKR, unit economics, human-in-the-loop."
)


# --------- decompose ---------
def decompose(brief: str) -> dict:
    if config.USE_LIVE:
        sys = (
            "Ты анализируешь бриф для построения skill portrait. "
            "Если бриф описывает образовательную программу, курс, ветку, паспорт программы или ТЗ на продукт обучения, "
            "то role/seniority должны описывать выпускника/learner после завершения программы, а не автора, методолога, дизайнера программы или команду запуска. "
            "Отдельно выдели operator_role, если в тексте есть роль того, кто проектирует/запускает программу. "
            "Верни только JSON с полями: "
            "artifact_type ('learner_brief'|'program_brief'|'mixed'), "
            "role, seniority, domain, operator_role, program_goal, "
            "must_include_areas (list 8-16 обязательных областей компетенций выпускника), "
            "duration_months_min, duration_months_max, hours_per_week, target_total_hours, если эти данные явно есть в брифе, "
            "sub_queries (list 4-6 поисковых запросов только про learner skills и graduate outcomes). "
            "Запрещено заполнять sub_queries вопросами про размер когорты, бюджет программы, staffing, загрузку преподавателей, ресурсы команды запуска, KPI самой программы. "
            "Нужно вытаскивать skills выпускника, а не операционные решения по запуску программы."
        )
        raw = json.loads(llm.content(llm.chat(
            config.MODEL_PLAN,
            [{"role": "system", "content": sys}, {"role": "user", "content": brief}],
            json_mode=True,
        )))
        spec = _normalized_spec(raw)
        spec.update({key: value for key, value in _extract_workload_from_text(brief).items() if value is not None})
        for field in ("duration_months_min", "duration_months_max", "hours_per_week", "target_total_hours"):
            if raw.get(field) not in (None, "") and field not in spec:
                spec[field] = raw[field]
        return spec
    return _mock_spec_from_brief(brief)


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


def _candidate_source_text(candidate: SkillCandidate) -> str:
    parts = [
        candidate.name,
        candidate.group,
        candidate.coverage_area or "",
        " ".join(candidate.tools),
        " ".join(indicator.text for indicator in candidate.indicators),
    ]
    return " ".join(part for part in parts if part)


def _localize_candidate(candidate: SkillCandidate) -> SkillCandidate:
    """Normalize catalog-facing labels to Russian without touching technical terms."""
    localized_name = language.localize_skill_label(candidate.name)
    if localized_name and localized_name != candidate.name:
        candidate.source_name = candidate.source_name or candidate.name
        candidate.name = localized_name
    candidate.group = language.localize_group_label(candidate.group) or candidate.group
    if candidate.coverage_area:
        candidate.coverage_area = language.localize_area_label(candidate.coverage_area) or candidate.coverage_area
    return candidate


def synthesize_draft_from_brief(brief: str, spec: dict) -> tuple[list[SkillCandidate], dict[str, Any] | None]:
    """Генерирует первичный shortlist из самого брифа без внешнего поиска.

    Этот draft нужен для дешёвого pre-match against canon: если навык уже есть в каталоге,
    Perplexity для него не вызывается.
    """
    if config.USE_LIVE:
        spec_context = {
            "artifact_type": spec.get("artifact_type"),
            "target_role": spec.get("role"),
            "target_seniority": spec.get("seniority"),
            "domain": spec.get("domain"),
            "operator_role": spec.get("operator_role"),
            "program_goal": spec.get("program_goal"),
            "must_include_areas": spec.get("must_include_areas", []),
        }
        if str(spec.get("artifact_type") or "").strip() in {"program_brief", "mixed"}:
            sys = (
                "Ты строишь первичный skill portrait выпускника образовательной программы только по тексту брифа, без внешнего поиска. "
                + RUSSIAN_OUTPUT_RULE
                + " "
                "Работай coverage-first по must_include_areas. "
                "Верни только строгий JSON вида "
                "{coverage:[{area,status,rationale,candidate_names}],"
                "candidates:[{name,group,coverage_area,indicators:[{text,bloom}],tools}]}. "
                "status в coverage: covered|partial|uncovered. "
                "Кандидаты должны быть learner-skills/graduate outcomes, а не роли, staffing, бюджет, ресурсы программы или решения команды запуска. "
                "На одну область дай 1-2 наиболее важных skill-кандидата. "
                "Название формулируй как нейтральную запись для справочника через отглагольное существительное: "
                "'Проведение интервью', 'Формулирование гипотезы', 'Настройка CI/CD'. "
                "Не используй инфинитивы и повелительные формулировки: плохо 'Провести интервью', 'Выбрать метрику', 'Запустить эксперимент'."
            )
        else:
            sys = (
                "Ты строишь первичный skill portrait обучаемого только по тексту брифа, без внешнего поиска. "
                + RUSSIAN_OUTPUT_RULE
                + " "
                "Верни строгий JSON {candidates:[{name,group,coverage_area,indicators:[{text,bloom}],tools}]}. "
                "Извлекай только learner-skills и graduate outcomes. "
                "Название формулируй как нейтральную запись для справочника через отглагольное существительное, не инфинитив."
            )
        data = json.loads(llm.content(llm.chat(
            config.MODEL_PLAN,
            [
                {"role": "system", "content": sys},
                {"role": "user", "content": json.dumps({"spec": spec_context, "brief": brief}, ensure_ascii=False)},
            ],
            json_mode=True,
        )))
        out: list[SkillCandidate] = []
        for i, it in enumerate(data.get("candidates", []), 1):
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            out.append(
                _localize_candidate(
                    SkillCandidate(
                        tmp_id=f"C{i:02d}",
                        name=name,
                        group=str(it.get("group") or "").strip(),
                        coverage_area=str(it.get("coverage_area") or "").strip() or None,
                        indicators=[
                            IndicatorSpec(text=ind["text"], bloom=normalize_bloom(ind.get("bloom"), spec, ind.get("text")))
                            for ind in it.get("indicators", [])
                            if ind.get("text")
                        ],
                        tools=[str(tool).strip() for tool in it.get("tools", []) if str(tool).strip()],
                        evidence_ids=[],
                    )
                )
            )
        return out, _build_coverage_audit(spec, out, data.get("coverage"))

    # Offline/mock режим: сохраняем существующие демо-кандидаты, но без обязательного evidence.
    out, coverage = synthesize_with_coverage([], spec)
    if out:
        return out, coverage
    areas = [str(area).strip() for area in spec.get("must_include_areas") or [] if str(area).strip()]
    fallback: list[SkillCandidate] = []
    for i, area in enumerate(areas[:8], 1):
        fallback.append(
            _localize_candidate(
                SkillCandidate(
                    tmp_id=f"C{i:02d}",
                    name=area,
                    group=str(spec.get("domain") or ""),
                    coverage_area=area,
                    indicators=[IndicatorSpec(text=f"Применяет навык в области: {area}", bloom="apply")],
                    tools=[],
                    evidence_ids=[],
                )
            )
        )
    return fallback, _build_coverage_audit(spec, fallback)


def _needs_evidence_enrichment(candidate: SkillCandidate) -> bool:
    if not _is_for_resolve(candidate):
        return False
    if candidate.resolution in {"matched", "alias"} and candidate.confidence >= config.TAU_CONFIDENCE:
        return False
    return candidate.resolution in {"new", "fuzzy"} or candidate.confidence < config.TAU_CONFIDENCE


def select_evidence_enrichment_candidates(cands: list[SkillCandidate]) -> list[SkillCandidate]:
    return [cand for cand in cands if _needs_evidence_enrichment(cand)]


def gather_evidence_for_gray_zone(
    cands: list[SkillCandidate],
    spec: dict,
    cache_conn: CatalogConnection | None = None,
) -> list[Evidence]:
    """Ищет evidence только для кандидатов, которые не закрылись каноном."""
    grouped: dict[str, list[SkillCandidate]] = {}
    for cand in cands:
        if not _needs_evidence_enrichment(cand):
            continue
        key = (cand.coverage_area or cand.group or cand.name).strip()
        if not key:
            key = cand.name
        grouped.setdefault(key, []).append(cand)

    evidence: list[Evidence] = []
    seen: dict[tuple[str, str], str] = {}
    role = str(spec.get("role") or "").strip()
    domain = str(spec.get("domain") or "").strip()
    max_queries = max(config.GRAY_SEARCH_MAX_QUERIES, 0)
    for group_index, (area, area_candidates) in enumerate(grouped.items()):
        if group_index >= max_queries:
            break
        skill_names = ", ".join(candidate.name for candidate in area_candidates[:4])
        query = (
            f"Навыки выпускника для роли {role} в домене {domain}. "
            f"Область: {area}. Кандидаты: {skill_names}. "
            "Найди подтверждающие frameworks, syllabus или вакансии."
        )
        group_evidence_ids: list[str] = []
        for hit in search(query, cache_conn=cache_conn):
            evidence_key = (str(hit.get("claim", "")).casefold(), str(hit.get("url", "")))
            evidence_id = seen.get(evidence_key)
            if evidence_id is None:
                evidence_id = f"E{len(evidence) + 1:02d}"
                seen[evidence_key] = evidence_id
                evidence.append(Evidence(
                    id=evidence_id,
                    **{k: hit[k] for k in ("claim", "source_type", "url", "snippet", "retrieved_at")},
                ))
            group_evidence_ids.append(evidence_id)
        if not group_evidence_ids:
            continue
        for cand in area_candidates:
            cand.evidence_ids = list(dict.fromkeys([*cand.evidence_ids, *group_evidence_ids]))
    return evidence


# --------- синтез кандидатов (с Блумом и инструментами) ---------
def synthesize_with_coverage(evidence: list[Evidence], spec: dict) -> tuple[list[SkillCandidate], dict[str, Any] | None]:
    ev_ids = [e.id for e in evidence]
    if config.USE_LIVE:
        cl = [{"id": e.id, "claim": e.claim, "type": e.source_type} for e in evidence]
        spec_context = {
            "artifact_type": spec.get("artifact_type"),
            "target_role": spec.get("role"),
            "target_seniority": spec.get("seniority"),
            "domain": spec.get("domain"),
            "operator_role": spec.get("operator_role"),
            "program_goal": spec.get("program_goal"),
            "must_include_areas": spec.get("must_include_areas", []),
        }
        if str(spec.get("artifact_type") or "").strip() in {"program_brief", "mixed"}:
            sys = (
                "Ты строишь skill portrait выпускника образовательной программы. "
                + RUSSIAN_OUTPUT_RULE
                + " "
                "Работай coverage-first: сначала посмотри на must_include_areas, затем попытайся закрыть их evidence. "
                "Верни только строгий JSON вида "
                "{coverage:[{area,status,rationale,candidate_names,evidence_ids}],"
                "candidates:[{name,group,coverage_area,indicators:[{text,bloom}],tools,evidence_ids}]}. "
                "status в coverage: covered|partial|uncovered. "
                "Кандидаты должны быть только learner-skills/graduate outcomes. "
                "Название кандидата формулируй как нейтральную запись для справочника через отглагольное существительное, а не как роль/должность человека. "
                "Хорошо: 'Проведение проблемных интервью', 'Настройка CI/CD', 'Планирование работ'. "
                "Плохо: 'Исследователь', 'Маркетолог', 'Стратег', 'Инженер', 'Провести интервью', 'Запустить эксперимент'. "
                "Не включай staffing decisions, преподавателей, бюджет, ресурсы программы, критерии набора, состав когорты, функции команды запуска, outsourcing-решения, роли операторов программы. "
                "Старайся не концентрироваться только в одной инженерной зоне: распределяй кандидатов по разным must_include_areas. "
                "На одну область давай 1-2 наиболее важных атомарных skill-кандидата, если evidence это поддерживает. "
                "evidence_ids разрешены только из предоставленного набора."
            )
        else:
            sys = (
                "Сгруппируй evidence в навыки-кандидаты выпускника. "
                + RUSSIAN_OUTPUT_RULE
                + " "
                "Строгий JSON {candidates:[{name,group,indicators:[{text,bloom}],tools,evidence_ids}]}. "
                "evidence_ids только из предоставленных. Навык без evidence не включай. "
                "Важное правило: извлекай только learner-skills и graduate outcomes. "
                "Название кандидата формулируй как нейтральный skill label для справочника, а не как роль или должность. "
                "Не включай staffing decisions, роли команды запуска программы, размер когорты, критерии набора, загрузку преподавателей, бюджет, ресурсы программы, outsourcing-решения. "
                "Если бриф про образовательную программу, ориентируйся на target_role и must_include_areas выпускника."
            )
        data = json.loads(llm.content(llm.chat(
            config.MODEL_PLAN,
            [{"role": "system", "content": sys}, {"role": "user", "content": json.dumps({"spec": spec_context, "evidence": cl}, ensure_ascii=False)}],
            json_mode=True,
        )))
        items = data.get("candidates", [])
        out: list[SkillCandidate] = []
        for i, it in enumerate(items, 1):
            ids = [x for x in it.get("evidence_ids", []) if x in ev_ids]
            if not ids:
                continue
            out.append(
                _localize_candidate(
                    SkillCandidate(
                        tmp_id=f"C{i:02d}",
                        name=it["name"],
                        group=it.get("group", ""),
                        coverage_area=str(it.get("coverage_area") or "").strip() or None,
                        indicators=[
                            IndicatorSpec(text=ind["text"], bloom=normalize_bloom(ind.get("bloom"), spec, ind.get("text")))
                            for ind in it.get("indicators", [])
                            if ind.get("text")
                        ],
                        tools=it.get("tools", []),
                        evidence_ids=ids,
                    )
                )
            )
        coverage = _build_coverage_audit(spec, out, data.get("coverage"))
        return out, coverage
    topics: list[tuple[str, list[str]]] = []
    for item in evidence:
        topic = item.claim or item.snippet
        if topic:
            topics.append((topic, [item.id]))
    if not topics:
        topics = [(str(area), []) for area in (spec.get("must_include_areas") or []) if str(area).strip()]

    out = []
    group = str(spec.get("domain") or spec.get("role") or "Общее").strip() or "Общее"
    for i, (topic, ids) in enumerate(topics[:12], 1):
        name = _topic_to_mock_skill_name(topic)
        area = _short_topic_label(topic)
        out.append(
            _localize_candidate(
                SkillCandidate(
                    tmp_id=f"C{i:02d}",
                    name=name,
                    group=group,
                    coverage_area=area,
                    indicators=[IndicatorSpec(text=f"Применяет навык в теме «{area}»", bloom="apply")],
                    tools=[],
                    evidence_ids=ids,
                )
            )
        )
    return out, _build_coverage_audit(spec, out)


def synthesize(evidence: list[Evidence], spec: dict) -> list[SkillCandidate]:
    candidates, _coverage = synthesize_with_coverage(evidence, spec)
    return candidates


def _confidence(cand: SkillCandidate, evidence: list[Evidence]) -> float:
    evs = [e for e in evidence if e.id in cand.evidence_ids]
    fw = any(e.source_type in ("framework", "syllabus") for e in evs)
    evidence_confidence = min(min(0.5 + 0.2 * len(evs), 0.95) + (0.1 if fw else 0.0), 0.97)
    match_confidence = 0.0
    if cand.resolution in {"matched", "alias"}:
        match_confidence = 0.98
    elif cand.resolution == "fuzzy":
        match_confidence = min(max((cand.match_score or 0.0) / 100.0, 0.55), 0.93)
    elif cand.resolution == "new":
        match_confidence = 0.5
    return round(max(evidence_confidence if evs else 0.0, match_confidence), 2)


# --------- жюри по серой зоне + триаж ---------
def _juror(model: str, cand: SkillCandidate) -> int:
    n = len(set(cand.evidence_ids))
    if model.startswith("openai"):
        return 1
    if model.startswith("anthropic"):
        return 1 if n >= 2 else 0
    return 0 if (cand.resolution == "new" and cand.bloom >= 4) else 1


def _needs_panel(cand: SkillCandidate) -> bool:
    return not (cand.resolution in ("matched", "alias") and cand.confidence >= config.TAU_CONFIDENCE)


def _is_for_resolve(cand: SkillCandidate) -> bool:
    return cand.entity_type == "skill" and cand.atomicity == "atomic"


def atomize_candidates(cands: list[SkillCandidate], spec: dict | None = None) -> list[SkillCandidate]:
    if spec:
        _reclassify_program_artifacts(cands, spec)
    atomized = stage_atomize.run(cands)
    for candidate in atomized:
        _localize_candidate(candidate)
        if candidate.entity_type == "skill":
            canonical_name = canonicalize_skill_name(candidate.name)
            if canonical_name and canonical_name != candidate.name:
                candidate.source_name = candidate.source_name or candidate.name
                candidate.name = canonical_name
            artifact_type = str((spec or {}).get("artifact_type") or "").strip()
            if artifact_type in {"program_brief", "mixed"} and not has_observable_action(candidate.name):
                candidate.decision = "needs_review"
                if "missing_observable_action" not in candidate.reasons:
                    candidate.reasons.append("missing_observable_action")
        if spec:
            candidate.indicators = [
                IndicatorSpec(text=indicator.text, bloom=normalize_bloom(indicator.bloom, spec, indicator.text))
                for indicator in candidate.indicators
            ]
    return atomized


def resolve_candidates(cands: list[SkillCandidate], evidence: list[Evidence], repo: CatalogRepo) -> None:
    for cand in cands:
        if not _is_for_resolve(cand):
            continue
        repo.resolve(cand)
        cand.confidence = _confidence(cand, evidence)


def select_council_candidates(cands: list[SkillCandidate]) -> list[SkillCandidate]:
    return [cand for cand in cands if _is_for_resolve(cand) and _needs_panel(cand)]


def run_council(cands: list[SkillCandidate]) -> dict[str, int]:
    council_candidates = select_council_candidates(cands)
    if config.USE_COUNCIL:
        for cand in council_candidates:
            votes = [_juror(model, cand) for model in config.MODEL_PANEL]
            cand.council_ran = True
            cand.council_agreement = round(sum(votes) / len(votes), 2)
            cand.confidence = round(0.6 * cand.confidence + 0.4 * cand.council_agreement, 2)
    return {
        "sent_to_council": len(council_candidates),
        "council_executed": len([cand for cand in cands if cand.council_ran]),
    }


def _meets_auto_accept_policy(cand: SkillCandidate, spec: dict[str, Any] | None = None) -> bool:
    artifact_type = str((spec or {}).get("artifact_type") or "").strip()
    if not has_observable_action(cand.name):
        return False
    # Новый skill в program_brief не публикуем автоматически: сначала нужен human check, иначе каталог быстро загрязняется.
    if (
        artifact_type in {"program_brief", "mixed"}
        and cand.resolution == "new"
        and not config.AUTO_ACCEPT_NEW_FOR_PROGRAM_BRIEF
    ):
        return False
    if not is_catalog_match_safe(cand, spec):
        return False
    return (
        cand.council_agreement is not None
        and cand.confidence >= config.AUTO_ACCEPT_CONFIDENCE
        and cand.council_agreement >= config.AUTO_ACCEPT_COUNCIL_AGREEMENT
    )


def triage_candidates(cands: list[SkillCandidate], spec: dict[str, Any] | None = None) -> None:
    artifact_type = str((spec or {}).get("artifact_type") or "").strip()
    for c in cands:
        if not _is_for_resolve(c):
            continue
        r = list(dict.fromkeys(c.reasons or []))
        n = len(set(c.evidence_ids))
        if c.resolution == "new":
            r.append("novel_skill")
            if artifact_type in {"program_brief", "mixed"} and not config.AUTO_ACCEPT_NEW_FOR_PROGRAM_BRIEF:
                r.append("program_brief_publication_guardrail")
        if c.resolution == "fuzzy":
            r.append("fuzzy_match_ambiguous")
        if c.resolution in {"matched", "alias", "fuzzy"} and not is_catalog_match_safe(c, spec):
            r.append("catalog_match_suspicious")
        if not has_observable_action(c.name):
            r.append("missing_observable_action")
        if c.confidence < config.TAU_CONFIDENCE:
            r.append("low_confidence")
        if n < config.MIN_SOURCES and c.resolution not in {"matched", "alias"}:
            r.append("single_source")
        if c.council_ran and c.council_agreement is not None and c.council_agreement < config.COUNCIL_AGREE_OK:
            r.append("council_split")
        r = list(dict.fromkeys(r))
        if not r and _meets_auto_accept_policy(c, spec):
            c.decision = "accepted"
            c.reasons = ["auto_accept_policy"]
            continue
        if c.resolution in {"matched", "alias"}:
            r = [reason for reason in r if reason not in {"novel_skill", "single_source", "fuzzy_match_ambiguous"}]
        c.decision = "accepted" if not r else "needs_review"
        c.reasons = r


def build_candidate_metrics(cands: list[SkillCandidate]) -> dict[str, int]:
    resolved_candidates = [cand for cand in cands if _is_for_resolve(cand)]
    return {
        "total_candidates": len(cands),
        "atomic_skill_candidates": len(resolved_candidates),
        "composite_candidates": len([cand for cand in cands if cand.atomicity == "composite"]),
        "non_skill_candidates": len([cand for cand in cands if cand.atomicity == "non_skill"]),
        "auto_accepted": len([cand for cand in resolved_candidates if not cand.council_ran and cand.decision == "accepted"]),
        "sent_to_council": len([cand for cand in resolved_candidates if cand.council_ran]),
        "accepted_after_council": len([cand for cand in resolved_candidates if cand.council_ran and cand.decision == "accepted"]),
        "review_after_council": len([cand for cand in resolved_candidates if cand.council_ran and cand.decision == "needs_review"]),
        "needs_review_total": len([cand for cand in cands if cand.decision == "needs_review"]),
        "accepted_total": len([cand for cand in resolved_candidates if cand.decision == "accepted"]),
        "matched_total": len([cand for cand in resolved_candidates if cand.resolution == "matched"]),
        "alias_total": len([cand for cand in resolved_candidates if cand.resolution == "alias"]),
        "fuzzy_total": len([cand for cand in resolved_candidates if cand.resolution == "fuzzy"]),
        "new_total": len([cand for cand in resolved_candidates if cand.resolution == "new"]),
    }


def run(brief: str, repo: CatalogRepo) -> tuple[dict, list[Evidence], list[SkillCandidate]]:
    spec = decompose(brief)
    cands, _coverage = synthesize_draft_from_brief(brief, spec)
    cands = atomize_candidates(cands, spec)
    cands, _normalize_report = stage_normalize.run(cands, spec)
    evidence: list[Evidence] = []
    resolve_candidates(cands, evidence, repo)
    evidence = gather_evidence_for_gray_zone(cands, spec)
    resolve_candidates(cands, evidence, repo)
    run_council(cands)
    triage_candidates(cands, spec)
    return spec, evidence, cands
