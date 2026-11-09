"""Patches, mechanical checks, route states and code assembly of the content draft, without HTTP."""

import ast
from pathlib import Path
from typing import cast

import pytest
import yaml
from fastapi import HTTPException
from project_generator_lab.content import render
from project_generator_lab.content.checks import blocking, parses, run_checks
from project_generator_lab.content.edits import apply, build_patches, normalize, parse_link, revert
from project_generator_lab.content.handoff import card
from project_generator_lab.content.models import (
    ContentChange,
    ContentDraft,
    ContentMessage,
    Frame,
    Link,
    Material,
    MaterialNeed,
    Rationale,
    Review,
    Summary,
    TaskContent,
    TaskDraft,
    Theory,
    TheoryTopic,
)
from project_generator_lab.content.stages import next_step, numbers
from project_generator_lab.content.writer import parts
from project_generator_lab.tests.content.fixtures import TASK_TEXT, handoff_fixture

PACKAGE = Path(__file__).resolve().parents[2]
SOURCES = ["https://docs.example.org/1", "https://docs.example.org/2", "https://docs.example.org/3"]


def task(number: int, status: str = "approved", **fields) -> TaskContent:
    base = {"number": number, "title": TASK_TEXT[number][0], "goal": f"Цель {number}", "context": f"Ситуация {number}.",
            "steps": [f"Сделай шаг задания {number}", f"Сохрани src/task{number}.md"], "expected": [f"В src/task{number}.md есть решение"],
            "result": f"src/task{number}.md",
            "peer_checks": [f"Файл {number} открывается"], "needs": [f"Понятие {number}"],
            "rationale": Rationale(concept="Пункт замысла", evidence="Сдача видна", choice="Формат", peer="Пир открывает файл"), "status": status}
    return TaskContent(**{**base, **fields})


def written(draft: ContentDraft, number: int) -> TaskContent:
    found = draft.task(number)
    assert found is not None
    return found


def stored(draft: ContentDraft, path: str) -> Material:
    found = draft.material(path)
    assert found is not None
    return found


def theory_of(draft: ContentDraft) -> Theory:
    assert draft.theory is not None
    return draft.theory


def complete_draft() -> ContentDraft:
    draft = ContentDraft(
        id="d1", owner_id="local", content_version="0.3", handoff=handoff_fixture("d1"), prompt_snapshots={},
        summary=Summary(title="Проект", annotation="Аннотация проекта для участника.", status="approved"),
        tasks=[task(1), task(2), task(3)],
        theory=Theory(status="approved", topics=[
            TheoryTopic(number=n, title=f"Тема {n}", text=f"Направление к заданию {n}, без ответа.", tasks=[n],
                        question=f"Что из темы {n} пригодится в задании?",
                        links=[Link(title=f"Источник {n}", url=SOURCES[n - 1])]) for n in (1, 2, 3)]),
        frame=Frame(introduction="Зачем проект. Ситуация: ты новый продакт в команде.", instruction=["Обязательно: таблицы."],
                    conclusion="Ты прошёл путь от ролей до бэклога.", status="approved"),
        materials=[Material(path="team.csv", kind="data", purpose="Данные команды", contains="Роли", tasks=[1],
                            content="role,person\nCEO,Петя\n", status="approved")],
        sources=list(SOURCES),
    )
    draft.review = Review(digest=draft.digest(), summary="Замечаний нет.")
    return draft


def propose(draft: ContentDraft, *changes: ContentChange, typed: str = "") -> ContentMessage:
    patches, notes = build_patches(draft, list(changes), typed=typed)
    message = ContentMessage(role="assistant", text="Предлагаю", patches=patches, status="pending" if patches else None, notes=notes)
    draft.messages.append(message)
    return message


# ---------- handoff card ----------

def test_card_shows_what_was_handed_over_and_what_is_missing():
    rows = {row["label"]: row for row in card(handoff_fixture())}
    assert rows["Задания"]["value"] == "3 основных · 12 ч из 12 ч"
    assert rows["Требования заказчика"]["ok"] is True and rows["ОР программы"]["ok"] is False
    assert rows["Замечания проверяющего"]["items"] == ["Задание 3 без явного критерия приоритета."]


# ---------- patches ----------

def test_patch_keeps_approval_and_refuses_stale_values():
    draft = complete_draft()
    message = propose(draft, ContentChange(part="task:2", field="steps", value="- Первый шаг\n- Второй шаг", reason="Короче"))
    assert message.patches[0].after == ["Первый шаг", "Второй шаг"] and message.patches[0].label == "Задание 2 · Что нужно сделать"
    apply(draft, message)
    assert written(draft, 2).steps == ["Первый шаг", "Второй шаг"] and written(draft, 2).status == "approved"
    stale = propose(draft, ContentChange(part="task:2", field="goal", value="Новая цель", reason="Точнее"))
    written(draft, 2).goal = "Изменено руками"
    with pytest.raises(HTTPException) as error:
        apply(draft, stale)
    assert error.value.status_code == 409 and written(draft, 2).goal == "Изменено руками"


@pytest.mark.parametrize("part, field, label", [
    ("summary", "annotation", "Название и аннотация · Аннотация"), ("topic:2", "question", "Раздел 2.2 · Вопрос к практике"),
    ("topic:2", "text", "Раздел 2.2 · Текст"), ("frame", "conclusion", "Введение, инструкция, заключение · Заключение"),
    ("material:team.csv", "purpose", "Материал team.csv · Зачем участнику"),
])
def test_every_part_can_be_edited_by_patch(part, field, label):
    draft = complete_draft()
    message = propose(draft, ContentChange(part=part, field=field, value="Новый текст этой части.", reason="Просили"))
    assert message.patches[0].label == label
    apply(draft, message)
    patched = {"summary": draft.summary, "topic:2": theory_of(draft).topic(2), "frame": draft.frame,
               "material:team.csv": draft.material("team.csv")}[part]
    assert getattr(patched, field) == "Новый текст этой части."


def test_unknown_parts_fields_and_empty_values_become_notes_not_patches():
    draft = complete_draft()
    message = propose(draft,
                      ContentChange(part="task:9", field="goal", value="x", reason="r"),
                      ContentChange(part="task:1", field="hours", value="5", reason="r"),
                      ContentChange(part="task:1", field="result", value="  ", reason="r"),
                      ContentChange(part="material:../secret.csv", field="content", value="a", reason="r"),
                      ContentChange(part="material:new.csv", field="content", value="a,b\n", reason="r"),
                      ContentChange(part="topic:7", field="text", value="Новое", reason="r"))
    assert message.patches == [] and len(message.notes) == 6


