"""Reading a project from its repository: README into tasks, check-list into questions, the two against each other."""

import io
import os
import zipfile

import pytest
from project_generator_lab.benchmark import repository
from project_generator_lab.benchmark.repository import Question, Section, parse_checklist, parse_readme
from project_generator_lab.tests.benchmark.project import CHECKLIST, README
from project_generator_lab.tests.benchmark.project import repository as written
from project_generator_lab.tests.benchmark.worded import kept


def test_a_readme_of_the_school_is_read_into_its_title_sections_and_tasks():
    readme = parse_readme(README)
    assert readme.title == "Каталог библиотеки"  # the emphasis marks of the heading are not the title
    assert readme.annotation.splitlines()[0] == "В этом проекте ты научишься описывать фонд библиотеки запросами."
    assert readme.images == 1
    # Everything that is not a task, in the order of the file; the lower headings of a section are its own text.
    assert [(s.role, s.title) for s in readme.sections] == [
        ("contents", "Содержание"), ("school", "Как учиться в «Школе 21»"), ("chapter", "Chapter I"), ("introduction", "Введение"),
        ("chapter", "Chapter II"), ("rules", "Рекомендации к выполнению этого проекта"), ("chapter", "Chapter III"), ("chapter", "Chapter IV")]
    assert readme.sections[5].text == "- Работай в своей базе данных.\n\n#### Таблица book\n\n- поле id — первичный ключ"
    assert readme.sections[-1] == Section("Chapter IV", "chapter", "Спасибо за работу.")  # a heading of the task's level ends the last task
    assert [(t.label, t.number, t.title, t.bonus) for t in readme.tasks] == [
        ("Задание 00", 0, "Читатели города", False),  # the title stands only in the table
        ("Задание 01", 1, "Книги на руках", False),  # the heading has a space before it
        ("Дополнительно. Задание 02", 2, "Отчёт для директора", True)]


def test_the_table_a_task_opens_with_is_read_by_its_rows():
    first, second, bonus = parse_readme(README).tasks
    assert first.submit == ["Директория для загрузки решений: ex00", "Файлы для загрузки: `ex00.sql`; `ex00.txt`"]  # «<br>» between two files
    assert (first.allowed, first.forbidden) == (["Язык: ANSI SQL"], ["Конструкции: `JOIN`"])
    # The lines of a code fence are the task's text, whatever they begin with; so is a heading under the task's own.
    assert first.text.splitlines()[2:6] == ["```sql", "# это строка кода, а не заголовок", "## Задание 77 — не задание", "SELECT name FROM reader;"]
    assert first.text.endswith("Результат отсортируй по имени.")
    assert second.submit == ["Директория в репозитории: ex01", "Ожидаемые файлы: `ex01.sql`"]  # the header row is two comments
    assert second.text == "Найди книги, которые не вернули вовремя.\n\n### Подсказка о датах\n\nСрок возврата — четырнадцать дней."
    assert (bonus.submit, bonus.allowed, bonus.forbidden, bonus.text) == ([], [], [], "Собери отчёт *report.md* о самых читаемых книгах.")
    assert (first.table, second.table, bonus.table) == (True, True, False)
    # The row above the line of dashes is the table's header: a title when it names the task, never a row of what to
    # hand in. A row that names no value and opens no group is kept as it stands, under the group it is in; a bold
    # row that is neither «Разрешено» nor «Запрещено» opens rows of its own, not more of what is forbidden.
    odd = parse_readme("# П\n\n## Задание 1\n\n| Параметр | Значение |\n|---|---|\n| **Запрещено** | |\n| `INSERT INTO` | |\n| | значение без ключа |\n"
                       "| **Рекомендации** | |\n| Редактор | любой |\n\nТекст.\n").tasks[0]
    assert (odd.title, odd.submit, odd.forbidden, odd.other, odd.text) == ("", [], ["`INSERT INTO`", "значение без ключа"], ["Рекомендации", "Редактор: любой"], "Текст.")
    # A table with no line of dashes has no header: its first row is a row. A header alone is still a table —
    # one that says nothing of what to hand in.
    bare = parse_readme("# П\n\n## Задание 1\n\n| Директория | ex03 |\n| Файлы | `a.sql` |\n\nТекст.\n").tasks[0]
    assert (bare.table, bare.submit) == (True, ["Директория: ex03", "Файлы: `a.sql`"])
    empty = parse_readme("# П\n\n## Задание 1\n\n| Задание 1: Название | |\n|---|---|\n\nТекст.\n").tasks[0]
    assert (empty.table, empty.title, empty.submit, empty.allowed, empty.forbidden, empty.other) == (True, "Название", [], [], [], [])


@pytest.mark.parametrize("heading, label, number, title, bonus", [
    ("## Задание 05 — Аудит", "Задание 05", 5, "Аудит", False),
    ("### Задание 06 — Продукт из данных", "Задание 06", 6, "Продукт из данных", False),
    ("## Part 2. Изменение данных", "Part 2", 2, "Изменение данных", False),
    ("## Дополнительно. Part 4. Метаданные", "Дополнительно. Part 4", 4, "Метаданные", True),
    ("### Задание 3. Собери бэклог", "Задание 3", 3, "Собери бэклог", False),  # the generator's own heading …
    ("### Бонусное задание 1*: Сравни подходы", "Бонусное задание 1*", 1, "Сравни подходы", True),  # … and its bonus one
    ("## Exercise 00 — Simple", "Exercise 00", 0, "Simple", False),
    ("## Задание 7", "Задание 7", 7, "", False),
    ("## Задание 1.2 Б", "Задание 1.2", 1, "Б", False),  # the second task of the first group, not «Задание 1» named «2 Б»
    ("## Задание 2: F#", "Задание 2", 2, "F#", False),  # «#» close a heading only after a space
    ("## Задание 4 ##", "Задание 4", 4, "", False),
    ("## Задание 3\t", "Задание 3", 3, "", False),
    ("## Задание 7 (дополнительное). Один правильный вариант", "Задание 7", 7, "Один правильный вариант", True),  # as the runs of September write it
    ("## Задание 2 (с данными)", "Задание 2", 2, "(с данными)", False),  # brackets of another kind are words of the title
    # The word of a bonus task as a sentence of its own before the title — the runs of 07.09 and 14.09 write it so.
    ("## Задание 6. Бонусное. Дамп памяти", "Задание 6", 6, "Дамп памяти", True),
    ("## Задание 6 — Дополнительное: Дамп памяти", "Задание 6", 6, "Дамп памяти", True),
    ("## Задание 6. Бонус — Дамп памяти", "Задание 6", 6, "Дамп памяти", True),
    ("## Задание 6. Бонус", "Задание 6", 6, "", True),
    ("## Задание 6. bonus", "Задание 6", 6, "", True),
    ("## Задание 6. Бонусное задание. Дамп памяти", "Задание 6", 6, "Дамп памяти", True),
    # A title that only begins with such a word is a title: the word is not a sentence of its own there.
    ("## Задание 4. Дополнительные индексы", "Задание 4", 4, "Дополнительные индексы", False),
    ("## Задание 5. Бонусы за скорость", "Задание 5", 5, "Бонусы за скорость", False),
    ("## Задание 5. Бонус-трек", "Задание 5", 5, "Бонус-трек", False),
    ("## Задание 5. Бонусная программа магазина", "Задание 5", 5, "Бонусная программа магазина", False),
    # The tasks of a project written the way of the bassin, and of this generator's README in that style.
    ("## Quest 3. Recursive", "Quest 3", 3, "Recursive", False),
    ("### Bonus Quest 5. Func", "Bonus Quest 5", 5, "Func", True),
    ("## QUEST 07 — Замок", "QUEST 07", 7, "Замок", False),
    ("## Квест 2: Ветки", "Квест 2", 2, "Ветки", False),
])
def test_a_task_is_known_by_its_heading(heading, label, number, title, bonus):
    [task] = parse_readme(f"# Проект\n\nАннотация.\n\n{heading}\n\nСделай.\n").tasks
    assert (task.label, task.number, task.title, task.bonus, task.text) == (label, number, title, bonus, "Сделай.")


def test_a_heading_is_read_in_time_that_grows_with_its_length():
    # A heading with a long run of spaces took a minute to read (independent check): the reader of one project must
    # not stall the report of the rest. Linear, this is instant.
    readme = parse_readme("# a" + " " * 200_000 + "b\n\n## Задание 1" + " " * 200_000 + "\n\nТекст.\n")
    assert readme.title.startswith("a ") and [t.label for t in readme.tasks] == ["Задание 1"]


def test_what_is_not_a_task_is_not_made_one():
    # «Задания» is a word of a heading, not a task; four spaces make a line code, not a heading; text before the first
    # heading is kept under no title and the first heading is still the title.
    readme = parse_readme("Вступление без заголовка.\n\n# Проект\n\nАннотация.\n\n## Задания проекта\n\n    ## Задание 9\n\n## Заключение\n\nВсё.\n")
    assert readme.tasks == [] and (readme.title, readme.annotation) == ("Проект", "Аннотация.")
    # A number of more digits than a task has is not a task's number.
    assert parse_readme("# П\n\n## Задание 1234567 года\n\nТекст.\n").tasks == []
    assert readme.sections == [Section("", "other", "Вступление без заголовка."), Section("Задания проекта", "other", "    ## Задание 9"),
                               Section("Заключение", "conclusion", "Всё.")]
    assert parse_readme("") == repository.Readme("", "", [], [], 0, False)
    # A heading above the task's level ends the task; one below it does not.
    deep = parse_readme("# П\n\n### Задание 1. А\n\nТекст.\n\n#### Деталь\n\nЕщё.\n\n## Заключение\n\nВсё.\n")
    assert deep.tasks[0].text == "Текст.\n\n#### Деталь\n\nЕщё." and deep.sections[-1].title == "Заключение"
    # A line of a step that begins with «#» is indented with the step: it is the step's, as the generator writes it.
    step = parse_readme("# П\n\n### Задание 1. А\n\n1. Запусти:\n   # установить зависимости\n   pip install x\n  ## и это не заголовок\n")
    assert [t.label for t in step.tasks] == ["Задание 1"] and step.tasks[0].text.splitlines()[1:] == ["   # установить зависимости", "   pip install x", "  ## и это не заголовок"]


