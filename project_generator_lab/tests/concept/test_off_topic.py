"""Noise in the inputs: text unrelated to the project is shown with its place, asked about, and — once the methodologist
agrees — cut out by code from everything the models read, the content generator included. A file fragment usually mixes
noise with text that matters (a .md file is read 35 lines at a time), so code cuts the quoted text, not the fragment."""

from typing import cast

import pytest
from project_generator_lab import bridge
from project_generator_lab.concept import runs
from project_generator_lab.concept.dialogue import dialogue_payload
from project_generator_lab.concept.ingestion import extract_material
from project_generator_lab.concept.models import InputCard, OffTopic, Question, Run, StartRequest, exclusions
from project_generator_lab.concept.patches import build_patches, checked_exclusions
from project_generator_lab.concept.planning import build_evidence, fingerprint, prepare
from project_generator_lab.concept.route import changed_fields, next_step
from project_generator_lab.concept.thread import FIELDS, MessageRequest, ProposedChange, Thread
from project_generator_lab.tests.concept.test_validation import WorkflowClient, reviewed_run
from project_generator_lab.tests.concept.test_workbench import request_fixture

BRIEF = "Бриф заказчика: интервью с клиентами службы доставки.\nКлиенты жалуются на сроки доставки и на поддержку."
BUNS = ("Булочки с корицей: 500 г муки, 10 г сухих дрожжей, 250 мл тёплого молока.\n"
        "Выпекать 25 минут при 180 градусах.")
CRITERIA = "Критерии заказчика: отчёт связывает выводы с цитатами интервью."


def noisy_request() -> StartRequest:
    request = request_fixture()
    text = f"{BRIEF}\n\n{BUNS}\n\n{CRITERIA}"
    material = extract_material("бриф.md", text.encode()).model_copy(update={"role": "customer"})
    return request.model_copy(update={"materials": [material]})


def fragment(request: StartRequest) -> str:
    [passage] = request.materials[0].passages  # brief, recipe and criteria are one fragment, as in a real .md brief
    return passage.id


class NoticingClient(WorkflowClient):
    """The interpreter marks the recipe (rewrapped, as models do), one item with only a quote, and two it made up."""

    async def generate(self, schema, system, payload):
        response, metrics = await super().generate(schema, system, payload)
        if schema is InputCard:
            key = fragment(self.request)
            response.off_topic = [
                OffTopic(evidence_id=key, quote="Булочки с корицей", why="Рецепт не связан с разбором интервью", text=BUNS.replace("\n", " ")),
                OffTopic(evidence_id=key, quote="Выпекать 25 минут", why="Тоже рецепт", text="Выпекать 30 минут"),
                OffTopic(evidence_id=key, quote="Пельмени", why="Нет такого текста", text="Пельмени"),
                OffTopic(evidence_id="s-invented-1", quote="?", why="?")]
            response.questions.append(Question(question="Не учитывать рецепт булочек в бриф.md?", why="Он не связан с проектом.", blocking=False))
        return response, metrics


def test_exclusion_entries_keep_multiline_quotes_whole():
    text = f"s-a-1: «{BUNS}» — рецепт\n\n- s-b-2 — чужая переписка\ns-a-1: бриф.md, строки 3–4: «Пельмени» — ещё"
    assert exclusions(text) == {"s-a-1": [BUNS, "Пельмени"], "s-b-2": [None]}


@pytest.mark.anyio
async def test_the_card_keeps_only_noise_that_is_really_where_it_says():
    request = noisy_request()
    run = Run(id=str(request.request_id), request=request, fingerprint=fingerprint(request))
    client = NoticingClient(request)
    await prepare(run, lambda r: None, client)
    # The interpreter saw the recipe: noticing it is its job, removing it is the methodologist's decision.
    assert any("дрожж" in e["text"] for e in client.calls[0][2]["evidence"])
    # The made-up id asked for one repair call; what still points nowhere is dropped, the attempt keeps the model's answer.
    assert [name for name, _, _ in client.calls] == ["InputCard", "InputCard"]
    assert run.input_card and [(x.quote, x.text) for x in run.input_card.off_topic] == [
        ("Булочки с корицей", BUNS.replace("\n", " ")), ("Выпекать 25 минут", "Выпекать 25 минут")]
    assert len(run.input_attempts[-1].off_topic) == 4
    thread = Thread(id=run.id, owner_id="local", runs=[run.id], draft=request.brief)
    item = cast(list[dict[str, str]], dialogue_payload(thread, run, MessageRequest(text="да, убери"))["off_topic"])[0]
    assert (item["source"], item["location"], item["text"]) == ("бриф.md", "Строки 1–7 · фрагмент 1", BUNS.replace("\n", " "))