def test_a_skipped_change_does_not_stop_the_following_ones():
    draft = complete_draft()
    message = propose(draft,
                      ContentChange(part="task:1", field="goal", value="Цель 1", reason="то же"),
                      ContentChange(part="material:new.md", field="purpose", value="Зачем", reason="нет такого файла"),
                      ContentChange(part="material:new.csv", field="content", value="a\n", reason="нет задания", task_numbers=[9]),
                      ContentChange(part="task:2", field="goal", value="Новая цель", reason="точнее"))
    assert [(p.part, p.field) for p in message.patches] == [("task:2", "goal")]
    assert len(message.notes) == 3 and "уже содержит" in message.notes[0] and "ещё не написано" in message.notes[1]


def test_a_refused_change_does_not_stop_the_ones_after_it():
    draft = complete_draft()
    message = propose(draft,
                      ContentChange(part="topic:1", field="links", value=["Блог — https://invented.example/post"], reason="выдумана"),
                      ContentChange(part="topic:2", field="links", value=["Просто текст без адреса"], reason="без адреса"),
                      ContentChange(part="summary", field="annotation", value="Коротко", reason="короче 20 знаков"),
                      ContentChange(part="task:2", field="goal", value="Новая цель", reason="точнее"))
    assert [(p.part, p.field) for p in message.patches] == [("task:2", "goal")] and len(message.notes) == 3


def test_optional_texts_can_be_emptied_but_required_ones_cannot():
    draft = complete_draft()
    message = propose(draft,
                      ContentChange(part="task:1", field="context", value="", reason="не нужно"),
                      ContentChange(part="topic:1", field="links", value=[], reason="не нужно"),
                      ContentChange(part="topic:1", field="question", value="", reason="не нужно"),
                      ContentChange(part="task:1", field="transition", value="", reason="пусто уже"),
                      ContentChange(part="task:1", field="goal", value="", reason="не нужно"),
                      ContentChange(part="task:1", field="expected", value=[], reason="не нужно"),
                      ContentChange(part="frame", field="introduction", value="", reason="не нужно"))
    assert [p.field for p in message.patches] == ["context", "links", "question"]
    assert sum("не может быть пустым" in note for note in message.notes) == 3


def test_links_come_from_the_search_or_from_the_methodologist():
    draft = complete_draft()
    invented = propose(draft, ContentChange(part="topic:1", field="links", value=["Источник 1 — https://docs.example.org/1",
                                                                                  "Блог — https://invented.example/post"], reason="Больше"))
    assert invented.patches == [] and "invented.example" in invented.notes[0]
    no_url = propose(draft, ContentChange(part="topic:1", field="links", value=["Просто текст без адреса"], reason="r"))
    assert no_url.patches == [] and "нет адреса" in no_url.notes[0]
    from_search = propose(draft, ContentChange(part="topic:1", field="links", value=["Источник 2 — https://docs.example.org/2 — про встречи"], reason="r"))
    typed = propose(draft, ContentChange(part="topic:2", field="links", value=["Мой гайд — https://mine.example/guide"], reason="r"),
                    typed="добавь https://mine.example/guide к теме 2")
    apply(draft, from_search)
    apply(draft, typed)
    assert theory_of(draft).topics[0].links == [Link(title="Источник 2", url="https://docs.example.org/2", why="про встречи")]
    assert [c.code for c in run_checks(draft) if c.code == "links"] == ["links"]


@pytest.mark.parametrize("line, expected", [
    ("Документация — https://a.example/x — раздел 2", Link(title="Документация", url="https://a.example/x", why="раздел 2")),
    ("https://a.example/y.", Link(title="https://a.example/y", url="https://a.example/y")),
    ("[Руководство](https://a.example/z) — глава 3", Link(title="Руководство", url="https://a.example/z", why="глава 3")),
])
def test_link_lines_are_parsed(line, expected):
    assert parse_link(line) == expected


@pytest.mark.parametrize("line", [
    "Документация — https://a.example/x — раздел 2", "https://a.example/y.", "[Руководство](https://a.example/z) — глава 3",
    "https://a.example/w — зачем", "Python — справочник — https://a.example/p", "- Статья — https://a.example/s",
])
def test_a_link_line_read_back_from_the_draft_is_the_same_line(line):
    """Whatever form a link is typed in, the stored link prints one line, and that line parses back to the same link."""
    once = normalize("links", [line])
    assert normalize("links", once) == once and parse_link(once[0]) == parse_link(line)


TITLES = ["", "Статья", "*args", "Зеркало https://mirror.example.org/a", "Док (англ.)"]
ADDRESSES = ["https://docs.example.org/1", "https://ru.wikipedia.org/wiki/P_(язык)", "https://docs.example.org/a_(b_(c))"]
WHYS = ["", "глава 3", "(англ.)", "— зачем —"]
FORMS = ["{t} — {u} — {w}", "[{t}]({u}) — {w}", "[{t}]({u}){w}", "{t} ({u}).{w}", "{t} {u}.", "[{t}]({u}", "{t} — {u} — [y](https://docs.example.org/8)"]


def test_every_generated_link_line_settles_on_one_link_with_its_own_address():
    """The property apply, revert and the staleness check rely on: a line is read into a link once, the link prints a
    line, and that line reads back into the same link. Each line also keeps the address it was written with."""
    for form in FORMS:
        for title in TITLES:
            for url in ADDRESSES:
                for why in WHYS:
                    if "http" in title and not form.startswith("["):
                        continue  # in plain text the first address is the link: an address in a title needs Markdown
                    line = form.format(t=title or "Без названия", u=url, w=why)
                    link = parse_link(line)
                    assert link is not None and link.url == url, (line, link)
                    assert parse_link(link.line()) == link, (line, link.line())
                    assert normalize("links", normalize("links", [line])) == normalize("links", [line])


@pytest.mark.parametrize("line, expected", [
    ("[Python](https://ru.wikipedia.org/wiki/Python_(язык)) — статья", Link(title="Python", url="https://ru.wikipedia.org/wiki/Python_(язык)", why="статья")),
    ("[Руководство](https://docs.example.org/9)(англ.)", Link(title="Руководство", url="https://docs.example.org/9", why="(англ.)")),
    ("[Руководство](https://docs.example.org/9)—глава (англ.)", Link(title="Руководство", url="https://docs.example.org/9", why="глава (англ.)")),
    ("[x](https://docs.example.org/7) [y](https://docs.example.org/8)", Link(title="x", url="https://docs.example.org/7", why="[y](https://docs.example.org/8)")),
    ("x — https://docs.example.org/7 — [y](https://docs.example.org/8)", Link(title="x", url="https://docs.example.org/7", why="[y](https://docs.example.org/8)")),
    ("[Зеркало https://mirror.example.org/a](https://docs.example.org/1)", Link(title="Зеркало", url="https://docs.example.org/1")),
    ("[Без скобки](https://docs.example.org/1", Link(title="Без скобки", url="https://docs.example.org/1")),
    ("Статья — https://docs.example.org/1 — зачем —", Link(title="Статья", url="https://docs.example.org/1", why="зачем")),
    ("https://docs.example.org/a_(b_(c)).", Link(title="https://docs.example.org/a_(b_(c))", url="https://docs.example.org/a_(b_(c))")),
])
def test_tricky_link_lines(line, expected):
    assert parse_link(line) == expected


