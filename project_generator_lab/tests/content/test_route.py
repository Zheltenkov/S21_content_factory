"""The content route in the chat: style → title → tasks with reasons → theory, introduction, materials → whole text →
critics → README by code."""

import io
import json
import shutil
import zipfile
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.content import api as content_api
from project_generator_lab.content import flow, store
from project_generator_lab.content.handoff import HandoffUnavailable
from project_generator_lab.content.models import ContentChange, ContentDraft, ContentRevision, CriticFinding
from project_generator_lab.content.writer import PROMPTS, Writer
from project_generator_lab.tests.content.fixtures import (
    DEAD,
    FACT_SOURCE,
    SEARCH_SOURCES,
    FakeModel,
    FakeSearch,
    handoff_fixture,
    open_pages,
    read_pages,
    shown,
    tables_written,
    wait_idle,
)

ROUTE = ["approve_style", "approve_summary", "approve_task", "approve_materials", "approve_task", "approve_materials", "approve_task",
         "approve_theory_plan", "approve_support", "run_review", "assemble"]


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def reviewer():
    critic = FakeModel("critic-model")
    critic.findings = [CriticFinding(part="task:2", severity="important", message="Пир не сможет проверить договорённости без записи встречи.",
                                     suggestion="Добавить в сдачу протокол встречи.")]
    return critic


@pytest.fixture
def search():
    return FakeSearch()