def test_a_block_of_code_is_closed_by_its_own_fence():
    # Four backticks hold three; a tilde fence holds backticks; neither holds a task.
    readme = parse_readme("# П\n\n## Задание 1\n\n````\n```\n## Задание 9\n```\n````\n\n~~~sql\n```\n## Задание 8\n~~~\n\n## Задание 2\n\nТекст.\n")
    assert [t.label for t in readme.tasks] == ["Задание 1", "Задание 2"] and not readme.unclosed
    # A fence with words after it does not close a block; a block never closed is said, not passed in silence.
    open_ = parse_readme("# П\n\n## Задание 1\n\n```sql\nSELECT 1;\n``` и дальше\n\n## Задание 2\n\nТекст.\n")
    assert [t.label for t in open_.tasks] == ["Задание 1"] and open_.unclosed
    # Code inside a line that happens to begin the line opens nothing.
    inline = parse_readme("# П\n\n## Задание 1\n\n```select 1;``` — проверь\n\n## Задание 2\n\nТекст.\n")
    assert [t.label for t in inline.tasks] == ["Задание 1", "Задание 2"] and not inline.unclosed


def test_the_sections_of_the_generators_own_readme_are_known():
    readme = parse_readme("# Проект\n\nАннотация.\n\n## Глава 1. Введение и инструкция\n\n### Введение\n\nЗачем.\n\n### Инструкция\n\n- Правило\n\n"
                          "## Глава 2. Теоретический блок\n\n### 2.1. Роли в команде\n\nНаправление.\n\n## Глава 3. Практический блок\n\n"
                          "### Задание 1. А\n\nТекст.\n\n## Заключение\n\nИтог.\n")
    assert [(s.role, s.title) for s in readme.sections] == [
        ("chapter", "Глава 1. Введение и инструкция"), ("introduction", "Введение"), ("rules", "Инструкция"), ("theory", "Глава 2. Теоретический блок"),
        ("theory", "2.1. Роли в команде"),  # a topic under the theory chapter is theory, whatever it is called
        ("chapter", "Глава 3. Практический блок"), ("conclusion", "Заключение")]
    # Rules that speak of theory are rules; what follows a task is not a topic of the theory above the task.
    odd = parse_readme("# П\n\n## Правила теоретической части\n\nТекст.\n\n## Теория\n\n### Задание 1. А\n\nТекст.\n\n### Данные проекта\n\nТекст.\n")
    assert [(s.role, s.title) for s in odd.sections] == [("rules", "Правила теоретической части"), ("theory", "Теория"), ("other", "Данные проекта")]


def test_the_blocks_of_the_generators_own_task_are_read_by_their_bold_lines():
    [task] = parse_readme("# П\n\n### Задание 1. А\n\n**Что нужно сделать**\n\nСитуация.\n\nЦель: уметь.\n\n1. Шаг\n\n```\n**не блок**\n```\n\n"
                          "**Формат сдачи**\n\nФайл src/a.md.\n\n**Жирное слово** в строке — не блок.\n").tasks
    assert task.blocks == {"Что нужно сделать": "Ситуация.\n\nЦель: уметь.\n\n1. Шаг\n\n```\n**не блок**\n```",
                           "Формат сдачи": "Файл src/a.md.\n\n**Жирное слово** в строке — не блок."}


def test_a_check_list_is_read_into_its_questions():
    checklist = parse_checklist(CHECKLIST)
    assert (checklist.introduction, checklist.guidelines) == ("- Проверка — часть обучения.", "- Оцениваются только файлы из папки src.")
    assert [(q.name, q.rating, q.bonus, q.code) for q in checklist.questions] == [
        ("Задание 00", "5", False, True),  # an indented block of code: the answer is shown to the peer
        ("Задание 01 Книги на руках", "5", False, False),  # a list is not code
        ("Дополнительно. Задание 02 — Отчёт для директора", "6", True, False)]
    assert Question("q", "Смотри:\n```sql\nSELECT 1;\n```", "5", False).code and not Question("q", "- пункт\n  продолжение пункта", "5", False).code
    with pytest.raises(ValueError, match=r"^чек-лист не читается как YAML \(ParserError\)$"):
        parse_checklist("sections: [\n  - name: 'x\n")
    for broken in ("- просто\n- список\n", "introduction: текст\n", "sections: не список\n", ""):
        with pytest.raises(ValueError, match="в чек-листе нет списка sections"):
            parse_checklist(broken)
    # What is not a question is passed over; a question without a name or a text is one with empty ones.
    loose = parse_checklist("sections:\n  - строка\n  - name: Main\n    questions:\n      - просто строка\n      - rating: 5\n  - name: Пусто\n"
                            "  - name: Не список\n    questions: {a: 1}\n")
    assert loose.questions == [Question("", "", "5", False)]
    # A bonus question is one of a bonus section or one whose own name says so.
    marked = parse_checklist("sections:\n  - name: Main\n    questions:\n      - name: Бонусное задание 1*\n      - name: Задание 7 (дополнительное). Вариант\n"
                             "  - name: Bonus part\n    questions:\n      - name: Part 2. B\n")
    assert [q.bonus for q in marked.questions] == [True, True, True]
    # A block of code is fenced or indented after an empty line; an item of a nested list and a wrapped line are not.
    assert not Question("q", "- пункт\n\n    - вложенный пункт\n    1. и ещё один", "5", False).code
    assert not Question("q", "Длинная строка,\n    перенесённая с отступом", "5", False).code
    assert Question("q", "Смотри:\n\n\tSELECT 1;", "5", False).code and Question("q", "    SELECT 1;", "5", False).code
    with pytest.raises(ValueError, match=r"^чек-лист не читается как YAML \(RecursionError\)$"):
        parse_checklist("a: " + "[" * 3000 + "]" * 3000)


def test_tasks_and_questions_are_paired_by_number_or_in_order():
    readme, checklist = parse_readme(README), parse_checklist(CHECKLIST)
    paired = repository.pair(readme, checklist)
    assert paired.by == "номеру" and (paired.tasks_without, paired.questions_without) == ([], [])
    assert {index: [q.name for q in found] for index, found in paired.questions.items()} == {
        0: ["Задание 00"], 1: ["Задание 01 Книги на руках"], 2: ["Дополнительно. Задание 02 — Отчёт для директора"]}
    # By number: two questions about one task, a task nobody asks about, a question about a task that is not there —
    # and a bonus task is not the main task of the same number.
    odd = checklist._replace(questions=[Question("Задание 00", "а", "5", False), Question("Задание 0 — ещё раз", "б", "5", False),
                                        Question("Задание 02 — основное", "в", "5", False), Question("Задание 09", "г", "5", False)])
    paired = repository.pair(readme, odd)
    assert {index: [q.text for q in found] for index, found in paired.questions.items()} == {0: ["а", "б"]}
    assert (paired.tasks_without, paired.questions_without) == (["Задание 01", "Дополнительно. Задание 02"], ["Задание 02 — основное", "Задание 09"])
    # One question that names no number is about no task: taken in order it would move every pair after it by one.
    mixed = checklist._replace(questions=[Question("Общие требования", "а", "5", False), Question("Задание 00", "б", "5", False),
                                          Question("Задание 01", "в", "5", False), Question("Задание 02", "г", "6", True)])
    paired = repository.pair(readme, mixed)
    assert paired.by == "номеру" and {index: [q.text for q in found] for index, found in paired.questions.items()} == {0: ["б"], 1: ["в"], 2: ["г"]}
    assert (paired.tasks_without, paired.questions_without) == ([], ["Общие требования"])
    # No name says a number: each part in order, one for one; what is left over is named.
    named = checklist._replace(questions=[Question("Creating a database", "а", "6", False), Question("Creating views", "б", "6", False),
                                          Question("Third", "в", "6", False), Question("Metadata", "г", "6", True), Question("More", "д", "6", True)])
    paired = repository.pair(readme, named)
    assert paired.by == "порядку" and {index: [q.text for q in found] for index, found in paired.questions.items()} == {0: ["а"], 1: ["б"], 2: ["г"]}
    assert (paired.tasks_without, paired.questions_without) == ([], ["Third", "More"])
    assert repository.pair(readme, None) == repository.Paired("порядку", {}, ["Задание 00", "Задание 01", "Дополнительно. Задание 02"], [])
    # «1.1» and «1.2» are two tasks, and a question is about the one it names; zeros before a number do not matter.
    grouped = parse_readme("# П\n\n## Задание 1.1 А\n\nТекст.\n\n## Задание 1.2 Б\n\nТекст.\n")
    paired = repository.pair(grouped, checklist._replace(questions=[Question("Задание 01.2", "б", "5", False), Question("Задание 1.1", "а", "5", False)]))
    assert [(t.ref, t.number) for t in grouped.tasks] == [("1.1", 1), ("1.2", 1)]
    assert {index: [q.text for q in found] for index, found in paired.questions.items()} == {0: ["а"], 1: ["б"]}
    # A question numbered deeper than the tasks is about the task its number begins with.
    deeper = repository.pair(readme, checklist._replace(questions=[Question("Задание 0.1 а", "а", "5", False), Question("Задание 0.2 б", "б", "5", False),
                                                                   Question("Задание 1.1", "в", "5", False), Question("Задание 02", "г", "6", True)]))
    assert {index: [q.text for q in found] for index, found in deeper.questions.items()} == {0: ["а", "б"], 1: ["в"], 2: ["г"]} and deeper.questions_without == []
    # One name that happens to hold a number is not a numbering: the questions are still taken in order.
    chance = repository.pair(readme, checklist._replace(questions=[Question("Part 1 of the review", "а", "5", False), Question("Second", "б", "5", False),
                                                                   Question("Report", "в", "6", True)]))
    assert chance.by == "порядку" and (chance.tasks_without, chance.questions_without) == ([], [])
    # Two tasks under one number cannot be told apart by it: in order, and never both questions to one task.
    twice = parse_readme("# П\n\n## Задание 1 А\n\nТекст.\n\n## Задание 1 Б\n\nТекст.\n")
    paired = repository.pair(twice, checklist._replace(questions=[Question("Задание 1 А", "а", "5", False), Question("Задание 1 Б", "б", "5", False)]))
    assert paired.by == "порядку" and {index: [q.text for q in found] for index, found in paired.questions.items()} == {0: ["а"], 1: ["б"]}
    # A bonus task and a question of the bonus section meet though the question's name does not say «bonus».
    extra = parse_readme("# П\n\n## Part 1. A\n\nТекст.\n\n## Дополнительно. Part 2. B\n\nТекст.\n")
    in_section = parse_checklist("sections:\n  - name: Main part\n    questions:\n      - name: Part 1. A\n  - name: Bonus part\n    questions:\n      - name: Part 2. B\n")
    assert repository.pair(extra, in_section).tasks_without == [] and repository.pair(extra, in_section).questions_without == []


