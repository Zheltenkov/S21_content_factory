# Модуль перевода

Модуль перевода работает с документами, README, Markdown и видео. Цель — сохранить структуру учебного материала, а не просто перевести plain text.

## Пользовательские сценарии

### Документ

1. Пользователь открывает вкладку “Перевод”.
2. Загружает `.txt`, `.md`, `.html`, `.docx` или `.pdf`.
3. Выбирает режим и целевой язык.
4. Запускает перевод.
5. Получает side-by-side сравнение и скачивает результат.

### Видео

1. Пользователь переключается на “Видео”.
2. Загружает видео до лимита `MAX_VIDEO_SIZE_BYTES`.
3. Выбирает целевой язык.
4. Система строит transcript, переводит сегменты и формирует субтитры.
5. После завершения во вкладке видео доступны скачивания: MP4, VTT, SRT, ASS, transcript JSON.

## Языки и письменность

| Код | Язык | Письменность |
|---|---|---|
| `en` | английский | латиница |
| `tg` | таджикский | кириллица |
| `uz` | узбекский | латиница |
| `kg` | кыргызский | кириллица |

Для `uz`, `kg`, `tg` важен post-check письменности: отдельные буквы или слова не должны проскакивать в неправильном алфавите.

## Что сохраняется при переводе

- Markdown-заголовки и списки.
- Таблицы.
- Формулы.
- Кодовые блоки.
- Mermaid-диаграммы.
- Пути к файлам и технические маркеры.

Если блок нельзя безопасно перевести, он должен быть сохранен как protected block и отображен без повреждения.

## API

| Метод | Endpoint | Назначение |
|---|---|---|
| POST | `/api/v1/translate/readme` | Перевод Markdown/README. |
| POST | `/api/v1/translate/document` | Перевод загруженного документа. |
| POST | `/api/v1/translate/video` | Видео, transcript и субтитры. |
| GET | `/api/v1/translate/status/{request_id}` | Статус и прогресс. |
| GET | `/api/v1/translate/download/{request_id}` | Скачать результат. |

## Data flow

```text
input file
-> extract text / transcript
-> normalize markdown
-> protect code, formulas, tables, Mermaid
-> chunk by sections
-> translate chunks
-> validate structure and language script
-> repair unsafe chunks
-> render preview and downloadable artifacts
```

## Видео

Видео pipeline:

```text
video -> audio extraction -> ASR -> segment translation -> VTT/SRT/ASS -> optional burn-in MP4
```

Ограничения:

- текущий лимит по умолчанию — 500 MB;
- параллельность регулируется `VIDEO_MAX_CONCURRENT_JOBS`;
- для MP4 нужен ffmpeg;
- если burn-in не удался, субтитры всё равно должны быть доступны.

## Failure modes

| Риск | Поведение |
|---|---|
| Невалидный LLM token | UI показывает понятную ошибку, job завершается failed. |
| Документ долго обрабатывается | UI показывает progress и статус ожидания. |
| Таблица или формула сломалась | Validation/repair или protected fallback. |
| Неверная письменность | Script check и repair-pass. |
| Видео слишком большое | Пользователь получает сообщение о лимите. |
| ffmpeg недоступен | Субтитры сохраняются, MP4 может быть недоступен. |
