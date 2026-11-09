"""A made-up project in the layout of a school repository, for the tests of the benchmark.

Not a project of the school: the etalons are the school's own and stay on the methodologists' disks. The shape is
theirs — the headings, the table a task opens with, the check-list — and every oddity met in the fifteen SQL etalons
is here once: a heading with a space before it, a title given only in the table, a «#» inside a code fence, a
subsection inside a task, a question named without a dash, a bonus part.
"""

from pathlib import Path

README = """## _Каталог библиотеки_

В этом проекте ты научишься описывать фонд библиотеки запросами.

💡 [Нажми сюда](https://example.org/poll), **чтобы поделиться обратной связью**.

## Содержание
- [Chapter I](#chapter-i)
- [Задание 00](#задание-00)

## Как учиться в «Школе 21»

- Ищи ответы сам и спрашивай соседей.

## Chapter I
## Введение

![shelf](misc/images/shelf.png)

Библиотека переезжает, и каталог нужно проверить до переезда.

## Chapter II
## Рекомендации к выполнению этого проекта

- Работай в своей базе данных.

#### Таблица book

- поле id — первичный ключ

## Chapter III <div id="chapter-iii"></div>
## Задание 00

| Задание 00: Читатели города | |
|---|---|
| Директория для загрузки решений | ex00 |
| Файлы для загрузки | `ex00.sql`<br><br>`ex00.txt` |
| **Разрешено** | |
| Язык | ANSI SQL |
| **Запрещено** | |
| Конструкции | `JOIN` |

Напиши запрос, который вернёт имена читателей из Казани.

```sql
# это строка кода, а не заголовок
## Задание 77 — не задание
SELECT name FROM reader;
```

Результат отсортируй по имени.

 ## Задание 01 — Книги на руках

| <!-- --> | <!-- --> |
|:--- |:--- |
| Директория в репозитории | ex01 |
| Ожидаемые файлы | `ex01.sql` |

Найди книги, которые не вернули вовремя.

### Подсказка о датах

Срок возврата — четырнадцать дней.

## Дополнительно. Задание 02 — Отчёт для директора

Собери отчёт *report.md* о самых читаемых книгах.

## Chapter IV

Спасибо за работу.
"""

CHECKLIST = """introduction: >
  - Проверка — часть обучения.
guidelines: >-
  - Оцениваются только файлы из папки src.
quick_actions:
  - Нет ошибок
language: ru
name: Library
comment: ""
sections:
  - kind: "1"
    name: Main part
    description: ""
    questions:
      - name: Задание 00
        description: >
          Проверь наличие файла ex00.sql

          - Скрипт выглядит так, как показано ниже.

                SELECT name FROM reader WHERE city = 'Kazan';

        rating: "5"
      - name: Задание 01 Книги на руках
        description: |
          - Попроси рассказать, как посчитан срок возврата.
          - В результате нет книг, возвращённых вовремя.
        rating: "5"
  - kind: "2"
    name: Bonus part
    description: ""
    questions:
      - name: Дополнительно. Задание 02 — Отчёт для директора
        description: "В отчёте названы три книги."
        rating: "6"
"""


def repository(folder: Path, readme: str = README, checklist: str | None = CHECKLIST, files: dict[str, str | bytes] | None = None,
               names: tuple[str, str] = ("README_RUS.md", "check-list_RUS.yml")) -> Path:
    """Write the project into the folder as a repository and return the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / names[0]).write_text(readme, encoding="utf-8")
    if checklist is not None:
        (folder / names[1]).write_text(checklist, encoding="utf-8")
    for path, content in (files or {}).items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return folder