def test_a_repository_is_read_from_its_folder(tmp_path, monkeypatch):
    folder = written(tmp_path / "LIB1", files={
        "README.md": "# English\n", "check-list.yml": "sections: []\n", "materials/model.sql": "CREATE TABLE book (id int);\n",
        "materials/guide.pdf": b"%PDF-1.4 \xff\xfe", "datasets/big.csv": "a,b\n" * 100, ".git/config": "[core]\n", "src/.gitkeep": "",
        "materials/README.md": "# Про материалы\n"})
    project = repository.read(folder)
    # The Russian pair is read when there is one; the other files of the repository are known by name and size.
    assert (project.name, project.readme_file, project.checklist_file, project.notes) == ("LIB1", "README_RUS.md", "check-list_RUS.yml", [])
    assert len(project.readme.tasks) == 3 and project.checklist is not None and len(project.checklist.questions) == 3
    assert [(f.path, f.size) for f in project.files] == [
        ("datasets/big.csv", 400), ("materials/guide.pdf", 11), ("materials/model.sql", 28), ("materials/README.md", 28)]
    # Text is read up to the limit, and only of a text format.
    monkeypatch.setattr(repository, "MAX_FILE", 399)
    assert {f.path: f.content for f in repository.read(folder).files} == {
        "datasets/big.csv": "", "materials/README.md": "# Про материалы\n", "materials/guide.pdf": "", "materials/model.sql": "CREATE TABLE book (id int);\n"}
    monkeypatch.setattr(repository, "MAX_FILES", 2)
    capped = repository.read(folder)
    assert [f.path for f in capped.files] == ["datasets/big.csv", "materials/guide.pdf"] and capped.notes == ["файлов 4, прочитаны первые 2"]


def test_what_a_repository_lacks_is_said(tmp_path, monkeypatch):
    # An archive wraps the repository in a folder of its name: the only subfolder with a README is the repository.
    written(tmp_path / "A" / "A", checklist=None, names=("README.md", "check-list.yml"))
    project = repository.read(tmp_path / "A")
    assert (project.name, project.readme_file, project.checklist_file, project.checklist, project.notes) == ("A", "README.md", "", None, ["чек-листа в репозитории нет"])
    # A Russian README beside an English check-list is read, and said.
    mixed = written(tmp_path / "B", names=("README_RUS.md", "check-list.yml"))
    assert repository.read(mixed).notes == ["README_RUS.md и check-list.yml на разных языках: русской пары в репозитории нет"]
    # A check-list that does not read is a note; the project is still read.
    broken = repository.read(written(tmp_path / "C", checklist="sections: [\n"))
    assert broken.checklist is None and broken.notes == ["check-list_RUS.yml: чек-лист не читается как YAML (ParserError)"]
    # The names are found whatever their case; a check-list too large to be one, or with no question in it, is said.
    cased = written(tmp_path / "G", names=("Readme_rus.MD", "Check-List_RUS.yml"), files={"readme.md": "# English\n"})
    assert (repository.read(cased).readme_file, repository.read(cased).checklist_file, repository.read(cased).notes) == ("Readme_rus.MD", "Check-List_RUS.yml", [])
    assert repository.read(written(tmp_path / "H", checklist="sections: [1, x]\n")).notes == ["в check-list_RUS.yml нет ни одного вопроса"]
    assert repository.read(written(tmp_path / "I", checklist="a: " + "[" * 3000 + "]" * 3000)).notes == ["check-list_RUS.yml: чек-лист не читается как YAML (RecursionError)"]
    (tmp_path / "J").mkdir()
    (tmp_path / "J" / "README.md").write_bytes("# Проект\n\n## Задание 1\n\nТекст.\n".encode("cp1251"))
    assert repository.read(tmp_path / "J").notes == ["README.md не в UTF-8: часть букв не прочитана", "чек-листа в репозитории нет"]
    (tmp_path / "J" / "check-list.yml").write_bytes(CHECKLIST.encode("cp1251"))
    assert repository.read(tmp_path / "J").notes[1] == "check-list.yml не в UTF-8: часть букв не прочитана"
    unclosed = written(tmp_path / "K", readme="# П\n\n## Задание 1\n\n```\nкод\n", checklist=None)
    assert repository.read(unclosed).notes == ["в README_RUS.md блок кода открыт и не закрыт: текст после него прочитан как код", "чек-листа в репозитории нет"]
    # No README — nothing to call a project: two subfolders with one each are two projects, not this one.
    written(tmp_path / "D" / "one")
    written(tmp_path / "D" / "two")
    (tmp_path / "E").mkdir()
    for name in ("D", "E", "нет такого"):
        with pytest.raises(ValueError, match=f"в каталоге «{name}» нет README_RUS.md или README.md"):
            repository.read(tmp_path / name)
    (tmp_path / "F").mkdir()
    (tmp_path / "F" / "README.md").write_text("  \n", encoding="utf-8")
    with pytest.raises(ValueError, match="README.md в «F» пустой или больше 2 МБ"):
        repository.read(tmp_path / "F")
    monkeypatch.setattr(repository, "MAX_TEXT", 3000)
    assert repository.read(written(tmp_path / "L", checklist=CHECKLIST + "# " + "я" * 3000 + "\n")).notes == ["check-list_RUS.yml больше 0 МБ, не прочитан"]
    monkeypatch.setattr(repository, "MAX_TEXT", 10)
    with pytest.raises(ValueError, match="README_RUS.md в «B» пустой или больше"):
        repository.read(mixed)


