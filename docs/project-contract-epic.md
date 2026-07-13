# Epic: от текстового описания проекта к типизированному контракту

**Статус:** план (не начат). Источник: внутренний прогон CSV → УП #30 (оценка 7.5/10) + архитектурный разбор.
**Цель:** пайплайн должен проверять *исполнимость и смысл* проектного результата, а не только заполненность строк. LLM — формулировщик; методические ограничения — детерминированный код.

## Корневая причина

`ProjectBlueprint` ([curriculum/domain.py:46](../src/content_factory/catalog/pipeline/curriculum/domain.py#L46)) хранит `title`, `artifact`, критерии как **строки** (`artifact: str`, `title: str`, `enrichment: dict[str,str]`). Пайплайн проверяет непустоту, а не executability. Следствия из прогона: длинные названия (13/20 > 72 симв.), формальные артефакты (схема вместо прототипа), непроверяемые критерии, generic template fallback, вводящая в заблуждение метрика «162.53 дня».

## Инвариант эпика (что делает его инкрементально-shippable)

**Gate блокирует publish/freeze УП, но НИКОГДА не блокирует создание draft.** Каждый слайс лендится зелёным независимо; draft всегда доступен методологу; ужесточение — только на пути публикации. Тот же strangler-подход, что и в остальных ре-монолитах.

## Принципы (из разбора + поправки)

1. **Один source of truth.** Контракт — истина; строковые `title`/`artifact`/`criteria` становятся *производными* (рендер из контракта) либо deprecated. Двойное представление разъедется — не допускать.
2. **Классификация project→area и project→template — явная и ранняя, не fuzzy.** Присваивать `project_type`/`policy_area` детерминированно на этапе группировки навыков (из `coverage_area`/bloom/skill-метаданных), LLM формулирует ВНУТРИ ограничения. Неуверенная классификация → `draft-only / нужен методолог`, никогда не тихий generic. (Иначе перенос старой хрупкости под новое имя.)
3. **Gate стадийно: measure → warn → enforce.** Метрики считаются с самого первого слайса в report-only; UP #30 фиксируется как baseline; gate (слайс 8) = «уже посчитанные метрики == 0».
4. **Политика в коде (policy registry), не в промпте.** LLM адаптирует формулировки под тему, но не может заменить работающий прототип схемой.
5. **Терминируемость.** Любой coverage-loop имеет bounded-retry + cost-cap; недостижимое покрытие → draft + флаг, не бесконечный цикл.
6. **Консервативные блокировки.** Дефолт вопроса = блокирующий; понижение — только явным правилом. Ложно заблокировать publish безопаснее, чем ложно опубликовать сырое.

## Целевая доменная модель (добавления в `curriculum/domain.py`)

Все — frozen dataclasses рядом с существующими `PlanNode`/`SkillOccurrence`/`ProjectBlueprint`/`PlanQualityMetrics`.

```
ProjectContract
  title: str
  project_type: Literal["lab","project","capstone"]
  policy_area: str                       # ключ в artifact policy registry
  primary_skills: tuple[str,...]
  reinforcement_skills: tuple[str,...]
  artifact_contract: ArtifactContract
  template_binding: TemplateBinding | None
  workload: WorkloadContract
  quality_status: Literal["draft","ready_with_questions","publishable"]

ArtifactContract
  artifact_type: str
  deliverables: tuple[str,...]
  evidence_requirements: tuple[str,...]
  acceptance_criteria: tuple[AcceptanceCriterion,...]
  execution_environment: str
  publication_constraints: tuple[str,...]

AcceptanceCriterion
  subject: str
  check: str
  expected_result: str
  evidence_type: str
  verification_mode: Literal["automatic","manual"]
  blocking: bool

WorkloadContract
  total_hours: int
  hours_per_week: int
  duration_weeks: float
  duration_months: float
  study_days_per_week: int | None        # только если задано в брифе

TemplateBinding
  template_code: str
  template_version: str
  source: Literal["brief","global","policy"]
  repeatable: bool
```

---

## Слайсы (dependency-order; каждый — зелёный, draft всегда доступен)

### Слайс 0 — закоммитить проверенные runtime-фиксы
Рабочее дерево уже содержит 2 реальных фикса + поддержку + тесты (8 файлов): schema-нормализация внешней границы поиска ([brief_evidence.py:27](../src/content_factory/catalog/pipeline/brief_evidence.py#L27)), authoritative DAG snapshot вместо бесконечного LLM-rebuild ([stage_catalog_to_dag.py](../src/content_factory/catalog/pipeline/stage_catalog_to_dag.py), [intake_dag.py](../src/content_factory/catalog/viewer/intake_dag.py)), + `test_brief_evidence.py`, `test_intake_reviews.py`, `test_regression_pipeline.py`.
- **Действие:** отдельный focused-коммит ДО эпика (не смешивать с рефактором). ruff/mypy/1220 passed/prod-check clean уже подтверждены.
- **Verify:** `pytest tests/catalog`, gate-статус.

### Слайс 1 — контракт-скелет + адаптер + baseline-метрики (report-only)
Фундамент всего эпика. Пока НИЧЕГО не блокирует.
- **Добавить** типы из «Целевой модели» в [domain.py](../src/content_factory/catalog/pipeline/curriculum/domain.py). Пока опциональное поле `contract: ProjectContract | None` на `ProjectBlueprint` (не замена строк).
- **Адаптер** `ProjectBlueprint ↔ ProjectContract`: строить контракт из существующих строковых полей (best-effort), рендерить строки из контракта. Codec-паттерн — как [paused_generation_codec.py](../src/content_factory/api/db/paused_generation_codec.py).
- **Метрики (вычисляются, сохраняются в quality-блок УП, НЕ блокируют):** `title_violation_count`, `generic_artifact_count`, `single_skill_project_pct`, `testable_criteria_coverage`, `template_binding_coverage`, `artifact_policy_coverage`, `blocking_questions`. Расширяет существующий `_quality_metrics` ([stage_dag_to_up.py:654](../src/content_factory/catalog/pipeline/stage_dag_to_up.py#L654)) и `PlanQualityMetrics`.
- **Baseline:** зафиксировать снапшот метрик УП #30 в golden-фикстуру `tests/curriculum/golden/up30_baseline.json`. Каждый следующий слайс сравнивается с ней.
- **Verify:** метрики появляются в UP-detail (report-only), регрессионный пайплайн зелёный, baseline записан.

### Слайс 2 — workload-контракт (быстрый независимый win)
Не зависит от контрактов; поднят из §7 вверх.
- Ввести `WorkloadContract`; каноника: `total_hours=478 → hours_per_week=20 → duration_weeks=23.9 → months≈5.5`.
- Убрать вводящие в заблуждение «дни» из UI/deprecated-API: источник — `config.UP_HOURS_PER_DAY=2.94` ([config.py:138](../src/content_factory/catalog/pipeline/config.py#L138)) в `_fill_effort_columns` ([stage_dag_to_up.py:527](../src/content_factory/catalog/pipeline/stage_dag_to_up.py#L527)). `total_days` оставить в БД для совместимости, но убрать из UI и пометить deprecated в API. `study_days_per_week` показывать только если есть в брифе.
- **Verify:** UP-detail показывает часы/недели/месяцы; `total_days` не в UI; тесты на расчёт.

### Слайс 3 — явная классификация project_type + policy_area (анти-хрупкость, ФУНДАМЕНТ §4)
Критический слайс: без него §4 воспроизведёт fuzzy-хрупкость.
- Присваивать `project_type` (`lab|project|capstone`) и `policy_area` **детерминированно на этапе группировки навыков** в проект (из `coverage_area`/bloom/skill-метаданных), ДО выбора артефакта/шаблона. Точка: `_project_from_nodes`/группировка в [planner.py](../src/content_factory/catalog/pipeline/curriculum/planner.py).
- Классификация инспектируема (лог/поле). При неуверенности → `quality_status="draft"` + причина, НЕ тихий generic.
- Capstone определяется структурно (последний интегративный, покрытие широкое), а не по названию.
- **Verify:** каждый проект УП#30 получает area/type; доля «unclassified» видна в метриках; ноль тихих fallback.

### Слайс 4 — artifact policy registry + Capstone-контракт (§2)
- **Policy registry в коде** (не промпт), keyed by `policy_area` → минимальный проверяемый результат (deliverables/evidence/acceptance/execution_env). Матрица из разбора: создание продукта / AI-автоматизация / инженерная дисциплина / эксплуатация / маркетинг / монетизация / AI quality-safety / capstone.
- `planner._artifact_for` ([planner.py:316](../src/content_factory/catalog/pipeline/curriculum/planner.py#L316)) и `_project_assessment_criteria` ([stage_dag_to_up.py:290](../src/content_factory/catalog/pipeline/stage_dag_to_up.py#L290)) эмитят `ArtifactContract`/`AcceptanceCriterion` из registry по `policy_area`. Generic-строка разрешена ТОЛЬКО в draft.
- Capstone — отдельный production-контракт (MVP, release, demo, метрики запуска, evidence обратной связи).
- `generic_artifact_count` и `artifact_policy_coverage` теперь считаются против registry.
- **Verify:** `generic_artifact_count` УП#30 падает; каждый publishable-проект имеет policy-artifact; LLM формулирует внутри контракта.

### Слайс 5 — ProjectTitlePolicy (§3)
- Детерминированный валидатор + ограничение генератора: 3–8 содержательных слов, ≤72 симв., название этапа НИКОГДА не как название проекта, контекст/навыки → в описание, запрет «часть N»/длинных перечислений/повтора блока, запрет обрезки посередине, уникальность смысловым уточнением.
- Заменить наследование заголовка этапа: `_project_title_for` = `block_key + suffix` ([planner.py:326](../src/content_factory/catalog/pipeline/curriculum/planner.py#L326)); учесть разбиение на чанки ([planner.py:500](../src/content_factory/catalog/pipeline/curriculum/planner.py#L500)).
- `title_violation_count` считается; нарушение блокирует publish, не draft.
- **Verify:** УП#30 rebuild → 0 длинных названий (примеры: «Прототип продукта с AI», «CI и релизный контур»).

### Слайс 6 — durable template binding + coverage loop (§4)
- **Split** «принять для текущего брифа» vs «опубликовать в глобальный справочник»: сейчас принятый шаблон сразу публикуется глобально ([artifact_templates.py:503](../src/content_factory/catalog/pipeline/artifact_templates.py#L503)), все активные грузятся глобально ([intake_dag.py:452](../src/content_factory/catalog/viewer/intake_dag.py#L452)).
- Точная связь `brief → project → template` + snapshot шаблона и версии внутри версии УП (`TemplateBinding`).
- Выбор шаблона **после группировки навыков в проект**, а не до (сейчас per-skill лексика — [planner.py:206](../src/content_factory/catalog/pipeline/curriculum/planner.py#L206)). Шаблоны текущего брифа приоритетнее глобальных.
- Заменить фиксированный `max_proposals=10` на **bounded coverage loop**: генерировать, пока не покрыты нужные типы проектов ИЛИ N итераций (cost-cap) → иначе draft + флаг неполного покрытия.
- Повторное применение шаблона — только если `repeatable`. Переименовать метрику в `template_bound_project_count`.
- **Verify:** `template_binding_coverage=100%` для publishable; поздние области не получают generic fallback; 10 шаблонов / 20 проектов — норм, если все 20 привязаны.

### Слайс 7 — BriefQuestion entity + readiness state machine (§6)
- Сущность `BriefQuestion`: категория, источник, статус, ответ, блокирующий признак, влияние на этапы. Заменить «любая строка с ?» → question ([journey.py:371](../src/content_factory/catalog/pipeline/curriculum/journey.py#L371)).
- Таксономия блокировки (консервативно, дефолт=блокирующий): аудитория, формат практики, метрика demo-day, выбор целевой роли — блокируют. Редакционные пожелания — неблокирующие явным правилом.
- `readiness_state` ([journey.py:101](../src/content_factory/catalog/pipeline/curriculum/journey.py#L101)): draft строить можно всегда; freeze/publication только при `blocking_questions == 0`. Убрать безусловный `ready_with_questions` как publishable.
- **Verify:** 8 вопросов УП#30 категоризированы; publish заблокирован пока blocking>0; draft доступен.

### Слайс 8 — semantic quality gate: enforce + waiver (§5)
Report-only метрики (слайс 1) → блокирующий publish-gate.
- Условия publishable: `title_violation_count==0`, `generic_artifact_count==0`, `artifact_policy_coverage==100%`, `template_binding_coverage==100%`, `testable_criteria_coverage==100%`, у каждого deliverable есть evidence, обязательные области брифа трассируются до проектов, Capstone выполняет production-контракт, `blocking_questions==0`, доля однонавыковых ≤25% (кроме явных lab).
- **Waiver:** любое исключение — только методический waiver с автором/причиной/версией УП. Иначе gate становится тупиком.
- Заменить смысл `enrichment_completeness_pct=100%` (сейчас = непустые строки — [stage_dag_to_up.py:654](../src/content_factory/catalog/pipeline/stage_dag_to_up.py#L654), [curriculum_ops.py:190](../src/content_factory/catalog/viewer/curriculum_ops.py#L190)).
- **Verify:** старый УП без контрактов не публикуется (ожидаемо); waiver-путь работает; draft не тронут.

### Слайс 9 — rebuild УП как новая версия + golden eval vs baseline
- Пересобрать тот же CSV в новую версию УП; прогнать golden eval против `up30_baseline.json` (слайс 1).
- **Целевой результат:** 0 длинных названий, 0 generic-артефактов, 100% template/policy binding, 100% проверяемых критериев, 0 блокирующих вопросов, корректно 478ч / 23.9нед / ~5.5мес.
- **Verify:** все метрики достигнуты; сравнение baseline→new в eval-отчёте; production_check clean.

---

## Метрики / golden eval (сквозные, вводятся в слайсе 1)

| Метрика | Слайс-владелец | Publishable-порог (слайс 8) |
|---|---|---|
| `title_violation_count` | 5 | 0 |
| `generic_artifact_count` | 4 | 0 |
| `artifact_policy_coverage` | 4 | 100% |
| `template_binding_coverage` | 6 | 100% |
| `testable_criteria_coverage` | 4 | 100% |
| `single_skill_project_pct` | 3 | ≤25% (кроме lab) |
| `blocking_questions` | 7 | 0 |
| workload корректность | 2 | 478ч/23.9нед/5.5мес |

## Риски

- **Классификация area/type** (слайс 3) — если станет fuzzy, весь эпик воспроизведёт старую проблему. Митигация: явная, ранняя, инспектируемая, fallback→draft.
- **Coverage loop** (слайс 6) — бесконечность/стоимость. Митигация: bounded-retry + cost-cap.
- **Gate day-one** — блокирует всё. Митигация: measure→warn→enforce, waiver.
- **Contract↔string drift** (слайс 1) — контракт как единственный source of truth, строки производные; миграция + codec + тесты round-trip.
- **Объём** — эпик на недели; строго по одному зелёному слайсу, draft всегда доступен.

## Порядок

0 (commit) → 1 (контракт+метрики) → 2 (workload) → 3 (классификация) → 4 (artifact registry) → 5 (title) → 6 (templates) → 7 (questions) → 8 (gate enforce) → 9 (rebuild+eval).
