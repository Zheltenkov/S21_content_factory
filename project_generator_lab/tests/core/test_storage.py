"""Storage: locks per thread or draft (shared while held, gone afterwards) and stored files of a newer release."""

import asyncio
import gc
import hashlib
import json

import pytest
from project_generator_lab.concept.models import Brief, Run
from project_generator_lab.concept.thread import Message, Thread
from project_generator_lab.content.models import ContentDraft
from project_generator_lab.core.contracts import STORED_SCHEMA
from project_generator_lab.core.storage import Locks, parse_stored
from project_generator_lab.evaluation.models import Evaluation
from pydantic import ValidationError


def test_a_lock_is_shared_while_held_and_forgotten_after():
    locks = Locks()
    order: list[str] = []

    async def change(name: str, key: str, pause: float) -> None:
        async with locks(key):
            order.append(f"{name}+")
            await asyncio.sleep(pause)
            order.append(f"{name}-")

    async def main() -> None:
        # Two changes of one thread never interleave; a change of another thread does not wait for them.
        await asyncio.gather(change("a", "t1", 0.02), change("b", "t1", 0), change("c", "t2", 0))

    asyncio.run(main())
    assert order.index("a-") < order.index("b+") and order.index("c+") < order.index("a-")
    gc.collect()
    assert len(locks.held) == 0
    # Retention: the same key while the first holder is still inside gets that same lock.
    first = locks("t3")
    assert locks("t3") is first and locks("t4") is not first


# ---------- stored files of a newer release ----------

def stored_thread(**top: object) -> dict:
    thread = Thread(id="t1", owner_id="local", runs=["r1"], draft=Brief(title="Проект", description="Описание проекта", audience="a", outcomes="Выделять факты"),
                    messages=[Message(role="assistant", text="Ответ")])
    return {**thread.model_dump(mode="json"), **top}


def test_a_file_of_a_newer_release_is_read_without_the_fields_this_release_does_not_know(caplog):
    data = stored_thread(schema_version=STORED_SCHEMA + 1, mood="новое поле")
    data["messages"][0]["tone"] = "вложенное новое поле"
    thread = parse_stored(Thread, json.dumps(data, ensure_ascii=False))
    assert thread.messages[0].text == "Ответ" and thread.draft.title == "Проект"
    # What is held now has this release's shape: saved back, it carries this release's number.
    assert thread.schema_version == STORED_SCHEMA
    assert f"stored Thread of schema {STORED_SCHEMA + 1} read by schema {STORED_SCHEMA}; left out: Thread.mood, Message.tone" in caplog.text
    caplog.clear()
    parse_stored(Thread, json.dumps(stored_thread(schema_version=STORED_SCHEMA + 1), ensure_ascii=False))
    assert "left out: nothing" in caplog.text


def test_an_unknown_field_in_a_file_of_this_or_an_older_release_is_still_refused():
    # A field since removed must not be guessed away: the old draft is set aside, as before.
    for version in (STORED_SCHEMA, 0):
        with pytest.raises(ValidationError):
            parse_stored(Thread, json.dumps(stored_thread(schema_version=version, mood="x"), ensure_ascii=False))
    with pytest.raises(ValidationError):
        parse_stored(Thread, json.dumps({k: v for k, v in stored_thread(mood="x").items() if k != "schema_version"}))
    for broken in ("{не json", "[]", "null"):
        with pytest.raises(ValidationError):
            parse_stored(Thread, broken)
    # Model answers and requests stay strict whatever they claim.
    with pytest.raises(ValidationError):
        Thread.model_validate(stored_thread(schema_version=STORED_SCHEMA + 1, mood="x"))


# The digest of the stored files' full schema (fields, types, allowed values, limits) recorded for each STORED_SCHEMA.
# A failing test means the stored shape changed: raise STORED_SCHEMA in core/contracts.py and record the new digest
# here. A rollback to the previous release then reads the new files, leaving new fields out (see above). New values of a
# Literal, a changed type or a tighter limit are not fixed by that: such a rollback needs the data backup
# (deploy/README.md).
SHAPES = {1: "befb3b90", 2: "286842e2", 3: "d9fe9b72", 4: "ab2471a9", 5: "7bc9282f", 6: "d9e95895", 7: "06ce175b"}


def test_the_stored_fields_match_the_schema_number():
    # Since schema 7 the evaluation artifacts participate in the same rollback contract.
    schemas = [model.model_json_schema() for model in (Thread, Run, ContentDraft, Evaluation)]
    digest = hashlib.sha256(json.dumps(schemas, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]
    assert SHAPES.get(STORED_SCHEMA) == digest, (
        f"Поля сохраняемых файлов изменились (отпечаток {digest}): увеличьте STORED_SCHEMA в core/contracts.py "
        "и запишите отпечаток в SHAPES")


def test_an_older_file_read_and_saved_again_carries_this_releases_number(tmp_path):
    # It gains this release's fields when it is saved. Under its old number the previous release, rolled back to,
    # took it for a file of its own and refused those fields (independent review 05.10.2026).
    older = parse_stored(Thread, json.dumps(stored_thread(schema_version=STORED_SCHEMA - 1), ensure_ascii=False))
    assert older.schema_version == STORED_SCHEMA
    from project_generator_lab.core.storage import write_model
    write_model(tmp_path / "thread.json", older)
    assert json.loads((tmp_path / "thread.json").read_text(encoding="utf-8"))["schema_version"] == STORED_SCHEMA
    # A file without a number (before numbers existed) and a file of this release are held with this number too.
    unnumbered = {k: v for k, v in stored_thread().items() if k != "schema_version"}
    assert parse_stored(Thread, json.dumps(unnumbered, ensure_ascii=False)).schema_version == STORED_SCHEMA
    assert parse_stored(Thread, json.dumps(stored_thread(schema_version=STORED_SCHEMA), ensure_ascii=False)).schema_version == STORED_SCHEMA


def test_a_run_stored_before_plan_rows_had_a_kind_opens_with_projects():
    from project_generator_lab.tests.concept.test_validation import new_run
    run = new_run()
    from project_generator_lab.concept.ingestion import parse_curriculum
    run.request.curriculum = parse_curriculum("plan.csv", "Название проекта;Формат\nПроект;\nИтог;экзамен\n".encode())
    run.request.selected_row_id = run.request.curriculum.rows[0].id
    stored = json.loads(run.model_dump_json())
    stored["schema_version"] = 1
    for row in stored["request"]["curriculum"]["rows"]:
        for added in ("kind", "code", "cases"):
            del row[added]
    opened = parse_stored(Run, json.dumps(stored, ensure_ascii=False))
    # A row without a kind is a project, as every row was; the run is now held, and saved, as a file of this release.
    assert [r.kind for r in opened.request.curriculum.rows] == ["project", "project"]  # type: ignore[union-attr]
    assert opened.schema_version == STORED_SCHEMA


def test_a_stored_model_without_a_schema_number_is_read_as_it_is():
    from pydantic import BaseModel

    class Note(BaseModel):
        text: str

    assert parse_stored(Note, '{"text": "а"}') == Note(text="а")
    assert "schema_version" not in parse_stored(Note, '{"text": "а"}').__dict__  # nothing is added to a model that keeps no number
    # A number in the file that the model has no field for: read by the usual strict rules, nothing to stamp.
    assert parse_stored(Note, json.dumps({"text": "а", "schema_version": STORED_SCHEMA + 1})) == Note(text="а")
