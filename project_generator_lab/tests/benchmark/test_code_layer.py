"""Layer 1: the generator's checks on a project read from its repository.

A measure that finds nothing in fifteen tested projects proves nothing by that alone. Here the same measure is shown a
project with one defect put into it at a time, and has to name each."""

import io
import json
import zipfile

import pytest
from project_generator_lab.benchmark import code_layer, repository
from project_generator_lab.benchmark.__main__ import main
from project_generator_lab.content.checks import run_checks
from project_generator_lab.tests.benchmark.project import CHECKLIST, README
from project_generator_lab.tests.benchmark.project import repository as written
from project_generator_lab.tests.benchmark.worded import CHECKLIST as DAY_CHECKLIST
from project_generator_lab.tests.benchmark.worded import META, kept

BONUS = "Собери отчёт *report.md* о самых читаемых книгах."
SENTENCE = "Перед началом работы обязательно примени скрипт с моделью базы данных из папки materials к своей базе."


def found(folder, **changes) -> list[tuple[str, str, str]]:
    measured = code_layer.measure(repository.read(written(folder, **changes)))
    return [(check.severity, check.part, check.message) for check in measured.checks]


def test_a_sound_project_passes_and_what_code_cannot_read_is_named(tmp_path):
    measured = code_layer.measure(repository.read(written(tmp_path / "LIB", files={"materials/model.sql": "CREATE TABLE book (id int);\n"})))
    assert measured.checks == [] and (measured.blockers, measured.warnings) == (0, 0)
    # The tasks go by their own numbers; the bonus task is plain text — the file it asks for is named in a sentence.
    assert measured.numbers == [0, 1, 2] and measured.untabled == ["Дополнительно. Задание 02"]
    # The answer the check-list shows the peer is counted, not judged: «эталонное решение» there is not a leak.
    shown = CHECKLIST.replace("Скрипт выглядит так", "Эталонное решение выглядит так")
    assert found(tmp_path / "shown", checklist=shown) == []


@pytest.mark.parametrize("change, said", [
    # A file whose name announces an answer; a file whose text does.
    ({"files": {"materials/solution.sql": "SELECT 1;\n"}}, [("block", "material:materials/solution.sql", "Имя материала «materials/solution.sql» похоже на готовый ответ. Переименуйте или уберите файл.")]),
    ({"files": {"materials/notes.md": "Правильный ответ: 42\n"}}, [("block", "material:materials/notes.md", "В материале «materials/notes.md» есть «Правильный ответ:» — похоже на ответ или служебную пометку.")]),
    # The same words in the task itself.
    ({"readme": README.replace("Результат отсортируй по имени.", "Эталонное решение приложено ниже.")},
     [("block", "task:0", "В задании 0 есть «Эталонное решение» — ответ или служебная пометка в тексте для участника.")]),
    # A task nobody asks about; a question about no task.
    ({"checklist": CHECKLIST.replace("name: Задание 01 Книги на руках", "name: Задание 07 Книги на руках")},
     [("block", "task:1", "В задании 1 нет: пункты для пира."), ("warn", "checklist", "Вопрос чек-листа «Задание 07 Книги на руках» не относится ни к одному заданию README: номера задания в названии нет или задания с таким номером нет.")]),
    # A table that says what is allowed and not what to hand in; a task of the table and nothing else.
    ({"readme": README.replace("| Директория в репозитории | ex01 |\n| Ожидаемые файлы | `ex01.sql` |", "| **Разрешено** | |\n| Язык | SQL |")},
     [("block", "task:1", "В задании 1 нет: формат сдачи.")]),
    ({"readme": README.replace("Найди книги, которые не вернули вовремя.\n\n### Подсказка о датах\n\nСрок возврата — четырнадцать дней.", "")},
     [("block", "task:1", "В задании 1 нет: «Что нужно сделать».")]),
    # No check-list at all: every task is without a question.
    ({"checklist": None}, [("block", "task:0", "В задании 0 нет: пункты для пира."), ("block", "task:1", "В задании 1 нет: пункты для пира."),
                           ("block", "task:2", "В задании 2 нет: пункты для пира.")]),
    # A note to oneself left in the text; a file that does not open as what its name says.
    ({"readme": README.replace("Библиотека переезжает", "TODO: дописать. Библиотека переезжает")}, [("block", "section:Введение", "Осталась заглушка «TODO» (section:Введение).")]),
    ({"readme": README.replace(BONUS, BONUS + " [вставить пример]")}, [("block", "task:2", "Осталась заглушка «[вставить» (task:2).")]),
    ({"checklist": CHECKLIST.replace("В отчёте названы три книги.", "TBD")}, [("block", "question:Дополнительно. Задание 02 — Отчёт для директора",
                                                                              "Осталась заглушка «TBD» (question:Дополнительно. Задание 02 — Отчёт для директора).")]),
    # A table that is a header and nothing under it is a table that says nothing of what to hand in.
    ({"readme": README.replace("| Директория в репозитории | ex01 |\n| Ожидаемые файлы | `ex01.sql` |\n", "")}, [("block", "task:1", "В задании 1 нет: формат сдачи.")]),
    ({"files": {"datasets/books.json": "{\"id\": 1,}\n", "datasets/ok.json": "{}\n", "datasets/rows.csv": "a,b\n1\n"}},
     [("block", "material:datasets/books.json", "Файл «datasets/books.json» не читается как json: JSONDecodeError."),
      ("block", "material:datasets/rows.csv", "Файл «datasets/rows.csv» не читается как csv: в строках CSV разное число колонок.")]),
])
def test_a_defect_put_into_the_project_is_named(tmp_path, change, said):
    assert found(tmp_path / "LIB", **change) == said


