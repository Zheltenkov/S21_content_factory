# Состояние сессии

Обновлён: 2026-10-07 · Агент: claude-opus-5 · Ветка: auditor-branch-dashboard · Рабочее дерево: грязно (73 записи)

## Задача
Перевести модель на `openai/gpt-6-sol`: в «Подготовке проекта», а поскольку модель
везде задаётся через env — во всей системе. Залить в git и на боевой сервер.

## Сделано
- Модель по умолчанию переведена на `openai/gpt-6-sol` во всей системе — коммит `65a7522`,
  подробности и намеренные исключения в [facts/model-gpt6-sol.md](facts/model-gpt6-sol.md).
- В лаборатории менять было нечего: `project_generator_lab/core/llm.py:72` и
  `PROJECT_LAB_MODEL` в локальном `.env` уже держали gpt-6-sol.
- Локальный `.env` переведён на gpt-6-sol (`POLZA_AI_MODEL`, `OPENAI_MODEL`, `MODEL_PLAN`,
  член openai в `MODEL_PANEL`, `MODEL_TEMPLATE_COUNCIL`, `TRANSLATE_SUBTITLES_MODEL`);
  в git он не входит. Копия до правки — `.env.bak.model-gpt6sol-*` в корне.
- Починены два устаревших теста: `tests/llm/test_llm_gateway.py:235` (модель читалась из
  личного `.env`, потому что `resolved_model()` ставит env выше явной модели маршрута) и
  `tests/utils/test_static_ui_contracts.py:60` (точное совпадение `class="intake-card"`
  перестало работать после добавления класса-модификатора). Оба в коммите `65a7522`.
- Снят `.git/index.lock` от 2026-09-26, пустой, без живого процесса git: он блокировал
  коммиты с 08.09 — предыдущий коммит в истории именно от этой даты.
- Прогон: 1552 теста прошли, ruff чист, YAML реестра разбирается.

## Следующий шаг
- Отправить ветку в remote и правку моделей на бой: `/opt/s21_content_factory/.env` и
  `/opt/s21_project_lab/model.env`, затем перезапуск `cf-app` и `cf-project-lab`.
  Env перебивает дефолт кода, поэтому без этого коммит на бою ничего не меняет.
- Привести строку модели в `deploy/env.production.sample` к gpt-6-sol.
- Проверить, держит ли gpt-6-sol строгую `json_schema` через Polza, и при успехе
  добавить его в allowlist `platform/llm/structured_runner.py:146`.

## Открытые вопросы
- Переход на gpt-6-sol втрое дороже по прайсу (2.0/10.0 против 0.75/4.5 USD за 1M):
  принять рост счёта на всём генераторе → ответ нужен от пользователя.
- Заводить ли `project_generator_lab/` под git целиком вместе с `data/` (бенчмарки,
  снимки запусков) → ответ нужен от пользователя.

## Не трогать
- Работа по РПД в дереве (`src/content_factory/rpd/*`, незакоммиченные
  `assessment.py`, `competency_agents.py`, `curriculum.py` и прочее, 334 строки в
  `docs/rpd-generator.md`) — чужой сессии. В коммит `65a7522` из общих файлов попали
  только строки про модель, остальное оставлено в дереве.
- `tests/api/routers/test_curriculum.py::test_build_context_rejects_ad_hoc_without_source_plan_id`
  падает — это та же незакоммиченная работа по РПД.
- `tests/content_gen/test_node_services.py::test_practice_service_blocks_critical_critic_issue`
  падает и на HEAD: код и тест совпадают с HEAD, текст сообщения критика разошёлся с
  ожиданием теста. К модели отношения не имеет.
- Боевой релиз лаборатории `20261007-01` от 07.10.2026 18:22 МСК выложен другим агентом —
  его состоянием эта сессия не управляет.