def test_a_link_prints_its_title_without_addresses_and_a_title_less_link_once():
    assert Link(title="Зеркало https://mirror.example.org/a", url=SOURCES[0]).line() == f"Зеркало — {SOURCES[0]}"
    assert Link(title="https://mirror.example.org/a", url=SOURCES[0]).line() == SOURCES[0]
    assert Link(title=SOURCES[0], url=SOURCES[0], why="зачем").line() == f"{SOURCES[0]} — зачем"
    # Links typed as one text are split into lines; a material given as lines is one file.
    assert normalize("links", f"А — {SOURCES[0]}\nБ — {SOURCES[1]}") == [f"А — {SOURCES[0]}", f"Б — {SOURCES[1]}"]
    assert normalize("content", ["a,b", "1,2"]) == "a,b\n1,2\n"


def test_a_link_is_proposed_in_the_form_it_is_stored_so_it_can_be_reverted():
    """Boundary: a Markdown link and an address with a trailing dot. Before the fix the patch kept the line as typed,
    the draft read it back as «title — url», and the revert was refused as «изменено позже»."""
    draft = complete_draft()
    message = propose(draft, ContentChange(part="topic:1", field="links", reason="r", value=[
        "Источник 1 — https://docs.example.org/1", "[Источник 2](https://docs.example.org/2) — про встречи", "https://docs.example.org/3."]))
    assert message.patches[0].after == ["Источник 1 — https://docs.example.org/1", "Источник 2 — https://docs.example.org/2 — про встречи",
                                        "https://docs.example.org/3"]
    apply(draft, message)
    assert [link.line() for link in theory_of(draft).topics[0].links] == message.patches[0].after
    revert(draft, message)
    assert theory_of(draft).topics[0].links == [Link(title="Источник 1", url="https://docs.example.org/1")] and message.status == "pending"
    # Retention: the same value typed by hand in the stored form is «already there», not a new patch.
    apply(draft, message)
    again = propose(draft, ContentChange(part="topic:1", field="links", value=message.patches[0].after, reason="r"))
    assert again.patches == [] and "уже содержит" in again.notes[0]


@pytest.mark.parametrize("typed, stored", [
    ("[Python](https://ru.wikipedia.org/wiki/Python_(язык))", Link(title="Python", url="https://ru.wikipedia.org/wiki/Python_(язык)")),
    ("[Доклад](https://docs.example.org/4.)", Link(title="Доклад", url="https://docs.example.org/4")),
    ("Статья (https://docs.example.org/5).", Link(title="Статья", url="https://docs.example.org/5")),
])
def test_an_address_with_brackets_or_a_final_dot_is_stored_whole_and_the_edit_can_be_reverted(typed, stored):
    # Independent review 01.10.2026: the Markdown form cut a Wikipedia address at its bracket, and a final dot made
    # the stored link differ from the proposal, so the revert was refused.
    draft = complete_draft()
    message = propose(draft, ContentChange(part="topic:1", field="links", reason="r", value=[f"Источник 1 — {SOURCES[0]}", typed]),
                      typed=f"добавь {typed}")
    apply(draft, message)
    assert theory_of(draft).topics[0].links[1] == stored
    revert(draft, message)
    assert message.status == "pending" and len(theory_of(draft).topics[0].links) == 1


def test_links_kept_by_an_earlier_version_are_not_proposed_again_nor_taken_for_invented_ones():
    draft = complete_draft()
    # As the link search may have stored them: an address inside the title, a title starting with «*»; and a link the
    # methodologist typed once (not among the search's sources), stored with a title that does not read back as is.
    mine = "https://mine.example/guide"
    theory_of(draft).topics[0].links = [Link(title="Зеркало https://mirror.example.org/a", url=SOURCES[0]), Link(title="*args", url=SOURCES[1]),
                                        Link(title="*Мой гайд", url=mine)]
    lines = [link.line() for link in theory_of(draft).topics[0].links]
    echo = propose(draft, ContentChange(part="topic:1", field="links", reason="r", value=lines))
    assert echo.patches == [] and "уже содержит" in echo.notes[0]
    more = propose(draft, ContentChange(part="topic:1", field="links", reason="r", value=[*lines, f"Новый — {SOURCES[2]}"]))
    assert more.patches, more.notes  # the kept links are not checked again as new ones
    apply(draft, more)
    # The address of each kept link stays its own; the mirror in a title is not taken for the link.
    assert [link.url for link in theory_of(draft).topics[0].links] == [SOURCES[0], SOURCES[1], mine, SOURCES[2]]


def test_a_proposal_that_can_no_longer_be_applied_is_withdrawn_when_the_draft_is_opened(monkeypatch):
    from project_generator_lab.content import api, store

    draft = complete_draft()
    stale = propose(draft, ContentChange(part="task:1", field="goal", value="Новая цель", reason="r"))
    stale.patches[0].before = "Цель, как её записала прошлая версия"  # e.g. a link line printed the old way
    monkeypatch.setattr(store, "load", lambda key: draft)
    shown = api.view(api.loaded("d1"), "d1")
    assert stale.status == "rejected" and "предложение снято" in stale.notes[-1]
    assert cast(dict, shown["next_step"])["action"] != "apply_pending"


def test_new_material_is_added_and_can_be_reverted_and_removal_is_explicit():
    draft = complete_draft()
    added = propose(draft, ContentChange(part="material:materials/report_template.md", field="content", value="# Отчёт\n",
                                         reason="Шаблон отчёта", task_numbers=[2]))
    assert added.patches[0].part == "material:report_template.md" and added.patches[0].before == ""
    apply(draft, added)
    assert stored(draft, "report_template.md").tasks == [2] and stored(draft, "report_template.md").status == "draft"
    revert(draft, added)
    assert draft.material("report_template.md") is None and added.status == "pending"
    removed = propose(draft, ContentChange(part="material:team.csv", field="content", value="", reason="Не нужен"))
    apply(draft, removed)
    assert draft.material("team.csv") is None
    with pytest.raises(HTTPException) as error:
        revert(draft, removed)
    assert error.value.status_code == 409


def test_material_content_keeps_its_whitespace():
    draft = complete_draft()
    yaml_file = propose(draft, ContentChange(part="material:config.yaml", field="content", value="  a: 1\n  b: 2", reason="образец", task_numbers=[1]))
    apply(draft, yaml_file)
    reloaded = ContentDraft.model_validate_json(draft.model_dump_json())
    assert stored(reloaded, "config.yaml").content == "  a: 1\n  b: 2\n"