def test_the_words_of_a_task_are_looked_through_whole_however_the_task_is_written(tmp_path):
    """Independent check, 05.10.2026: one bold line named as a block of the generator made a school task «written in
    blocks», and the text outside the blocks — an answer, a note to oneself — was looked at by no check."""
    readme = README.replace("Найди книги, которые не вернули вовремя.", "Эталонное решение: select 1. TODO дописать.\n\n**Что должно получиться**\n\n- таблица")
    assert found(tmp_path / "A", readme=readme) == [
        ("block", "task:1", "В задании 1 есть «Эталонное решение» — ответ или служебная пометка в тексте для участника."),
        ("block", "task:1", "Осталась заглушка «TODO» (task:1).")]
    # The rows of the table are words of the task too; and a name in an example is not a note to oneself.
    assert found(tmp_path / "B", readme=README.replace("| Конструкции | `JOIN` |", "| Конструкции | XXX |")) == [("block", "task:0", "Осталась заглушка «XXX» (task:0).")]
    assert found(tmp_path / "C", checklist=CHECKLIST.replace("SELECT name FROM reader", "create or replace view xxxxx as select name from reader")) == []
    # Every line under the heading is looked through as it stands: the header of the table, a cell with a tag in it.
    header = README.replace("| <!-- --> | <!-- --> |", "| Эталонное решение: select 1 | |")
    assert found(tmp_path / "D", readme=header) == [("block", "task:1", "В задании 1 есть «Эталонное решение» — ответ или служебная пометка в тексте для участника.")]
    assert found(tmp_path / "E", readme=README.replace("| Ожидаемые файлы | `ex01.sql` |", "| Ожидаемые файлы | <название>.sql |")) == [
        ("block", "task:1", "Осталась заглушка «<название» (task:1).")]


def test_a_task_in_the_generators_blocks_is_read_by_the_template_not_by_look_alikes(tmp_path):
    def blocks(doing: str) -> list[tuple[str, str, str]]:
        readme = f"# П\n\nАннотация проекта.\n\n### Задание 1. Основное\n\n**Что нужно сделать**\n\n{doing}\n\n**Что должно получиться**\n\n- Файл\n\n**Формат сдачи**\n\nsrc/a.md\n"
        return found(tmp_path / str(len(list(tmp_path.iterdir()))), readme=readme, checklist="sections:\n  - name: Main\n    questions:\n      - {name: 'Задание 1. Основное', description: '- Открывается'}\n")

    assert blocks("Ситуация.\n\nЦель: уметь.\n\n1. Сделай") == []
    # The goal is the template's line after the situation: a line of the situation that begins the same way is the
    # situation's, and its numbered list is not the steps.
    assert blocks("Ситуация.\nЦель: заработать денег.\n1. Пункт ситуации\n\nЦель: уметь.") == [("block", "task:1", "В задании 1 нет: «Что нужно сделать».")]
    assert blocks("Ситуация.\nЦель: заработать денег.\n\nЦель: уметь.\n\n1. Сделай\n   # строка шага\n100. И сотый шаг") == []


def test_a_readme_with_no_task_in_it_is_not_measured(tmp_path, capsys):
    """A README the reader recognised nothing in — another encoding, other headings — came out as «без блокеров»."""
    odd = written(tmp_path / "root" / "odd", readme="Проект\n======\n\n<h2>Задание 1</h2>\n\nТекст.\n")
    with pytest.raises(ValueError, match="^в README_RUS.md проекта «odd» не найдено ни одного задания: заголовков «Задание N», «Part N», «Quest N» нет$"):
        code_layer.measure(repository.read(odd))
    # Why nothing was recognised, when the reader knows: the note of the encoding goes with the refusal.
    (tmp_path / "cp" ).mkdir()
    (tmp_path / "cp" / "README.md").write_bytes("# Проект\n\n## Задание 1\n\nТекст.\n".encode("utf-16"))
    with pytest.raises(ValueError, match="заголовков «Задание N», «Part N», «Quest N» нет; README.md не в UTF-8: часть букв не прочитана; чек-листа в репозитории нет$"):
        code_layer.measure(repository.read(tmp_path / "cp"))
    written(tmp_path / "root" / "sound")
    assert main([str(tmp_path / "root"), "--out", str(tmp_path / "out")]) == 0
    data = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert [p["project"] for p in data["projects"]] == ["sound"]
    assert data["passed"] == ["в README_RUS.md проекта «odd» не найдено ни одного задания: заголовков «Задание N», «Part N», «Quest N» нет"]
    capsys.readouterr()


