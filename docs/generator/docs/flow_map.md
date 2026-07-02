# Карта процессов генерации контента (AgentFlow)

Граф выполнения пайплайна генерации описан в конфиге и выполняется рантаймом `AgentFlowRunner` (`content_gen/workflow/flow_runner.py`). Старый путь `content_gen/agents/flow.py` оставлен как compatibility shim для paused sessions и старых импортов. Конкретные handlers нод находятся в `GenerationFlowHandlers` (`content_gen/flow_handlers.py`). Исполняемый конфиг: `content_gen/config/flow.yaml`. Единый контракт нод: [content_gen/config/node_contracts.yaml](../content_gen/config/node_contracts.yaml). Документированная версия с комментариями по нодам: [content_gen/config/flow_documented.yaml](../content_gen/config/flow_documented.yaml).

## Схема переходов

Линейная цепочка:

```
context → task_planning → title_annotation → skeleton → theory → practice → global_quality → evaluation → translate → finalize
```

`translate` имеет условие `run_if: target_language != 'ru'`; при русском языке узел пропускается, но переход к `finalize` сохраняется.

## Таблица нод: входы, выходы, переходы

| # | Node ID | Назначение | Входы (из контекста) | Выходы в контекст | Переход |
|---|---------|------------|----------------------|-------------------|--------|
| 0 | context | Seed + контекст учебного плана | — | seed, context_meta, context_analysis, context_bundle, similar_projects, warnings | → task_planning |
| 1 | task_planning | План практики и контракты деятельности | seed, context_meta, context_analysis | seed, task_plan, story_map_contract, practice_plan_contract, artifact_chain_plan, evidence_specs, warnings | → title_annotation |
| 2 | title_annotation | Название и аннотация | seed, context_meta | title, annotation | → skeleton |
| 3 | skeleton | Каркас README + глава 1 | seed, context_meta, title, annotation, story_map_contract, practice_plan_contract | markdown, title, annotation, intro_section, blueprint, warnings, issues | → theory |
| 4 | theory | Глава 2 (теория) | seed, context_meta, markdown, story_map_contract, practice_plan_contract, artifact_chain_plan, section_contexts | markdown, theory_parts, warnings, issues | → practice |
| 5 | practice | Глава 3 + critic | seed, markdown, story_map_contract, practice_plan_contract, artifact_chain_plan, section_contexts | markdown, practice_tasks, warnings, issues, practice_critic_issues, artifact_chain_plan, evidence_specs, dataset_files, section_contexts | → global_quality |
| 6 | global_quality | Глобальное качество текста | seed, markdown | markdown | → evaluation |
| 7 | evaluation | Оценка по рубрике | seed, markdown | rubric_json, issues | → translate |
| 8 | translate | Перевод, если нужен | seed, markdown | markdown, translated_markdown | → finalize |
| 9 | finalize | Сборка результата через ResultAssembler | seed, markdown, context_meta, context_analysis, context_bundle, title, annotation, intro_section, theory_parts, practice_tasks, blueprint, rubric_json, task_plan, story_map_contract, practice_plan_contract, artifact_chain_plan, evidence_specs, translated_markdown | result, assets, project_spec, section_contexts | — |

## Единый контракт ноды

Каждая нода описана в `content_gen/config/node_contracts.yaml`. Это не дублирующий flow, а операционный паспорт узла:

- `node_id`, `role` — идентификатор и ответственность;
- `input_schema`, `output_schema` — ключи mutable flow context, которые должны совпадать с `flow.yaml`;
- `prompt_id`, `prompt_version` — версия prompt/config policy для trace и offline eval;
- `model_role` — роль из `config/model_registry.yaml`;
- `validators` — кодовые проверки и typed schema, которые считаются источником truth;
- `repair_policy`, `fallback_policy` — что можно исправлять и как деградировать;
- `observability_tags` — tags, которые попадают в `NodeTraceEvent.metadata`.

Тест `tests/content_gen/test_node_contracts.py` проверяет, что контракт не расходится с `flow.yaml` и `model_registry.yaml`.

## Durable Execution И Recovery

Runtime теперь пишет production-checkpoint после каждого узла в `generation_workflow_checkpoints`:

