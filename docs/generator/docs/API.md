# API

Базовый URL локально: `http://127.0.0.1:8000/api/v1`.

Большинство endpoints требуют авторизацию через JWT:

```http
Authorization: Bearer <token>
```

Исключения: healthcheck, статические страницы и auth endpoints.

## Auth

| Метод | Endpoint | Назначение |
|---|---|---|
| POST | `/auth/register` | Создать пользователя. Домен email ограничивается `ALLOWED_EMAIL_DOMAIN`. |
| POST | `/auth/login` | Войти и получить access token. |
| POST | `/auth/logout` | Завершить текущую сессию. |
| GET | `/auth/auth/me` | Проверить текущего пользователя и валидность сессии. |
| GET | `/auth/sessions` | Список активных сессий пользователя. |
| POST | `/auth/forgot-password` | Запрос восстановления пароля. |
| POST | `/auth/reset-password` | Сброс пароля по токену восстановления. |

Сообщение для незарегистрированного пользователя должно быть пользовательским, без `[object Object]`: “Вы не зарегистрированы. Используйте домен Школы 21.”

## Генерация

| Метод | Endpoint | Назначение |
|---|---|---|
| GET | `/dashboard/recent` | Последние запуски пользователя и активные задачи. |
| POST | `/curriculum/upload` | Загрузить CSV учебного плана. |
| POST | `/curriculum/build-context` | Собрать контекст проекта из curriculum данных. |
| POST | `/generate` | Запустить генерацию проекта. |
| GET | `/generate/status/{request_id}` | Получить статус, прогресс, checkpoint или результат. |
| POST | `/generate/cancel/{request_id}` | Остановить запуск. |
| POST | `/generate/workflow/{request_id}/command` | Resume/retry/cancel workflow command. |
| GET | `/download/{request_id}` | Скачать архив результата. |
| GET | `/metrics/{request_id}` | Получить статистику результата. |
| GET | `/rubric/{request_id}` | Получить rubric report результата. |

`POST /generate` принимает multipart form-data. Основной контракт передается в `seed_data` как JSON. Файлы используются как дополнительный контекст, но production-контекст проекта строится из curriculum payload.

Минимальные поля `seed_data`:

```json
{
  "project_type": "group",
  "direction": "PjM",
  "thematic_block": "Блок 2. Подготовка проекта",
  "project_description": "Описание проекта",
  "learning_outcomes": ["..."],
  "skills": ["..."],
  "storytelling": "Сценарий или SJM-контекст",
  "storytelling_type": "sjm_practice"
}
```

Русский язык является основным языком генерации. Перевод выполняется отдельным post-processing этапом или через модуль перевода.

## Методологический режим

| Метод | Endpoint | Назначение |
|---|---|---|
| GET | `/generate/review/{request_id}` | Получить текущую контрольную точку. |
| POST | `/generate/review/{request_id}/approve` | Принять этап и продолжить генерацию. |
| POST | `/generate/review/{request_id}/reject` | Отклонить этап. |
| POST | `/generate/review/{request_id}/request-changes` | Отправить правку методолога. |
| POST | `/generate/review/{request_id}/preview-changes` | Предпросмотр scoped revision. |
| POST | `/generate/review/{request_id}/approve-diff` | Применить подготовленный diff. |
| POST | `/generate/review/{request_id}/assistant-command` | Команда из чата методолога. |

Контрольные точки сохраняются в БД. После рестарта приложение должно показывать восстановимое состояние, а не бесконечный `running`.

## Перегенерация

| Метод | Endpoint | Назначение |
|---|---|---|
| POST | `/regenerate` | Точечная перегенерация выбранных частей README. |

Перегенерация работает schema-first:

1. UI передает выбранные секции и инструкции.
2. Backend строит typed patch.
3. Patch применяется детерминированно.
4. README повторно валидируется.
5. UI показывает validation report и diff.

Правка одной секции не должна бесконтрольно менять соседние секции. Исключение — структурные зависимости: содержание, номера глав, ссылки и критерии.

## Проверка README

| Метод | Endpoint | Назначение |
|---|---|---|
| POST | `/readme/check` | Проверить README по рубрике. |
| POST | `/readme/improve/extract` | Подготовить данные для улучшения README. |
| POST | `/readme/improve/generate` | Запустить улучшение README. |
| GET | `/readme/improve/status/{generation_request_id}` | Статус улучшения. |
| GET | `/readme/improve/diff/{request_id}` | Diff улучшенной версии. |
| GET | `/readme/improve/download/{generation_request_id}` | Скачать улучшенный README. |

## Перевод

| Метод | Endpoint | Назначение |
|---|---|---|
| POST | `/translate/readme` | Перевести Markdown/README. |
| POST | `/translate/document` | Перевести загруженный документ. |
| POST | `/translate/video` | Перевести видео и субтитры. |
| GET | `/translate/status/{request_id}` | Статус перевода. |
| GET | `/translate/download/{request_id}` | Скачать Markdown, видео, VTT, SRT, ASS или transcript. |
| GET | `/translate/subtitles/{request_id}` | Получить субтитры. |

Лимит видео задается `MAX_VIDEO_SIZE_BYTES`. В актуальном `.env.example` значение — 500 MB.

## Health

| Метод | Endpoint | Назначение |
|---|---|---|
| GET | `/health` | Проверка доступности приложения. |

## Ошибки

API должен возвращать человекочитаемое сообщение в `detail` или `message`. UI не должен показывать пользователю raw exception, `[object Object]` или технический stack trace.