def test_revert_states_and_status_codes():
    draft = complete_draft()
    message = propose(draft, ContentChange(part="task:1", field="goal", value="Новая цель", reason="точнее"))
    with pytest.raises(HTTPException) as nothing:
        revert(draft, message)
    assert nothing.value.status_code == 409
    message.status = "rejected"
    revert(draft, message)
    assert message.status == "pending" and written(draft, 1).goal == "Цель 1"
    apply(draft, message)
    written(draft, 1).goal = "Ещё новее"
    with pytest.raises(HTTPException) as later:
        revert(draft, message)
    assert later.value.status_code == 409 and message.status == "applied"
    written(draft, 1).goal = "Новая цель"
    revert(draft, message)
    assert written(draft, 1).goal == "Цель 1" and message.status == "pending"


def test_methodologist_values_are_normalized_to_the_field_type():
    draft = complete_draft()
    message = propose(draft, ContentChange(part="task:1", field="steps", value=["Один шаг"], reason="короче"),
                      ContentChange(part="task:1", field="goal", value="Цель", reason="короче"))
    apply(draft, message, {0: "1. Первый\n\n- Второй\n", 1: ["Цель", "в двух строках"]})
    assert written(draft, 1).steps == ["Первый", "Второй"] and written(draft, 1).goal == "Цель\nв двух строках"
    assert message.patches[0].edited and message.patches[1].edited


# ---------- checks ----------

def test_complete_draft_passes_every_check():
    assert run_checks(complete_draft(), final=True) == []


@pytest.mark.parametrize("path, content", [
    ("answers.csv", "a,b\n1,2\n"),
    ("team.csv", "role,person\nCEO,Правильный ответ: отвечает за сроки\n"),
    ("keys/roles.csv", "a,b\n"),
])
def test_solution_in_materials_blocks_assembly(path, content):
    draft = complete_draft()
    draft.materials = [Material(path=path, kind="data", purpose="Данные", contains="Роли", tasks=[1], content=content, status="approved")]
    assert [c.code for c in blocking(draft)] == ["solution"]


def test_names_that_merely_contain_key_or_result_are_fine():
    draft = complete_draft()
    draft.materials = [Material(path=name, kind="template", purpose="Шаблон", contains="Колонки", tasks=[1], content="a,b\n", status="approved")
                       for name in ("results_template.csv", "keyboard_layout.csv", "monkey_data.csv")]
    assert blocking(draft) == []


def test_solution_markers_in_tasks_and_theory_block():
    draft = complete_draft()
    draft.tasks[0] = task(1, context="Эталонный ответ: CEO отвечает за сроки.")
    theory_of(draft).topics[1].text = "Правильный ответ: встречу ведёт продакт."
    assert sorted((c.code, c.part) for c in blocking(draft)) == [("solution", "task:1"), ("solution", "topic:2")]


def test_task_without_submission_or_peer_points_blocks():
    draft = complete_draft()
    draft.tasks[1] = task(2, result=" ", peer_checks=[])
    [check] = [c for c in blocking(draft) if c.code == "submission"]
    assert check.part == "task:2" and "формат сдачи" in check.message and "пункты для пира" in check.message


def test_outcomes_and_hours_are_warnings():
    draft = complete_draft()
    draft.handoff.tasks[2].outcome_numbers = [1]
    draft.handoff.hours = 20
    warnings = [c for c in run_checks(draft) if c.code == "coverage"]
    assert len(warnings) == 2 and all(c.severity == "warn" for c in warnings)
    assert "«Декомпозировать фичу»" in warnings[0].message


def test_theory_gaps_are_warnings():
    draft = complete_draft()
    theory_of(draft).topics[2].tasks = [2]
    theory_of(draft).topics[0].links = []
    found = sorted((c.part, c.severity) for c in run_checks(draft) if c.code == "theory")
    assert found == [("task:3", "warn"), ("topic:1", "warn")]


def test_copy_paste_between_tasks_is_warned_but_short_common_phrases_are_not():
    draft = complete_draft()
    shared = "Сохрани итоговую таблицу в папке src под именем roles и проверь что она открывается на компьютере пира"
    draft.tasks[0] = task(1, context=shared)
    assert [c for c in run_checks(draft) if c.code == "copy_paste"] == []
    draft.tasks[2] = task(3, context=shared)
    assert [c for c in run_checks(draft) if c.code == "copy_paste"] == []
    # Repeating scene context is intentional; substantial overlap in the actual work is still reported.
    duplicate = shared + " Затем сравни значения каждой строки с исходными данными и объясни расхождения."
    draft.tasks[0].steps = [duplicate]
    draft.tasks[2].steps = [duplicate]
    [check] = [c for c in run_checks(draft) if c.code == "copy_paste"]
    assert check.severity == "warn" and "1 и 3" in check.message


def test_peer_points_in_one_pattern_are_not_copy_paste():
    draft = complete_draft()
    pattern = "Попроси рассказать, как принималось решение и какие варианты были отброшены по ходу работы над заданием"
    draft.tasks = [task(n, peer_checks=[pattern]) for n in (1, 2, 3)]
    assert [c for c in run_checks(draft) if c.code == "copy_paste"] == []


def test_the_way_the_work_is_handed_in_is_the_same_in_every_task_and_is_not_copy_paste():
    """Decided 05.10.2026: the learning is peer to peer, the work of every task is handed in the same way. Nine tested
    projects of fifteen repeated a passage between tasks."""
    draft = complete_draft()
    same = "Положи файл с решением в папку src своего репозитория в ветке develop и покажи его пиру на встрече при проверке"
    draft.tasks = [task(n, result=same) for n in (1, 2, 3)]
    assert [c for c in run_checks(draft) if c.code == "copy_paste"] == []
    # What is to be done is still compared, and so is what has to come out.
    draft.tasks = [task(n, result=same, expected=[same]) for n in (1, 2, 3)]
    assert sorted(c.part for c in run_checks(draft) if c.code == "copy_paste") == ["task:2", "task:3", "task:3"]


def test_the_one_right_result_may_be_shown_to_the_peer_and_not_to_the_learner():
    """Decided 05.10.2026: a task with one right result shows it in the check-list, as the tested projects of the
    school do — 103 questions of 117 there hold the reference script. The README still may not."""
    draft = complete_draft()
    draft.tasks[0] = task(1, peer_checks=["Результат совпадает с эталоном. Эталонное решение: SELECT name FROM person;", "Попроси рассказать, как составлен запрос"])
    assert [c for c in run_checks(draft, final=True) if c.code == "solution"] == []
    for shown in (task(1, steps=["Эталонное решение: SELECT name FROM person;"]), task(1, expected=["Правильный ответ: 42"]), task(1, context="Ключ ответов лежит рядом.")):
        spoiled = complete_draft()
        spoiled.tasks[0] = shown
        assert [(c.severity, c.part) for c in run_checks(spoiled) if c.code == "solution"] == [("block", "task:1")]
    # A note to oneself left in a point for the peer is still a note left in the text.
    draft.tasks[0] = task(1, peer_checks=["TODO: дописать вопрос"])
    assert [c.message for c in run_checks(draft, final=True) if "заглушка" in c.message] == ["Осталась заглушка «TODO» (task:1)."]