@pytest.fixture
def client(tmp_path, monkeypatch, model, reviewer, search):
    monkeypatch.setattr(store, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(content_api, "model_configuration", lambda: ("test-key", "unused", "fake-model"))
    monkeypatch.setattr(content_api.settings, "handoff", lambda run_id: handoff_fixture(run_id))
    monkeypatch.setattr(content_api.settings, "writer", lambda: Writer(model, reviewer=reviewer, search=search, opener=open_pages, reader=read_pages))
    with TestClient(app_module.app) as test_client:
        yield test_client


def advance(client, run_id, action, number=None):
    response = client.post(f"/api/content/{run_id}/advance", json={"action": action, "number": number})
    assert response.status_code == 200, response.text
    return wait_idle(client, run_id)


def begin(client, run_id):
    """Start the content and agree to the style the assistant proposed: the title and the annotation are written."""
    client.post(f"/api/content/{run_id}/start")
    assert wait_idle(client, run_id)["next_step"]["action"] == "approve_style"
    return advance(client, run_id, "approve_style")


def walk(client, run_id, stop=None):
    """Press every primary chip until nothing is left to press, or until the named step is offered."""
    client.post(f"/api/content/{run_id}/start")
    view = wait_idle(client, run_id)
    pressed: list[str] = []
    while view["next_step"]["action"] and view["next_step"]["action"] != stop:
        # A step that fails is offered again: pressing it for ever would hang the tests instead of failing one.
        assert len(pressed) < 40, (pressed[-3:], view["messages"][-1]["text"])
        pressed.append(view["next_step"]["action"])
        view = advance(client, run_id, view["next_step"]["action"], view["next_step"]["number"])
    return view, pressed


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed: the page of the project was not checked")
def test_the_page_of_the_whole_project_shows_what_the_generator_wrote(client):
    """07.10.2026: the tab «открыть отдельно» showed the tables of a finished project as lines of bars. The page has its
    own reading of markdown, so it is checked against what the generator really writes — not against a sample."""
    run_id = str(uuid4())
    walk(client, run_id)
    readme = client.get(f"/api/content/{run_id}/preview").text
    seen = shown(readme)
    assert tables_written(readme) == seen["tables"] >= 2, (tables_written(readme), seen)
    assert seen["leftovers"] == [] and seen["marks"] == [] and seen["dead"] == []
    assert seen["toc"] >= 4 and seen["headings"] - seen["toc"] == 1  # the title of the project stays out of the contents
    # Every file of the project the README links to opens on the page: the file route has it.
    linked = list(dict.fromkeys(path for path, _ in seen["files"]))
    assert linked and all(path.startswith("materials/") for path in linked), linked
    for path in linked:
        served = client.get(f"/api/content/{run_id}/file", params={"path": path})
        assert served.status_code == 200 and served.text.strip(), path
        if path.endswith(".md"):
            inner = shown(served.text, f"?run={run_id}&doc=file&path={path}")
            assert inner["leftovers"] == [] and inner["marks"] == [] and inner["dead"] == [] and inner["headings"] >= 1
    missing = client.get(f"/api/content/{run_id}/file", params={"path": "src/report.md"})
    assert missing.status_code == 404 and "Файлы из src/ создаёт участник" in missing.json()["detail"]


def test_before_start_the_chip_offers_content_and_nothing_is_stored(client):
    run_id = str(uuid4())
    view = client.get(f"/api/content/{run_id}").json()
    assert view["exists"] is False and view["next_step"]["action"] == "start"
    assert not store.exists(run_id)


def test_unapproved_concept_cannot_start(client, monkeypatch):
    def refuse(run_id):
        raise HandoffUnavailable("Сначала согласуйте замысел.")

    monkeypatch.setattr(content_api.settings, "handoff", refuse)
    run_id = str(uuid4())
    assert client.get(f"/api/content/{run_id}").status_code == 409
    assert client.post(f"/api/content/{run_id}/start").status_code == 409


def test_start_shows_what_the_generator_received(client):
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    view = wait_idle(client, run_id)
    assert view["messages"][1]["part"] == "handoff"
    rows = {row["label"]: row for row in view["handoff_card"]}
    assert rows["Требования заказчика"]["value"] == "есть" and rows["ОР программы"]["ok"] is False
    assert rows["Приложенные файлы"]["items"] == ["бриф.docx · целиком", "ответы.docx · только проверяющему"]
    assert rows["Вопросы замысла"]["items"] == ["Какой трекер доступен в кампусе?"]
    # The style comes first: nothing of the text is written before the methodologist has seen how it will be told.
    assert view["next_step"]["action"] == "approve_style" and view["summary"] is None
    assert (rows["Стиль подачи"]["value"], rows["Сквозной сюжет серии"]["value"]) == ("предложит ассистент", "нет")


def test_full_route_in_the_agreed_order_and_readme_only_at_the_end(client, model, reviewer, search):
    run_id = str(uuid4())
    view, pressed = walk(client, run_id)
    assert pressed == ROUTE and view["next_step"]["title"] == "README и чек-лист готовы"
    # One call per part in the agreed order; links by the research agent; the critic on its own model.
    # Each task is followed by its own materials; theory and the introduction come after all tasks.
    assert [name for name, _, _ in model.calls] == ["StyleDraft", "SummaryDraft", "TaskDraft", "MaterialDraft", "TaskDraft", "MaterialDraft",
                                                    "TaskDraft", "TheoryPlanDraft", "TheoryDraft", "LinkSelection", "FrameDraft", "FactReport"]
    assert [name for name, _, _ in reviewer.calls] == ["CriticReport"]
    assert [kind for kind, _ in search.calls] == ["sources", "facts"]
    # Files «for the reviewer» reach the critic only.
    writer_files = {f["name"] for _, _, payload in model.calls if "project" in payload and "attached_files" in payload["project"]
                    for f in payload["project"]["attached_files"]}
    assert writer_files == {"бриф.docx"}
    assert {f["name"] for f in reviewer.calls[0][2]["project"]["attached_files"]} == {"бриф.docx", "ответы.docx"}
    # The whole text was shown before the critics, and the README only after the transfer.
    parts = [m["part"] for m in view["messages"] if m["part"]]
    assert parts.index("full") < parts.index("checklist") < parts.index("review") < parts.index("readme")
    assert view["checklist"]["disclaimer"] == "" and "- Подлежат оцениванию" in view["checklist"]["guidelines"]
    # What was agreed is said in words before the next part is begun; the first task is named the model for the rest.
    said = [m["text"] for m in view["messages"] if m["role"] == "assistant" and "согласован" in m["text"] and not m["part"]]
    assert said == [
        "Стиль согласован: «рабочая легенда». Пишу название и аннотацию…", "Название и аннотация согласованы. Пишу задание 1…",
        "Задание 1 согласовано — это образец для остальных. Готовлю материалы к заданию 1…",
        "Материалы к заданию 1 согласованы: team.csv. Пишу задание 2…", "Задание 2 согласовано. Готовлю материалы к заданию 2…",
        "Материалы к заданию 2 согласованы: meeting_template.md. Пишу задание 3…", "Задание 3 согласовано. Думаю, как подать теорию…",
        "Подача теории согласована: короткая глава в README. Готовлю теорию и введение…"]
    assert "Теория и введение согласованы. " in "".join(m["text"] for m in view["messages"])

    draft = store.load(run_id)
    # A dead page never becomes a link; a number outside the opened pages is dropped.
    assert draft.theory and [link.url for t in draft.theory.topics for link in t.links] == SEARCH_SOURCES
    assert DEAD not in draft.sources and set(SEARCH_SOURCES) <= set(draft.sources)
    assert draft.review and [(f.source, f.part, f.severity) for f in draft.review.findings] == [
        ("method", "task:2", "important"), ("facts", "task:3", "important"), ("facts", "", "advice")]
    assert draft.review.findings[1].sources == [FACT_SOURCE] and draft.review.findings[2].sources == []

    text = client.get(f"/api/content/{run_id}/readme").text
    # The assistant chose the working legend (the concept gives no story of a series): the README is laid out as one.
    order = ["## Содержание", "## Старт работы", "## Теория", "### 1. Тема для задания 1", "## Задание 1. Роли в команде",
             "## Задание 3. Бэклог первого спринта", "## Итог", "## Что сдаётся"]
    assert [text.index(f"\n{h}\n") for h in order] == sorted(text.index(f"\n{h}\n") for h in order)
    for block in ("**Как учиться в «Школе 21»:**", "**Как работать с проектом:**", "**Материалы:**", "**Результат:** Файл src/task1.md.",
                  "**Зачем:** Результат понадобится в задании 2.", "| Задание | Что сдаётся | Как проверяется |"):
        assert block in text
    assert "Теория: [1. Тема для задания 1](#1-тема-для-задания-1)." in text
    assert "- [Документация к теме 1](https://docs.example.org/roles) — Раздел про основы" in text
    assert "**Вопрос к практике:** Как понятие 1 меняет твоё решение?" in text
    assert "- Все файлы необходимо создавать в папке _src/_ склонированного репозитория." in text
    assert "Почему такое задание" not in text and "Готовое распределение ролей" not in text
    full = client.get(f"/api/content/{run_id}/text").text
    assert "**Почему такое задание** (для методолога)" in full and "Внутри нет: Готовое распределение ролей" in full

    checklist = yaml.safe_load(client.get(f"/api/content/{run_id}/check-list").text)
    assert [q["name"] for q in checklist["sections"][0]["questions"]] == ["Задание 1. Роли в команде", "Задание 2. План встречи",
                                                                           "Задание 3. Бэклог первого спринта"]
    archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/content/{run_id}/bundle").content))
    assert sorted(archive.namelist()) == ["README.md", "check-list.yml", "materials/meeting_template.md", "materials/team.csv",
                                          "misc/images/.gitkeep", "src/.gitkeep"]


def test_tasks_are_written_and_agreed_one_by_one_each_with_its_materials(client, model):
    run_id = str(uuid4())
    view, _ = walk(client, run_id, stop="approve_task")
    assert [t["number"] for t in view["tasks"]] == [1] and view["next_step"]["button"] == "Задание 1 подходит — материалы к нему (1)"
    view = advance(client, run_id, "approve_task", 1)
    # The task's own file is written right after it, before the next task.
    assert [(m["path"], m["status"]) for m in view["materials"]] == [("team.csv", "draft")] and len(view["tasks"]) == 1
    assert view["next_step"]["button"] == "Материалы к заданию 1 подходят — писать задание 2"
    view = advance(client, run_id, "approve_materials", 1)
    assert [t["number"] for t in view["tasks"]] == [1, 2] and view["tasks"][1]["status"] == "draft"
    assert view["materials"][0]["status"] == "approved" and view["next_step"]["button"] == "Задание 2 подходит — материалы к нему (1)"
    # The second task was written with the approved first one as its model.
    assert model.calls[-1][2]["exemplar"]["title"] == "Роли в команде"
    view = advance(client, run_id, "approve_task", 2)
    view = advance(client, run_id, "approve_materials", 2)
    # Task 3 needs no file: after it the assistant asks how the theory is to be given, and then writes it.
    assert view["next_step"]["button"] == "Задание 3 подходит — как подать теорию"
    view = advance(client, run_id, "approve_task", 3)
    assert view["theory"] is None and view["theory_plan"]["status"] == "draft" and view["next_step"]["action"] == "approve_theory_plan"
    view = advance(client, run_id, "approve_theory_plan")
    assert view["theory"] and view["frame"] and view["next_step"]["action"] == "approve_support"


def test_extra_materials_of_one_task_are_not_added_silently(client, model):
    run_id = str(uuid4())
    model.extra_materials = 3
    view, _ = walk(client, run_id, stop="approve_task")
    assert [m["path"] for m in view["materials"]] == ["team.csv", "extra1.md"]
    said = view["messages"][-1]["text"]
    # One message says it all, each part after the other: the task, what was added, what was not and why.
    assert said.startswith("Задание 1 «") and " Понадобятся материалы: team.csv, extra1.md — подготовлю сразу после согласования задания." in said
    assert said.endswith(" Модель просила ещё extra2.md, extra3.md — не добавил: больше 2 новых файлов на задание обычно значит справки, "
                         "которые повторяют теорию. Нужен файл — попросите в чате.")


def test_readme_waits_for_the_critics_and_stale_chips_are_refused(client):
    run_id = str(uuid4())
    view, _ = walk(client, run_id, stop="run_review")
    assert view["next_step"]["action"] == "run_review"
    assert client.post(f"/api/content/{run_id}/advance", json={"action": "assemble"}).status_code == 409
    response = client.get(f"/api/content/{run_id}/readme")
    assert response.status_code == 409 and "Критики ещё не проверяли" in response.json()["detail"]
    # The preview and the whole text are there at any time, holes included.
    assert client.get(f"/api/content/{run_id}/preview").status_code == 200
    assert "## Теоретический блок" in client.get(f"/api/content/{run_id}/text").text
    preview = yaml.safe_load(client.get(f"/api/content/{run_id}/check-list-preview").text)
    assert [q["name"] for q in preview["sections"][0]["questions"]][0] == "Задание 1. Роли в команде"


def test_disclaimer_sent_in_the_chat_goes_into_the_check_list(client, model):
    run_id = str(uuid4())
    walk(client, run_id, stop="run_review")
    model.revisions = [ContentRevision(reply="Добавил дисклеймер.", changes=[
        ContentChange(part="checklist", field="disclaimer", value="Проверка оценивает работу, а не участника.", reason="Прислал методолог")])]
    client.post(f"/api/content/{run_id}/messages", json={"text": "Дисклеймер чек-листа: Проверка оценивает работу, а не участника."})
    view = wait_idle(client, run_id)
    assert view["messages"][-1]["patches"][0]["label"] == "Чек-лист · Дисклеймер"
    assert model.calls[-1][2]["parts"][-1]["id"] == "checklist"
    advance(client, run_id, "apply_pending")
    preview = yaml.safe_load(client.get(f"/api/content/{run_id}/check-list-preview").text)
    assert preview["guidelines"].endswith("- Проверка оценивает работу, а не участника.")


def test_critics_can_run_again_and_an_edit_makes_the_review_stale(client, model):
    run_id = str(uuid4())
    view, _ = walk(client, run_id, stop="assemble")
    assert view["review_stale"] is False
    assert [a["action"] for a in view["next_step"]["alternatives"]] == ["run_review", "find_links"]  # one link per section: more can be searched
    model.revisions = [ContentRevision(reply="Учёл замечание 1.", changes=[
        ContentChange(part="task:2", field="result", value="Файл src/task2.md и протокол встречи src/meeting.md.", reason="Замечание 1")])]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "учти замечание 1"}).json()
    message = view["messages"][-1]
    client.post(f"/api/content/{run_id}/messages/{message['id']}/decision", json={"verdict": "apply"})
    view = client.get(f"/api/content/{run_id}").json()
    assert view["review_stale"] is True and "Текст менялся после проверки" in view["next_step"]["text"]
    assert model.calls[-1][2]["review"][0]["message"].startswith("Пир не сможет")
    view = advance(client, run_id, "run_review")
    assert view["review_stale"] is False and view["next_step"]["action"] == "assemble"


