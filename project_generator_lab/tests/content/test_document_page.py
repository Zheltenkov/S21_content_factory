"""The page that shows a project in a tab of its own (static/document.js) reads Markdown by itself — so it is checked
against what the generator really writes, not against a sample: tables, numbered steps, examples as code, the links
of the contents, of the theory and of the sources (07.10.2026: the tables of a finished project were lines of bars;
the independent review of the same day: the source of an excerpt led nowhere, the steps after a block of code were
numbered from one again, a title with an underscore had another anchor than its link)."""

import json
import shutil

import pytest
from project_generator_lab.content import render, writer
from project_generator_lab.content.models import Excerpt
from project_generator_lab.tests.content.fixtures import probe, shown, tables_written
from project_generator_lab.tests.content.test_theory_plan import planned

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed: the page of the project was not checked")
WIKI = "https://ru.wikipedia.example.org/wiki/Python_(язык_программирования)"


def programming(profile, presentation):
    """A whole draft with what a project about code has: names with an underscore in the titles, a backtick in an
    example, a step with a block of code and one with a list, a source whose address has brackets, an excerpt."""
    draft = planned(profile, presentation)
    draft.content_version = writer.CONTENT_VERSION
    assert draft.theory is not None
    task = draft.tasks[0]
    task.title = "Функция ft_strlen и модуль string_utils"
    task.examples = ["echo `date +%Y` → 2026", "print(f'{a}') → `1`", "SELECT `name` FROM t → Анна"]
    task.steps = ["Создай файл src/ft_strlen.c:\n```c\nsize_t ft_strlen(const char *s);\n```", "Собери проект:\n- запусти make\n- проверь вывод",
                  "Запусти тесты", "Сдай работу"]
    draft.theory.topics[0].title = "Имена snake_case и __init__"
    link = draft.theory.topics[0].links[0]
    draft.theory.topics[0].links = [link.model_copy(update={"url": WIKI, "title": "Python (язык программирования)"}),
                                    link.model_copy(update={"url": "https://developer.example.org/docs/Array", "title": "Array [MDN]"})]
    draft.theory.excerpt = Excerpt(topic=2, title="git-rebase [options] Documentation", url="https://git-scm.example.org/docs/git-rebase",
                                   quote="git rebase [<options>] reapplies commits on top of another base tip.")
    return draft


@needs_node
@pytest.mark.parametrize("profile", ["legend", "quest"])
@pytest.mark.parametrize("presentation", ["chapter", "both", "materials"])
def test_the_page_shows_a_project_about_code_as_the_repository_would(profile, presentation):
    draft = programming(profile, presentation)
    readme = render.readme(draft)
    seen = shown(readme)
    assert tables_written(readme) == seen["tables"] >= 1, (tables_written(readme), seen["tables"])
    assert seen["leftovers"] == [] and seen["marks"] == [] and seen["dead"] == []
    # Every link inside the README — of the contents, of «Теория:» under a task — leads to a heading the page has.
    assert seen["anchors"] and set(seen["anchors"]) <= set(seen["ids"]), sorted(set(seen["anchors"]) - set(seen["ids"]))
    assert any("ft_strlen" in anchor for anchor in seen["anchors"])
    # The sources open as the addresses they are: brackets inside an address, the address of the excerpt. Where the
    # theory is in the materials, they stand in its file.
    if presentation != "materials":
        assert WIKI in seen["outside"] and "https://git-scm.example.org/docs/git-rebase" in seen["outside"]
    assert all(path.startswith("materials/") for path, _ in seen["files"]), seen["files"]
    # The steps of a task are one list: the code of a step and the list inside a step are parts of the step.
    if profile == "legend":
        assert seen["numbered"][0] == [1, 4], seen["numbered"]
    # An example with a backtick inside is one piece of code, to the letter.
    assert {"echo `date +%Y`", "SELECT `name` FROM t", "print(f'{a}')"} <= set(seen["code"]), seen["code"]
    # The file of the theory: its own headings are where the links of the README point.
    name = render.theory_path(draft)
    if name:
        inner = shown(render.theory_file(draft), f"?run=r1&doc=file&path=materials/{name}")
        assert inner["leftovers"] == [] and inner["marks"] == [] and inner["dead"] == []
        pointed = {anchor for path, anchor in seen["files"] if path == f"materials/{name}" and anchor}
        assert pointed and pointed <= set(inner["ids"]), sorted(pointed - set(inner["ids"]))
        assert WIKI in inner["outside"] and "https://git-scm.example.org/docs/git-rebase" in inner["outside"]


TITLES = ["Задание 1. Роли: кто за что?", "Quest 3. ft_strlen", "Имена snake_case", "__init__ и main", "C++ и C#", "Файл a_b.py", "50% — половина",
          "Ёлка № 5", "Цена: 5 ₽/час", "a  b   c", "«Кавычки» и “другие”", "Что такое API (REST)?", "Git: ветки — слияние", "Шаг 1/2", "e-mail & co.",
          "Привет-_-мир", "1. Тема 1", "A.B.C", "état café", "-тире в начале-", "_подчёркивание_", "Quest 3. ft_strlen", "Задание 1. Роли: кто за что?",
          "Quest 3. ft_strlen"]


@needs_node
def test_a_heading_has_on_the_page_the_anchor_the_readme_links_to():
    """The README links to its headings by the ids GitLab gives them (render.anchor); the page must give the same."""
    seen: dict[str, int] = {}
    written = [render.anchor(title, seen) for title in TITLES]
    assert probe(json.dumps(TITLES, ensure_ascii=False), "slugs") == written
    assert written[1] == "quest-3-ft_strlen" and written[-1] == "quest-3-ft_strlen-2"
