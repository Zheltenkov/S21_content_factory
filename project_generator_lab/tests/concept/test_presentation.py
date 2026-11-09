"""How the text is to be told — the style and the story of the series — is asked in the form and read by the content
stage only. The concept decides which tasks prove the outcomes; it is not shown these fields and does not change
with them (decided 06.10.2026)."""

import json

import pytest
from project_generator_lab import bridge
from project_generator_lab.concept.models import PRESENTATION, Brief
from project_generator_lab.concept.planning import build_evidence, context_payload
from project_generator_lab.concept.route import changed_fields
from project_generator_lab.concept.thread import FIELDS
from project_generator_lab.concept.validation import source_registry
from project_generator_lab.tests.concept.test_dialogue import DialogueClient, api, start_thread  # noqa: F401 - fixture
from project_generator_lab.tests.concept.test_validation import new_run
from project_generator_lab.tests.concept.test_workbench import request_fixture
from project_generator_lab.tests.test_bridge import approved_run, folders  # noqa: F401 - fixture
from pydantic import ValidationError

STORY = "Комната с терминалом. ИИ по имени Марк открывает двери за починенные модули."


def told(**fields):
    """The request of the tests with the presentation fields filled."""
    request = request_fixture()
    return request.model_copy(update={"brief": request.brief.model_copy(update=fields)})


def test_the_form_takes_a_style_it_knows_and_a_story_of_any_words():
    base = request_fixture().brief.model_dump()
    assert (Brief(**base).style_profile, Brief(**base).series_story) == ("", "")
    for profile in ("", "quest", "legend"):
        assert Brief(**{**base, "style_profile": profile, "series_story": f"  {STORY} "}).series_story == STORY
    with pytest.raises(ValidationError):
        Brief(**{**base, "style_profile": "комикс"})
    with pytest.raises(ValidationError):
        Brief(**{**base, "series_story": "я" * 6001})
    assert PRESENTATION == ("style_profile", "series_story", "repository", "generation", "collaboration_mode") and set(PRESENTATION) <= set(Brief.model_fields)
    # The address of the repository is the plan's own words, kept as given; the concept does not read it either.
    kept = Brief(**{**base, "repository": "  https://git.example.org/masters/D1P1_Terminal.ID_1.git "})
    assert kept.repository == "https://git.example.org/masters/D1P1_Terminal.ID_1.git" and "repository" not in kept.for_concept()
    assert Brief(**base).repository == ""
    with pytest.raises(ValidationError):
        Brief(**{**base, "repository": "я" * 501})


def test_the_concept_is_not_shown_how_the_text_is_told():
    plain, styled = request_fixture(), told(style_profile="quest", series_story=STORY)
    # Not a source of the concept: no evidence of the form carries it, and the card of sources counts and signs the form without it.
    evidence, _ = build_evidence(styled)
    assert [e.id for e in evidence] == [e.id for e in build_evidence(plain)[0]]
    assert not [e for e in evidence if "quest" in e.text or "Марк" in e.text]
    form, same = source_registry(styled, evidence)[0], source_registry(plain.model_copy(update={"request_id": styled.request_id}), evidence)[0]
    assert (form.sha256, form.extracted_count, form.selected_count) == (same.sha256, same.extracted_count, same.selected_count)
    # Not in what the models of the concept are sent.
    run = new_run()
    run.request = styled
    sent = json.dumps(context_payload(run), ensure_ascii=False)
    assert "style_profile" not in sent and "series_story" not in sent and "Марк" not in sent
    assert set(styled.brief.for_concept()) == set(Brief.model_fields) - set(PRESENTATION)
    # The fingerprint of a form stored before these fields is what it was: written as the form was then.
    assert json.loads(plain.brief.model_dump_json(exclude=set(PRESENTATION))) == plain.brief.for_concept()


def test_the_chat_of_the_concept_neither_edits_them_nor_asks_for_a_new_version(api, monkeypatch):  # noqa: F811
    # A change of the style must not send the methodologist through the paid preparation of the concept again: the
    # fields are not among those the chat of the concept edits, and are talked over when the content begins.
    assert not set(PRESENTATION) & set(FIELDS)
    thread, model = start_thread(api, monkeypatch, lambda r: DialogueClient(r, None))
    api.post(f"/api/threads/{thread['id']}/messages", json={"text": "Сделай задание про переговоры"})
    sent = next(payload for name, _, payload in model.calls if name == "Proposal")
    assert "style_profile" not in sent["draft"] and "series_story" not in sent["draft"] and "title" in sent["draft"]
    refused = api.post(f"/api/threads/{thread['id']}/fields", json={"field": "style_profile", "value": "quest"})
    assert refused.status_code == 422


def test_a_run_differs_from_its_thread_only_by_what_the_concept_reads():
    from project_generator_lab.concept.thread import Thread

    run = new_run()
    thread = Thread(id="t", owner_id="local", runs=[run.id], draft=run.request.brief.model_copy(update={"style_profile": "quest"}))
    assert changed_fields(thread, run) == []


def test_a_new_version_does_not_tell_the_concept_that_the_style_changed():
    from project_generator_lab.concept.validation import revision_changes

    previous = new_run()
    again = previous.request.model_copy(update={"brief": previous.request.brief.model_copy(update={
        "style_profile": "quest", "series_story": STORY, "hours": 6})})
    assert [change.location for change in revision_changes(previous, again)] == ["brief.hours"]


def test_the_content_stage_is_handed_both(folders):  # noqa: F811
    run = approved_run()
    assert (bridge.handoff(run.id).style_profile, bridge.handoff(run.id).series_story) == ("", "")
    run.request.brief.style_profile, run.request.brief.series_story = "legend", STORY
    from project_generator_lab.concept import runs

    runs.save_run(run)
    handoff = bridge.handoff(run.id)
    assert (handoff.style_profile, handoff.series_story) == ("legend", STORY)
