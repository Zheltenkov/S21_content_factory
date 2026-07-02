# Content Generator

Content Generator — веб-приложение для генерации, проверки, точечной доработки и перевода учебных проектов Школы 21.

Система принимает паспорт учебной программы, сторителлинг и параметры проекта, собирает итоговый `README.md`, формирует `check-list.yml`, проверяет результат по рубрике и позволяет дорабатывать отдельные части без полной пересборки проекта.

## Что умеет

- Генерирует учебный проект по учебному плану и контексту программы.
- Поддерживает два workflow profile: обычный режим и методологический режим с контрольными точками.
- Формирует итоговый `README.md`, архив проекта и `check-list.yml` для p2p-проверки.
- Проверяет README по 39 критериям качества.
- Делает schema-first перегенерацию выбранных разделов: выбранные секции, инструкции, typed patch, deterministic apply, validation report.
- Переводит документы, README, видео и субтитры с сохранением структуры Markdown, таблиц, формул и диаграмм.
- Ведет статусы запусков, checkpoints, retry/resume и историю пользовательских запусков.

## Основные сценарии

### Генерация

1. Методолог загружает CSV с паспортом учебной программы.
2. Выбирает проект, тип сторителлинга и режим работы.
3. Запускает генерацию.
4. Получает итоговый README, практику, данные, метрики, отчет и архив.
5. При необходимости запускает точечную перегенерацию выбранных разделов.

### Методологический режим

Пайплайн останавливается на контрольных точках. Методолог видит результат этапа, пишет правки в чат ассистента, сравнивает изменения, принимает результат и продолжает генерацию.

### Проверка README

Пользователь загружает README, запускает проверку и получает таблицу критериев: пройдено, предупреждения, не пройдено. Непройденные критерии можно использовать как основу для запроса правок.

### Перевод

Переводчик работает с документами и видео. Для языков используется ожидаемая письменность:

- таджикский — кириллица;
- кыргызский — кириллица;
- узбекский — латиница.

## Архитектура

```text
static/                 веб-интерфейс
api/                    FastAPI, auth, endpoints, persistence services
content_gen/            доменная логика генерации, проверки, перевода и перегенерации
content_gen/config/     flow.yaml, node contracts, model registry
content_gen/evaluation/ eval-наборы и regression harness
tests/                  unit, integration, API и UI-contract тесты
docs/                   актуальная проектная документация
```

Ключевой принцип: deterministic orchestration и typed contracts остаются в коде, а LLM используется только там, где нужен содержательный текст или перевод.

## Быстрый старт

Требования:

- Python 3.10+;
- PostgreSQL для production;
- LLM provider key: OpenRouter по умолчанию; OpenAI, Azure OpenAI, DeepSeek или GigaChat можно включить явно;
- ffmpeg для видео-перевода.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
alembic upgrade head
python run.py
```

Локальный адрес по умолчанию: `http://127.0.0.1:8000`.

## Конфигурация

Основные переменные окружения описаны в `.env.example`.

Обязательные production-настройки:

- `DATABASE_URL`
- `JWT_SECRET_KEY`
- `ALLOWED_EMAIL_DOMAIN`
- один активный LLM provider key
- `CORS_ORIGINS`

Секреты не коммитятся. Файл `.env` должен оставаться локальным или задаваться на сервере через защищенный secret store.

## Проверки

Базовый запуск тестов:

```powershell
python -m pytest
```

Частые точечные проверки:

```powershell
python -m pytest tests/utils/test_static_ui_contracts.py -q
python -m pytest tests/content_gen/test_regeneration_pipeline.py -q
python -m pytest tests/api/routers/test_readme_translate_documents.py -q
```

## Документация

- [Индекс документации](docs/README.md)
- [Архитектура](docs/ARCHITECTURE.md)
- [API](docs/API.md)
- [Карта flow](docs/flow_map.md)
- [Критерии README](docs/CRITERIA.md)
- [Модуль перевода](docs/TRANSLATION_MODULE.md)
- [Деплой](docs/DEPLOYMENT.md)
- [Тестирование](docs/TESTING.md)
- [Инструкция для методолога](docs/ИНСТРУКЦИЯ_ПО_ИСПОЛЬЗОВАНИЮ.md)

## Production notes

- В production таблицы создаются только миграциями Alembic.
- Старые `running/resuming` запуски при старте переводятся в восстановимое состояние, чтобы они не висели как активные задачи.
- Доступ к результатам и скачиваниям должен проходить через auth и ownership checks.
- Для видео действует лимит `MAX_VIDEO_SIZE_BYTES`; текущее значение по умолчанию в `.env.example` — 500 MB.