def test_what_a_project_says_cannot_write_the_reports_own_lines(tmp_path):
    """A question named with a line break and a heading after it made a heading of the report; so could a note."""
    forged = CHECKLIST.replace("name: Задание 01 Книги на руках", "name: \"Лишний\\n\\n## Поддельный проект\\n\\n| a | b |\"")
    measured = code_layer.measure(repository.read(written(tmp_path / "LIB", checklist=forged)))
    report = code_layer.report([measured._replace(project=measured.project._replace(name="LIB\n## Ещё один", notes=["заметка\n## И ещё"]))], ["пропуск\n## Тоже"], ["отложен\n## И он"])
    assert [line for line in report.splitlines() if line.startswith("## ")] == ["## Что проверено", "## Проекты", "## LIB ## Ещё один"]
    assert code_layer.flat("<h2>Заголовок</h2>\n<script>") == "&lt;h2>Заголовок&lt;/h2> &lt;script>"  # a tag stays a word
    assert "- замечание · Вопрос чек-листа «Лишний ## Поддельный проект | a | b |» не относится ни к одному заданию README: номера задания в названии нет или задания с таким номером нет." in report.splitlines()
    assert sum(line.startswith("| ") for line in report.splitlines()) == 1 + len(code_layer.APPLIED) + 1 + 1  # the two tables and nothing else


def test_two_tasks_with_one_fault_are_two_lines(tmp_path):
    same = code_layer.Check(code="submission", severity="block", part="task:1", message="В задании 1 нет: формат сдачи.")
    assert code_layer.said([same, same]) == ["блокер · В задании 1 нет: формат сдачи."] * 2
    twice = code_layer.measure(repository.read(written(tmp_path / "T", readme="# П\n\nАннотация.\n\n## Задание 1 А\n\nТекст.\n\n## Задание 1 Б\n\nТекст.\n", checklist=None)))
    assert twice.numbers == [1, 2] and [c.message for c in twice.checks] == ["В задании 1 нет: пункты для пира.", "В задании 2 нет: пункты для пира."]


def test_a_passage_repeated_by_many_tasks_is_one_remark_with_the_number_of_pairs(tmp_path):
    # A common setup instruction is valid. Duplicating substantial task work must still be detected.
    readme = README.replace("Результат отсортируй по имени.", SENTENCE).replace("Срок возврата — четырнадцать дней.", SENTENCE).replace(BONUS, BONUS + "\n\n" + SENTENCE)
    assert code_layer.measure(repository.read(written(tmp_path / "SETUP", readme=readme))).checks == []
    work = ("Выбери двадцать записей каталога, проверь даты выдачи и возврата каждой книги, вычисли длительность "
            "чтения для каждого читателя, сравни результаты по месяцам, найди самые частые задержки, объясни причины "
            "расхождений и подготовь таблицу с исходными значениями, расчётами, выбранными допущениями и выводами "
            "для библиотекаря, который будет менять порядок напоминаний о возврате книг в следующем месяце.")
    measured = code_layer.measure(repository.read(written(tmp_path / "LIB", readme=readme.replace(SENTENCE, work))))
    assert [(check.code, check.part) for check in measured.checks] == [("copy_paste", "task:1"), ("copy_paste", "task:2"), ("copy_paste", "task:2")]
    lines = code_layer.said(measured.checks)
    assert len(lines) == 1 and lines[0].endswith("— пар заданий: 3")
    assert "совпадение не доказывает дублирование заданий" in lines[0]
    assert (measured.blockers, measured.warnings) == (0, 1)
    # Different findings stay different lines, in the order found; a blocker is not a remark.
    other = code_layer.Check(code="submission", severity="block", part="task:1", message="В задании 1 нет: формат сдачи.")
    assert code_layer.said([other, *measured.checks[:1]]) == [
        "блокер · В задании 1 нет: формат сдачи.", lines[0].removesuffix(" — пар заданий: 3")]


def test_main_and_bonus_tasks_that_share_a_number_go_by_their_place(tmp_path):
    readme = "# П\n\nАннотация проекта.\n\n### Задание 1. Основное\n\n**Что нужно сделать**\n\nЦель: уметь.\n\n1. Сделай\n\n**Что должно получиться**\n\n" \
             "- Файл\n\n**Формат сдачи**\n\nsrc/a.md\n\n### Бонусное задание 1*: Сверх\n\n**Что нужно сделать**\n\nЦель: уметь больше.\n\n1. Сделай ещё\n\n" \
             "**Что должно получиться**\n\n**Формат сдачи**\n\nsrc/b.md\n"
    checklist = "sections:\n  - name: Основная часть\n    questions:\n      - {name: 'Задание 1. Основное', description: '- Файл открывается'}\n" \
                "      - {name: 'Бонусное задание 1*: Сверх', description: '- Файл открывается'}\n"
    measured = code_layer.measure(repository.read(written(tmp_path / "P", readme=readme, checklist=checklist)))
    assert measured.numbers == [1, 2] and measured.untabled == []
    # A task written in the generator's blocks is asked for every block of a draft: «Что должно получиться» is empty in the bonus one.
    assert [(c.part, c.message) for c in measured.checks] == [("task:2", "В задании 2 нет: «Что должно получиться».")]
    report = code_layer.report([measured], [], [])
    assert "Номера заданий в замечаниях: 1 — «Задание 1», 2 — «Бонусное задание 1*»." in report