@pytest.mark.skipif(os.name == "nt", reason="making a link needs a privilege on Windows")
def test_a_link_is_not_a_file_of_the_project(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("не из проекта", encoding="utf-8")
    folder = written(tmp_path / "L", files={"materials/a.txt": "своё"})
    (folder / "materials" / "link.txt").symlink_to(secret)
    assert [f.path for f in repository.read(folder).files] == ["materials/a.txt"]


def test_what_the_generator_assembles_is_read_back_as_what_it_wrote(tmp_path):
    """The reader is the same for an etalon and for the generator's own result: a draft assembled into a repository
    and read from it gives its tasks back — titles, numbers, every block, a question each."""
    from project_generator_lab.benchmark import code_layer
    from project_generator_lab.content.render import bundle
    from project_generator_lab.tests.content.test_parts import complete_draft

    draft = complete_draft()
    draft.tasks[0].steps = ["Собери таблицу ролей\nи отметь, кто за что отвечает", "Сверь её с данными команды"]
    draft.tasks[0].constraints = ["Только таблица"]
    zipfile.ZipFile(io.BytesIO(bundle(draft))).extractall(tmp_path / "P")
    project = repository.read(tmp_path / "P")
    assert draft.summary and (project.readme.title, project.notes) == (draft.summary.title, [])
    assert [f.path for f in project.files] == ["materials/team.csv"]
    assert [(t.number, t.title) for t in project.readme.tasks] == [(t.number, t.title) for t in draft.tasks]
    paired = repository.pair(project.readme, project.checklist)
    assert paired.by == "номеру" and (paired.tasks_without, paired.questions_without) == ([], [])
    for read, wrote in zip(code_layer.as_tasks(project, paired), draft.tasks, strict=True):
        assert (read.goal, read.context, read.steps, read.expected, read.constraints, read.result, read.transition) == (
            wrote.goal, wrote.context, wrote.steps, wrote.expected, wrote.constraints, wrote.result, wrote.transition)
        assert read.peer_checks == ["\n".join(f"- {item}" for item in wrote.peer_checks)]


def test_the_edges_of_a_task_and_its_table():
    """What a mutation run showed no test held (05.10.2026): a task with nothing under it, a table that ends the
    file, tables of one and of three lines, an indented table, a cell that ends with a line break."""
    readme = parse_readme("# П\n\n## Задание 1\n## Задание 2\n\n\n## Задание 3\n\n  | Файлы | `a.sql`<br> |\n\n## Задание 4\n\n| Задание 4: Название | |\n|---|---|\n"
                          "| Файлы | `b.sql` |\n| **Советы** | |\n| Редактор | любой |\n| **Ещё** | |\n| Время | час |")
    one, two, three, four = readme.tasks
    assert (one.text, one.raw, one.table, two.text, two.table) == ("", "", False, "", False)
    assert (three.table, three.submit, three.text) == (True, ["Файлы: `a.sql`"], "")  # one line is a table; the «<br>» at the end is not a file
    assert (four.title, four.submit, four.other, four.text) == ("Название", ["Файлы: `b.sql`"], ["Советы", "Редактор: любой", "Ещё", "Время: час"], "")
    assert four.raw.splitlines()[0] == "| Задание 4: Название | |"
    # The table ends where the lines of bars end, with or without an empty line after it.
    close = parse_readme("# П\n\n## Задание 1\n| Файлы | `a.sql` |\nТекст сразу за таблицей.\n").tasks[0]
    assert (close.submit, close.text) == (["Файлы: `a.sql`"], "Текст сразу за таблицей.")


def test_the_roles_of_sections_do_not_spread_further_than_their_chapter():
    readme = parse_readme("# П\n\n## Глава 2. Теоретический блок\n\n### 2.1. Первая\n\nТекст.\n\n### 2.2. Вторая\n\nТекст.\n\n## Данные\n\nТекст.\n\n### Ещё данные\n\nТекст.\n")
    assert [(s.role, s.title) for s in readme.sections] == [("theory", "Глава 2. Теоретический блок"), ("theory", "2.1. Первая"), ("theory", "2.2. Вторая"),
                                                           ("other", "Данные"), ("other", "Ещё данные")]


def test_a_numbering_is_what_most_names_carry():
    readme = parse_readme("# П\n\n" + "".join(f"## Задание {n}\n\nТекст.\n\n" for n in range(1, 6)))
    names = ["Общее", "Задание 2", "Ещё", "Задание 4", "Итог"]  # two of five: not a numbering
    assert repository.pair(readme, repository.Checklist("", "", [Question(name, "т", "5", False) for name in names])).by == "порядку"
    names[0] = "Задание 1"  # three of five
    paired = repository.pair(readme, repository.Checklist("", "", [Question(name, "т", "5", False) for name in names]))
    assert paired.by == "номеру" and sorted(paired.questions) == [0, 1, 3] and paired.questions_without == ["Ещё", "Итог"]


def test_the_limits_are_met_exactly(tmp_path, monkeypatch):
    folder = written(tmp_path / "P", files={"materials/a.txt": "я" * 100, "materials/b.txt": "б"})
    monkeypatch.setattr(repository, "MAX_FILE", 200)  # a file of exactly the limit is read
    assert [bool(f.content) for f in repository.read(folder).files] == [True, True]
    monkeypatch.setattr(repository, "MAX_FILES", 2)  # as many files as the limit are all read, and nothing is said
    assert repository.read(folder).notes == []
    size = (folder / "check-list_RUS.yml").stat().st_size
    monkeypatch.setattr(repository, "MAX_TEXT", max(size, (folder / "README_RUS.md").stat().st_size))
    assert repository.read(folder).notes == [] and repository.read(folder).checklist is not None


def checklist(*names: tuple[str, bool]) -> repository.Checklist:
    return repository.Checklist("", "", [Question(name, "т", "5", bonus) for name, bonus in names])


def test_a_question_asked_about_several_tasks_is_a_question_of_each():
    readme = parse_readme("# П\n\n" + "".join(f"## Задание {n}. Имя\n\nТекст.\n\n" for n in range(1, 7)) + "## Задание 7. Бонусное. Имя\n\nТекст.\n")
    several = checklist(("Задания 1—2. Разведка и модули", False), ("Задания 3-4. Модуль и отбраковка", False), ("Задание 5. Отчёт", False),
                        ("Задание 6. Обе цифры", False), ("Приёмка на контрольном файле", False), ("Задание 7. Реставратор", True))
    paired = repository.pair(readme, several)
    assert paired.by == "номеру" and paired.tasks_without == [] and paired.questions_without == ["Приёмка на контрольном файле"]
    assert {place: [q.name for q in found] for place, found in paired.questions.items()} == {
        0: ["Задания 1—2. Разведка и модули"], 1: ["Задания 1—2. Разведка и модули"], 2: ["Задания 3-4. Модуль и отбраковка"],
        3: ["Задания 3-4. Модуль и отбраковка"], 4: ["Задание 5. Отчёт"], 5: ["Задание 6. Обе цифры"], 6: ["Задание 7. Реставратор"]}


@pytest.mark.parametrize("name, tasks", [
    ("Задания 2–4", [1, 2, 3]), ("Tasks 5 - 6: both", [4, 5]), ("ЧАСТИ 1—2.", [0, 1]), ("Упражнения 1—2.5 и далее", None),  # «2.5» is not the end of a row
    ("Задания 1 и 2", [0, 1]), ("Задания 1, 3 и 5. Через одно", [0, 2, 4]), ("Tasks 1 and 2", [0, 1]), ("Задания 1 & 2", [0, 1]), ("Задания 1—2, 5", [0, 1, 4]),
    ("Задания 1,2,3", [0, 1, 2]), ("Задания 2 и 2 и 3", [1, 2]),  # a task named twice is asked about once
    # A row that names a task the README does not have is about no task: it is not quietly taken for the rest.
    ("Задания 5—9. Часть из них есть", None), ("Задания 8—9. Таких нет", None), ("Задания 1 и 7", None),
    ("Задания 4—2. Наоборот", None), ("Задания 3—3. Одно", None), ("Задания 1—2—3", None), ("Задания 1—22. Слишком много", None),
    ("Задание 1—2. Одно задание с тире", [0]), ("Задания 12—13", None), ("Задания 1 или 2", None), ("Задания 1 и2", None), ("Задания 1и 2", None),
    # The row is taken whole or not at all: what follows it must not be one more number.
    ("Задания 1—2 и 3.1", None), ("Задания 1, 2, 3.5", None), ("Задания 1 и 2 и 1234567", None), ("Задания 1 и 2, остальное позже", [0, 1]),
])
def test_what_a_row_of_tasks_is_and_is_not(name, tasks):
    readme = parse_readme("# П\n\n" + "".join(f"## Задание {n}\n\nТекст.\n\n" for n in range(1, 7)))
    paired = repository.pair(readme, checklist((name, False), ("Задание 1", False), ("Задание 2", False)))
    assert paired.by == "номеру"
    assert sorted(place for place, found in paired.questions.items() if name in [q.name for q in found]) == (tasks or [])
    assert [len([q for q in found if q.name == name]) for found in paired.questions.values() if name in [q.name for q in found]] == [1] * len(tasks or [])
    assert paired.questions_without == ([] if tasks else [name])


def test_a_row_names_no_more_tasks_than_a_question_is_ever_about():
    assert repository.numbers_of("1—20") == list(range(1, 21)) and repository.numbers_of("1—21") == []  # twenty tasks at most
    assert repository.numbers_of("1—10, 12—21") == list(range(1, 11)) + list(range(12, 22)) and repository.numbers_of("1—10, 12—22") == []
    assert repository.numbers_of("3,4 И 6") == [3, 4, 6] and repository.numbers_of("7") == [] and repository.numbers_of("2 and 2") == []
    assert repository.numbers_of("4—2, 5, 6") == [] and repository.numbers_of("1—999999") == []  # one part that names nothing spoils the row
    many = parse_readme("# П\n\n" + "".join(f"## Задание {n}\n\nТекст.\n\n" for n in range(1, 23)))
    paired = repository.pair(many, checklist(("Задания 1—20", False), ("Задание 21", False), ("Задание 22", False)))
    assert sorted(paired.questions) == list(range(22)) and paired.questions_without == []
    # A row that names nothing is not a number: two such names of three do not make the names a numbering.
    few = parse_readme("# П\n\n" + "".join(f"## Задание {n}\n\nТекст.\n\n" for n in range(1, 4)))
    assert repository.pair(few, checklist(("Задания 3—1", False), ("Задания 1—40", False), ("Задание 3", False))).by == "порядку"
    assert repository.pair(few, checklist(("Задания 1—3", False), ("Задания 1 и 2", False), ("Итог", False))).by == "номеру"


def test_a_name_is_one_line_and_is_matched_at_once_however_long_its_spaces():
    import time

    folded = parse_checklist("sections:\n  - name: Часть\n    questions:\n      - name: \"Задание 1.\\n\\n   Разведка\\tи счёт \"\n        description: т\n")
    assert [q.name for q in folded.questions] == ["Задание 1. Разведка и счёт"]
    hostile = "Задание 1" + " " * 3000 + "." + " " * 3000 + "бонус" + " " * 3000 + ":" + " " * 3000 + "x\ny"
    started = time.perf_counter()
    assert repository.TASK.match(hostile) and len(repository.cells("|a" + "|" * 200_000 + "x")) == 200_001
    assert repository.SEVERAL.match("Задания 1" + " , 2" * 20_000 + " ,") and repository.numbers_of("1" + " , 2" * 20_000) == [1, 2]
    assert time.perf_counter() - started < 2  # the first two took seconds to minutes when tried in every way


def test_a_row_of_tasks_is_looked_for_in_its_own_part_and_counts_for_a_numbering():
    readme = parse_readme("# П\n\n## Задание 1\n\nТ.\n\n## Задание 2\n\nТ.\n\n## Задание 3 (дополнительное)\n\nТ.\n\n## Задание 4 (дополнительное)\n\nТ.\n")
    paired = repository.pair(readme, checklist(("Задания 1—2", False), ("Задания 3—4", True), ("Итог", False)))
    assert paired.by == "номеру" and sorted(paired.questions) == [0, 1, 2, 3] and paired.questions_without == ["Итог"]  # two names of three number tasks
    # The same row asked in the other part names tasks that part does not have.
    assert repository.pair(readme, checklist(("Задания 1—2", True), ("Задания 3—4", False), ("Задание 1", False))).questions_without == ["Задания 1—2", "Задания 3—4"]
    # A row that runs from the main part into the bonus one names, in its own part, a task that part does not have.
    across = repository.pair(readme, checklist(("Задания 2—3", False), ("Задание 1", False), ("Задание 4", True)))
    assert across.questions_without == ["Задания 2—3"] and across.tasks_without == ["Задание 2", "Задание 3"]


def test_a_bar_inside_a_cell_is_not_the_end_of_the_cell():
    assert repository.cells("| Что сдаёшь | cat a \\| wc -l |") == ["Что сдаёшь", "cat a | wc -l"]
    assert repository.cells("| один |") == ["один"] and repository.cells("|| а | б ||") == ["а", "б"] and repository.cells("а | б") == ["а", "б"]
    assert repository.cells("| а | б \\|") == ["а", "б |"]  # the last bar is of the cell, not the edge of the row
    assert repository.cells("|X|X|") == ["X", "X"] and repository.cells("| a \\") == ["a \\"] and repository.cells("| a \\||") == ["a |"]
    task = parse_readme("# П\n\n## Задание 1\n\n| Задание 1: Поток | |\n|---|---|\n| Файлы | `a.sh` |\n| Команда | `cat a \\| wc` |\n\nСделай.\n").tasks[0]
    assert task.submit == ["Файлы: `a.sh`", "Команда: `cat a | wc`"]


WORD = "README.md.docx — документ Word: прочитан как текст с заголовками, списками и таблицами; блоки кода в нём не отличаются от абзацев"


def test_a_working_folder_is_read_by_the_files_its_keeper_names(tmp_path):
    folder = kept(tmp_path / "day", files={"README_old.md.docx": b"not a document", "Эталоны (не грузим)/solution.py": "print(42)\n",
                                           "Эталоны (не грузим)/deep/notes.md": "ответ\n", "datasets/bank.csv": "id;текст\n1;a;b\n"})
    project = repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml", ["README_old.md.docx", "Эталоны (не грузим)"], ["datasets/bank.csv"])
    assert (project.readme_file, project.checklist_file) == ("README.md.docx", "D1_check-list_RUS.yml")
    assert [task.label for task in project.readme.tasks] == ["Задание 1", "Задание 2", "Задание 3"]
    assert project.checklist is not None and len(project.checklist.questions) == 2
    # The README and the check-list are not files of the project twice over; what was to be skipped is not there at all.
    assert [file.path for file in project.files] == ["datasets/bank.csv", "datasets/station.log", "materials/terminal_rus.md"]
    assert project.broken == ("datasets/bank.csv",)
    assert project.notes == [WORD, "по meta.yaml не читались (3 файлов): «README_old.md.docx», «Эталоны (не грузим)»",
                             "по meta.yaml испорчены намеренно, на читаемость не проверялись: «datasets/bank.csv»"]
    # Named by hand, the two files are a pair by the word of whoever named them: «_RUS» in one name only is not a remark.
    assert not any("на разных языках" in note for note in project.notes)


def test_without_a_word_from_its_keeper_a_working_folder_is_not_guessed_at(tmp_path):
    folder = kept(tmp_path / "day", files={"README_old.md.docx": b"x", "drafts/README (1).md": "# Черновик\n", "platform/check-list.yml": "sections: []\n"})
    said = ("в каталоге «day» нет README_RUS.md или README.md; похожие файлы: «README.md.docx», «README_old.md.docx», «drafts/README (1).md»"
            " — назовите нужный в meta.yaml: readme")
    with pytest.raises(ValueError) as refused:
        repository.read(folder)
    assert str(refused.value) == said
    # The README named, the check-list not: the two that look like one are offered, neither is taken.
    project = repository.read(folder, "README.md.docx")
    assert project.checklist is None and project.notes[1] == (
        "чек-листа в репозитории нет; похожие файлы: «D1_check-list_RUS.yml», «platform/check-list.yml» — назовите нужный в meta.yaml: checklist")
    with pytest.raises(ValueError, match="^в каталоге «empty» нет README_RUS.md или README.md$"):
        repository.read(written(tmp_path / "empty", names=("notes.txt", "list.txt")))
    # No more names are offered than a line holds.
    many = kept(tmp_path / "many", files={f"README_{n}.md": "x" for n in range(9)})
    with pytest.raises(ValueError) as refused:
        repository.read(many)
    assert str(refused.value).count("«README") == repository.MAX_ALIKE


@pytest.mark.parametrize("named, said", [
    ({"readme": "README.docx"}, "«day»: в meta.yaml поле readme называет «README.docx», а «README.docx» в «day» нет"),
    ({"readme": "docs/README.md.docx"}, "«day»: в meta.yaml поле readme называет «docs/README.md.docx», а «docs» в «day» нет"),
    ({"readme": "datasets"}, "«day»: «datasets» из meta.yaml — не файл"),
    ({"readme": "README.md.docx", "checklist": "check-list.yml"}, "«day»: в meta.yaml поле checklist называет «check-list.yml», а «check-list.yml» в «day» нет"),
    ({"readme": "README.md.docx", "checklist": "materials"}, "«day»: «materials» из meta.yaml — не файл"),
    # A name with a slip in it must not pass for «nothing to skip»: the answers would be read as the project's files.
    ({"readme": "README.md.docx", "skip": ["Эталоны"]}, "«day»: в meta.yaml поле skip называет «Эталоны», а «Эталоны» в «day» нет"),
    ({"readme": "README.md.docx", "skip": ["materials/nothing.md"]}, "«day»: в meta.yaml поле skip называет «materials/nothing.md», а «nothing.md» в «materials» нет"),
    ({"readme": "README.md.docx", "broken": ["datasets/bank.csv"]}, "«day»: в meta.yaml поле broken называет «datasets/bank.csv», а «bank.csv» в «datasets» нет"),
    ({"readme": "README.md.docx", "broken": ["datasets"]}, "«day»: в meta.yaml поле broken называет не файл проекта: «datasets»"),
    ({"readme": "README.md.docx", "skip": ["datasets"], "broken": ["datasets/station.log"]}, "«day»: в meta.yaml поле broken называет не файл проекта: «datasets/station.log»"),
    # Out of the folder there is nothing to name.
    ({"readme": "../day/README.md.docx"}, "«day»: в meta.yaml поле readme называет «../day/README.md.docx», а «..» в «day» нет"),
    ({"readme": "README.md.docx", "skip": ["/"]}, "«day»: в meta.yaml поле skip пустое"),
    ({"readme": "README.md.docx", "skip": ["."]}, "«day»: в meta.yaml поле skip называет «.», а «.» в «day» нет"),
])
def test_a_name_that_names_nothing_is_refused_by_its_field(tmp_path, named, said):
    with pytest.raises(ValueError) as refused:
        repository.read(kept(tmp_path / "day"), **named)
    assert str(refused.value) == said


def test_a_path_is_read_as_it_was_typed_on_either_system(tmp_path):
    folder = kept(tmp_path / "day", files={"platform/list.yml": "sections: []\n", "Эталоны/й.txt": "x"})
    project = repository.read(folder, "README.md.docx", "platform\\list.yml", ["Эталоны/"])
    assert project.checklist_file == "platform/list.yml" and "Эталоны/й.txt" not in [file.path for file in project.files]
    # «й» typed as one letter names the folder an archive from a Mac unpacked with «й» as two, and the other way round.
    one, two = "Крайний", "Кра" + "и\u0306" + "ний"
    for on_disk, typed in ((one, two), (two, one)):
        place = kept(tmp_path / f"day{len(on_disk)}", files={f"{on_disk}/a.txt": "x"})
        assert [file.path for file in repository.read(place, "README.md.docx").files][-1].endswith("/a.txt")
        assert not [file.path for file in repository.read(place, "README.md.docx", skip=[typed]).files if file.path.endswith("a.txt")]


@pytest.mark.skipif(os.name == "nt" or __import__("sys").platform == "darwin", reason="the two spellings are one name there")
def test_two_folders_whose_names_differ_only_in_how_a_letter_is_typed_are_told_apart_or_refused(tmp_path):
    one, two = "Крайний", "Кра" + "и\u0306" + "ний"
    folder = kept(tmp_path / "day", files={f"{one}/a.txt": "x", f"{two}/b.txt": "x"})
    # Typed exactly as one of them, the name is that one.
    assert [file.path for file in repository.read(folder, "README.md.docx", skip=[one]).files if file.path.endswith(".txt")] == [f"{two}/b.txt"]
    assert [file.path for file in repository.read(folder, "README.md.docx", skip=[two]).files if file.path.endswith(".txt")] == [f"{one}/a.txt"]
    # Typed in a third way — one «й» as one letter, the other as two — the name fits both and is neither: refused.
    short, long = "й", "и\u0306"
    twice = kept(tmp_path / "twice", files={f"{short}{short}/a.txt": "x", f"{long}{long}/b.txt": "x"})
    with pytest.raises(ValueError) as refused:
        repository.read(twice, "README.md.docx", skip=[long + short])
    assert str(refused.value) == f"«twice»: в meta.yaml поле skip называет «{long}{short}», а имён «{long}{short}» в «twice» несколько: буквы набраны по-разному"


def test_with_a_name_given_the_folder_is_the_project_and_its_own_meta_is_not_a_file_of_it(tmp_path):
    inner = kept(tmp_path / "wrapped" / "day")
    with pytest.raises(ValueError, match="поле readme называет «README.md.docx», а «README.md.docx» в «wrapped» нет"):
        repository.read(tmp_path / "wrapped", "README.md.docx")  # paths are from the folder itself: no looking one folder deeper
    assert repository.read(tmp_path / "wrapped", "day/README.md.docx").readme_file == "day/README.md.docx"
    (inner / "meta.yaml").write_text("note: что известно о случае\n", encoding="utf-8")
    (inner / "materials" / "meta.yaml").write_text("a: 1\n", encoding="utf-8")
    assert [file.path for file in repository.read(inner, "README.md.docx").files] == ["D1_check-list_RUS.yml", "datasets/station.log", "materials/meta.yaml",
                                                                                      "materials/terminal_rus.md"]
    # A README under the school's name is taken without a word, and one in Word is said to be one.
    written(tmp_path / "plain")
    assert repository.read(tmp_path / "plain", skip=[]).notes == [] and repository.read(inner, "README.md.docx").notes[0] == WORD


@pytest.mark.skipif(os.name == "nt", reason="making a link needs a privilege on Windows")
def test_a_link_names_nothing(tmp_path):
    folder = kept(tmp_path / "day")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "README.md").write_text("# Чужой\n\n## Задание 1\n\nТекст.\n", encoding="utf-8")
    (folder / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="поле readme называет «link/README.md», а «link» в «day» нет"):
        repository.read(folder, "link/README.md")