def test_search_failure_leaves_theory_without_links_and_says_so(client, search):
    run_id = str(uuid4())
    search.fail = True
    view, _ = walk(client, run_id, stop="approve_support")
    assert view["theory"]["note"].startswith("Ссылки не найдены")
    assert all(not t["links"] for t in view["theory"]["topics"])
    assert any(c["code"] == "theory" and "нет ссылок" in c["message"] for c in view["checks"])
    assert {"action": "find_links", "button": "Найти ссылки для теории"} in view["next_step"]["alternatives"]
    view = advance(client, run_id, "approve_support")
    view = advance(client, run_id, "run_review")
    assert view["review"]["notes"][0].startswith("Фактическая проверка не выполнена")
    assert view["next_step"]["action"] == "assemble"


def test_links_found_again_from_the_chat_come_as_patches(client, model, search):
    run_id = str(uuid4())
    search.no_links = True
    view, _ = walk(client, run_id, stop="approve_support")
    assert all(not t["links"] for t in view["theory"]["topics"]) and "не вернул ни одной ссылки" in view["theory"]["note"]
    search.no_links = False
    model.revisions = [ContentRevision(reply="Запускаю поиск ссылок.", find_links=True)]
    client.post(f"/api/content/{run_id}/messages", json={"text": "поищи и добавь ссылки"})
    view = wait_idle(client, run_id)
    proposal = view["messages"][-1]
    assert proposal["status"] == "pending" and len(proposal["patches"]) == 3 and proposal["patches"][0]["label"] == "Раздел 1 · Где изучить"
    assert view["next_step"]["action"] == "apply_pending"
    advance(client, run_id, "apply_pending")
    draft = store.load(run_id)
    assert draft.theory and [t.links[0].url for t in draft.theory.topics] == SEARCH_SOURCES


