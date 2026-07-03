# Phase 5 — план переноса catalog-UI (viewer) WSGI → FastAPI

## Context

`src/content_factory/catalog/viewer/app.py` — единственный оставшийся legacy-веб-сервер:
сырой WSGI (`wsgiref`), ~7.6k строк, **37 роут-веток** в одном `if/elif` внутри замыкания
`create_app` (захвачены `env`, `summary`, `db_path`), смонтирован в FastAPI через
`WSGIMiddleware` + самодельный `PrefixRewriteASGI`. Audit-UI уже нативный FastAPI.

Цель: перенести **весь** UI каталога на нативный FastAPI — визуал, все кнопки и механики
1:1 — и убрать WSGI-mount/prefix-хак. Инкрементально, каждый срез зелёный.

### Что переносим (инвентарь)

- **19 шаблонов Jinja** (`base.html` + competencies/profiles/reviews/intake/up_*/catalog_admin_*).
- **static**: `styles.css`, `school21-sber-logo.jpg`.
- **7 Jinja-фильтров** (`review_*_label`, `edge_reason_label`, `format_local_datetime`),
  **nav** (`get_main_nav`/`get_secondary_nav`/`show_secondary_nav`/`detect_route_zone` из
  `route_zones.py`), общий контекст рендера (nav, secondary_nav, route_zone, summary,
  complexity_options, intake_progress_steps).
- **Механики**: ~30 форм `<form method="post|get" action="/…">` (PRG: POST→mutation→302),
  submit-кнопки (`action-btn*`), 1 инлайн-`<script>` в `intake.html` (fetch-поллинг статуса
  джобы + 21 addEventListener), CSV-выгрузка (`/intake/jobs/<id>/plan.csv`), multipart-загрузка
  брифа (intake POST), JSON-эндпоинты статуса/следующего шага.
- **Данные**: raw `sqlite3` (`open_db(db_path)` на запрос), reusable top-level data-функции
  (`fetch_all`, `get_curriculum_plan`, `list_recent_intake_jobs`, `build_*` …) — **не меняем**.
- **Auth**: у viewer своей нет; native-роуты вешаем на общий `get_current_user` приложения.

### Полный список роутов (37) — цель портирования
```
GET   /                                         → redirect /intake
GET   /favicon.ico
GET   /static/*                                 → FastAPI StaticFiles
GET   /catalog-admin                            → redirect /catalog-admin/groups
GET/POST /catalog-admin/candidate-competencies
GET/POST /catalog-admin/archive
GET/POST /catalog-admin/artifact-templates
GET   /catalog-admin/skillsets
GET   /catalog-admin/skillsets/<id>
GET/POST /catalog-admin/groups
GET/POST /catalog-admin/groups/<id>/...
GET/POST /catalog-admin/skills/<id>/...         (merge/edit/inline)
GET   /competencies    GET /competencies/<id>
GET   /profiles        GET /profiles/<id>
GET/POST /reviews      POST /reviews/build-dag  POST /reviews/apply-catalog
GET/POST /intake       POST /intake/jobs/clear
GET   /intake/jobs/<id>            GET /intake/jobs/<id>/status
POST  /intake/jobs/<id>/next-step  POST /intake/jobs/<id>/build-dag
POST  /intake/jobs/<id>/apply-catalog  POST /intake/jobs/<id>/candidate-decision
GET   /intake/jobs/<id>/plan.csv
GET   /up   POST /up/cleanup-empty   GET /up/plans/<id>/...  (+row edit, template-proposals, delete)
```

## Целевая архитектура

```text
src/content_factory/catalog/web/          (новый пакет — нативный FastAPI UI каталога)
  __init__.py
  rendering.py     Jinja2Templates(viewer/templates) + 7 фильтров + shared-context + nav
  deps.py          get_conn() (per-request sqlite), пути DEFAULT_DB/SUMMARY, auth dep
  routers/
    pages.py       read-only: /, /competencies, /profiles, /reviews(GET), /up(GET)
    catalog_admin.py   groups/skillsets/skills/candidate-competencies/archive/artifact-templates
    intake.py      workspace + create + status(JSON) + next-step/build-dag/apply-catalog/…/plan.csv
    reviews.py     reviews POST + build-dag + apply-catalog
    up.py          plans detail + rows + template-proposals + delete/cleanup
viewer/            остаётся: templates/, static/, data-функции, миграции (переиспользуем)
```

Роутер монтируется `app.include_router(catalog_web.router)` с префиксом `/app/spravochnik`
вместо `WSGIMiddleware`-mount.

### Сохранение визуала и механик 1:1
- **Шаблоны и `styles.css` переиспользуем без изменений** — весь визуал/кнопки/классы уже там.
- Единственная правка шаблонов: URL-префикс. Сейчас `action="/reviews"` работает благодаря
  `PrefixRewriteASGI`. В нативных роутах вводим Jinja-global `base="/app/spravochnik"` и меняем
  `action="/x"`/`href="/x"` → `action="{{ base }}/x"` (механически, ~30 форм + nav-ссылки).
  Это единственное касание шаблонов; визуально ничего не меняется.
- **PRG сохраняем**: POST → мутация → `RedirectResponse(status_code=303)` на ту же страницу.
- **intake.html JS/поллинг**: оставляем как есть; fetch бьёт в те же (теперь FastAPI) JSON-роуты
  `/intake/jobs/<id>/status` — контракт ответа сохраняем байт-в-байт.