def test_the_generators_own_result_is_measured_as_its_draft_is(tmp_path):
    """One rule for both sides: the text checks of a draft and of the same draft read back from its repository agree —
    on a sound draft and on one with a copied passage, an answer in a task and an empty block."""
    from project_generator_lab.content.render import bundle
    from project_generator_lab.tests.content.test_parts import complete_draft

    def both(draft) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
        folder = tmp_path / str(len(list(tmp_path.iterdir())))
        zipfile.ZipFile(io.BytesIO(bundle(draft))).extractall(folder)
        direct = [(c.code, c.part, c.message) for c in run_checks(draft) if c.code in {"solution", "submission", "copy_paste"}]
        return direct, [(c.code, c.part, c.message) for c in code_layer.measure(repository.read(folder)).checks]

    assert both(complete_draft()) == ([], [])
    spoiled = complete_draft()
    spoiled.tasks[1].steps = [*spoiled.tasks[0].steps, SENTENCE + " Потом сверь результат с соседом и запиши расхождения в общий файл проекта."]
    spoiled.tasks[2].steps = [SENTENCE + " Потом сверь результат с соседом и запиши расхождения в общий файл проекта."]
    spoiled.tasks[0].context = "Ситуация.\n\nПравильный ответ: роль CEO у Пети."
    spoiled.tasks[2].expected = []
    direct, read_back = both(spoiled)
    assert sorted(direct) == sorted(read_back) and sorted(code for code, _, _ in direct) == ["copy_paste", "solution", "submission"]


def test_the_generators_quest_is_measured_as_its_draft_is(tmp_path):
    """The same rule for the style «квест» (0.5): what the checks of the text say of a draft, they say of the draft read
    back from its repository — nothing of a sound one; a copied passage and an answer in the story of a spoiled one."""
    from project_generator_lab.content.render import bundle
    from project_generator_lab.tests.content.test_style import styled_draft

    def both(draft) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        folder = tmp_path / str(len(list(tmp_path.iterdir())))
        zipfile.ZipFile(io.BytesIO(bundle(draft))).extractall(folder)
        measured = code_layer.measure(repository.read(folder))
        assert measured.untabled == []  # every quest of the generator has its line «Сдать:»
        direct = [(c.code, c.part) for c in run_checks(draft) if c.code in {"solution", "submission", "copy_paste"}]
        return sorted(direct), sorted((c.code, c.part) for c in measured.checks)

    assert both(styled_draft("quest")) == ([], [])
    spoiled = styled_draft("quest")
    passage = SENTENCE + " Потом сверь результат с соседом и запиши расхождения в общий файл проекта."
    spoiled.tasks[0].steps = [passage, "Сохрани src/task1.md"]
    spoiled.tasks[1].steps = [passage, "Сохрани src/task2.md"]
    spoiled.tasks[2].context = "Ситуация 3.\n\nПравильный ответ: роль CEO у Пети."
    direct, read_back = both(spoiled)
    assert direct == read_back == [("copy_paste", "task:2"), ("solution", "task:3")]


# An invented project in the shape of the bassin's templates (see test_repository.BASSIN), with a check-list.
QUESTS = """# D09T01. Склад

Проект познакомит с чтением файлов.

## Chapter III

## Quest 1. Count

Ты видишь терминал.

#### Получен Quest 1. Создать программу src/count.c, которая считает строки файла.

## Quest 2. Table

**Получен Quest 2.** Создать программу src/table.c.

- В src/table.c есть таблица
- Программа собирается без предупреждений

**Сдать:** src/table.c

## Bonus Quest 3. Graph

Дверь закрыта.
"""
ASKS = "sections:\n- name: Основная часть\n  questions:\n" + "".join(
    f"  - {{name: '{name}', description: 'Запусти программу и сверь вывод.', rating: '2'}}\n" for name in ("Quest 1. Count", "Quest 2. Table", "Bonus Quest 3. Graph"))