def test_agreement_approves_the_part_under_discussion_and_proceed_goes_on(client):
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    # «Пойдёт» to the proposed style agrees to it — and to nothing more: the title is written, not approved.
    client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"})
    view = wait_idle(client, run_id)
    assert view["style"]["status"] == "approved" and view["summary"]["status"] == "draft" and view["tasks"] == []
    client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"})
    view = wait_idle(client, run_id)
    assert view["summary"]["status"] == "approved" and view["tasks"][0]["number"] == 1
    assert view["tasks"][0]["rationale"]["concept"].startswith("Реализует пункт замысла 1")
    client.post(f"/api/content/{run_id}/messages", json={"text": "дальше"})
    view = wait_idle(client, run_id)
    assert view["tasks"][0]["status"] == "approved" and view["materials"][0]["status"] == "draft"
    # «Пойдёт» on the task's materials approves them and goes on to the next task.
    client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"})
    view = wait_idle(client, run_id)
    assert view["materials"][0]["status"] == "approved" and [t["number"] for t in view["tasks"]] == [1, 2]


def test_model_failure_keeps_ready_parts_and_offers_the_step_again(client, model):
    run_id = str(uuid4())
    model.fail_on = {"TaskDraft:2"}
    begin(client, run_id)
    view = advance(client, run_id, "approve_summary")
    view = advance(client, run_id, "approve_task", 1)
    view = advance(client, run_id, "approve_materials", 1)
    assert [t["number"] for t in view["tasks"]] == [1]
    assert "Не получилось" in view["messages"][-1]["text"] and view["next_step"]["button"] == "Написать задание 2"
    model.fail_on = set()
    view = advance(client, run_id, "write_task", 2)
    assert [t["number"] for t in view["tasks"]] == [1, 2]