def test_a_readme_named_by_hand_has_no_pair_and_what_lies_beside_it_is_a_file(tmp_path):
    # «README.md» beside the Word README is a draft, not the README in another language: it is read as a file.
    folder = kept(tmp_path / "day", files={"README.md": "# Черновик\n\nПравильный ответ: 42\n", "check-list.yml": "sections: []\n"})
    project = repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml")
    assert [file.path for file in project.files] == ["check-list.yml", "datasets/station.log", "materials/terminal_rus.md", "README.md"]
    assert "Правильный ответ: 42" in project.files[-1].content
    assert [file.path for file in repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml", ["README.md"]).files][0] == "check-list.yml"
    # Only the check-list named: the README is found under the school's name, with its pair; the other check-list is a file.
    school = written(tmp_path / "school", files={"README.md": "# In English\n", "check-list.yml": "sections: []\n", "lists/day.yml": CHECKLIST})
    project = repository.read(school, checklist="lists/day.yml")
    assert (project.readme_file, project.checklist_file) == ("README_RUS.md", "lists/day.yml")
    assert [file.path for file in project.files] == ["check-list.yml", "check-list_RUS.yml"] and project.notes == []  # and no word of languages
    # Only a file to skip named: both are found under the school's names, and nothing of theirs is a file.
    assert [file.path for file in repository.read(school, skip=["lists"]).files] == []


def test_what_is_to_be_read_cannot_be_what_is_to_be_skipped(tmp_path):
    folder = kept(tmp_path / "day", files={"check-list.yml": CHECKLIST, "old/README.md.docx": b"x"})
    for named, skip, both in (("", ["README.md.docx"], "readme"), ("D1_check-list_RUS.yml", ["D1_check-list_RUS.yml"], "checklist"),
                              ("check-list.yml", ["check-list.yml", "README.md.docx"], "readme и checklist")):
        with pytest.raises(ValueError) as refused:
            repository.read(folder, "README.md.docx", named, skip)
        assert str(refused.value) == f"«day»: в meta.yaml поле skip называет то же, что {both}"
    with pytest.raises(ValueError, match="то же, что readme"):
        repository.read(folder, "old/README.md.docx", skip=["old"])  # the folder it lies in is skipped
    # A check-list found under the school's name and said to be skipped is no check-list — and is not offered back.
    project = repository.read(folder, "README.md.docx", skip=["check-list.yml"])
    assert project.checklist is None and project.checklist_file == ""
    assert project.notes[1:] == ["чек-листа в репозитории нет; похожие файлы: «D1_check-list_RUS.yml» — назовите нужный в meta.yaml: checklist",
                                 "по meta.yaml не читались (1 файлов): «check-list.yml»"]
    assert "check-list.yml" not in [file.path for file in project.files]


def test_a_working_folder_is_held_to_the_limits_of_a_repository(tmp_path, monkeypatch):
    folder = kept(tmp_path / "day")
    (folder / "README.md.docx").rename(folder / "README.MD.DOCX")  # the ending in capitals is a Word document too
    assert len(repository.read(folder, "README.MD.DOCX").readme.tasks) == 3
    size = len(repository.word.markdown(folder / "README.MD.DOCX"))
    monkeypatch.setattr(repository, "MAX_TEXT", size)
    assert repository.read(folder, "README.MD.DOCX").readme.title == "День 01. Журнал"
    monkeypatch.setattr(repository, "MAX_TEXT", size - 1)  # the text of the document, not the size of its file, is what is limited
    with pytest.raises(ValueError, match="README.MD.DOCX в «day» пустой или больше"):
        repository.read(folder, "README.MD.DOCX")


def test_a_name_of_one_kind_given_is_enough_to_make_the_folder_the_project(tmp_path):
    written(tmp_path / "wrapped" / "inner", files={"data/bank.csv": "a;b\n"})
    assert repository.read(tmp_path / "wrapped").readme_file == "README_RUS.md"  # nothing named: the repository one folder deeper is found
    for named in ({"checklist": "inner/check-list_RUS.yml"}, {"skip": ["inner/data"]}, {"broken": ["inner/data/bank.csv"]}):
        with pytest.raises(ValueError, match="^в каталоге «wrapped» нет README_RUS.md или README.md; похожие файлы: «inner/README_RUS.md»"):
            repository.read(tmp_path / "wrapped", **named)
    # Look-alikes are found whatever marks their names are written with.
    kept(tmp_path / "day", files={"lists/Check_List.YAML": "sections: []\n", "lists/checklist.yml": "x", "lists/checklist.txt": "x", "lists/checklist.yml.d/a.md": "x"})
    assert repository.read(tmp_path / "day", "README.md.docx").notes[1] == (
        "чек-листа в репозитории нет; похожие файлы: «D1_check-list_RUS.yml», «lists/Check_List.YAML», «lists/checklist.yml» — назовите нужный в meta.yaml: checklist")


@pytest.mark.skipif(os.name == "nt", reason="making a link needs a privilege on Windows")
def test_a_readme_that_is_a_link_is_not_the_readme(tmp_path):
    (tmp_path / "outside.md").write_text(README, encoding="utf-8")
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "README.md").symlink_to(tmp_path / "outside.md")
    with pytest.raises(ValueError, match="^в каталоге «repo» нет README_RUS.md или README.md"):
        repository.read(tmp_path / "repo")


