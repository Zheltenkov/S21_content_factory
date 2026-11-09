"""What one project cost, the whole chain: found by any id of the project, summed by stage, kept from other owners."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab import usage as report
from project_generator_lab.concept import freshness, runs
from project_generator_lab.concept import models as concept_models
from project_generator_lab.concept import thread as thread_models
from project_generator_lab.content import api as content_api
from project_generator_lab.content import flow, store
from project_generator_lab.content import models as content_models
from project_generator_lab.content.writer import Writer
from project_generator_lab.core import search, usage
from project_generator_lab.core.llm import ModelClient
from project_generator_lab.tests.concept.test_validation import reviewed_run
from project_generator_lab.tests.content.fixtures import (
    FakeModel,
    FakeSearch,
    handoff_fixture,
    open_pages,
    read_pages,
    wait_idle,
)
from project_generator_lab.tests.core.test_llm_contract import Provider, reply
from project_generator_lab.tests.core.test_usage import Finder, found

THREAD = "3b0978c6-8e12-4299-8596-e7a696071369"
SECOND = "d3f55f69-cb54-47d5-9396-abb1dcf9cc8b"
OTHER = "d3f55f00-0000-4000-8000-000000000000"


@pytest.fixture
def folders(tmp_path, monkeypatch):
    assert usage.USAGE_DIR == tmp_path / "usage"   # tests/conftest.py
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(store, "CONTENT_DIR", tmp_path / "content")
    (tmp_path / "runs" / "threads").mkdir(parents=True)
    (tmp_path / "content").mkdir()
    return tmp_path


def thread(folder, ident=THREAD, run_ids=(THREAD, SECOND), owner="local", title="Переговоры с заказчиком", created="2026-10-07T07:30:00+00:00", replies=2):
    messages = [{"role": "methodologist", "text": "…"}] * replies + [{"role": "assistant", "text": "…"}, "not a message"]
    data = {"id": ident, "owner_id": owner, "runs": list(run_ids), "draft": {"title": title}, "created_at": created, "messages": messages}
    (folder / "runs" / "threads" / f"{ident}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def spend(scope, asked, model="openai/gpt-6-sol", tokens=(1000, 100), cost=None, owner="local", outcome="ok", kind="model"):
    token, who = usage.scope.set(scope), usage.current_owner.set(owner)
    try:
        usage.record(kind, asked, model, tokens[0], tokens[1], 2000, outcome=outcome, cost_rub=cost)
    finally:
        usage.scope.reset(token)
        usage.current_owner.reset(who)


def project(ident, owner=None):
    found = report.project_of(ident, owner)
    assert found is not None, ident
    return found


def test_every_stage_is_named_by_a_schema_the_code_has():
    """A schema renamed in the code would leave its calls under a bare name: the map is checked against the classes."""
    from project_generator_lab.evaluation import models as evaluation_models

    known = {name for module in (concept_models, thread_models, content_models, freshness, evaluation_models) for name in vars(module)} | {search.TEXT_SEARCH}
    assert set(report.STAGES) <= known, set(report.STAGES) - known
    for name in ("TaskDraft", "PlainTaskDraft", "TheoryDraft", "NotedTheoryDraft", "ContentRevision", "Proposal", "SearchAnswer"):
        assert name in report.STAGES
    assert report.STAGES["TaskDraft"] == report.STAGES["PlainTaskDraft"] and report.STAGES["TheoryDraft"] == report.STAGES["NotedTheoryDraft"]
    assert report.STAGES["SummaryDraft"] == report.STAGES["TitledSummaryDraft"]
    assert all(stage.startswith(("Замысел · ", "Контент · ")) for stage in report.STAGES.values())



def test_a_project_is_found_by_any_of_its_ids_and_by_their_first_characters(folders):
    thread(folders)
    thread(folders, ident=OTHER, run_ids=(OTHER,), title="Другой", created="2026-10-07T09:00:00+00:00")
    for ident in (THREAD, SECOND, "d3f55f69", "content-d3f55f69", "  CONTENT-D3F55F69 ", "3b0978"):
        assert project(ident)["id"] == THREAD
    assert project("d3f55f00")["title"] == "Другой"
    assert report.project_of("d3f55f") is None  # fits two projects: names none
    assert report.project_of("3b097") is None and report.project_of("") is None  # too short to name a project
    assert report.project_of("aaaaaaaa") is None
    assert project(SECOND, "local")["id"] == THREAD and report.project_of(SECOND, "user-7") is None


def test_the_dialogues_come_newest_first_and_a_file_that_does_not_read_is_passed_over(folders):
    assert report.threads() == []
    thread(folders)
    thread(folders, ident=OTHER, run_ids=(OTHER,), owner="user-7", title="Другой", created="2026-10-07T09:00:00+00:00", replies=0)
    (folders / "runs" / "threads" / "broken.json").write_text("{", encoding="utf-8")
    (folders / "runs" / "threads" / "strange.json").write_text('{"id": 1}', encoding="utf-8")
    assert [(one["id"], one["owner"], one["title"], one["replies"], one["runs"]) for one in report.threads()] == [
        (OTHER, "user-7", "Другой", 0, [OTHER]), (THREAD, "local", "Переговоры с заказчиком", 2, [THREAD, SECOND])]
    assert [one["id"] for one in report.threads("user-7")] == [OTHER]


def test_no_dialogues_folder_is_no_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "absent")
    assert report.threads() == [] and report.project_of(THREAD) is None


def test_the_account_of_a_project_is_the_whole_chain(folders):
    thread(folders)
    (folders / "content" / f"{SECOND}.json").write_text(json.dumps(
        {"messages": [{"role": "methodologist"}, {"role": "assistant"}, {"role": "methodologist"}, {"role": "methodologist"}]}), encoding="utf-8")
    spend(THREAD, "InputCard", cost=10.0)
    spend(THREAD, "Proposal", cost=2.0)
    spend(SECOND, "Concept", tokens=(100_000, 10_000), cost=30.0)
    spend(SECOND, "TaskDraft", cost=5.0)
    spend(SECOND, "PlainTaskDraft", cost=5.0, outcome="failed:schema")
    spend(SECOND, "search_text", model="perplexity/sonar-pro", kind="search", cost=1.5)
    spend(SECOND, "ContentRevision", cost=4.0)
    spend(SECOND, "Concept", owner="user-7", cost=1000.0)
    spend(OTHER, "Concept", cost=500.0)
    found = report.report(project("content-d3f55f69"))
    assert (found["project"], found["title"], found["concept_versions"], found["content_drafts"]) == (THREAD, "Переговоры с заказчиком", 2, [SECOND])
    assert found["replies"] == {"concept": 2, "content": 3} and found["recorded"] is True and found["since"] == "07.10.2026"
    assert (found["calls"], found["failed"], found["charged_rub"], found["charged_calls"]) == (8, 1, 1057.5, 8)
    assert (found["unpriced"], found["unpriced_models"]) == (1, ["perplexity/sonar-pro"])
    assert [(row["stage"], row["calls"]) for row in found["stages"]] == [
        ("Замысел · разбор входов", 1), ("Замысел · диалог", 1), ("Замысел · план заданий", 2), ("Контент · задания", 2),
        ("Контент · поиск (ссылки, факты, первоисточник)", 1), ("Контент · диалог", 1)]
    mine = report.report(project(SECOND, "local"), "local")
    assert (mine["calls"], mine["charged_rub"]) == (7, 57.5)
    # 6 priced calls of the writer: 5 × (1000 in, 100 out) and the concept (100 000 in, 10 000 out), at 2 and 10 $ a million.
    assert mine["price_usd"] == round((5 * 1000 + 100_000) * 2 / 1e6 + (5 * 100 + 10_000) * 10 / 1e6, 4)


def test_a_project_made_before_the_account_says_so_instead_of_showing_zeros(folders, capsys):
    thread(folders)
    found = report.report(project(THREAD))
    assert found["recorded"] is False and found["calls"] == 0 and found["content_drafts"] == [] and found["replies"] == {"concept": 2, "content": 0}
    text = "\n".join(report.lines(found))
    assert "Записей о вызовах нет: учёт ведётся с 07.10.2026" in text and "Этап" not in text and "контент: не начат" in text
    assert report.main([]) == 0
    listed = capsys.readouterr().out
    assert "3b0978c6  2026-10-07 07:30        —        —         —  Переговоры с заказчиком" in listed


def test_the_account_as_text_names_both_sums_and_what_the_price_list_leaves_out(folders):
    thread(folders)
    (folders / "content" / f"{SECOND}.json").write_text("{", encoding="utf-8")
    spend(THREAD, "Concept", tokens=(250_000, 50_000), cost=120.5)
    spend(SECOND, "search_text", model="perplexity/sonar-pro", kind="search", tokens=(40, 60))
    spend(SECOND, "CriticReport", model="anthropic/claude-opus-5", outcome="failed:http502")
    text = report.lines(report.report(project(THREAD)))
    assert text[0] == "Проект: «Переговоры с заказчиком» · диалог 3b0978c6 · версий замысла: 2 · контент: d3f55f69"
    assert text[1] == "Шагов методолога в чатах (сообщения и кнопки маршрута): замысел — 2, контент — 0."
    assert text[2] == "Вызовов: 3 (не удалось: 1, исправлений формата: 0) · вход 251 040 · выход 50 160 токенов · время моделей 0,1 мин"
    assert text[3] == "По прайс-листу: 1,00 $ — без 2 вызовов моделей, которых нет в прайс-листе: anthropic/claude-opus-5, perplexity/sonar-pro"
    assert text[5:7] == ["", "Этап                                            Вызовов       Вход     Выход        $         ₽"]
    assert text[-2:] == ["", "По моделям: anthropic/claude-opus-5 — 1 выз., — $; openai/gpt-6-sol — 1 выз., 1,00 $; perplexity/sonar-pro — 1 выз., — $"]
    assert text[4] == "Списано: 120,50 ₽ — по данным шлюза (cost_rub); сумму назвали 1 вызовов из 3"
    rows = {line.split("  ")[0]: line for line in text[6:]}
    assert rows["Замысел · план заданий"].split()[-2:] == ["1,000", "120,50"]
    assert rows["Контент · поиск (ссылки, факты, первоисточник)"].split()[-2:] == ["—", "—"]
    assert text[-1].startswith("По моделям: ") and "perplexity/sonar-pro — 1 выз., — $" in text[-1] and "openai/gpt-6-sol — 1 выз., 1,00 $" in text[-1]
    spend(THREAD, "Concept")
    silent = [line for line in report.lines(report.report(project(THREAD))) if line.startswith("Списано")]
    assert silent == ["Списано: 120,50 ₽ — по данным шлюза (cost_rub); сумму назвали 1 вызовов из 4"]


def test_a_sum_that_leaves_some_calls_of_its_row_out_is_marked(folders):
    thread(folders)
    spend(THREAD, "FactReport", tokens=(500_000, 0))
    spend(THREAD, "FactReport", model="perplexity/sonar-pro", tokens=(40, 60))
    spend(THREAD, "search_text", model="perplexity/sonar-pro", kind="search")
    text = report.lines(report.report(project(THREAD)))
    rows = {line.split("  ")[0]: line.split() for line in text}
    assert rows["Контент · проверка фактов"][-2:] == ["1,000*", "—"]
    assert rows["Контент · поиск (ссылки, факты, первоисточник)"][-2:] == ["—", "—"]
    assert "* без вызовов моделей, которых нет в прайс-листе" in text
    assert "perplexity/sonar-pro — 2 выз., — $" in text[-1] and "openai/gpt-6-sol — 1 выз., 1,00 $" in text[-1]
    # No star without a row that leaves some of its calls out: all priced, or none.
    for unpriced in (False, True):
        usage.USAGE_DIR.joinpath(f"{THREAD}.jsonl").unlink()
        spend(THREAD, "Concept", model="perplexity/sonar-pro" if unpriced else "openai/gpt-6-sol")
        assert "* без вызовов моделей, которых нет в прайс-листе" not in report.lines(report.report(project(THREAD)))


def test_a_gateway_that_named_no_cost_is_said_so(folders):
    thread(folders)
    spend(THREAD, "Concept")
    text = report.lines(report.report(project(THREAD)))
    assert "Списано: шлюз не назвал сумму ни одного вызова (поля cost_rub в ответах нет)." in text
    assert not any(line.startswith("По моделям") for line in text)
    assert text[3] == "По прайс-листу: 0,00 $"


def test_the_command_shows_one_project_the_last_ones_and_says_when_there_is_none(folders, capsys):
    assert report.main([]) == 0 and capsys.readouterr().out == "Проектов нет.\n"
    thread(folders)
    thread(folders, ident=OTHER, run_ids=(OTHER,), title="Другой", created="2026-10-07T09:00:00+00:00")
    spend(SECOND, "Concept", tokens=(500_000, 0), cost=77.0)
    assert report.main(["content-d3f55f69"]) == 0
    shown = capsys.readouterr().out
    assert shown.startswith("Проект: «Переговоры с заказчиком» · диалог 3b0978c6 · версий замысла: 2 · контент: не начат\nШагов методолога в чатах")
    assert report.main(["d3f55f69", "--json"]) == 0
    as_json = capsys.readouterr().out
    assert json.loads(as_json)["charged_rub"] == 77.0 and "Переговоры с заказчиком" in as_json and as_json.startswith('{\n "project"')
    assert report.main(["aaaaaaaa"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "Проект «aaaaaaaa» не найден" in captured.err
    assert report.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Диалог    Начат             Вызовов        $         ₽  Проект"
    assert out[1].startswith("d3f55f00  2026-10-07 09:00") and out[2].startswith("3b0978c6  2026-10-07 07:30        1     1,00     77,00  Переговоры")
    assert out[3] == ""
    assert "Учёт вызовов ведётся с 07.10.2026" in out[-1]
    assert report.main(["--last", "1"]) == 0
    assert "3b0978c6" not in capsys.readouterr().out
    assert report.main(["--last", "0"]) == 0     # «none» is not asked for: the newest is shown
    assert "d3f55f00" in capsys.readouterr().out
    assert report.main(["--last", "1", "--json"]) == 0
    listed = capsys.readouterr().out
    assert [one["project"] for one in json.loads(listed)] == [OTHER] and "Другой" in listed and listed.startswith('[\n {\n  "project"')
    # A long title is cut so that the row stays a row; the time of the models is in minutes.
    thread(folders, ident=OTHER, run_ids=(OTHER,), title="Д" * 80, created="2026-10-07T09:00:00+00:00")
    token = usage.scope.set(OTHER)
    try:
        usage.record("model", "Concept", "openai/gpt-6-sol", 1, 1, 366_000)
    finally:
        usage.scope.reset(token)
    assert report.main(["--last", "1"]) == 0
    assert capsys.readouterr().out.splitlines()[1].endswith("  " + "Д" * 70)
    assert "время моделей 6,1 мин" in report.lines(report.report(project(OTHER)))[2]


# --- through the application: the route of a request names the project its calls are written under ---

def dialogue(folders, monkeypatch, answers):
    """A project with a concept under review and its dialogue; the gateway answers from a script."""
    run = reviewed_run()
    runs.save_run(run)
    monkeypatch.setattr(runs, "model_configuration", lambda: ("test-key", "unused", "configured/model"))
    Provider(monkeypatch, answers)
    return run


def said(text="Проект рассчитан на шесть часов.", tokens=(700, 40), cost=0.75):
    answer = reply(json.dumps({"kind": "question", "reply": text}, ensure_ascii=False), usage=tokens)
    answer.usage.model_extra = {"cost_rub": cost}
    return answer


def test_a_reply_in_the_dialogue_is_written_under_its_project_and_the_account_is_served_to_its_owner(folders, monkeypatch):
    run = dialogue(folders, monkeypatch, [said(), reply('{"answered": []}', usage=(300, 5))])
    with TestClient(app_module.app) as client:
        opened = client.post("/api/threads", json={"run_id": run.id}).json()
        heard = client.post(f"/api/threads/{opened['id']}/messages", json={"text": "Сколько часов на проект?"})
        assert heard.status_code == 200, heard.text
        served = client.get(f"/api/usage/{run.id}")
        assert client.get(f"/api/usage/{OTHER}").status_code == 404
        assert client.get("/api/usage/content-d3f55f69").status_code == 422
    assert served.status_code == 200, served.text
    found = served.json()
    assert found["project"] == run.id and found["recorded"] is True and found["replies"] == {"concept": 1, "content": 0}
    first = found["stages"][0]
    assert (first["stage"], first["calls"]) == ("Замысел · диалог", 1) and (first["input_tokens"], first["output_tokens"]) == (700, 40)
    assert found["charged_rub"] == 0.75 and found["models"][0]["model"] == "configured/model"
    assert all(call.scope == run.id and call.owner == "local" for call in usage.calls_of([run.id]))


@pytest.mark.parametrize("spelled", [str.upper, lambda ident: ident.replace("-", ""), lambda ident: "{" + ident + "}", lambda ident: "urn:uuid:" + ident])
def test_however_the_address_spells_the_id_the_call_is_written_under_the_project(folders, monkeypatch, spelled):
    """The route reads an id in every way a UUID is spelled; the account is kept by the one way the files are named."""
    run = dialogue(folders, monkeypatch, [said(), reply('{"answered": []}', usage=(300, 5))])
    with TestClient(app_module.app) as client:
        client.post("/api/threads", json={"run_id": run.id})
        heard = client.post(f"/api/threads/{spelled(run.id)}/messages", json={"text": "Сколько часов на проект?"})
    assert heard.status_code == 200, heard.text
    assert [call.asked for call in usage.calls_of([run.id])][:1] == ["Proposal"]
    assert [path.name for path in usage.USAGE_DIR.iterdir()] == [f"{run.id}.jsonl"]


def test_behind_the_prefix_of_the_proxy_the_call_is_written_all_the_same(folders, monkeypatch):
    """Production serves the module under «/app/project-preparation» (deploy/Dockerfile): the address a request
    carries there begins with the prefix, and the project must be found all the same."""
    run = dialogue(folders, monkeypatch, [said(), reply('{"answered": []}', usage=(300, 5))])
    prefix = "/app/project-preparation"
    with TestClient(app_module.app, root_path=prefix) as client:
        client.post(f"{prefix}/api/threads", json={"run_id": run.id})
        heard = client.post(f"{prefix}/api/threads/{run.id}/messages", json={"text": "Сколько часов на проект?"})
        served = client.get(f"{prefix}/api/usage/{run.id}").json()
    assert heard.status_code == 200, heard.text
    assert served["recorded"] is True and served["stages"][0]["stage"] == "Замысел · диалог"


def test_the_project_of_a_request_is_what_its_route_names_and_nothing_is_left_of_the_request_before():
    def asked(**params):
        async def request():   # a request is a task of its own: it begins with whatever the context held
            await app_module.project_scope(SimpleNamespace(path_params=params))  # type: ignore[arg-type]
            return usage.scope.get()

        return asyncio.run(request())

    token = usage.scope.set("left by the request before")
    try:
        assert asked(run_id=SECOND.upper()) == SECOND
        assert asked(thread_id=THREAD, message_id="m1") == THREAD
        assert asked(ident=THREAD) == ""            # the account itself is asked for: no project is worked on
        assert asked(run_id=SECOND) == SECOND and asked() == ""
        assert asked(run_id="not-an-id") == "" and asked(run_id="") == ""
    finally:
        usage.scope.reset(token)


def test_every_route_that_works_on_a_project_names_it_by_a_parameter_the_account_knows():
    """A route added with another name for the id — «draft_id» — would make its calls without a project."""
    worked_on = [path for path in app_module.app.openapi()["paths"] if path.startswith(("/api/runs/{", "/api/threads/{", "/api/content/{"))]
    assert len(worked_on) >= 25, worked_on
    unnamed = [path for path in worked_on if path.split("{")[1].split("}")[0] not in app_module.PROJECT_PARAMS]
    assert unnamed == []


def test_a_search_for_what_is_current_is_written_under_its_run(folders, monkeypatch):
    run = reviewed_run()
    runs.save_run(run)
    Finder(monkeypatch, found(json.dumps({"summary": "Замысел актуален."}, ensure_ascii=False), cost_rub=2.4))
    with TestClient(app_module.app) as client:
        checked = client.post(f"/api/runs/{run.id}/freshness")
    assert checked.status_code == 200, checked.text
    assert [(c.kind, c.asked, c.scope, c.cost_rub) for c in usage.calls_of([run.id])] == [("search", "SearchAnswer", run.id, 2.4)]
    assert report.STAGES["SearchAnswer"] == "Замысел · проверка актуальности (поиск)"


def test_a_reply_in_the_chat_of_the_content_is_written_under_its_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONTENT_DIR", tmp_path / "content")
    monkeypatch.setattr(content_api, "model_configuration", lambda: ("test-key", "unused", "configured/model"))
    monkeypatch.setattr(content_api.settings, "handoff", lambda run_id: handoff_fixture(run_id))
    fake = FakeModel()
    monkeypatch.setattr(content_api.settings, "writer", lambda: Writer(fake, search=FakeSearch(), opener=open_pages, reader=read_pages))  # type: ignore[arg-type]
    with TestClient(app_module.app) as client:
        client.post(f"/api/content/{SECOND}/start")
        wait_idle(client, SECOND)
        assert usage.calls_of([SECOND]) == []          # the stand-in model is not a call of a gateway
        Provider(monkeypatch, [said("Стиль выбран по аудитории.", tokens=(9000, 120), cost=4.2)])
        monkeypatch.setattr(content_api.settings, "writer", lambda: Writer(ModelClient(), search=FakeSearch(), opener=open_pages, reader=read_pages))  # type: ignore[arg-type]
        heard = client.post(f"/api/content/{SECOND}/messages", json={"text": "Почему такой стиль?"})
        assert heard.status_code == 200, heard.text
        wait_idle(client, SECOND)
    assert [(c.asked, c.scope, c.input_tokens, c.cost_rub) for c in usage.calls_of([SECOND])] == [("ContentRevision", SECOND, 9000, 4.2)]


def test_the_account_of_another_owner_is_not_served(folders, monkeypatch):
    thread(folders, owner="user-7")
    spend(THREAD, "Concept", owner="user-7", cost=10.0)
    with TestClient(app_module.app) as client:
        assert client.get(f"/api/usage/{THREAD}").status_code == 404


def test_calls_another_owner_made_under_my_project_are_not_in_my_account(folders):
    """An administrator who opened the project, an evaluation run on the server: what they spent is theirs."""
    thread(folders)
    spend(THREAD, "Concept", cost=10.0)
    spend(THREAD, "Concept", owner="user-7", cost=1000.0)
    with TestClient(app_module.app) as client:
        found = client.get(f"/api/usage/{THREAD}").json()
    assert (found["calls"], found["charged_rub"]) == (1, 10.0)


def test_a_job_is_accounted_under_its_own_run_or_draft_whichever_request_started_it(folders, monkeypatch):
    seen = []

    async def job(*given):
        seen.append(usage.scope.get())

    monkeypatch.setattr(flow, "run_job", job)
    monkeypatch.setattr(runs, "prepare", job)
    monkeypatch.setattr(runs, "ModelClient", lambda: None)
    monkeypatch.setattr(runs, "reviewer_client", lambda: None)
    run = reviewed_run()

    async def go():
        flow.launch(SECOND, ("write", None), lambda: None)  # type: ignore[arg-type, return-value]
        runs.launch(run)
        await asyncio.gather(flow.jobs[SECOND], runs.active[run.id])

    asyncio.run(go())
    assert seen == [SECOND, run.id] and usage.scope.get() == ""


# --- projects that are not one dialogue with its versions ---

def lone(folder, ident, owner="local", title="Без диалога", created="2026-10-07T06:00:00+00:00"):
    data = {"id": ident, "owner_id": owner, "created_at": created, "request": {"brief": {"title": title}}}
    (folder / "runs" / f"{ident}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_a_file_that_keeps_less_than_this_version_writes_is_read_all_the_same(folders):
    """A dialogue or a run kept by an older version — or by hand — has its id; the rest is what the file has."""
    (folders / "runs" / "threads" / f"{THREAD}.json").write_text(json.dumps({"id": THREAD, "runs": [THREAD]}), encoding="utf-8")
    assert report.threads() == [{"id": THREAD, "runs": [THREAD], "owner": "", "title": "", "created_at": "", "replies": 0}]
    (folders / "runs" / f"{OTHER}.json").write_text(json.dumps({"id": OTHER, "request": {"brief": {}}}), encoding="utf-8")
    assert report.lone_runs() == [{"id": OTHER, "runs": [OTHER], "owner": "local", "title": "", "created_at": "", "replies": 0}]
    assert report.lone_runs(owner="local") == report.lone_runs() and report.lone_runs(owner="user-7") == []
    lone(folders, SECOND, created="2026-10-07T10:00:00+00:00", title="Позже")
    lone(folders, "aaaaaaaa-0000-4000-8000-000000000000", created="2026-10-07T08:00:00+00:00", title="Раньше")
    assert [one["title"] for one in report.lone_runs()] == ["Позже", "Раньше", ""]      # the newest first
    assert [one["title"] for one in report.lone_runs("d3f55f")] == ["Позже", ""] and report.lone_runs(taken=frozenset({SECOND, OTHER}))[0]["title"] == "Раньше"
    # The replies of every draft of the project are counted, and a draft that does not read hides none of the others.
    for run_id, replies in ((THREAD, 2), (OTHER, 1)):
        (folders / "content" / f"{run_id}.json").write_text(json.dumps({"messages": [{"role": "methodologist"}] * replies + [{"role": "assistant"}]}), encoding="utf-8")
    (folders / "content" / f"{SECOND}.json").write_text("{", encoding="utf-8")
    assert report.content_replies([SECOND, THREAD, "absent", OTHER]) == 3


def test_a_run_begun_without_a_dialogue_is_a_project_of_one_version(folders, capsys):
    thread(folders)
    for run_id in (THREAD, SECOND):
        lone(folders, run_id, title="Версия в диалоге")
    lone(folders, OTHER)
    lone(folders, "eeeeeeee-0000-4000-8000-000000000000", owner="user-7", title="Чужой")
    for name in ("00000000-broken", "broken", "ffffffff-broken"):   # wherever the folder lists it, the rest is read
        (folders / "runs" / f"{name}.json").write_text("{", encoding="utf-8")
    spend(OTHER, "Concept", cost=12.0)
    found = report.report(project("d3f55f00"))
    assert (found["project"], found["title"], found["concept_versions"], found["replies"]) == (OTHER, "Без диалога", 1, {"concept": 0, "content": 0})
    assert (found["calls"], found["charged_rub"]) == (1, 12.0)
    assert report.project_of(OTHER, "user-7") is None and project(SECOND)["id"] == THREAD   # a version is not a project by itself
    assert [one["id"] for one in report.projects("local")] == [THREAD, OTHER] and len(report.projects()) == 3
    assert [one["title"] for one in report.projects("user-7")] == ["Чужой"] and report.project_of("eeeeeeee", "local") is None
    assert report.main([]) == 0
    assert [line[:8] for line in capsys.readouterr().out.splitlines()[1:4]] == ["3b0978c6", "d3f55f00", "eeeeeeee"]


def test_a_version_opened_as_a_dialogue_of_its_own_still_names_the_whole_project(folders):
    thread(folders)                                                                   # the dialogue with both versions
    thread(folders, ident=SECOND, run_ids=(SECOND,), created="2026-10-07T08:00:00+00:00")   # the later version, opened alone
    assert project(SECOND)["id"] == THREAD and project("content-d3f55f69")["runs"] == [THREAD, SECOND]
    assert project(THREAD)["id"] == THREAD
    thread(folders, ident=OTHER, run_ids=(OTHER, SECOND), created="2026-10-07T05:00:00+00:00")   # as many versions, begun earlier
    assert project(SECOND)["id"] == OTHER


@pytest.mark.parametrize("ident", ["../../etc", "d3f55f69*", "d3f55f69/../x", "D3F55F6", "content-", "zzzzzzzz", "d3f55", "-d3f55f69", "d3f55f69" + "0" * 29])
def test_an_id_is_hexadecimal_and_six_characters_at_least(folders, ident):
    thread(folders)
    lone(folders, OTHER)
    assert report.project_of(ident) is None or ident == "D3F55F6"   # capitals are read as the id is


def test_the_evaluation_of_the_chat_keeps_its_calls_out_of_the_account_of_the_project(folders):
    from project_generator_lab import chat_eval

    thread(folders)
    with chat_eval.sandbox(store.CONTENT_DIR, runs.RUNS_DIR, THREAD):
        assert usage.USAGE_DIR != folders / "usage" and runs.RUNS_DIR != folders / "runs"
        spend(THREAD, "ContentRevision")
        assert len(usage.calls_of([THREAD])) == 1
    assert usage.USAGE_DIR == folders / "usage" and usage.calls_of([THREAD]) == []
