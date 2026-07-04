# УП → Генератор: загрузка учебного плана и генерация проекта

**Дата:** 2026-07-04
**Статус:** проектирование (approach A утверждён)

## Цель

Дать методологу в генераторе контента выбрать сохранённый **учебный план (УП)**, затем **блок** и **проект** внутри него; данные проекта автоматически подгружаются в форму генерации; методолог правит и запускает генерацию. Один вход — из генератора.

## Что уже существует (не строим заново)

Значительная часть трубопровода проложена в фазе 4c рефакторинга:

- **Персист УП в Postgres.** УП создаётся/правится в каталоге (`/app/spravochnik/up`); после каждой мутации `up.py::_redirect_synced` вызывает `_sync_up_curriculum()` → зеркалит в реляционные таблицы Postgres `catalog.curriculum_plan` + `catalog.curriculum_plan_row` (best-effort, авто).
- **API учебных планов** (`api/routers/curriculum.py`, prefix `/api/v1/curriculum`):
  - `GET /plans` — список планов (`_mirror_plan_summary`: id, title, direction, blocks, projects, updated_at).
  - `GET /plans/{source_id}` — собранный payload плана (`_assemble_plan_payload`: план + плоский `rows`, сгруппированный по `block_index`).
  - `POST /plans/sync` — ручной пересинк.
- **Генератор уже принимает curriculum_context.** `ProjectSeed` (generation/models/schemas.py) содержит `title_seed, project_description, thematic_block, direction, skills, learning_outcomes, platform_name, curriculum_context{...}`; `context_phase_executor.py` читает `curriculum_context.block_name`, `previous_projects` и т.д. Генерация из проекта УП уже работает на уровне доменной модели — не хватает только UI-обвязки.
- **Единая авторизация.** `ToolAuthCookieMiddleware` защищает `/app/generate`, `/app/auditor`, `/app/spravochnik/*` одним cookie генератора. Вне объёма этого спека (оставляем как есть).

## Объём

**В объёме:**
1. Новый эндпоинт: seed-проекта из строки УП (маппинг на сервере).
2. Панель «Загрузить из УП» в SPA генератора (`static/index.html`): каскад план → блок → проект → prefill формы.
3. Тесты: маппинг seed (unit) + контракт эндпоинта (api) + смоук панели.

**Вне объёма:** правки авторизации; deep-link из каталога (approach B); пакетная генерация по блоку; изменение самого пайплайна генерации; правки схемы БД.

## Архитектура

### Компонент 1 — Seed-эндпоинт (бэкенд, единственный источник маппинга)

```
GET /api/v1/curriculum/plans/{plan_id}/rows/{block_index}/{project_index}/seed
  -> 200 { seed: <ProjectSeed-совместимый dict>, meta: {plan_title, block_title, project_name} }
  -> 404 если план/блок/проект не найдены
```

Реализация в `curriculum.py`:
- Читает план + строки из реляционного зеркала (те же хелперы, что `/plans/{id}`).
- Находит строку по `(block_index, project_index_in_block)`.
- Маппит строку УП → dict полей `ProjectSeed` (см. таблицу). Функция-маппер `build_seed_from_plan_row(plan, row) -> dict` — чистая, изолированная, покрыта unit-тестом.
- Возвращает JSON. **Не** запускает генерацию (её запустит существующий `/generate` после правки формы).

Маппинг строки УП → ProjectSeed:

| ProjectSeed поле        | Источник (curriculum_plan_row / plan) |
|-------------------------|----------------------------------------|
| `title_seed`            | `project_name`                         |
| `project_description`   | `project_summary`                      |
| `thematic_block`        | `block_title`                          |
| `direction`             | `plan.direction`                       |
| `skills`                | `skills_list` ∪ `weighted_skills` (разбор в список, uniq) |
| `learning_outcomes`     | `outcomes_skills` (разбор в список)    |
| `platform_name`         | `platform_project_name`                |
| `curriculum_context`    | `{ block_name: block_title, block_goal, project_index: project_index_in_block, current_project_description: project_summary }` |

Разбор строк-списков (`skills_list`, `weighted_skills`, `outcomes_skills`) — по существующим разделителям УП (перенос строки / `;` / `,`), пустые отбрасываются, порядок сохраняется, дубли убираются. Переиспользовать имеющийся сплиттер, если есть; иначе один маленький хелпер.

### Компонент 2 — Панель «Загрузить из УП» в SPA генератора

В `static/index.html` (+ его JS) добавить сворачиваемую панель над формой генерации:

1. При открытии панели — `GET /api/v1/curriculum/plans` → выпадающий список планов (title + direction + счётчики).
2. Выбор плана — `GET /api/v1/curriculum/plans/{id}` → построить каскад: список **блоков** (по `block_index`, заголовок `block_title`), в каждом — список **проектов** (`project_name`).
3. Клик по проекту — `GET .../rows/{block}/{project}/seed` → заполнить поля существующей формы генерации значениями из `seed` (title, description, skills, block, direction, platform, learning_outcomes, curriculum_context в скрытом поле).
4. Методолог правит любые поля → жмёт **Generate** → существующий `POST /api/v1/generate` (без изменений контракта).

UI-минимализм: три связанных `<select>` (план → блок → проект) или план-dropdown + дерево. Пустое состояние: «Планов пока нет — создайте УП в Справочнике». Ошибка загрузки — неблокирующее сообщение, форма остаётся ручной.

### Поток данных

```
Каталог УП (SQLite) --auto-sync--> Postgres mirror (curriculum_plan[_row])
                                          |
Generator SPA  --GET /plans------------->|  список планов
               --GET /plans/{id}-------->|  блоки+проекты
               --GET .../seed----------->|  build_seed_from_plan_row -> ProjectSeed dict
               <---- prefill формы -------
               --POST /generate--------->  существующий пайплайн (curriculum_context уже поддержан)
```

## Обработка ошибок

- Seed-эндпоинт: несуществующий plan/block/project → `404` с понятным `detail`. Пустой `project_summary` → seed отдаётся, `project_description=""` (методолог заполнит; форма валидирует обязательность как сейчас).
- Синк отсутствует/устарел (план правился, но зеркало не догналось) — панель показывает то, что в Postgres; кнопка «Обновить» дергает `POST /plans/sync`. (Авто-синк уже есть на мутациях УП, так что рассинхрон маловероятен.)
- SPA: сетевые ошибки на любом шаге каскада → неблокирующее уведомление, ручной ввод остаётся доступным.

## Тестирование

- **Unit** `build_seed_from_plan_row`: полная строка → все поля; частичная строка (пустые skills/outcomes) → корректные дефолты; разбор списков (разделители, uniq, порядок).
- **API-контракт** seed-эндпоинта: валидный plan/block/project → 200 + форма seed; несуществующие индексы → 404; проверка на реальном зеркале (фикстура с планом).
- **Смоук** (Playwright, локально): открыть панель → выбрать план → блок → проект → поля формы заполнились → без ошибок в консоли.
- Существующий набор тестов остаётся зелёным (никаких правок контракта `/generate`).

## Открытые допущения (зафиксированы)

- Генерируется **один проект за раз** (пакет по блоку — вне объёма).
- Маппинг seed живёт **только на сервере**; SPA не дублирует логику полей.
- УП уже в Postgres к моменту выбора (гарантируется авто-синком на мутациях каталога).
