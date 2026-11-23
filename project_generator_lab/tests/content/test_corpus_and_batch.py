"""Large materials resume after failure; batch generation never impersonates human approval."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from project_generator_lab import app as app_module
from project_generator_lab.content import corpus, flow, store
from project_generator_lab.content.models import ContentDraft, Material, MaterialDraft
from project_generator_lab.content.writer import CONTENT_VERSION, Writer, snapshot
from project_generator_lab.core.llm import CallMetrics, ModelUnavailable
from project_generator_lab.tests.content.fixtures import FakeModel, FakeSearch, handoff_fixture, open_pages, read_pages


def new_draft():
    return ContentDraft(id=str(uuid4()), owner_id="local", content_version=CONTENT_VERSION,
                        handoff=handoff_fixture(), prompt_snapshots=snapshot())


def test_a_failed_large_corpus_resumes_completed_batches_without_padding(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONTENT_DIR", tmp_path)
    draft = new_draft()
    draft.materials = [Material(path="dialogs.txt", kind="data", purpose="Считать частоты слов", contains="Синтетические диалоги разных тематик",
                                target_words=1700, tasks=[1])]
    store.save(draft)

    class CorpusWriter:
        fail = True
        requested = []

        async def material_batch(self, seen, path, words, part):
            self.requested.append(part)
            if self.fail and part == 2:
                raise ModelUnavailable("Перерыв провайдера")
            return MaterialDraft(content=" ".join(f"часть{part}слово{i}" for i in range(words)), note="Синтетические данные"), CallMetrics("test", 1, 1, 1)

    writer = CorpusWriter()
    with pytest.raises(ModelUnavailable):
        asyncio.run(flow.write_corpus(draft.id, "dialogs.txt", writer))
    partial = store.load(draft.id).materials[0]
    assert len(partial.generation_parts) == 1 and not partial.content and partial.status == "planned"
    writer.fail = False
    asyncio.run(flow.write_corpus(draft.id, "dialogs.txt", writer))
    ready = store.load(draft.id).materials[0]
    assert corpus.word_count(ready.content) == 1700 and ready.status == "draft"
    assert writer.requested == [1, 2, 2, 3]
    assert "содержательная проверка" in ready.note


def test_batch_writes_missing_parts_and_reviews_without_approving_them(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONTENT_DIR", tmp_path)
    draft = new_draft()
    store.save(draft)
    model = FakeModel()
    reviewer = FakeModel()
    writer = Writer(model, reviewer=reviewer, search=FakeSearch(), opener=open_pages, reader=read_pages)
    asyncio.run(flow.prepare_bundle(draft.id, writer))
    ready = store.load(draft.id)
    assert ready.batch_prepared and ready.review and ready.summary and ready.theory and ready.frame
    assert len(ready.tasks) == len(ready.handoff.tasks)
    assert all(t.status == "draft" for t in ready.tasks)
    assert all(m.status == "draft" for m in ready.materials)
    before = ready.digest()
    calls_before = (len(model.calls), len(reviewer.calls))
    asyncio.run(flow.prepare_bundle(draft.id, writer))
    assert store.load(draft.id).digest() == before
    assert (len(model.calls), len(reviewer.calls)) == calls_before
    with TestClient(app_module.app) as client:
        result = client.post(f"/api/content/{draft.id}/approve-bundle")
        assert result.status_code == 200
        assert all(t["status"] == "approved" for t in result.json()["tasks"])


def test_settings_can_be_reset_after_a_provider_rejects_custom_sampling(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "CONTENT_DIR", tmp_path)
    draft = new_draft()
    draft.handoff.generation.temperature = 0.8
    store.save(draft)
    with TestClient(app_module.app) as client:
        invalid = client.post(f"/api/content/{draft.id}/generation", json={"temperature": 0.9, "top_p": 0.8})
        assert invalid.status_code == 422
        reset = client.post(f"/api/content/{draft.id}/generation", json={"variety": "expressive"})
        assert reset.status_code == 200
        assert store.load(draft.id).handoff.generation.sampling() == {}