- **CSV/multipart/JSON**: `Response(media_type="text/csv")`, `UploadFile`/`Form(...)`,
  `JSONResponse` — эквиваленты WSGI-хелперов.

### Соответствие WSGI → FastAPI
| WSGI (viewer) | FastAPI |
|---|---|
| `application(environ, start_response)` + if/elif | отдельные `@router.get/post` функции |
| `render(tpl, ctx)` (замыкание) | `rendering.render(request, tpl, ctx)` (module-level) |
| `html_response/redirect_response/json_response/not_found` | `HTMLResponse`/`RedirectResponse(303)`/`JSONResponse`/`HTTPException(404)` |
| `parse_multipart_form_data`/`parse_qs` тела | `Form(...)`, `UploadFile`, `Request.form()` |
| `open_db(db_path)` per request | `Depends(get_conn)` (yield + close) |
| статик через `/static/` ветку | `app.mount(".../static", StaticFiles(...))` |
| auth через mount | `Depends(get_current_user)` |

## Фазы (каждая — зелёные тесты + boot-smoke + ручной обход страниц)

### 5.1 Фундамент + read-only страницы
- Вынести из замыкания `create_app` в `catalog/web/rendering.py`: сборку Jinja-env, 7 фильтров,
  shared-context, nav; добавить global `base`. `create_app` пока остаётся (WSGI-mount живёт).
- Создать `catalog/web/{deps,routers/pages}.py`; смонтировать StaticFiles; включить router
  **перед** WSGI-mount (native-роуты перекрывают смонтированные для перенесённых путей).
- Портировать: `/` (redirect), `/competencies`(+`/<id>`), `/profiles`(+`/<id>`), `/reviews`(GET),
  `/up`(GET). Обновить префиксы ссылок в этих шаблонах.
- Тесты: `TestClient` на каждую GET-страницу (200 + ключевой контент). Тек. 53 теста не трогаем.

### 5.2 catalog-admin (GET+POST, формы)
- Портировать groups/skillsets/skills(merge/edit)/candidate-competencies/archive/artifact-templates.
- POST через `Form(...)` → мутация (те же data-функции) → 303. Обновить префиксы форм в
  `catalog_admin_*.html`. TestClient: GET-страницы + по одному POST-happy-path на форму.

### 5.3 intake (workspace + джобы + JSON + CSV + upload)
- GET `/intake` (workspace_state), POST `/intake` (multipart upload брифа → `UploadFile`),
  `/intake/jobs/<id>` (GET), `/status`(JSON), `/next-step|/build-dag|/apply-catalog|
  /candidate-decision`(POST), `/plan.csv`(CSV), `/intake/jobs/clear`.
- Сохранить контракт `/status` JSON и инлайн-JS `intake.html` без изменений.
- Тесты: создание джобы (мок LLM-стадий, как в текущем regression-тесте), поллинг `/status`,
  CSV-выгрузка.

### 5.4 reviews + up/plans
- reviews POST/`build-dag`/`apply-catalog`; up `/plans/<id>` detail, row edit, template-proposals,
  delete, cleanup-empty. Обновить префиксы `reviews.html`/`up_*.html`. TestClient happy-paths.

### 5.5 Cutover + чистка
- Заменить в `main.py` `app.mount("/app/spravochnik", build_spravochnik_app(...))` на
  `include_router`. Удалить `spravochnik_mount.py` (`PrefixRewriteASGI`/`WSGIMiddleware`/
  `build_spravochnik_app`) и WSGI-диспетчер `create_app.app`; оставить `create_app`-инициализацию
  схемы или вынести её в `deps`/startup. Data-функции и `apply_runtime_migrations` остаются.
- Полный `pytest` + `ruff`/`mypy` + ручной E2E-обход всех страниц/кнопок.

## Критические файлы
- Создать: `src/content_factory/catalog/web/**` (rendering, deps, 5 routers).
- Изменить: `viewer/templates/*.html` (только URL-префикс `{{ base }}`), `api/main.py` (mount→router).
- Переиспользовать без изменений: `viewer/static/*`, data-функции `viewer/app.py`,
  `route_zones.py`, `migrations.py`, `observability.py`.
- Удалить в 5.5: `api/integrations/spravochnik_mount.py`, WSGI-часть `create_app`.

## Риски
- Огромный `create_app` — data-функции и WSGI-диспетчер в одном файле; выносить render/nav
  без задевания data-функций (53 теста — страховка).
- `PrefixRewriteASGI` переписывал абсолютные ссылки в HTML/JSON — после нативных роутов вместо
  него используем `{{ base }}` в шаблонах; проверить ссылки в инлайн-JS `intake.html`.
- Multipart-загрузка брифа и CSV-выгрузка — покрыть тестами (сейчас парсились вручную).
- Порядок регистрации: native router включать до WSGI-mount на время миграции (перекрытие путей).

## Проверка (Definition of done)
- Все 37 роутов — нативные FastAPI; `spravochnik_mount`/WSGI удалены.
- `TestClient`-тесты на каждый роут (GET-контент + POST-механики); 53 pipeline-теста зелёные.
- Ручной side-by-side E2E: каждая страница и каждая кнопка/форма работают идентично текущему
  mounted-viewer (визуал не изменился — шаблоны/CSS те же).
- `python run.py` → `/app/spravochnik/*` полностью на FastAPI.
