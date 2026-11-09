"""What a material is for is written to the learner (content 0.8): the README of a legend shows the line in its table
of materials, beside a text that speaks to the learner as «ты». On the first project made by a real model
(07.10.2026) the three lines of the table were written for the methodologist: «Задаёт вариант помощника, для которого
участник формулирует промпт…»."""

import pytest
from project_generator_lab.content import checks, render, writer
from project_generator_lab.content.checks import about_the_learner, run_checks, voice
from project_generator_lab.content.edits import apply, by_its_tasks
from project_generator_lab.content.models import ContentChange
from project_generator_lab.tests.content.test_parts import propose
from project_generator_lab.tests.content.test_theory_plan import planned

# The table of the project of 07.10.2026, as the model wrote it.
WRITTEN = ["Задаёт вариант помощника, для которого участник формулирует промпт и выбирает проверочные сообщения.",
           "Даёт исходный контекст для сопоставления сообщения-триггера, правил агента и записей журнала.",
           "Служит исходным журналом JSONL для программы, расследования и независимой проверки выводов пиром."]
TO_THE_LEARNER = ["По этой карточке ты пишешь системный промпт и подбираешь проверочные сообщения.",
                  "Описание инцидента: с ним ты сверяешь сообщение-триггер, правила агента и записи журнала.",
                  "Журнал вызовов в JSONL: его разбирает твоя программа, по нему же пир проверит твои выводы.",
                  "Шаблон протокола: заполни его после встречи.", "Содержит роли команды — по ним ты распределишь задачи.",
                  "Нужен тебе для сверки итогов."]
# A plain name of the file is neither written to the learner nor about him: it is left alone — and so is a word of the
# same root that is not the learner of the project, or a noun that only ends as a verb does.
PLAIN = ["Данные о команде для заданий 1–2.", "Список участников встречи: по нему сверяются роли.", "Участники встречи и их роли.",
         "Отчёт о продажах за квартал.", "Бюджет проекта по статьям.", "Лишь образец формата.", "Роли участников, которые видит студент-новичок на схеме."]
ABOUT_THE_LEARNER = ["Участник заполняет шаблон после встречи.", "Шаблон, который студенты заполняют после встречи.", "Заполняется участником после встречи.",
                     "Журнал, который участником разбирается в задании 2.", "По нему слушатель сверит итоги.", "Ученики сравнивают по нему роли."]
ABOUT_THE_FILE = ["Содержит роли команды.", "Нужен для сверки итогов.", "Используется в заданиях 1 и 2.", "Служит образцом формата", "задаёт тон",
                  "Предназначена для проверки пиром.", "  Является исходным журналом."]


def legend(*lines, version=None, profile="legend"):
    """A whole draft of this version of the generator whose materials are said to be for this."""
    draft = planned(profile, "chapter")
    draft.content_version = writer.CONTENT_VERSION if version is None else version
    first = draft.materials[0]
    draft.materials = [first.model_copy(update={"path": f"file{i}.md", "purpose": line}) for i, line in enumerate(lines, 1)] if lines else draft.materials
    return draft


def said(draft):
    return [(c.severity, c.part, c.message) for c in voice(draft)]


def test_the_writer_of_a_task_is_asked_to_write_the_line_to_the_learner():
    frozen = writer.snapshot()
    assert writer.CONTENT_VERSION == "0.11"
    assert "purpose — одна фраза участнику на «ты»: что он делает с этим файлом" in frozen["task"]
    assert "стоит в таблице материалов README, и читает её сам участник" in frozen["task"] and "purpose — что участник с ним делает" not in frozen["task"]


def test_the_table_of_a_legend_shows_the_line_to_the_learner_and_no_other_readme_does():
    line = "По этой карточке ты пишешь системный промпт."
    assert f"| {line} | [file1.md](materials/file1.md) |" in render.readme(legend(line))
    assert line not in render.readme(legend(line, profile="quest"))