def test_a_quest_is_asked_for_its_line_and_its_question(tmp_path):
    measured = code_layer.measure(repository.read(written(tmp_path / "Q", readme=QUESTS, checklist=ASKS)))
    tasks = code_layer.as_tasks(measured.project, measured.paired)
    assert [code_layer.shape(task) for task in measured.project.readme.tasks] == ["quest", "quest", "text"]
    # The story is the situation, the line of the quest is what to do; what stands in a list right after it must come out.
    assert [(t.context, t.steps, t.expected, t.result) for t in tasks] == [
        ("Ты видишь терминал.", ["Создать программу src/count.c, которая считает строки файла."], [], ""),
        ("", ["Создать программу src/table.c."], ["В src/table.c есть таблица", "Программа собирается без предупреждений"], "src/table.c"),
        ("", ["Дверь закрыта."], [], "")]
    assert [(t.goal, t.constraints, t.transition) for t in tasks] == [("", [], "")] * 3
    # Only the list right after the line is what must come out: a list further on is of something else. Two lines «Сдать:» are both handed in.
    far = QUESTS.replace("- В src/table.c есть таблица", "Сначала прочитай листок.\n\n- В src/table.c есть таблица").replace("**Сдать:** src/table.c", "**Сдать:** src/table.c\n\nСдать: src/notes.md")
    second = code_layer.said_by(repository.parse_readme(far).tasks[1])
    assert (second.steps, second.expected, second.result) == (["Создать программу src/table.c."], [], "src/table.c; src/notes.md")
    # A quest that opens with the school's table is read by its table, as any task that has one: the line of the quest does not change that.
    tabled = repository.parse_readme(QUESTS.replace("**Получен Quest 2.**", "| Файлы | `t.c` |\n\n**Получен Quest 2.**")).tasks[1]
    assert code_layer.shape(tabled) == "table" and tabled.quest is not None
    assert (code_layer.said_by(tabled).result, code_layer.said_by(tabled).steps[0], code_layer.said_by(tabled).expected) == ("Файлы: `t.c`", "**Получен Quest 2.** Создать программу src/table.c.", [])
    # A quest whose line names the file has no line «Сдать:»: that is how the bassin writes it — not asked for, and named as unread.
    assert measured.checks == [] and measured.untabled == ["Quest 1", "Bonus Quest 3"]
    assert code_layer.row(measured)["tasks_without_table"] == ["Quest 1", "Bonus Quest 3"] and (code_layer.row(measured)["tasks"], code_layer.row(measured)["bonus_tasks"]) == (2, 1)
    # A quest whose line says nothing has nothing to do: that is asked of it as of any task.
    empty = code_layer.measure(repository.read(written(tmp_path / "E", readme=QUESTS.replace(" Создать программу src/table.c.", ""), checklist=ASKS)))
    assert [(c.severity, c.part, c.message) for c in empty.checks] == [("block", "task:2", "В задании 2 нет: «Что нужно сделать».")]
    # …and one nobody asks about has no question for the peer.
    unasked = code_layer.measure(repository.read(written(tmp_path / "U", readme=QUESTS, checklist=ASKS.replace("Quest 2. Table", "Таблица"))))
    assert [(c.severity, c.part) for c in unasked.checks] == [("block", "task:2"), ("warn", "checklist")] and "пункты для пира" in unasked.checks[0].message
    line = next(row for row in code_layer.report([measured], [], []).splitlines() if row.startswith("| У задания есть что делать"))
    assert "строка «Сдать:» квеста" in line and "«что делать» у квеста — строка «Получен Quest N.»" in line


def test_the_report_says_what_was_measured_what_was_not_and_every_finding(tmp_path):
    sound = code_layer.measure(repository.read(written(tmp_path / "LIB|1", files={"materials/model.sql": "CREATE TABLE book (id int);\n", "misc/a.png": b"\x89PNG"})))
    spoiled = code_layer.measure(repository.read(written(tmp_path / "LIB2", checklist=None, names=("README.md", "check-list.yml"),
                                                         readme=README.replace("## Chapter IV", "## Что дальше"))))
    report = code_layer.report([sound, spoiled], ["«exam» — не случай: нет meta.yaml, etalon/, inputs/ и README"], ["later"])
    lines = report.splitlines()
    assert lines[2] == "Проектов: 2. Без блокеров: 1 из 2. Блокеров всего: 3, замечаний: 0. Заданий: 6, из них формат сдачи кодом не прочитан у 2."
    assert "Отложенные случаи не измерялись (1): later. Они запускаются только перед релизом: `--release`." in lines
    assert lines[lines.index("Пропущено:") + 1] == "- «exam» — не случай: нет meta.yaml, etalon/, inputs/ и README"
    # What the layer applies and what it cannot — in every report, so that «no findings» is not read as «all checked».
    assert "| Покрытие ОР, часы заданий | нет | нужны входы проекта (строка УП) |" in lines
    assert sum(line.startswith("| ") and " | да | " in line for line in lines) == 6
    assert "| Готовый ответ во введении, правилах, аннотации | нет |" in report  # what the generator does not check is not checked here
    assert "| LIB\\|1 | 2 | 1 | 3 | номеру | 2 | 1 | 1 | 2 (1) | 0 | 0 |" in lines  # a «|» in a name does not break the table
    assert "| LIB2 | 2 | 1 | 0 | — | 2 | 0 | 1 | 0 (0) | 3 | 0 |" in lines
    first, second = report.split("## LIB|1")[1].split("## LIB2")
    assert first.strip().splitlines() == ["README: `README_RUS.md`, чек-лист: `check-list_RUS.yml`.",
                                          "- формат сдачи записан не таблицей и не блоком, кодом не проверен: Дополнительно. Задание 02", "Замечаний кода нет."]
    assert second.strip().splitlines() == [
        "README: `README.md`, чек-лист: `нет`.", "Разделы вне шаблона: «Что дальше».", "- чек-листа в репозитории нет",
        "- формат сдачи записан не таблицей и не блоком, кодом не проверен: Дополнительно. Задание 02",
        "- блокер · В задании 0 нет: пункты для пира.", "- блокер · В задании 1 нет: пункты для пира.", "- блокер · В задании 2 нет: пункты для пира."]
    # The same, as data.
    row = code_layer.row(sound)
    assert {key: row[key] for key in ("project", "tasks", "bonus_tasks", "questions", "paired_by", "tasks_without_table", "questions_with_code", "images",
                                      "files", "files_read", "blockers", "warnings", "findings")} == {
        "project": "LIB|1", "tasks": 2, "bonus_tasks": 1, "questions": 3, "paired_by": "номеру", "tasks_without_table": ["Дополнительно. Задание 02"],
        "questions_with_code": 1, "images": 1, "files": 2, "files_read": 1, "blockers": 0, "warnings": 0, "findings": []}
    assert row["numbers"] == [[0, "Задание 00"], [1, "Задание 01"], [2, "Дополнительно. Задание 02"]]
    assert code_layer.report([], [], []).splitlines()[2] == "Проектов: 0. Без блокеров: 0 из 0. Блокеров всего: 0, замечаний: 0. Заданий: 0, из них формат сдачи кодом не прочитан у 0."