def test_the_rule_for_the_writer_says_when_the_reference_goes_to_the_peer():
    from project_generator_lab.content.writer import CONTENT_VERSION, snapshot

    lines = snapshot()["task"].splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("- peer_checks"))
    # The rule of the points for the peer and what stands under it, up to the next field.
    rule = "\n".join([lines[start], *(line for line in lines[start + 1:next(i for i in range(start + 1, len(lines)) if lines[i].startswith("- "))])])
    assert "Если у задания один верный результат" in rule and "Если верных решений много, эталон не пиши" in rule
    assert "Без эталонных ответов" not in rule and CONTENT_VERSION == "0.11"
    # What the learner reads stays free of reference values — but for the examples, which show the form of a result.
    expected = next(line for line in lines if line.startswith("- expected"))
    assert "Без эталонных значений: значения показывают только examples" in expected
    # Decided 06.10.2026 on the good run of the intensive: two to four points, a command with its output, an action
    # before the one who checks, a value that is not among the examples, and nothing the task did not say.
    for said in ("2–4 пункта", "команда и ожидаемый вывод", "действие при проверяющем", "которого нет в примерах",
                 "Пункт не вводит требований, которых нет в тексте задания"):
        assert said in rule, said


@pytest.mark.parametrize("path, content, reason", [
    ("data.json", '{"a": 1,}', "json"), ("data.yaml", "a: [1, 2", "yaml"), ("data.csv", "a,b\n1,2,3\n", "csv"),
])
def test_completeness_blocks_unreadable_materials(path, content, reason):
    draft = complete_draft()
    draft.materials = [Material(path=path, kind="data", purpose="Данные", contains="Строки", tasks=[1], content=content, status="approved")]
    [check] = blocking(draft)
    assert check.code == "complete" and reason in check.message


@pytest.mark.parametrize("content, reads", [
    # A table saved by a Russian Excel: «;» between the columns, commas inside the sentences.
    ("id;вопрос;ответ\n1;Что, если нет?;да\n2;Как, почему и зачем?;нет\n", True),
    ("id;вопрос;ответ\n1;Что, если; нет?;да\n", False),  # a «;» too many is a column too many
    ("id\tвопрос\n1\tЧто, если; нет?\n", True), ("id\tвопрос\n1\tЧто,\tесли\n", False),
    ("a,b\n1,2\n", True), ("a,b\n1,2,3\n", False), ("a,b\n\n1,2\n", True), ("один столбец\nвторая строка\n", True), ("", True),
    ('a,b\n"1,5",2\n', True), ('a;b\n"x;y";2, 3\n', True),  # the mark inside quotes is a letter of the cell
    ("a;b,c\n1;2\n", True), ("a,b;c;d\n1;2;3\n", True),  # a comma in one cell of a table of «;»
    # What read before reads now: commas are tried first, and the first row is not asked which mark is the table's.
    ("SELECT 1; SELECT 2\nSELECT 3\n", True), ("for(i=0;i<n;i++),цикл\nx=1,присваивание\n", True), ("a\tb\tc,d\n1,2\n", True),
    # Another mark saves a table only when it cuts every row into the same two columns or more.
    ("a,b\nc\n", False), ("a,b\n1;2\n", False), ("a,b\n1;2;3\n", False),
    # Not caught, as it never was: rows of «;» that differ, with no comma in them, are one column of text to the comma.
    ("id;вопрос\n1;a;b\n", True),
])
def test_a_table_reads_when_some_mark_cuts_its_rows_evenly(content, reads):
    assert (parses(Material(path="data.csv", kind="data", purpose="Данные", contains="Строки", content=content)) == "") is reads


def test_a_quote_that_never_closes_under_another_mark_is_not_an_error_of_the_table():
    # Under «;» the quote in the third row opens a cell that swallows the rest of the file and outgrows what a cell
    # may be. The table is judged by its commas, as before: its rows differ — and, without the row of three, do not.
    rest = "x;\"y,1\n" + "1,2\n" * 40_000
    assert parses(Material(path="data.csv", kind="data", purpose="Данные", contains="Строки", content="a,b\n1,2,3\n" + rest)) == "в строках CSV разное число колонок"
    assert parses(Material(path="data.csv", kind="data", purpose="Данные", contains="Строки", content="a,b\n" + rest)) == ""


@pytest.mark.parametrize("path, announced", [
    # What the authors mark the closed part of a project with.
    ("Эталоны (не грузим)/calc.py", True), ("tests (не грузим)/calc.py", True), ("НЕ ГРУЗИТЬ! Что делают пиры.docx", True), ("test (не для пиров)/a.py", True),
    ("Не для выдачи пирам/a.py", True), ("materials/эталонный_вывод.txt", True), ("не_грузим/a.txt", True), ("не  для\tпиров.md", True),
    # A file whose whole name is the word.
    ("materials/решение.py", True), ("Решения.md", True), ("ответы.txt", True), ("materials/ответ.md", True), ("src/решение_2.py", True), ("ответы 12.tar.gz", True),
    ("решение", True),
    # The same words inside a name are everyday ones.
    ("materials/формат_ответа.md", False), ("materials/пример_ответа_api.json", False), ("materials/дерево_решений.py", False), ("вопросы_и_ответы.md", False),
    ("materials/Решения заданий.md", False), ("materials/разрешено.md", False), ("materials/ответственный.md", False), ("решение/данные.csv", False),
    ("materials/ключ.txt", False), ("materials/не грусти.md", False), ("materials/недля.md", False), ("ответы2020год.txt", False),
    # The Latin words are read as they always were: a space is not a mark that sets «key» apart.
    ("materials/key.txt", True), ("data/keys/a.txt", True), ("materials/primary key.sql", False), ("materials/keyboard.md", False), ("materials/answers.csv", True),
    ("docs/etalon.md", True), ("materials/monkey.txt", False),
])
def test_a_name_that_announces_an_answer_does_so_in_either_alphabet(path, announced):
    from project_generator_lab.content.checks import SOLUTION_PATH

    assert bool(SOLUTION_PATH.search(path)) is announced


def test_the_format_of_a_file_is_told_by_the_ending_of_its_name_in_any_letters():
    from project_generator_lab.benchmark.repository import TextFile

    for path, content in (("a.yml", "a: [1, 2"), ("A.YAML", "a: [1, 2"), ("data/b.CSV", "a,b\n1,2,3\n"), ("v1.2/c.Json", "{"), ("archive.json.yml", "a: [1")):
        assert parses(TextFile(path, len(content), content)) != ""
    for path in ("notes.yml.txt", "csv.txt", "table.csv.md"):  # the ending, not a word somewhere in the name
        assert parses(TextFile(path, 9, "a: [1, 2")) == "" and parses(TextFile(path, 9, "a,b\n1,2,3\n")) == ""


