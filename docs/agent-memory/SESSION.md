# Состояние сессии

Обновлён: 2026-10-26 · Агент: claude-opus-5 · Ветка: auditor-branch-dashboard · Рабочее дерево: грязно (чужая работа по РПД)

## Задача
Модуль перевода документов: починить PDF, добавить PPTX/XLSX/YAML/TXT на вход-выход и
принимать архив проекта целиком, переводя внутри документы, конфиги, комментарии в коде и
примеры данных. Замысел — `docs/superpowers/specs/2026-10-26-archive-translation-design.md`,
разбор решений — [facts/translation-handler-registry.md](facts/translation-handler-registry.md).

Договорённость пользователя: **везти одним релизом в конце этапа**, ничего по частям.

## Сделано
Все пять частей замысла закрыты, 1416 тестов зелёные, ruff и mypy чисты, ветка отправлена
в `origin` (`04b3973`). На боевой сервер **не выложено**.

- **Ядро.** `TextUnit` + `FormatHandler`, движок перевода, реестр обработчиков. `apply`
  получает исходные байты, поэтому вёрстка сохраняется по построению.
- **Форматы.** yaml, xlsx, pptx (без `python-pptx`), pdf — PDF снова возвращается PDF с
  вёрсткой, ради чего всё и затевалось. Плюс csv/tsv и html.
- **Код.** Шесть ручных сканеров заменены грамматиками tree-sitter: bash, js, ts, c, cpp,
  go, java, sql; Python остался на `tokenize` + `ast`. Разбор безопасности нашёл в ручных
  сканерах шесть расхождений подряд — грамматика закрыла весь класс.
- **Архив.** `POST /translate/archive/inspect` (опись и смета без обращения к модели) и
  `POST /translate/archive/{plan_id}/start`. Результат — тот же архив с теми же именами
  плюс `ПЕРЕВОД-ОТЧЁТ.md`.
- **Страница.** Третий источник «▣ Архив» рядом с документом и видео, таблица с галочками,
  смета пересчитывается по ним. Вся логика в `static/js/modules/translationArchive.js`.
  Проверено в браузере на описи настоящего курса.
- Удалено 289 строк мёртвой DOCX-механики из `document_translation.py` (674 → 385 строк).

## Следующий шаг
**Релиз на бой.** Нужно в таком порядке:
1. Снимок `cf-app` через `docker commit` — [facts/cf-app-writable-layer.md](facts/cf-app-writable-layer.md).
2. Поставить в контейнер `tree-sitter` и `tree-sitter-language-pack` (как ffmpeg), иначе
   перевод комментариев в коде не заработает.
3. Выложить код (`docker cp` + перезапуск), сверить sha256, проверить 113 аудитов на месте.
4. Прогон живого перевода архива курса, чтобы увидеть качество и цену по журналу вызовов.

## Открытые вопросы
- В курсе лежат парой `README.md` (английский) и `README_RUS.md` (русский). Опись берёт
  русский, и перевод по принятому правилу ляжет обратно в `README_RUS.md`, а методист,
  вероятно, ждёт его в `README.md`. → ответ нужен от пользователя.
- Переход на gpt-6-sol втрое дороже по прайсу (2.0/10.0 против 0.75/4.5 USD за 1M) →
  от пользователя.
- Заводить ли `project_generator_lab/` под git целиком вместе с `data/` → от пользователя.
- Доступа в Jira нет: коннектор требует OAuth через настройки claude.ai → от пользователя.
- Проверить, держит ли gpt-6-sol строгую `json_schema` через Polza, и при успехе добавить
  его в allowlist `src/content_factory/platform/llm/structured_runner.py:146`.

## Не трогать
- `cf-app` пересоздавать только через снимок работающего контейнера —
  [facts/cf-app-writable-layer.md](facts/cf-app-writable-layer.md). Образы
  `deploy-app:running-20261007` и `deploy-app:prebuilt-backup-20261007` не удалять: это откат.
- Работа по РПД в дереве (`src/content_factory/rpd/*`, незакоммиченные `assessment.py`,
  `competency_agents.py`, `curriculum.py`, правки в `docs/rpd-generator.md`) — чужой сессии.
- `tests/api/routers/test_curriculum.py::test_build_context_rejects_ad_hoc_without_source_plan_id`
  падает — та же незакоммиченная работа по РПД.
- `tests/content_gen/test_node_services.py::test_practice_service_blocks_critical_critic_issue`
  падает и на HEAD: текст сообщения критика разошёлся с ожиданием теста.
- Логика страниц живёт в `/static/js/`, не во встроенном `<script>` —
  [facts/corporate-tls-proxy.md](facts/corporate-tls-proxy.md).