def test_the_command_measures_a_folder_and_keeps_back_what_is_kept_back(tmp_path, capsys):
    root = tmp_path / "cases"
    written(root / "B1" / "B1")
    written(root / "kept" / "etalon", checklist=None)
    (root / "kept" / "meta.yaml").write_text("split: held_out\n", encoding="utf-8")
    (root / "new" / "inputs").mkdir(parents=True)
    (root / "junk").mkdir()
    (root / "bad" / "etalon").mkdir(parents=True)  # a case whose etalon holds no README
    assert main([str(root), "--out", str(tmp_path / "out")]) == 0
    printed = capsys.readouterr().out
    assert "Проектов: 1. Без блокеров: 1 из 1. Блокеров всего: 0, замечаний: 0." in printed and f"Отчёт: {tmp_path / 'out' / 'report.md'}" in printed
    data = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert [project["project"] for project in data["projects"]] == ["B1"] and data["held_out"] == ["kept"]
    assert data["passed"] == ["«junk» — не случай: нет meta.yaml, etalon/, inputs/ и README", "в каталоге «etalon» нет README_RUS.md или README.md",
                              "«new» — у случая нет готового проекта (etalon/): слою кода нечего измерять"]
    assert (tmp_path / "out" / "report.md").read_text(encoding="utf-8").count("## B1") == 1
    # Before a release the kept-back cases are measured too, under the name of the case.
    assert main([str(root), "--out", str(tmp_path / "release"), "--release"]) == 0
    data = json.loads((tmp_path / "release" / "report.json").read_text(encoding="utf-8"))
    assert [(project["project"], project["blockers"]) for project in data["projects"]] == [("B1", 0), ("kept", 3)] and data["held_out"] == []
    with pytest.raises(SystemExit) as refused:
        main([str(tmp_path / "нет такого")])
    assert refused.value.code == 2 and "нет такого» нет" in capsys.readouterr().err
    (tmp_path / "file.txt").write_text("занято", encoding="utf-8")
    with pytest.raises(SystemExit) as refused:
        main([str(root), "--out", str(tmp_path / "file.txt")])
    assert refused.value.code == 2 and "файл, а нужен каталог" in capsys.readouterr().err


def test_one_project_that_breaks_the_reader_does_not_cost_the_report_of_the_rest(tmp_path, monkeypatch, capsys):
    written(tmp_path / "root" / "a")
    written(tmp_path / "root" / "b")
    read = repository.read

    def broken(folder, *named):
        if folder.name == "a":
            raise RuntimeError("что-то не предусмотренное")
        return read(folder, *named)

    monkeypatch.setattr(repository, "read", broken)
    assert main([str(tmp_path / "root"), "--out", str(tmp_path / "out")]) == 0
    data = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert [p["project"] for p in data["projects"]] == ["b"] and data["passed"] == ["«a» — не прочитан (RuntimeError)"]
    capsys.readouterr()


def test_what_a_task_says_is_put_into_the_fields_the_checks_read(tmp_path):
    project = repository.read(written(tmp_path / "LIB"))
    first = code_layer.as_tasks(project, repository.pair(project.readme, project.checklist))[0]
    assert (first.number, first.title, first.result) == (0, "Читатели города", "Директория для загрузки решений: ex00; Файлы для загрузки: `ex00.sql`; `ex00.txt`")
    assert first.constraints == ["Разрешено — Язык: ANSI SQL", "Запрещено — Конструкции: `JOIN`"] and first.steps[0] == "Напиши запрос, который вернёт имена читателей из Казани."
    assert first.peer_checks == [project.checklist.questions[0].text] if project.checklist else False
    # The items of a list are its marked lines and the indented lines under them; what follows the list is not an item.
    assert code_layer.items("1. Первый\n   продолжение\n\nПросто текст\n2. Второй\n10. Десятый", code_layer.NUMBERED) == ["Первый\nпродолжение", "Второй", "Десятый"]
    assert code_layer.items("Вступление\n- пункт", code_layer.BULLET) == ["пункт"]
    # A task in blocks with no line of the goal has no situation either: its steps are the block.
    [plain] = repository.parse_readme("# П\n\n### Задание 1. А\n\n**Что нужно сделать**\n\n1. Сделай\n2. Сдай\n").tasks
    assert code_layer.said_by(plain) == code_layer.Said("", "", ["Сделай", "Сдай"], [], [], "", "")