def test_valid_structured_materials_parse():
    for path, content in (("a.json", '{"a": [1, 2]}'), ("a.yaml", "a:\n  - 1\n"), ("a.yml", "b: 2\n"), ("a.csv", "x,y\n1,2\n"), ("a.md", "# {not json")):
        assert parses(Material(path=path, kind="data", purpose="Данные", contains="Строки", content=content)) == ""


@pytest.mark.parametrize("text, found", [
    ("TODO: дописать", "TODO"), ("см. TBD", "TBD"), ("XXX", "XXX"), ("[Вставить пример]", "[Вставить"), ("<ИМЯ>", "<ИМЯ"), ("Lorem Ipsum dolor", "Lorem Ipsum"),
    # A name in an example, a size, a word that only contains the letters: not a note to oneself.
    ("create or replace view xxxxx as", None), ("размер XXXL", None), ("todo-лист в нижнем регистре", None), ("TODOS", None),
    # «todo:» is a note in any letters; a thing called TODO-лист or TODO_LIST is what a project may be about.
    ("todo: дописать условие", "todo:"), ("Tbd : позже", "Tbd :"), ("Сделай TODO-лист на FastAPI", None), ("константа TODO_LIST", None),
])
def test_a_note_to_oneself_is_a_word_of_its_own_in_capitals(text, found):
    from project_generator_lab.content.checks import stubs

    assert [check.message for check in stubs([("task:1", text)])] == ([f"Осталась заглушка «{found}» (task:1)."] if found else [])


def test_a_material_nested_by_the_thousand_is_a_material_that_does_not_read():
    deep = Material(path="deep.yaml", kind="data", purpose="Данные", contains="Строки", content="a: " + "[" * 3000 + "]" * 3000)
    assert parses(deep) == "RecursionError"


def test_completeness_names_every_hole_and_placeholders():
    draft = complete_draft()
    assert draft.summary and draft.frame
    draft.summary.status, draft.frame.status, draft.review = "draft", "draft", None
    draft.tasks = [task(1), task(2, status="draft")]
    stored(draft, "team.csv").status, stored(draft, "team.csv").content = "planned", ""
    draft.tasks[0].steps.append("TODO: дописать")
    messages = [c.message for c in blocking(draft)]
    for expected in ("Название и аннотация не согласованы", "Задание 2 не согласовано", "Задание 3 не написано",
                     "Введение и ситуация — не согласовано", "не подготовлен", "TODO", "Критики ещё не проверяли"):
        assert any(expected in m for m in messages), expected
    # The same holes, but the route only reports them when there is nothing left to do.
    assert next_step(draft)["action"] == "approve_summary"


def test_material_path_is_kept_inside_materials():
    for bad in ("../x.csv", "/etc/passwd.csv", "data.exe", "имя.csv", "a b.csv", "a/b/c/d.csv"):
        with pytest.raises(ValueError):
            MaterialNeed(path=bad, kind="data", purpose="Данные", contains="Строки")
    assert MaterialNeed(path="materials\\sub/data.csv", kind="data", purpose="Данные", contains="Строки").path == "sub/data.csv"


def test_model_list_items_lose_their_own_numbers_and_bullets():
    rationale = Rationale(concept="Пункт замысла", evidence="Сдача видна", choice="Формат", peer="Пир открывает файл")
    drafted = TaskDraft(title="Роли", goal="Цель задания", steps=["1. Сделай", "2) Проверь", "- Сдай", "  ", "10. Десятый"], result="src/a.md",
                        expected=["1. Таблица ролей"], constraints=["— Без шаблонов"],
                        peer_checks=["• Файл есть", "* Таблица полная"], needs=["- Понятие"], rationale=rationale)
    assert drafted.steps == ["Сделай", "Проверь", "Сдай", "Десятый"] and drafted.peer_checks == ["Файл есть", "Таблица полная"]
    assert drafted.needs == ["Понятие"] and drafted.expected == ["Таблица ролей"]
    kept = TaskDraft(title="Роли", goal="Цель задания", steps=["2024 год — старт", "3D-модель"], result="src/r.md", peer_checks=["x"],
                     expected=["z"], needs=["y"], rationale=rationale)
    assert kept.steps == ["2024 год — старт", "3D-модель"]


# ---------- route states ----------

STATES = {
    "summary-none": ("write_summary", None, "Предложить название и аннотацию"),
    "summary-draft": ("approve_summary", None, "Название и аннотация подходят — писать задание 1"),
    "task1-missing": ("write_task", 1, "Написать задание 1"),
    "task1-draft": ("approve_task", 1, "Задание 1 подходит — писать задание 2"),
    "task2-missing": ("write_task", 2, "Написать задание 2"),
    "task2-draft": ("approve_task", 2, "Задание 2 подходит — писать задание 3"),
    "task3-missing": ("write_task", 3, "Написать задание 3"),
    "task3-draft": ("approve_task", 3, "Задание 3 подходит — теория и введение"),
    "task1-with-files": ("approve_task", 1, "Задание 1 подходит — материалы к нему (1)"),
    "theory-missing": ("write_support", None, "Подготовить теорию и введение"),
    "material-planned": ("write_materials", 1, "Подготовить материалы к заданию 1"),
    "frame-draft": ("approve_support", None, "Всё подходит — показать весь текст"),
    "material-draft": ("approve_materials", 1, "Материалы к заданию 1 подходят — писать задание 2"),
    "not-reviewed": ("run_review", None, "Проверить критиками"),
    "reviewed": ("assemble", None, "Перенести в README и чек-лист"),
    "assembled": (None, None, ""),
    "busy": (None, None, ""),
    "pending": ("apply_pending", None, "Применить правки"),
}


@pytest.mark.parametrize("state", list(STATES))
def test_next_step_for_every_state(state):
    draft = complete_draft()
    assert draft.summary and draft.frame
    if state == "summary-none":
        draft.summary = None
    elif state == "summary-draft":
        draft.summary.status = "draft"
    elif state == "task1-missing":
        draft.tasks = []
    elif state == "task1-draft":
        draft.tasks = [task(1, status="draft")]
    elif state == "task2-missing":
        draft.tasks = [task(1)]
    elif state == "task2-draft":
        draft.tasks = [task(1), task(2, status="draft")]
    elif state == "task3-missing":
        draft.tasks = [task(1), task(2)]
    elif state == "task1-with-files":
        draft.tasks = [task(1, status="draft")]
        stored(draft, "team.csv").status = "planned"
    elif state == "task3-draft":
        draft.tasks = [task(1), task(2), task(3, status="draft")]
    elif state == "theory-missing":
        draft.theory = None
    elif state == "material-planned":
        stored(draft, "team.csv").status = "planned"
    elif state == "frame-draft":
        draft.frame.status = "draft"
    elif state == "material-draft":
        stored(draft, "team.csv").status = "draft"
    elif state == "not-reviewed":
        draft.review = None
    elif state == "assembled":
        draft.assembled_at = "2026-09-29T12:00:00+00:00"
    elif state == "busy":
        draft.busy = "Пишу задание 2"
    elif state == "pending":
        propose(draft, ContentChange(part="task:1", field="goal", value="Новая цель", reason="точнее"))
    step = next_step(draft)
    assert (step["action"], step["number"], step["button"]) == STATES[state]
    if state in {"reviewed", "assembled"}:
        # One link per section: a second search is offered next to the critics.
        assert step["alternatives"] == [{"action": "run_review", "button": "Проверить критиками ещё раз"},
                                        {"action": "find_links", "button": "Найти ещё ссылки для теории"}]
    if state == "busy":
        assert step["title"] == "Пишу задание 2"


