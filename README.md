# S21 Content Factory

Единая платформа School 21 для генерации, проверки (аудита) и каталога компетенций
учебного контента. Один устанавливаемый пакет `content_factory`, одно FastAPI-приложение,
одна база (PostgreSQL), общий LLM-гейтвей (по умолчанию Polza).

Собрана слиянием трёх ранее самостоятельных репозиториев
(`Content_generator_ver1`, `Proverka`/Auditor, `Spravochnik`) — см.
[docs/MIGRATION_LOG.md](docs/MIGRATION_LOG.md).

## Архитектура

```text
src/content_factory/
  platform/      общее ядро: llm (мульти-провайдер gateway, Polza по умолчанию),
                 observability, exceptions  (config/cache/prompts/db/domain — задел)
  generation/    движок генерации учебных проектов (агенты, orchestrator, methodology,
                 workflow, validators, evaluation, didactics)
  audit/         аудитор контента (ex-Proverka): checks, extraction, ingestion,
                 exporters, web-render — 39 критериев README
  catalog/       каталог компетенций (ex-Spravochnik): intake-пайплайн + нативный FastAPI UI
  api/           FastAPI: routers, db (SQLAlchemy + Alembic), services, integrations,
                 templates, static
  config/        model_registry.yaml (данные)
  didactics/     дидактические фрагменты (данные)

migrations/      единый Alembic (001–018; 014–017 = каталог/intake/DAG в Postgres,
                 018 = runtime snapshot/status для генерации проектов из УП)
tests/           единый pytest: generation + audit + catalog
scripts/         эксплуатационные скрипты (в т.ч. migrate_catalog_to_postgres.py)
docs/            документация (+ MIGRATION_LOG.md)
legacy/          заархивированные не-кодовые ассеты трёх исходников
```

Ключевой принцип: детерминированная оркестрация и типизированные контракты — в коде,
LLM — только там, где нужен содержательный текст/перевод.

## Быстрый старт

Требования: Python **3.12**, PostgreSQL, LLM-ключ (по умолчанию `POLZA_AI_API_KEY`),
ffmpeg для видео-перевода.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env    # заполнить секреты (как минимум POLZA_AI_API_KEY, DATABASE_URL, JWT_SECRET_KEY)
alembic upgrade head           # применить схему (001–018)
python run.py                  # http://127.0.0.1:8000
```

Каталог и intake работают в Postgres. Исторический импорт из SQLite-артефакта нужен
только для переноса старого каталога в новую БД:

```bash
python scripts/migrate_catalog_to_postgres.py \
  --sqlite src/content_factory/catalog/artifacts/skills_catalog.sqlite \
  --pg-url "$DATABASE_URL"
```

## Сценарии (роуты)

- `/app/generate` — генерация учебного проекта (README, практика, метрики, архив).
- `/app/learning-projects` — operational cockpit проектов из утвержденных УП:
  readiness, snapshot lineage, статусы и история генераций.
- `/app/auditor` — аудит README по критериям качества (FastAPI-нативно).
- `/app/spravochnik` — каталог компетенций / intake (нативный FastAPI UI).
- API: `/api/v1/generate`, `/api/v1/auditor/*`, `/api/v1/spravochnik/*`,
  `/api/v1/curriculum/*`, `/api/v1/curriculum-projects/*`.

## Проверки

```powershell
python -m pytest            # единый набор (generation + audit + catalog)
ruff check src/
mypy src/
```

## Конфигурация

Все переменные — в [.env.example](.env.example). Обязательные для production:
`DATABASE_URL`, `JWT_SECRET_KEY`, `ALLOWED_EMAIL_DOMAIN`, один LLM-ключ (по умолчанию
`POLZA_AI_API_KEY`), `CORS_ORIGINS`. Секреты не коммитятся (`.env` — в `.gitignore`).

## Провайдер LLM

Единый гейтвей `content_factory.platform.llm` (litellm/instructor). Провайдер по умолчанию —
**Polza** (`LLM_PROVIDER=polza`, OpenAI-совместимый). Audit и catalog используют тот же
`POLZA_AI_API_KEY`. Docs Polza: https://polza.ai/docs/glavnoe/quickstart