def test_a_frame_of_one_column_after_a_heading_is_the_text_of_the_task():
    # A document from Google Docs keeps a sample of code in a table of one cell: it is not the table of what to hand in.
    framed = parse_readme("# П\n\n## Задание 1. Запуск\n\n| python3 src/main.py |\n|---|\n\nЗапусти программу.\n").tasks[0]
    assert not framed.table and framed.submit == [] and "python3 src/main.py" in framed.text
    two = parse_readme("# П\n\n## Задание 1\n\n| Задание 1: Запуск | |\n|---|---|\n| Файлы | `a.py` |\n\nЗапусти.\n").tasks[0]
    assert two.table and two.submit == ["Файлы: `a.py`"] and two.text == "Запусти."


def test_where_words_are_expected_a_list_is_no_words():
    import time

    odd = parse_checklist("introduction: [a, b]\nguidelines: {a: 1}\nsections:\n  - name: [Bonus]\n    questions:\n"
                          "      - {name: [Задание 1], description: {a: b}, rating: 5}\n      - {name: 7, description: 1.5, rating: true}\n")
    assert (odd.introduction, odd.guidelines) == ("", "") and odd.questions == [Question("", "", "5", False), Question("7", "1.5", "", False)]
    # Nine lines of YAML that refer to one another: written out, they would be gigabytes.
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n" + "".join(f"{chr(98 + n)}: &{chr(98 + n)} [{', '.join('*' + chr(97 + n) for _ in range(10))}]\n" for n in range(8))
    started = time.perf_counter()
    [question] = parse_checklist(bomb + "sections:\n  - name: *i\n    questions:\n      - {name: *i, description: *i, rating: *i}\n").questions
    assert question == Question("", "", "", False) and time.perf_counter() - started < 2


