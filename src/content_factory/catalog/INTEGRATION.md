# Интеграция intake в catalog UI

## Живые точки входа
```text
src/content_factory/catalog/
  pipeline/                   # decompose/search/synthesize/atomize/resolve/council/triage
  db/                         # Postgres connection + sqlite-compatible adapter layer
  viewer/
    intake_ops.py             # intake jobs, DAG build, review queue, runtime repairs
    curriculum_ops.py         # curriculum-plan CRUD/CSV/payload sync
    templates/intake.html     # живая страница intake
    templates/reviews.html    # живая страница review + кнопка сборки DAG
  web/
    routers/intake.py         # FastAPI transport for /app/spravochnik/intake*
    routers/reviews.py        # FastAPI transport for /app/spravochnik/reviews*
migrations/
  014-017                     # Postgres schema for catalog/intake/DAG/curriculum
```

## Как это работает сейчас
1. `POST /app/spravochnik/intake` создаёт `intake_job` и запускает intake в фоне.
2. Intake сохраняет `profile_brief`, `evidence_source`, `skill_suggestion` и записи в `review_queue`.
3. В intake DAG больше не строится. В `result_payload.dag` сохраняется deferred-state.
4. Методолог подтверждает/отклоняет `review_queue`.
5. Отдельный шаг `POST /app/spravochnik/reviews/build-dag` или
   `POST /app/spravochnik/intake/jobs/<id>/build-dag` строит DAG только по:
   - `entity_type = skill`
   - `atomicity = atomic`
   - `decision = accepted`

## Ключевой инвариант
Схема каталога/intake управляется Alembic-миграциями. Runtime preflight в
`viewer/intake_ops.py` не меняет схему: он выполняет только идемпотентный repair
review-ссылок и восстановление зависших intake jobs.

## Зависимости
`pydantic`, `rapidfuzz`, `networkx`, `requests`, `python-docx`
