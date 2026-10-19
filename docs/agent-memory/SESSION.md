# Состояние сессии

Обновлён: 2026-10-19 · Агент: claude-opus-5 · Ветка: auditor-branch-dashboard · Рабочее дерево: грязно (73 записи)

## Задача
19.10.2026: разобрать жалобу «модуль РПД на бою упал» (ERR_EMPTY_RESPONSE).
Разобрано — модуль работает, подробности в
[facts/rpd-generate-sync-wait.md](facts/rpd-generate-sync-wait.md).

Прежняя задача (07.10): перевести модель на `openai/gpt-6-sol`: в «Подготовке проекта», а поскольку модель
везде задаётся через env — во всей системе. Залить в git и на боевой сервер.

## Сделано
- Найдена и устранена настоящая причина `ERR_EMPTY_RESPONSE` у методистов: корпоративный
  прокси обрывает ответ, внутри которого есть `<script>` с загрузкой файлов. Установлено
  пробами на боевом адресе (четыре круга), разбор и таблица проб —
  [facts/corporate-tls-proxy.md](facts/corporate-tls-proxy.md). Скрипты страниц РПД
  вынесены в `static/js/rpd-page.js` и `static/js/rpd-waiting.js` — коммит `69be43a`,
  выложен (sha256 сверен, скрипты отдаются 200, 113 аудитов и 6 РПД на месте).
- РПД добавлен в `protected_prefixes` (`api/main.py:102`) — коммит `26ed4e0`, выложен.
  Без сессии страница вела не на вход, а отдавала `401 {"detail": ...}`; это и видели
  в инкогнито. Теперь `303 → /?next=/app/rpd`, как у остальных инструментов.
- Сборка РПД переведена в фон — коммит `ff098d6`, выложен в `cf-app` (`docker cp` +
  `docker restart`, sha256 сверен, 113 аудитов и ffmpeg на месте, журнал чист).
  `generate` и `registry/{id}` отвечают 202 сразу, есть `GET /app/rpd/{run_id}/status`,
  страница показывает счётчик и сама открывает результат; упавшая сборка называет причину.
  Разбор инцидента — [facts/rpd-generate-sync-wait.md](facts/rpd-generate-sync-wait.md).
- Код: модель по умолчанию переведена на gpt-6-sol во всей системе — коммит `65a7522`,
  разбор и намеренные исключения в [facts/model-gpt6-sol.md](facts/model-gpt6-sol.md).
  В лаборатории менять было нечего: `project_generator_lab/core/llm.py:72` уже держал gpt-6-sol.
- Ветка `auditor-branch-dashboard` отправлена в `origin` (GitHub, Zheltenkov/S21_content_factory).
- Бой, лаборатория: `/opt/s21_project_lab/model.env` переведён на gpt-6-sol
  (`PROJECT_LAB_MODEL`, `POLZA_AI_MODEL`), контейнер `cf-project-lab` пересоздан.
  `after_release.sh` прошёл: api 401, страница 303, 13 запусков на месте, ошибок 0.
  До правки бой был на gpt-5.4-mini — локальный gpt-6-sol туда не попадал.
- Бой, основное приложение: `/opt/s21_content_factory/.env` переведён на gpt-6-sol и
  применён — `cf-app` пересоздан из снимка работающего контейнера
  (`docker commit` → `deploy-app:latest` → `up --force-recreate --no-build --no-deps`),
  поэтому горячий код, ffmpeg и 113 аудитов на месте. В окружении контейнера
  `POLZA_AI_MODEL`, `MODEL_PLAN`, `MODEL_PANEL`, `MODEL_TEMPLATE_COUNCIL` = gpt-6-sol;
  дефолты модели и цены реестра в коде контейнера тоже заменены. Проверено:
  `/api/v1/auth/me` 401, `/app/auditor` 303, ошибок в журнале 0. При перезапуске
  оборвался один считавшийся запуск (`reconciled stale active runs=1`).
  Откат: образ `deploy-app:running-20261007`, прежний сборочный образ
  `deploy-app:prebuilt-backup-20261007`, копии окружения `.bak.model-20261007`.
- Копия аудитов снята на хост: `/opt/backups/auditor/auditor_20261007`, 711 МБ, 113 веток.
  Они лежали только в записываемом слое контейнера — см.
  [facts/cf-app-writable-layer.md](facts/cf-app-writable-layer.md).
- Починены два устаревших теста (`tests/llm/test_llm_gateway.py:235`,
  `tests/utils/test_static_ui_contracts.py:60`) — в коммите `65a7522`.
- Снят `.git/index.lock` от 2026-09-26: пустой, без живого процесса git, блокировал
  коммиты с 08.09.
- Прогон: 1552 теста прошли, ruff чист.

## Следующий шаг
- Дождаться подтверждения методиста, что `/app/rpd` открывается с рабочим VPN, и убрать
  пробы: `/var/www/probe` и два блока `location ^~ /probe/` в `/etc/nginx/conf.d/cf.conf`.
- Фоновая сборка на бою уже проверена живым запуском `930717461e52…` (09:10 UTC):
  браузер опрашивал `/status` до готовности. Отдельных действий не требует.
- Прогнать на бою реальный замысел и генерацию README, чтобы убедиться в качестве
  ответов gpt-6-sol и увидеть цену по журналу вызовов.
- Проверить, держит ли gpt-6-sol строгую `json_schema` через Polza, и при успехе
  добавить его в allowlist `src/content_factory/platform/llm/structured_runner.py:146`.

## Открытые вопросы
- Переход на gpt-6-sol втрое дороже по прайсу (2.0/10.0 против 0.75/4.5 USD за 1M):
  принять рост счёта на всём генераторе → ответ нужен от пользователя.
- Заводить ли `project_generator_lab/` под git целиком вместе с `data/` → от пользователя.
- Доступа в Jira нет: плагин `atlassian-rovo` в каталоге Codex стоит как `AVAILABLE`,
  учётных данных нет нигде, коннектор требует OAuth → от пользователя.

## Не трогать
- `cf-app` пересоздавать только через снимок работающего контейнера —
  [facts/cf-app-writable-layer.md](facts/cf-app-writable-layer.md). Образы
  `deploy-app:running-20261007` и `deploy-app:prebuilt-backup-20261007` не удалять:
  это откат.
- Работа по РПД в дереве (`src/content_factory/rpd/*`, незакоммиченные `assessment.py`,
  `competency_agents.py`, `curriculum.py`, 334 строки в `docs/rpd-generator.md`) — чужой
  сессии. В коммит `65a7522` из общих файлов попали только строки про модель.
- `tests/api/routers/test_curriculum.py::test_build_context_rejects_ad_hoc_without_source_plan_id`
  падает — та же незакоммиченная работа по РПД.
- `tests/content_gen/test_node_services.py::test_practice_service_blocks_critical_critic_issue`
  падает и на HEAD: текст сообщения критика разошёлся с ожиданием теста. К модели не относится.
