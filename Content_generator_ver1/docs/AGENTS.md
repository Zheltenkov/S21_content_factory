# Агентная Система Content Generator

## Обзор

Content Generator использует оркестрацию через `AgentFlow`, но в текущем production-контуре это не "свободная мультиагентность", а управляемый пайплайн с явными фазами и typed state.

Ключевой принцип:

- deterministic orchestration в `orchestrator.py` и `flow.yaml`;
- специализированные агенты только для отдельных шагов генерации;
- контекст проекта приходит из `curriculum_context`, а не из локального retrieval-контура.

## Основной Контур

Фактический runtime-пайплайн:

1. `context`
   Фаза собирает `ProjectSeed`, `ProjectContextMeta`, `ContextAnalysisResult` и `ProjectContextBundle` из входного seed и контекста учебного плана.
2. `task_planning`
   Определение числа задач, сложности и контрактов story/practice/artifact chain.
3. `title_annotation`
   Генерация названия и аннотации как отдельного reviewable артефакта.
4. `skeleton`
   Создание каркаса README.
5. `theory`
   Генерация теории с проверками и локальными repair-pass.
6. `practice`
   Генерация практики.
7. `global_quality`
   Глобальная редактура и доводка.
8. `evaluation`
   Рубрическая оценка.
9. `translate`
   Перевод после оценки.
10. `finalize`
   Сбор результата, архивных артефактов и метаданных.

Каждый runtime-узел имеет единый операционный контракт в `content_gen/config/node_contracts.yaml`: role, входы/выходы flow context, prompt/config version, model_role, validators, repair/fallback policy и observability tags.

## Роли Компонентов

### Orchestrator

Файл: `content_gen/orchestrator.py`

Отвечает за:

- запуск flow;
- перенос состояния между узлами;
- финальную сборку отчёта;
- сериализацию `flow_trace`;
- упаковку артефактов.

### GenerationFlowHandlers

Файл: `content_gen/flow_handlers.py`

Это application-layer слой над node services/executors. Он связывает AgentFlow-ноды с concrete services и не должен скрывать бизнес-логику в prompt-only виде.

### Agents

Директория: `content_gen/agents/`

Production-значимые агенты:

- `SkeletonAgent`
- `IntroRulesAgent`
- `TheoryAgent`
- `PracticeAgent`
- `TaskPlanner`
- `TitleAnnotationAgent`
- `ContentEditorAgent`
- `StyleGuardAgent`
- `TranslatorAgent`
- `RegenerationAgent`

Служебные агенты:

- `DatasetGeneratorAgent`
  Генерирует файлы данных для практических задач.

## Structured Outputs

Файл: `content_gen/llm/structured_output.py`

Structured Outputs применяются там, где ответ модели критичен по контракту. Основной принцип:

- schema-first;
- Pydantic как доменный контракт;
- fallback/repair вне промпта, в коде.

## Что Удалено Из Runtime

Из production-контура выведены:

- локальный каталог `data/` как источник контекста;
- обязательный bootstrap локального retrieval-индекса;
- compatibility-фасады локального контекстного поиска.

Это важно для handoff:

- devops не должен поднимать локальный индекс как обязательную часть сервиса;
- отсутствие локального markdown-корпуса больше не считается ошибкой запуска;
- контекстный контракт теперь проходит через входной `seed`.

## Связанные Документы

- [ARCHITECTURE.md](ARCHITECTURE.md)
- [CRITERIA.md](CRITERIA.md)
- [API.md](API.md)
