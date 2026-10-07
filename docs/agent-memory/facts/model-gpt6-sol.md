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

**Как применять:** gpt-6-sol дороже gpt-5.4-mini примерно втрое по прайсу
(`project_generator_lab/concept/config/model_prices.json`: 2.0/10.0 против
0.75/4.5) — при росте счёта смотреть сюда. Не сделано и требует доступа:
отправка ветки `auditor-branch-dashboard` в remote, правка моделей в боевых
файлах окружения (`/opt/s21_content_factory/.env` и `/opt/s21_project_lab/model.env`,
имя переменной `PROJECT_LAB_MODEL`) и перезапуск контейнеров. Env перебивает
дефолт кода, поэтому без правки боевого окружения коммит на бою ничего не меняет.
Строку с моделью в `deploy/env.production.sample` тоже надо привести к gpt-6-sol.

Связано: [[project-lab-production]]