def test_restart_clears_busy_with_a_note(client):
    run_id = str(uuid4())
    store.save(ContentDraft(id=run_id, owner_id="local", content_version="0.2", handoff=handoff_fixture(run_id),
                            prompt_snapshots=PROMPTS.snapshot("system"), busy="Пишу задание 2"))
    flow.recover()
    view = client.get(f"/api/content/{run_id}").json()
    assert view["busy"] == "" and "Сервер перезапущен" in view["messages"][-1]["text"]
    assert view["next_step"]["action"] == "write_summary"


def test_prompts_are_frozen_with_the_draft(client, model, monkeypatch):
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    frozen = model.calls[0][1]
    monkeypatch.setattr(PROMPTS, "folder", PROMPTS.folder.parent / "missing")
    client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"})
    wait_idle(client, run_id)
    assert model.calls[-1][1].startswith(frozen.split("\n\n")[0])


def test_foreign_draft_is_not_visible(client):
    run_id = str(uuid4())
    client.post(f"/api/content/{run_id}/start")
    wait_idle(client, run_id)
    draft = store.load(run_id)
    draft.owner_id = "someone-else"
    store.save(draft)
    assert client.get(f"/api/content/{run_id}").status_code == 404
    assert client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"}).status_code == 404


def test_revision_request_reaches_model_with_focus_and_links_are_guarded(client, model):
    run_id = str(uuid4())
    walk(client, run_id, stop="approve_support")
    model.revisions = [ContentRevision(reply="Добавил ссылки.", changes=[
        ContentChange(part="topic:1", field="links", value=["Придуманный сайт — https://invented.example/x"], reason="Больше источников")]),
        ContentRevision(reply="Добавил вашу ссылку.", changes=[
            ContentChange(part="topic:1", field="links", value=["Документация к теме 1 — https://docs.example.org/roles — Раздел про основы",
                                                                 "Ваш источник — https://mine.example/guide"], reason="Прислал методолог")])]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "добавь источников к теме 1"}).json()
    name, _, payload = model.calls[-1]
    assert name == "ContentRevision" and payload["focus"] == "theory, frame"
    assert payload["known_links"][:3] == SEARCH_SOURCES
    refused = view["messages"][-1]
    assert refused["patches"] == [] and "не нашёл поиск" in refused["notes"][0]
    view = client.post(f"/api/content/{run_id}/messages", json={"text": "добавь https://mine.example/guide к теме 1"}).json()
    accepted = view["messages"][-1]
    assert accepted["status"] == "pending" and accepted["patches"][0]["label"] == "Раздел 1 · Где изучить"
    client.post(f"/api/content/{run_id}/messages/{accepted['id']}/decision", json={"verdict": "apply"})
    draft = store.load(run_id)
    assert draft.theory and [link.url for link in draft.theory.topics[0].links] == ["https://docs.example.org/roles", "https://mine.example/guide"]
    view = client.get(f"/api/content/{run_id}").json()
    assert any(c["code"] == "links" and "mine.example" in c["message"] for c in view["checks"])