def test_a_task_in_blocks_is_asked_for_every_block_it_lacks(tmp_path):
    def blocks(body: str) -> list[str]:
        readme = f"# П\n\nАннотация проекта.\n\n### Задание 1. Основное\n\n{body}\n"
        folder = written(tmp_path / str(len(list(tmp_path.iterdir()))), readme=readme, checklist="sections:\n  - name: Main\n    questions:\n      - {name: 'Задание 1', description: '- Открывается'}\n")
        return [check.message for check in code_layer.measure(repository.read(folder)).checks]

    # No line of the goal: the steps are read from the top of the block. No block — the block is missing.
    assert blocks("**Что нужно сделать**\n\n1. Сделай\n\n**Что должно получиться**\n\n- Файл\n\n**Формат сдачи**\n\nsrc/a.md") == []
    assert blocks("**Что нужно сделать**\n\n1. Сделай") == ["В задании 1 нет: «Что должно получиться», формат сдачи."]
    # A bold line that is not the generator's first block does not make the task one of blocks: it is plain text.
    assert blocks("**Важно**\n\nСделай отчёт *report.md*.") == []


def test_only_a_repeated_passage_is_gathered_and_two_pairs_are_two(tmp_path):
    other = code_layer.Check(code="checklist", severity="warn", part="checklist", message="Смотри, например: вот так.")
    assert code_layer.said([other, other]) == ["замечание · Смотри, например: вот так."] * 2
    pair = code_layer.Check(code="copy_paste", severity="warn", part="task:2", message="Задания 1 и 2 повторяют один текст, например: «а б в…».")
    modern = code_layer.Check(code="copy_paste", severity="warn", part="task:3",
                              message="В условиях заданий 2 и 3 есть значительное текстовое совпадение: «а б в…». Проверьте учебную функцию повтора.")
    assert code_layer.said([pair, modern]) == ["замечание · Значительное текстовое совпадение: «а б в…». "
                                               "Проверьте учебную функцию повтора; совпадение не доказывает дублирование заданий. — пар заданий: 2"]
    assert found(tmp_path / "F", files={"datasets/data.v2.json": "{"}) == [("block", "material:datasets/data.v2.json", "Файл «datasets/data.v2.json» не читается как json: JSONDecodeError.")]


