# Архитектура

Content Generator построен как production-oriented LLM application: orchestration, доменные контракты, model invocation, validation и persistence разделены по слоям.

## Цели архитектуры

- Управляемая генерация учебного проекта, а не свободная “мультиагентность”.
- Повторяемость результата через typed state, checkpoints и validation reports.
- Human-in-the-loop для методолога без потери состояния при рестарте процесса.
- Точечные правки через typed patch, а не переписывание всего README.
- Наблюдаемость: flow trace, статусы узлов, usage/cost tracking, rubric reports.

## Слои

| Слой | Где находится | Ответственность |
|---|---|---|
| UI | `static/` | Страницы генерации, проверки, перевода, методологический чат, визуализация Markdown/диаграмм. |
| API | `api/routers/` | HTTP-контракты, auth, upload/download, polling, workflow commands. |
| Application services | `api/services/`, `content_gen/flow_handlers.py` | Запуск генерации, сохранение результатов, восстановление workflow, review actions. |
| Domain | `content_gen/` | Генерация, методологический gate, regeneration pipeline, checklist, validation. |
| LLM infra | `content_gen/llm/`, `config/model_registry.yaml` | Выбор provider/model, structured output, retries, timeout, budget. |
| Persistence | `api/db/`, `migrations/` | Пользователи, сессии, запуски, checkpoints, результаты, логи. |
| Evaluation | `content_gen/evaluation/`, `tests/evaluation/` | Regression datasets, regeneration harness, quality checks. |

## Runtime flow

Основной граф описан в `content_gen/config/flow.yaml`:

```text
context -> task_planning -> title_annotation -> skeleton -> theory -> practice
-> global_quality -> evaluation -> translate -> finalize
```

`translate` выполняется только если итоговый язык не русский. При обычной русскоязычной генерации узел пропускается и flow переходит к `finalize`.

Подробная карта нод: [flow_map.md](flow_map.md).

## Node contracts

Каждая нода имеет контракт в `content_gen/config/node_contracts.yaml`:

- входные и выходные ключи flow context;
- prompt/config version;
- model role;
- validators;
- repair/fallback policy;
- observability tags.

Тест `tests/content_gen/test_node_contracts.py` защищает flow от drift: новый узел нельзя добавить без явного контракта.

## Workflow profiles

Режимы работы не должны расползаться по UI-условиям. Для этого используются workflow profiles:

- обычный режим — полный автоматический pipeline с результатом в конце;
- методологический режим — pipeline с контрольными точками, ассистентом, preview diff и решением методолога.

UI читает capabilities профиля и показывает только доступные действия.

## Methodology mode

Методологический режим состоит из трех частей:

1. `MethodologyGate` оценивает результат этапа и определяет, нужен ли human review.
2. `MethodologyAssistant` принимает правки обычным языком и связывает их с выбранным target.
3. `ScopedRevision` строит ограниченную правку текущего блока и показывает diff до применения.

Решения методолога сохраняются в БД. Если процесс перезапущен, запуск можно восстановить из checkpoint/session payload.

## Regeneration pipeline

Перегенерация устроена как schema-first pipeline:

1. `RegenerationRequest` содержит выбранные секции, локальные инструкции и общую правку.
2. Сервис строит typed patch с явной областью применения.
3. Apply-слой детерминированно заменяет/добавляет/удаляет блоки README.
4. Валидатор пересчитывает структуру, содержание, рубрику и отчет.
5. UI показывает diff и validation report.

Правка может затрагивать содержание, номера и ссылки, если пользователь добавляет или удаляет главу. Это разрешенная структурная зависимость, а не “рандомное” изменение соседних секций.

## Checklist generation

`content_gen/checklist.py` строит `check-list.yml` из финального README и практических задач. Файл попадает в архив как `check-list.yml`.

Источник truth — финальный README после всех принятых правок, а не промежуточные черновики.

## Translation pipeline

Документный перевод защищает Markdown-блоки, таблицы, формулы, код и Mermaid. Видео-перевод строит transcript, VTT, SRT, ASS и при необходимости MP4 с субтитрами.

Поддерживаемая письменность:

- `tg` — кириллица;
- `kg` — кириллица;
- `uz` — латиница.

## State and recovery

Состояния generation workflow пишутся в БД после ключевых шагов:

- active/running;
- paused/needs review;
- interrupted;
- completed;
- failed;
- cancelled.

При старте приложения зависшие `running`, `resuming` и похожие состояния переводятся в восстановимый interrupted/status, чтобы dashboard не показывал вечные активные задачи.

## Observability

Минимальный набор сигналов:

- request logs и user runs;
- flow trace по нодам;
- duration/status/error по этапам;
- LLM usage и role-level budget;
- rubric report;
- regeneration validation report;
- translation job progress.

Опционально включаются OTEL/Langfuse через `.env`.

## Failure modes

| Риск | Как обрабатывается |
|---|---|
| LLM вернул невалидную структуру | Structured output validation, repair/fallback. |
| Процесс упал на контрольной точке | Paused session/checkpoint сохраняются в БД. |
| Перегенерация ухудшила rubric | UI показывает предупреждение и validation report. |
| Mermaid не рендерится | UI показывает исходный блок и человекочитаемую ошибку. |
| Перевод завис | Polling/status, job state, пользовательское сообщение. |
| Старый токен auth | Session guard переводит пользователя на login без raw error. |