def test_an_edit_after_the_review_is_noticed():
    draft = complete_draft()
    assert "Текст менялся" not in str(next_step(draft)["text"])
    written(draft, 1).goal = "Другая цель"
    assert "Текст менялся после проверки" in str(next_step(draft)["text"])
    written(draft, 1).status = "approved"
    assert draft.review and draft.review.digest != draft.digest()


def test_blocks_stop_the_route_before_readme():
    step = next_step(complete_draft(), ["Материал «a.json» не читается."])
    assert step["action"] is None and step["title"] == "Перед переносом в README нужно поправить" and "a.json" in str(step["text"])


def test_single_task_project_and_scattered_numbers():
    assert numbers([2]) == "задание 2" and numbers([2, 3, 4]) == "задания 2–4" and numbers([1, 3]) == "задания 1, 3"
    draft = complete_draft()
    draft.handoff.tasks = draft.handoff.tasks[:1]
    draft.tasks = [task(1, status="draft")]
    assert next_step(draft)["button"] == "Задание 1 подходит — теория и введение"


# ---------- assembly by code ----------

@pytest.mark.parametrize("title, expected", [
    ("Как учиться в «Школе 21»", "как-учиться-в-школе-21"),
    ("Задание 1. Роли в команде", "задание-1-роли-в-команде"),
    ("Задание 2. Улучшение качества секвенирования (очистка чтений)", "задание-2-улучшение-качества-секвенирования-очистка-чтений"),
    ("Глава 2. Теоретический блок", "глава-2-теоретический-блок"),
    ("2.1. Роли в команде", "21-роли-в-команде"),
    ("Задание 4. Бэклог — первый спринт", "задание-4-бэклог-первый-спринт"),
])
def test_toc_anchors_follow_gitlab(title, expected):
    assert render.anchor(title, {}) == expected


def test_duplicate_headings_get_numbered_anchors():
    seen: dict[str, int] = {}
    assert [render.anchor("Итог", seen) for _ in range(3)] == ["итог", "итог-1", "итог-2"]


def test_readme_puts_theory_before_practice_and_links_tasks_to_it():
    text = render.readme(complete_draft())
    assert (text.index("## Глава 2. Теоретический блок") < text.index("### 2.1. Тема 1") < text.index("## Глава 3. Практический блок")
            < text.index("### Задание 1.") < text.index("## Заключение"))
    assert "Теория: [2.2. Тема 2](#22-тема-2)." in text and "- [Источник 2](https://docs.example.org/2)" in text
    assert "- [Глава 2. Теоретический блок](#глава-2-теоретический-блок)\n  - [2.1. Тема 1](#21-тема-1)" in text
    assert "**Вопрос к практике:** Что из темы 2 пригодится в задании?" in text
    assert "Пункт замысла" not in text and "Понятие 1" not in text  # reasons and needs are for the methodologist


def test_readme_uses_fixed_school_blocks_verbatim():
    text = render.readme(complete_draft())
    school = render.school()
    for line in cast(list[str], school["how_to_learn"]) + cast(list[str], school["how_to_work"]):
        assert f"- {line}" in text
    # «Как учиться» opens Chapter 1, before the introduction, as in PM1.
    assert text.index("### Как учиться в «Школе 21»") < text.index("### Введение")
    assert "- [Глава 1. Введение и инструкция](#глава-1-введение-и-инструкция)\n  - [Как учиться в «Школе 21»](#как-учиться-в-школе-21)" in text
    assert "\n### Введение\n\nЗачем проект." in text and "- Обязательно: таблицы." in text
    assert "💡" not in text  # no feedback link until a survey url is set


def test_preview_of_an_empty_draft_shows_holes():
    draft = complete_draft()
    draft.summary, draft.frame, draft.theory, draft.tasks = None, None, None, []
    text = render.readme(draft)
    assert text.startswith(f"# {draft.handoff.title}\n") and text.count("_Ещё не написано._") == 3 and "### Задание" not in text


def test_multiline_step_stays_inside_its_list_item():
    draft = complete_draft()
    draft.tasks[0] = task(1, steps=["Первый шаг\nс продолжением", "Второй"])
    assert "1. Первый шаг\n   с продолжением\n2. Второй" in render.readme(draft)


def test_full_text_has_reasons_and_materials_with_safe_fences():
    draft = complete_draft()
    stored(draft, "team.csv").content = "Пример:\n```\nкод\n```\n"
    stored(draft, "team.csv").path = "team.md"
    text = render.full_text(draft)
    assert "**Почему такое задание** (для методолога)" in text and "- Как проверит пир: Пир открывает файл" in text
    assert "````\nПример:\n```\nкод\n```\n````" in text


def test_checklist_survives_yaml_special_characters():
    draft = complete_draft()
    draft.tasks[0] = task(1, title='Роли: "кто" и #зачем', peer_checks=["- уже с дефисом", "key: value", "  отступ", "многострочный\nпункт"])
    question = yaml.safe_load(render.checklist(draft))["sections"][0]["questions"][0]
    assert question["name"] == 'Задание 1. Роли: "кто" и #зачем'
    assert question["description"].splitlines()[:3] == ["- - уже с дефисом", "- key: value", "- отступ"]


def test_checklist_texts_are_editable_and_the_disclaimer_closes_the_guidelines():
    draft = complete_draft()
    draft.handoff.rules["program_rules"] += "\n- К каждому чек-листу проверки прилагаются дисклеймеры."
    render.ensure_checklist(draft)  # the api fills it when a draft is loaded
    assert [c.part for c in run_checks(draft) if c.code == "open"] == ["checklist"]  # the rules want one, there is none
    message = propose(draft, ContentChange(part="checklist", field="disclaimer",
                                           value="Проверка оценивает работу, а не участника.\nОценка не влияет на стипендию.", reason="Текст методистов"))
    assert message.patches[0].label == "Чек-лист · Дисклеймер" and message.patches[0].before == ""
    apply(draft, message)
    assert not [c for c in run_checks(draft) if c.code == "open"]
    data = yaml.safe_load(render.checklist(draft))
    assert data["guidelines"].endswith("\n- Проверка оценивает работу, а не участника.\n  Оценка не влияет на стипендию.")
    assert "Дисклеймер:** Проверка оценивает работу" in render.full_text(draft)
    # The introduction is the school's, yet editable per project; an empty one is refused, an empty disclaimer is not.
    edited = propose(draft, ContentChange(part="checklist", field="introduction", value="- Короткое вступление.", reason="r"),
                     ContentChange(part="checklist", field="guidelines", value="", reason="r"),
                     ContentChange(part="checklist", field="disclaimer", value="", reason="убрать"))
    assert [p.field for p in edited.patches] == ["introduction", "disclaimer"] and "не может быть пустым" in edited.notes[0]
    apply(draft, edited)
    assert yaml.safe_load(render.checklist(draft))["introduction"] == "- Короткое вступление."