def test_agreed_exclusion_becomes_a_brief_edit_and_a_wrong_one_is_refused():
    run = reviewed_run()
    request = noisy_request()
    run.request, run.evidence = request, build_evidence(request)[0]
    key = fragment(request)
    thread = Thread(id=run.id, owner_id="local", runs=[run.id], draft=run.request.brief)

    def propose(value: str) -> tuple[list, list[str]]:
        change = ProposedChange(field="excluded_sources", value=value, reason="Методолог согласился", origin="answer", question_numbers=[1])
        return checked_exclusions(run, *build_patches(thread.draft, [change]))

    patches, notes = propose(f"{key}: «{BUNS}» — рецепт выпечки")
    assert not notes and patches[0].label == FIELDS["excluded_sources"] == "Не учитывать фрагменты файлов"
    # Text that is not there, a fragment that does not exist, a whole fragment that also holds the brief: refused.
    for wrong, reason in ((f"{key}: «Пельмени» — рецепт", "нет текста «Пельмени»"), ("s-none-1: «x» — ?", "нет во входах")):
        refused, notes = propose(wrong)
        assert not refused and reason in notes[0]
    run.evidence[-1] = run.evidence[-1].model_copy(update={"text": run.evidence[-1].text * 20})
    refused, notes = propose(f"{key} — рецепт")
    assert not refused and "нужна точная цитата" in notes[0]
    thread.draft = thread.draft.model_copy(update={"excluded_sources": patches[0].after})
    assert changed_fields(thread, run) == ["excluded_sources"] and next_step(thread, run)["action"] == "new_version"


def test_excluded_text_reaches_no_model_and_not_the_content_generator(tmp_path, monkeypatch):
    request = noisy_request()
    key = fragment(request)
    exclusion = f"{key}: «{BUNS.replace(chr(10), ' ')}» — рецепт выпечки\n{key}: «Пельмени» — чужой текст"
    request = request.model_copy(update={"brief": request.brief.model_copy(update={"excluded_sources": exclusion})})
    evidence, warnings = build_evidence(request)
    [kept] = [e for e in evidence if e.id == key]
    # The recipe is cut; the brief and the criteria around it stay. A quote that is not there cuts nothing.
    assert "дрожж" not in kept.text and "Выпекать" not in kept.text and BRIEF in kept.text and CRITERIA in kept.text
    assert any("по решению методолога вырезано" in w and "остальной текст учитывается" in w for w in warnings)
    assert any("«Пельмени» не найден дословно" in w for w in warnings)
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    run = reviewed_run()
    run.request = request
    run.status, run.approved_at = "approved", "2026-09-30T10:00:00+00:00"
    runs.save_run(run)
    [source] = bridge.handoff(run.id).sources
    assert "Клиенты жалуются" in source.text and CRITERIA in source.text and "дрожж" not in source.text


def test_a_second_exclusion_is_checked_alone_and_the_first_is_not_rechecked():
    """After the first exclusion the next version no longer has the recipe in its evidence. When the methodologist then
    excludes something else, the model writes the whole field again, the first line included: only the new line is
    checked, or the whole proposal would be refused because the recipe «is not there»."""
    run = reviewed_run()
    request = noisy_request()
    key = fragment(request)
    first = f"{key}: «{BUNS}» — рецепт выпечки"
    request = request.model_copy(update={"brief": request.brief.model_copy(update={"excluded_sources": first})})
    run.request, run.evidence = request, build_evidence(request)[0]
    thread = Thread(id=run.id, owner_id="local", runs=[run.id], draft=request.brief)
    change = ProposedChange(field="excluded_sources", value=f"{first}\n{key}: «{CRITERIA}» — не нужно", reason="r", origin="request")
    patches, notes = checked_exclusions(run, *build_patches(thread.draft, [change]))
    assert not notes and len(patches) == 1
    wrong = ProposedChange(field="excluded_sources", value=f"{first}\n{key}: «Пельмени» — нет такого", reason="r", origin="request")
    patches, notes = checked_exclusions(run, *build_patches(thread.draft, [wrong]))
    assert not patches and "нет текста «Пельмени»" in notes[0] and "Булочки" not in notes[0]