def test_the_check_list_is_the_first_under_a_school_name_that_is_not_skipped(tmp_path):
    folder = written(tmp_path / "P", files={"check-list.yml": CHECKLIST.replace("Main part", "English part")})
    assert repository.read(folder, skip=["check-list.yml"]).checklist_file == "check-list_RUS.yml"
    english = repository.read(folder, skip=["check-list_RUS.yml"])
    assert english.checklist_file == "check-list.yml" and english.files == [] and english.notes == [
        "README_RUS.md и check-list.yml на разных языках: русской пары в репозитории нет", "по meta.yaml не читались (1 файлов): «check-list_RUS.yml»"]
    # The README found under a school's name cannot be the one to skip either.
    with pytest.raises(ValueError, match="^«P»: в meta.yaml поле skip называет то же, что readme$"):
        repository.read(folder, skip=["README_RUS.md"])


def test_what_was_passed_over_is_counted_in_files_of_the_project(tmp_path):
    folder = kept(tmp_path / "day", files={"old/a.txt": "x", "old/.gitkeep": "", "old/.git/config": "x", "old/deep/b.txt": "x"})
    assert repository.read(folder, "README.md.docx", skip=["old"]).notes[-1] == "по meta.yaml не читались (2 файлов): «old»"


@pytest.mark.skipif(os.name == "nt", reason="making a link needs a privilege on Windows")
def test_a_linked_folder_is_not_the_repository_and_a_linked_file_is_not_offered(tmp_path):
    written(tmp_path / "outside")
    (tmp_path / "case").mkdir()
    (tmp_path / "case" / "inner").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert repository.find(tmp_path / "case") is None
    with pytest.raises(ValueError, match="^в каталоге «case» нет README_RUS.md или README.md$"):
        repository.read(tmp_path / "case")
    (tmp_path / "linked").mkdir()
    (tmp_path / "linked" / "README.md").symlink_to(tmp_path / "outside" / "README_RUS.md")
    (tmp_path / "linked" / "README_draft.md").write_text("# Черновик\n", encoding="utf-8")
    with pytest.raises(ValueError) as refused:
        repository.read(tmp_path / "linked")
    assert str(refused.value) == "в каталоге «linked» нет README_RUS.md или README.md; похожие файлы: «README_draft.md» — назовите нужный в meta.yaml: readme"


def test_the_edges_of_reading_a_working_folder(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="^в каталоге «nowhere» нет README_RUS.md или README.md$"):
        repository.read(tmp_path / "nowhere")
    folder = kept(tmp_path / "day", files={"datasets/a.csv": "a;b\n", "datasets/b.csv": "a;b\n"})
    # A path that goes on through a file names nothing.
    with pytest.raises(ValueError, match="поле readme называет «README.md.docx/x», а «x» в «README.md.docx» нет"):
        repository.read(folder, "README.md.docx/x")
    project = repository.read(folder, "README.md.docx", broken=["datasets/a.csv", "datasets/b.csv"])
    assert project.notes[-1] == "по meta.yaml испорчены намеренно, на читаемость не проверялись: «datasets/a.csv», «datasets/b.csv»"
    with pytest.raises(ValueError, match="поле broken называет не файл проекта: «datasets», «materials»$"):
        repository.read(folder, "README.md.docx", broken=["datasets", "materials"])
    # A folder whose name begins with a dot is not the folder an archive wrapped the repository in.
    written(tmp_path / "wrapped" / "inner")
    (tmp_path / "wrapped" / ".idea").mkdir()
    assert repository.find(tmp_path / "wrapped") == tmp_path / "wrapped" / "inner"
    # A check-list of exactly the limit is read; one byte more — it is not.
    long = written(tmp_path / "long", checklist=CHECKLIST + "# " + "я" * 20_000 + "\n")
    size = (long / "check-list_RUS.yml").stat().st_size
    monkeypatch.setattr(repository, "MAX_TEXT", size)
    assert repository.read(long).checklist is not None
    monkeypatch.setattr(repository, "MAX_TEXT", size - 1)
    assert repository.read(long).checklist is None and repository.read(long).notes == ["check-list_RUS.yml больше 0 МБ, не прочитан"]


def test_the_files_stand_in_one_order_on_any_system(tmp_path):
    """Paths compare with no regard to case on Windows and with it elsewhere: «README» before «datasets» here,
    after it there. The list goes to the judge and into the key of a paid answer, so the order is one — that of
    Windows: by the names in small letters, folder by folder."""
    folder = kept(tmp_path / "day", files={"Zeta.txt": "я\n", "alpha.txt": "а\n", "Beta/inner.txt": "б\n", "beta.txt": "в\n", "src/B.py": "1\n", "src/a.py": "2\n"})
    project = repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml")
    mine = [file.path for file in project.files if not file.path.startswith(("datasets/", "materials/"))]
    assert mine == ["alpha.txt", "Beta/inner.txt", "beta.txt", "src/a.py", "src/B.py", "Zeta.txt"]
    # The order of the paths themselves, the one in use before, is another: capital letters first.
    assert sorted(mine) == ["Beta/inner.txt", "Zeta.txt", "alpha.txt", "beta.txt", "src/B.py", "src/a.py"]


# An invented project in the shape the templates of the bassin have when read from a document of Word: the story,
# the line of the quest as a heading of the fourth level, a statement of several paragraphs as several such headings.
BASSIN = """# D09T01. Склад

Проект познакомит с чтением файлов.

## Chapter I

## Level 1. Room 1

Ты входишь в комнату. На стене записки.

## Chapter II

## List 1

Файл читают построчно.

## Chapter III

## Quests: Level 1. Room 1

## Quest 1. Count

Ты видишь терминал. На экране мигает надпись.

#### Получен Quest 1. Создать программу src/count.c, которая считает строки файла.

| Входные данные | Выходные данные |
|---|---|
| `a.txt` | `3` |

***LOADING…***

## Quest 2. Table

> Запустить программу `src/count.c`

Дверь не открывается.

#### Получен Quest 2. Создать программу src/table.c, которая печатает таблицу трёх функций:

#### - первой;

#### - второй.

Все значения печатаются с точностью до 7 знаков.

#### Заголовок после текста — уже не формулировка.

## Bonus Quests: Level 1. Room 1

## Bonus Quest 3. Graph

#### Получен Quest 3. Дополнить программу src/table.c графиком.
#### Иных требований нет.

## Chapter IV

Дверь открыта.
"""


def test_a_project_of_the_bassin_is_read_by_its_quests():
    readme = parse_readme(BASSIN)
    assert [(t.label, t.number, t.ref, t.title, t.bonus) for t in readme.tasks] == [
        ("Quest 1", 1, "1", "Count", False), ("Quest 2", 2, "2", "Table", False), ("Bonus Quest 3", 3, "3", "Graph", True)]
    one, two, three = (repository_quest(t) for t in readme.tasks)
    # The story before the line, the line without the words «Получен Quest N.», what stands after it.
    assert one == ("Ты видишь терминал. На экране мигает надпись.", "Создать программу src/count.c, которая считает строки файла.",
                   "| Входные данные | Выходные данные |\n|---|---|\n| `a.txt` | `3` |\n\n***LOADING…***")
    # A statement of several paragraphs is several headings of one level: taken while they last, and no further.
    assert two.story == "> Запустить программу `src/count.c`\n\nДверь не открывается."
    assert two.statement == "Создать программу src/table.c, которая печатает таблицу трёх функций:\n- первой;\n- второй."
    assert two.rest == "Все значения печатаются с точностью до 7 знаков.\n\n#### Заголовок после текста — уже не формулировка."
    assert three == ("", "Дополнить программу src/table.c графиком.\nИных требований нет.", "")
    # The text of the task is whole as before: the checks of words look through all of it.
    assert readme.tasks[0].text.startswith("Ты видишь терминал.") and "#### Получен Quest 1." in readme.tasks[0].text and readme.tasks[0].submit == []
    # A heading that names the quests of a room is no quest; a note of the theory is theory; nothing else changed its role.
    assert [(s.title, s.role, s.text) for s in readme.sections] == [
        ("Chapter I", "chapter", ""), ("Level 1. Room 1", "other", "Ты входишь в комнату. На стене записки."), ("Chapter II", "chapter", ""),
        ("List 1", "theory", "Файл читают построчно."), ("Chapter III", "chapter", ""), ("Quests: Level 1. Room 1", "other", ""),
        ("Bonus Quests: Level 1. Room 1", "other", ""), ("Chapter IV", "chapter", "Дверь открыта.")]


def repository_quest(task):
    assert task.quest is not None, task.label
    return task.quest