- `checkpoint_index` совпадает с позицией узла в `flow.yaml`, поэтому retry/resume не зависит от длины process-local списка шагов;
- `input_hash` берется из `NodeTraceEvent` и фиксирует вход узла;
- `output_artifact` хранит компактный JSON-safe результат узла для UI/debug;
- `context_snapshot` хранит сериализованный flow context после узла и позволяет восстановить запуск после падения процесса;
- `validation_result`, `retry_count`, `duration_ms` дают node-level observability.

Команды `cancel`, `resume`, `retry_node`, `regenerate_section` пишутся в `generation_workflow_states.commands`. `GenerationWorkflowService.build_recovery_session()` строит restartable session из БД:

- обычный recovery продолжает с узла после последнего успешного checkpoint;
- `retry_node` стартует с checkpoint перед целевой нодой;
- `regenerate_section` сначала мапит section на node (`theory → theory`, `practice → practice`, `quality → global_quality`) и дальше использует тот же node-level retry path;
- если checkpoint'ов еще нет, запуск восстанавливается из `metadata.project_seed_payload`.

На старте приложения активные workflow из предыдущего процесса (`running`, `node_completed`, `resuming`) переводятся в `interrupted`: они больше не выглядят как живая фоновая задача, но остаются восстановимыми через workflow command `resume` или `retry_node`.

После завершения графа `FlowResultFinalizer` проверяет, что `finalize` создал `OrchestratorResult`, сериализует `flow_trace` и передает результат в `MethodologyTraceRecorder` для записи review/repair summaries.

## Условия переходов

- В YAML все рёбра заданы без поля `condition`: после каждой ноды выполняется ровно одна следующая по графу.
- Единственная ветка в YAML: `translate` выполняется только если `target_language != 'ru'`; при пропуске переход в `finalize` выполняется в любом случае.

## Методологический gate

После ключевых нод `context`, `task_planning`, `skeleton`, `theory`, `practice`, `evaluation`, `finalize` запускается `MethodologyGate`.

Gate не генерирует контент и не является свободным агентом. Это deterministic evaluation layer с typed-контрактом `StageReviewResult`:

- `status`: `passed`, `warning`, `failed`, `skipped`
- `issues`: список методологических замечаний с severity и code
- `repair_instructions`: инструкции для repair-pass или human review
- `human_review_required`: флаг ручной проверки
- `metrics` и `evidence`: проверяемые признаки стадии

Результаты gate попадают в `report_json.methodology_reviews`, `report_json.methodology_summary` и issues соответствующего шага в `flow_trace`.

После gate может запускаться `MethodologyRepairController`: deterministic repair-pass с typed-контрактом `StageRepairResult`. Он не вызывает LLM и не переписывает контент свободно. Текущая политика:

- максимум одна попытка repair на стадию;
- только allowlist issue-кодов (`task_planning`, `skeleton.blueprint_missing`, `theory.parts_missing`, `practice.tasks_missing`);
- repair делает только безопасные структурные правки: синхронизация `TaskPlan`/`ProjectSeed`, восстановление `ProjectBlueprint`, парсинг `TheoryPart` и `PracticeTask` из уже сгенерированного markdown;
- после успешной правки gate запускается повторно один раз, чтобы trace видел состояние после repair.

Результаты repair попадают в `report_json.methodology_repairs`, `report_json.methodology_repair_summary` и issues соответствующего шага в `flow_trace`.

Запись этих артефактов выполняет `MethodologyTraceRecorder`: он синхронизирует `ProjectFlowState`, сериализует `StageReviewResult`/`StageRepairResult` и добавляет summaries в финальный `report_json`.

## Связанные файлы

- Конфиг (исполняемый): `content_gen/config/flow.yaml`
- Контракты нод: `content_gen/config/node_contracts.yaml`
- Конфиг с комментариями: `content_gen/config/flow_documented.yaml`
- Рантайм графа: `content_gen/workflow/flow_runner.py`
- Реализации нод: `content_gen/flow_handlers.py`
- Методологический gate: `content_gen/methodology/gate.py`
- Ограниченный repair-pass: `content_gen/methodology/repair.py`
- Trace/report recorder методологии: `content_gen/methodology/trace.py`
- Финальная сборка результата: `content_gen/result_assembly.py`
- Пост-обработка завершенного Flow: `content_gen/flow_result.py`
- Компоновка зависимостей и запуск: `content_gen/orchestrator.py`
