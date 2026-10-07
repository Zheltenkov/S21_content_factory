---
name: model-gpt6-sol
description: С 2026-10-07 модель по умолчанию во всей системе — openai/gpt-6-sol; что осталось сделать в боевом окружении
metadata:
  type: decision
---

2026-10-07 по требованию пользователя модель по умолчанию во всей системе переведена
на `openai/gpt-6-sol` — коммит `65a7522`. До этого на gpt-6-sol работала только
«Подготовка проекта» (`project_generator_lab/core/llm.py:72` и `PROJECT_LAB_MODEL`),
а генератор, аудитор и конвейер навыков держали `openai/gpt-5.4-mini`.

Переведены: шесть маршрутов polza в `src/content_factory/config/model_registry.yaml`
(вместе с ценами 2.0/10.0 USD за 1M вместо устаревших 0.15/0.60),
`DEFAULT_MODEL_BY_PROVIDER` и `ModelRegistry.default()` в
`src/content_factory/platform/llm/model_registry.py`, `MODEL_PLAN` и член openai в
`MODEL_PANEL` (`src/content_factory/catalog/pipeline/config.py`), запас генератора
в `phase_executors.py`, три дефолта аудитора (`aligner.py`, `cli.py`, `web_app.py`),
образцы `.env.example` и документации.

Намеренно оставлены прежними: `MODEL_SEARCH` (perplexity/sonar),
`WHISPER_ASR_MODEL` (whisper-1), цепочки запаса deepseek и gigachat — это другие
поставщики и другие задачи. Allowlist строгой JSON-схемы в
`platform/llm/structured_runner.py` тоже не тронут: поддержка `json_schema` у
gpt-6-sol не проверена (проба через Polza висела без ответа и была снята), а без
записи в allowlist код сам переходит на `response_format="json_object"`, который
держит любая OpenAI-совместимая модель.

**Почему:** модель задаётся через env во всех модулях, поэтому два разных дефолта
означали, что фактическую модель определял только файл окружения. Один дефолт
убирает расхождение между модулями и между локальной машиной и сервером.

Состояние боя на 2026-10-07: до этой правки сервер был целиком на
`openai/gpt-5.4-mini`, включая лабораторию (`PROJECT_LAB_MODEL` в
`/opt/s21_project_lab/model.env`) — локальный gpt-6-sol на бой не попадал.

Сделано на сервере: оба файла окружения переведены на gpt-6-sol
(`/opt/s21_content_factory/.env`: `POLZA_AI_MODEL`, `MODEL_PLAN`, член openai в
`MODEL_PANEL`, `MODEL_TEMPLATE_COUNCIL`; `/opt/s21_project_lab/model.env`:
`POLZA_AI_MODEL`, `PROJECT_LAB_MODEL`), копии рядом с суффиксом
`.bak.model-20261007`. Контейнер `cf-project-lab` пересоздан — лаборатория на бою
действительно работает на gpt-6-sol, `after_release.sh` прошёл: api 401, страница
303, 13 запусков на месте, ошибок в журнале 0.

**Как применять:** gpt-6-sol дороже gpt-5.4-mini примерно втрое по прайсу
(`project_generator_lab/concept/config/model_prices.json`: 2.0/10.0 против
0.75/4.5) — при росте счёта смотреть сюда. Основное приложение `cf-app` остаётся
на gpt-5.4-mini: его переменные окружения вшиты при создании контейнера, а
пересоздавать его нельзя — см. [[cf-app-writable-layer]]. Модель там сменится при
следующем полноценном релизе, файл окружения к нему уже готов. Строку с моделью в
`deploy/env.production.sample` тоже надо привести к gpt-6-sol (локально файл не
читался — блокировал классификатор прав).

Связано: [[project-lab-production]]