def test_the_lines_the_model_wrote_on_07_10_are_told_of_and_the_same_said_to_the_learner_are_not():
    found = said(legend(*WRITTEN))
    assert [(severity, part) for severity, part, _ in found] == [("warn", "material:file1.md"), ("warn", "material:file2.md"), ("warn", "material:file3.md")]
    assert found[0][2] == ("Пояснение к материалу «file1.md» в таблице README говорит об участнике в третьем лице: «" + WRITTEN[0] + "». "
                           "Эту строку читает сам участник — напишите, что он делает с файлом, обращаясь к нему: «По этому файлу ты…».")
    assert all("описывает роль файла в проекте, а не то, что с ним делает участник: «" in message for _, _, message in found[1:])
    assert said(legend(*TO_THE_LEARNER)) == [] and said(legend(*PLAIN)) == []
    assert [c.code for c in run_checks(legend(*WRITTEN)) if c.code == "voice"] == ["voice"] * 3


@pytest.mark.parametrize("line", ABOUT_THE_LEARNER)
def test_a_line_about_the_learner_as_a_third_person_is_told_of(line):
    assert about_the_learner(line, "участник") and [message.split(" README ")[1].split(":")[0] for _, _, message in said(legend(line))] == ["говорит об участнике в третьем лице"]


@pytest.mark.parametrize("line", ABOUT_THE_FILE)
def test_a_line_about_the_role_of_the_file_is_told_of_unless_it_speaks_to_the_learner(line):
    assert not about_the_learner(line, "участник") and len(said(legend(line))) == 1
    assert said(legend(line.rstrip(". ") + " — по нему ты сверяешь итоги.")) == []


@pytest.mark.parametrize("line", [*TO_THE_LEARNER, *PLAIN])
def test_a_line_to_the_learner_or_a_plain_name_is_left_alone(line):
    assert said(legend(line)) == []


def test_the_word_of_the_project_for_its_learner_is_known():
    draft = legend("Курсант заполняет шаблон после встречи.", "Шаблон, который заполняет курсант.")
    assert said(draft) == []
    draft.handoff = draft.handoff.model_copy(update={"learner_term": " Курсант "})
    assert [part for _, part, _ in said(draft)] == ["material:file1.md"]
    assert about_the_learner("курсант сверяет", "курсант") and not about_the_learner("курсант сверяет", "") and not about_the_learner("", "участник")


def test_a_long_line_is_cut_in_the_message():
    [(_, _, message)] = said(legend("Служит " + "очень " * 60 + "длинным пояснением."))
    assert len(message.split("«")[2].split("»")[0]) == 160


def test_the_line_is_looked_at_only_where_the_readme_shows_it_and_the_writer_was_asked():
    assert len(said(legend(*WRITTEN))) == 3
    assert said(legend(*WRITTEN, profile="quest")) == []                       # a quest has no table of materials
    for version in ("0.7", "0.6", "0.5", ""):
        assert said(legend(*WRITTEN, version=version)) == [], version          # written by the prompt of its time
        assert not [c for c in run_checks(legend(*WRITTEN, version=version), final=True) if c.code == "voice"]
    for version in ("0.8", "0.10", "1.0"):
        assert len(said(legend(*WRITTEN, version=version))) == 3, version
    plain = legend(*WRITTEN)
    plain.style = None                                                          # begun before the styles
    assert said(plain) == []
    # The lines of the stand-in model the other tests are written with are not told of: nothing is added to their checks.
    assert said(legend()) == [] and said(planned("legend", "both")) == []


def test_a_material_added_in_the_chat_is_named_by_its_tasks_not_by_the_reason_of_the_change():
    """The reason of a change is written to the methodologist — «Критик: пиру нужен протокол» — and stood in the table
    of the README as what the file is for."""
    assert [by_its_tasks(numbers) for numbers in ([], [2], [1, 3])] == ["Материал проекта", "Материал к заданию 2", "Материал к заданиям 1, 3"]
    for version, line in ((writer.CONTENT_VERSION, "Материал к заданию 2"), ("0.7", "Критик: пиру нужен протокол встречи")):
        draft = legend(version=version)
        added = propose(draft, ContentChange(part="material:protocol.md", field="content", value="# Протокол\n",
                                             reason="Критик: пиру нужен протокол встречи", task_numbers=[2]))
        apply(draft, added)
        material = draft.material("protocol.md")
        assert material is not None and (material.purpose, material.contains, material.tasks) == (line, "Критик: пиру нужен протокол встречи", [2]), version
        assert f"| {line} | [protocol.md](materials/protocol.md) |" in render.readme(draft)
        assert not [c for c in voice(draft) if c.part == "material:protocol.md"]
    assert checks.ROLE.match("Служит") and not checks.ROLE.match("Служитель") and not checks.TO_READER.search("лишь тишь")