def test_checklist_reaches_the_chat_model_but_not_the_critic():
    draft = complete_draft()
    render.ensure_checklist(draft)
    assert "checklist" in [p["id"] for p in parts(draft)]
    assert "checklist" not in [p["id"] for p in parts(draft, rationale=True)]
    assert draft.checklist and draft.checklist.guidelines.startswith("- Подлежат оцениванию")


def test_checklist_has_the_platform_shape():
    data = yaml.safe_load(render.checklist(complete_draft()))
    assert list(data) == ["introduction", "quick_actions", "language", "guidelines", "sections"]
    assert data["sections"][0]["kind"] == "1" and set(data["sections"][0]["questions"][0]) == {"name", "description", "rating"}


# ---------- module boundaries ----------

def imports(folder: Path) -> set[str]:
    found: set[str] = set()
    for file in folder.rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(("." * node.level) + node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
    return found


def test_concept_and_content_never_import_each_other():
    content, concept = imports(PACKAGE / "content"), imports(PACKAGE / "concept")
    assert not any("concept" in name for name in content), content
    assert not any("content" in name for name in concept), concept
    assert not any(name.startswith("..") and not name.startswith("..core") for name in content | concept)


def test_a_value_the_draft_cannot_take_is_never_proposed_nor_written():
    # Audit 30.09.2026: a too-short annotation applied with «пойдёт» was written into the draft, the file stopped
    # loading, and «Начать» set it aside and started over — approved tasks were lost.
    from project_generator_lab.content.edits import expire_stale

    draft = complete_draft()
    short = propose(draft, ContentChange(part="summary", field="annotation", value="Коротко.", reason="Короче"))
    assert short.patches == [] and short.notes == ["«Название и аннотация · Аннотация»: слишком коротко — нужно не меньше 20 знаков — правка не предложена."]
    # Retention: a value that fits is proposed as before.
    fine = propose(draft, ContentChange(part="summary", field="annotation", value="Короче, но всё ещё аннотация проекта.", reason="Короче"))
    assert [p.field for p in fine.patches] == ["annotation"]
    # The methodologist's own edit of the proposal is tried on a copy: refused whole, the draft and the proposal untouched.
    before = draft.model_dump()
    with pytest.raises(HTTPException) as error:
        apply(draft, fine, {0: "Мало."})
    assert error.value.status_code == 422 and "не меньше 20 знаков" in error.value.detail
    assert draft.model_dump() == before and fine.status == "pending"
    ContentDraft.model_validate(draft.model_dump())
    # A proposal whose text changed under it is withdrawn with a note; one that still fits stays waiting.
    other = propose(draft, ContentChange(part="task:1", field="goal", value="Новая цель задания", reason="Точнее"))
    written(draft, 1).goal = "Изменено генерацией"
    assert expire_stale(draft) == ["Задание 1 · Цель"]
    assert other.status == "rejected" and other.notes[-1].startswith("Текст изменился после предложения")
    assert fine.status == "pending"
    # Nor can a rejected stale proposal be brought back to «waiting».
    with pytest.raises(HTTPException) as error:
        revert(draft, other)
    assert error.value.status_code == 409 and other.status == "rejected"


def test_a_proposal_left_stale_by_apply_or_revert_is_withdrawn_and_a_new_material_with_a_short_reason_is_a_note():
    # Independent review 30.09.2026: apply M1, the model proposes M2 on the same field, revert M1 — both waited and
    # «Применить» failed with 409 forever; a one-letter reason for a new material crashed the chat with 500.
    draft = complete_draft()
    m1 = propose(draft, ContentChange(part="task:1", field="goal", value="Первая новая цель", reason="r"))
    apply(draft, m1)
    m2 = propose(draft, ContentChange(part="task:1", field="goal", value="Вторая новая цель", reason="r"))
    revert(draft, m1)
    assert (m1.status, m2.status) == ("pending", "rejected") and m2.notes[-1].startswith("Текст изменился после предложения")
    # Bringing a rejected proposal back replaces a waiting one on the same field.
    m3 = propose(draft, ContentChange(part="task:1", field="goal", value="Третья новая цель", reason="r"))
    m1.status = "rejected"
    revert(draft, m1)
    assert (m1.status, m3.status) == ("pending", "rejected")
    short = propose(draft, ContentChange(part="material:extra.csv", field="content", value="a,b\n1,2\n", reason="ок", task_numbers=[1]))
    assert short.patches == [] and "слишком коротко" in short.notes[0]


def test_reverting_a_new_material_removes_it_and_a_restore_the_draft_cannot_take_is_refused():
    draft = complete_draft()
    added = propose(draft, ContentChange(part="material:extra.csv", field="content", value="a,b\n1,2\n", reason="Таблица для задания", task_numbers=[1]))
    apply(draft, added)
    assert draft.material("extra.csv") is not None
    revert(draft, added)
    assert draft.material("extra.csv") is None and added.status == "pending"
    # A crafted old value the draft cannot take back (an annotation shorter than 20 characters) is refused, not written.
    from project_generator_lab.content.models import Patch as ContentPatch

    assert draft.summary is not None
    annotation = draft.summary.annotation
    old = ContentMessage(role="assistant", text="", status="applied", patches=[
        ContentPatch(part="summary", field="annotation", label="Название и аннотация · Аннотация", before="Коротко", after=annotation, reason="r")])
    draft.messages.append(old)
    with pytest.raises(HTTPException) as error:
        revert(draft, old)
    assert error.value.status_code == 409 and error.value.detail == "Отменить нельзя: слишком коротко — нужно не меньше 20 знаков"
    assert draft.summary.annotation == annotation and old.status == "applied"


def test_a_rejected_proposal_to_create_a_material_can_be_brought_back():
    # The material does not exist yet: «nothing» reads as the empty text the proposal started from.
    draft = complete_draft()
    added = propose(draft, ContentChange(part="material:extra.csv", field="content", value="a,b\n1,2\n", reason="Таблица для задания", task_numbers=[1]))
    added.status = "rejected"
    revert(draft, added)
    assert added.status == "pending"
    apply(draft, added)
    assert stored(draft, "extra.csv").content == "a,b\n1,2\n"