def test_the_quest_of_a_group_project_has_no_number():
    readme = parse_readme("# D05P01. Игра\n\n## Chapter III\n\n## Quest: Level 1. Hall\n\nВ зале стоит автомат.\n\n#### Получен Quest. Разработать программу src/game.c для двух игроков.\n\n"
                          "**Управление**\n\n- A и Z.\n\n## Bonus Quest: Level 1. Hall\n\n#### Получен Quest. Сделать игру в реальном времени.\n\n## Chapter IV\n\nКонец.\n")
    assert [(t.label, t.number, t.ref, t.title, t.bonus) for t in readme.tasks] == [("Quest", 1, "", "Level 1. Hall", False), ("Bonus Quest", 1, "", "Level 1. Hall", True)]
    assert repository_quest(readme.tasks[0]) == ("В зале стоит автомат.", "Разработать программу src/game.c для двух игроков.", "**Управление**\n\n- A и Z.")
    assert repository_quest(readme.tasks[1]).statement == "Сделать игру в реальном времени." and [s.title for s in readme.sections] == ["Chapter III", "Chapter IV"]
    # The same heading with numbered quests after it is the heading of their room: nothing stands under it.
    room = parse_readme("# П\n\n## Quest: Level 1. Room 3\n\n## Quest 1. Introducing\n\n#### Получен Quest 1. Создать программу src/hello.c.\n")
    assert [t.label for t in room.tasks] == ["Quest 1"] and [(s.title, s.role) for s in room.sections] == [("Quest: Level 1. Room 3", "other")]
    # …and with text under it but no line of a quest it is a section too: a quest is known by its line, not by a word of its heading.
    plain = parse_readme("# П\n\n## Quest: как пройти\n\nЧитай записки и иди дальше.\n\n## Quests\n\nИх семь.\n")
    assert plain.tasks == [] and [s.title for s in plain.sections] == ["Quest: как пройти", "Quests"]
    # Without numbers the two are told apart by their part: the questions of a check-list that numbers nothing go to them in order.
    paired = repository.pair(readme, parse_checklist("sections:\n- name: Основная часть\n  questions:\n  - {name: Игра, description: Сыграй партию.}\n"
                                                     "- name: Бонусная часть\n  questions:\n  - {name: Режим, description: Проверь скорость.}\n"))
    assert paired.by == "порядку" and {place: [q.name for q in asked] for place, asked in paired.questions.items()} == {0: ["Игра"], 1: ["Режим"]}
    # A question that numbers a quest names none of these: a quest without a number is not taken for «Quest 1».
    numbered = repository.pair(readme, parse_checklist("sections:\n- name: Часть\n  questions:\n  - {name: Quest 1. Игра, description: Сыграй.}\n"))
    assert numbered.by == "номеру" and numbered.questions == {} and numbered.questions_without == ["Quest 1. Игра"] and numbered.tasks_without == ["Quest", "Bonus Quest"]


@pytest.mark.parametrize("line, statement", [
    ("#### Получен Quest 4. Остановить процесс.", "Остановить процесс."),
    ("**Получен Quest 4.** Остановить процесс.", "Остановить процесс."),          # as this generator's README writes it
    ("Получен Quest 4. Остановить процесс.", "Остановить процесс."),
    ("получен quest 12: остановить процесс", "остановить процесс"),
    ("#### Получен Bonus Quest 4. Остановить процесс.", "Остановить процесс."),
    ("***Получен Quest.*** Остановить процесс.", "Остановить процесс."),
    ("  #### Получен Quest 4. Остановить процесс.", None),                       # indented by two: a line of a list item, not a heading
    ("Тебе получен Quest 4. Остановить процесс.", None),                         # the words are not at the head of the line
    ("Получен Questionnaire 4. Остановить процесс.", None),
    ("Получен Quest 4567890. Остановить процесс.", None),                        # not a number of a quest
    ("Получены Quest 4 и 5.", None),
    ("```\nПолучен Quest 4. Остановить процесс.\n```", None),                    # shown as code, not said
])
def test_what_the_line_of_a_quest_is_and_is_not(line, statement):
    [task] = parse_readme(f"# П\n\n## Quest 4. Kill\n\nСюжет.\n\n{line}\n\nПосле.\n").tasks
    assert (task.quest.statement if task.quest else None) == statement
    if statement:
        assert task.quest and (task.quest.story, task.quest.rest) == ("Сюжет.", "После.")
    # The line makes a quest of a task under any heading; a task without it is what it was.
    assert parse_readme("# П\n\n## Задание 1\n\nСделай.\n").tasks[0].quest is None


def test_the_statement_of_a_quest_ends_with_its_paragraph():
    def quest(body):
        return repository_quest(parse_readme(f"# П\n\n## Quest 1. A\n\n{body}\n").tasks[0])

    # A paragraph of several lines is one statement; a table, a block of code or a heading right under it is not a part of it.
    assert quest("Получен Quest 1. Создать программу,\nкоторая считает.\n\nДальше.") == ("", "Создать программу,\nкоторая считает.", "Дальше.")
    assert quest("Получен Quest 1. Создать программу.\n| a | b |\n|---|---|") == ("", "Создать программу.", "| a | b |\n|---|---|")
    assert quest("Получен Quest 1. Создать программу.\n```\nкод\n```") == ("", "Создать программу.", "```\nкод\n```")
    assert quest("Получен Quest 1. Создать программу.\n#### Подзаголовок").rest == "#### Подзаголовок"
    # The line alone, with nothing after the words, is a statement that says nothing — and is still where the story ends.
    assert quest("Сюжет.\n\n#### Получен Quest 1.\n\nТекст.") == ("Сюжет.", "", "Текст.")
    # Of two such lines the first is the line of the quest; the second is what stands after it.
    assert quest("#### Получен Quest 1. Первое.\n\n#### Получен Quest 2. Второе.") == ("", "Первое.", "#### Получен Quest 2. Второе.")
    # A heading of another level after the line of a heading is not its continuation.
    assert quest("#### Получен Quest 1. Создать.\n\n##### Мелко\n\n#### Крупно") == ("", "Создать.", "##### Мелко\n\n#### Крупно")
    # Nor is a line of text right under it, nor a line that only looks like a heading — indented, it is a line of a list item.
    assert quest("#### Получен Quest 1. Создать.\nТекст сразу за заголовком.") == ("", "Создать.", "Текст сразу за заголовком.")
    assert quest("#### Получен Quest 1. Создать.\n  #### С отступом") == ("", "Создать.", "  #### С отступом")
    # A row of a table ends the paragraph of the statement wherever it is indented to.
    assert quest("Получен Quest 1. Создать.\n  | a | b |") == ("", "Создать.", "  | a | b |")
    # The line «Сдать:» is read after the line of the quest, outside code, and only in a task that has no table of its own.
    handed = parse_readme("# П\n\n## Quest 1. A\n\nСдать: не это.\n\n**Получен Quest 1.** Создать.\n\n```\nСдать: и не это\n```\n\n**Сдать:** src/a.c\n\nСдать: и src/b.c\n").tasks[0]
    assert handed.submit == ["src/a.c", "и src/b.c"]
    tabled = parse_readme("# П\n\n## Quest 1. A\n\n| Файлы | `x.c` |\n\n**Получен Quest 1.** Создать.\n\n**Сдать:** src/a.c\n").tasks[0]
    assert tabled.submit == ["Файлы: `x.c`"] and repository_quest(tabled).statement == "Создать."
    assert parse_readme("# П\n\n## Задание 1\n\nСделай.\n\n**Сдать:** src/a.c\n").tasks[0].submit == []


def test_the_questions_of_a_check_list_name_quests_as_they_name_tasks():
    readme = parse_readme(BASSIN)
    checklist = parse_checklist("sections:\n- name: Основная часть\n  questions:\n  - {name: Quest 1. Count, description: Запусти.}\n  - {name: Quests 1—2, description: Сравни.}\n"
                                "  - {name: Bonus Quest 3. Graph, description: Посмотри график.}\n  - {name: Quest 9. Нет такого, description: Проверь.}\n")
    assert [q.bonus for q in checklist.questions] == [False, False, True, False]
    paired = repository.pair(readme, checklist)
    assert paired.by == "номеру" and {place: [q.name for q in asked] for place, asked in paired.questions.items()} == {
        0: ["Quest 1. Count", "Quests 1—2"], 1: ["Quests 1—2"], 2: ["Bonus Quest 3. Graph"]}
    assert (paired.tasks_without, paired.questions_without) == ([], ["Quest 9. Нет такого"])


def test_the_generators_quest_is_read_back_as_what_it_wrote(tmp_path):
    """The README of the style «квест» is this generator's own: a draft assembled into a repository and read from it
    gives its quests back — the story, the line of the quest, what must come out, what is handed in, a question each."""
    from project_generator_lab.benchmark import code_layer
    from project_generator_lab.content.render import bundle, statement
    from project_generator_lab.tests.content.test_style import styled_draft

    draft = styled_draft("quest")
    zipfile.ZipFile(io.BytesIO(bundle(draft))).extractall(tmp_path / "Q")
    project = repository.read(tmp_path / "Q")
    titles = [t.title for t in draft.tasks]
    assert [(t.label, t.number, t.title, t.bonus) for t in project.readme.tasks] == [
        ("Quest 1", 1, titles[0], False), ("Quest 2", 2, titles[1], False), ("Bonus Quest 3", 3, titles[2], True)]
    assert [s.role for s in project.readme.sections if s.title.startswith("List ")] == ["theory"] * 3
    paired = repository.pair(project.readme, project.checklist)
    assert paired.by == "номеру" and (paired.tasks_without, paired.questions_without) == ([], [])
    assert [code_layer.shape(task) for task in project.readme.tasks] == ["quest"] * 3
    for read, wrote in zip(code_layer.as_tasks(project, paired), draft.tasks, strict=True):
        assert (read.context, read.steps, read.expected, read.result) == (wrote.context, [statement(wrote.steps)], wrote.expected, wrote.result)
        assert read.peer_checks == ["\n".join(f"- {item}" for item in wrote.peer_checks)]