def test_a_draft_of_an_earlier_version_is_set_aside_not_guessed(client):
    run_id = str(uuid4())
    store.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    store.path(run_id).write_text(json.dumps({"id": run_id, "skeleton": {}}), encoding="utf-8")
    assert client.get(f"/api/content/{run_id}").json()["next_step"]["action"] == "start"
    assert client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"}).status_code == 409
    client.post(f"/api/content/{run_id}/start")
    assert wait_idle(client, run_id)["style"] is not None
    assert [p.name.startswith(f"{run_id}.old-") for p in store.CONTENT_DIR.iterdir() if p.suffix == ".bak"] == [True]


def test_someone_elses_unreadable_draft_is_neither_set_aside_nor_revealed(client):
    # Audit 30.09.2026: «start» moved an unreadable draft aside before checking whose it was, and «load» answered 409
    # (so the draft exists) before checking the owner.
    run_id = str(uuid4())
    store.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    store.path(run_id).write_text(json.dumps({"id": run_id, "owner_id": "someone-else", "skeleton": {}}), encoding="utf-8")
    assert client.post(f"/api/content/{run_id}/messages", json={"text": "пойдёт"}).status_code == 404
    assert client.post(f"/api/content/{run_id}/start").status_code == 404
    assert store.path(run_id).is_file() and not [p for p in store.CONTENT_DIR.iterdir() if p.suffix == ".bak"]