def test_a_day_kept_in_a_working_folder_is_measured_as_a_repository_is(tmp_path):
    folder = kept(tmp_path / "day")
    measured = code_layer.measure(repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml"))
    # One question asked about two tasks is the peer's question of each: neither task is without one.
    assert measured.checks == [] and measured.paired.by == "номеру" and measured.paired.tasks_without == []
    assert measured.untabled == ["Задание 1", "Задание 2", "Задание 3"]  # «Результат:» lines are not the table or the block code reads
    assert code_layer.row(measured)["bonus_tasks"] == 1


def test_a_file_damaged_on_purpose_is_not_asked_to_read_and_any_other_is(tmp_path):
    files = {"datasets/bank.csv": "id;вопрос\n1;что, если;лишнее поле\n", "datasets/other.csv": "a,b\n1,2,3\n"}
    folder = kept(tmp_path / "day", files=files)

    def unread(**named) -> list[str]:
        measured = code_layer.measure(repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml", **named))
        return [check.part for check in measured.checks if check.code == "complete"]

    assert unread() == ["material:datasets/bank.csv", "material:datasets/other.csv"]
    assert unread(broken=["datasets/bank.csv"]) == ["material:datasets/other.csv"]
    assert unread(broken=["datasets/bank.csv", "datasets/other.csv"]) == []
    # Not asked to read as a table, it is still looked through for an answer left in it.
    (folder / "datasets" / "bank.csv").write_text("id;вопрос\n1;Правильный ответ: 42;лишнее\n", encoding="utf-8")
    measured = code_layer.measure(repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml", broken=["datasets/bank.csv", "datasets/other.csv"]))
    assert [(check.severity, check.part) for check in measured.checks] == [("block", "material:datasets/bank.csv")]


def test_a_question_left_over_is_said_to_be_left_over_in_the_way_it_was(tmp_path):
    # By number: the question names a task there is not.
    by_number = DAY_CHECKLIST.replace("Задание 3. Реставратор", "Задание 4. Реставратор")
    folder = kept(tmp_path / "numbered")
    (folder / "D1_check-list_RUS.yml").write_text(by_number, encoding="utf-8")
    said = [check.message for check in code_layer.measure(repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml")).checks]
    assert said == ["В задании 3 нет: пункты для пира.",
                    "Вопрос чек-листа «Задание 4. Реставратор» не относится ни к одному заданию README: номера задания в названии нет или задания с таким номером нет."]
    # In order: two questions of the bonus part for its one task — the second may well be about that task too.
    in_order = ("sections:\n  - name: Основная часть\n    questions:\n      - {name: 01. Разведка, description: т, rating: '5'}\n      - {name: 02. Счёт, description: т, rating: '5'}\n"
                "  - name: Бонусная часть\n    questions:\n      - {name: 03. Собрано, description: т, rating: '5'}\n      - {name: 04. Обобщено, description: т, rating: '5'}\n")
    (folder / "D1_check-list_RUS.yml").write_text(in_order, encoding="utf-8")
    measured = code_layer.measure(repository.read(folder, "README.md.docx", "D1_check-list_RUS.yml"))
    assert measured.paired.by == "порядку" and [(check.severity, check.message) for check in measured.checks] == [
        ("warn", "Вопрос чек-листа «04. Обобщено» не сопоставлен с заданием: вопросы не называют номера заданий и взяты по порядку, а этот в своей части лишний по счёту.")]


def test_the_command_measures_working_folders_by_their_meta(tmp_path, capsys):
    root = tmp_path / "runs"
    kept(root / "D1_28-09", files={"Эталоны (не грузим)/solution.py": "print(42)\n", "datasets/bank.csv": "id;текст\n1;a, b;c\n"},
         meta=META + 'skip: ["Эталоны (не грузим)"]\nbroken: ["datasets/bank.csv"]\n')
    kept(root / "D1_07-09", files={"Эталоны (не грузим)/solution.py": "print(42)\n"}, meta=META)  # what is kept from the learners was not said to be
    kept(root / "D1_14-09", meta=META + 'skip: ["Эталоны"]\n')  # a slip in a name
    kept(root / "unnamed")  # no meta.yaml: not a case
    assert main([str(root), "--out", str(tmp_path / "out")]) == 0
    data = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert data["passed"] == ["«unnamed» — не случай: нет meta.yaml, etalon/, inputs/ и README",
                              "«D1_14-09»: в meta.yaml поле skip называет «Эталоны», а «Эталоны» в «D1_14-09» нет"]
    early, late = data["projects"]
    assert (early["project"], early["blockers"], late["project"], late["blockers"]) == ("D1_07-09", 1, "D1_28-09", 0)
    assert early["findings"] == ["блокер · Имя материала «Эталоны (не грузим)/solution.py» похоже на готовый ответ. Переименуйте или уберите файл."]
    # Named in Russian, what is kept from the learners is found by its name as well.
    kept(tmp_path / "named" / "D1", files={"Эталоны (не грузим)/calc.py": "print(42)\n", "materials/решение.py": "print(42)\n", "materials/формат_ответа.md": "json\n"}, meta=META)
    assert main([str(tmp_path / "named"), "--out", str(tmp_path / "out2")]) == 0
    assert json.loads((tmp_path / "out2" / "report.json").read_text(encoding="utf-8"))["projects"][0]["findings"] == [
        "блокер · Имя материала «materials/решение.py» похоже на готовый ответ. Переименуйте или уберите файл.",
        "блокер · Имя материала «Эталоны (не грузим)/calc.py» похоже на готовый ответ. Переименуйте или уберите файл."]
    assert late["readme"] == "README.md.docx" and late["checklist"] == "D1_check-list_RUS.yml" and late["files"] == 3
    assert late["notes"][1:] == ["по meta.yaml не читались (1 файлов): «Эталоны (не грузим)»", "по meta.yaml испорчены намеренно, на читаемость не проверялись: «datasets/bank.csv»"]
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "README: `README.md.docx`, чек-лист: `D1_check-list_RUS.yml`." in report and "| Файлы CSV, JSON, YAML читаются | да | файлы до 300 КБ; файл, испорченный намеренно" in report
    capsys.readouterr()


def test_pairs_taken_in_order_are_said_to_be_a_guess_when_some_names_carry_numbers(tmp_path):
    # Two names of five number a task: not a numbering, the questions go in order — and «Задание 2» stands against task 4.
    readme = "# П\n\nАннотация.\n\n" + "".join(f"## Задание {n}. Имя {n}\n\nСделай дело номер {n} по-своему.\n\n" for n in range(1, 6))
    names = ["Общее", "Разведка", "Задание 1. Имя", "Задание 2. Имя", "Итог"]
    asked = "sections:\n  - name: Основная часть\n    questions:\n" + "".join(f"      - {{name: '{name}', description: т, rating: '5'}}\n" for name in names)
    measured = code_layer.measure(repository.read(written(tmp_path / "P", readme=readme, checklist=asked)))
    assert measured.paired.by == "порядку" and [q.name for q in measured.paired.questions[3]] == ["Задание 2. Имя"]
    assert [(check.severity, check.message) for check in measured.checks] == [("warn", (
        "Номер задания называют 2 из 5 вопросов чек-листа — не большинство (или номера заданий в README повторяются), поэтому вопросы сопоставлены с заданиями "
        "по порядку. Проверьте пары: вопрос с номером мог встать не к своему заданию."))]
    # A row of tasks is a number too: one such name among five is enough for the word of caution.
    names = ["Общее", "Разведка", "Задания 1—2", "Ещё", "Итог"]
    asked = "sections:\n  - name: Основная часть\n    questions:\n" + "".join(f"      - {{name: '{name}', description: т, rating: '5'}}\n" for name in names)
    [check] = code_layer.measure(repository.read(written(tmp_path / "row", readme=readme, checklist=asked))).checks
    assert check.message.startswith("Номер задания называют 1 из 5 вопросов")
    # No name numbers a task, or most do: nothing of the kind is said.
    for names in (["а", "б", "в", "г", "д"], [f"Задание {n}" for n in range(1, 6)]):
        asked = "sections:\n  - name: Основная часть\n    questions:\n" + "".join(f"      - {{name: '{name}', description: т, rating: '5'}}\n" for name in names)
        assert code_layer.measure(repository.read(written(tmp_path / names[0], readme=readme, checklist=asked))).checks == []
