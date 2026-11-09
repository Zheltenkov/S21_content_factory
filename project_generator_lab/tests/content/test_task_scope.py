"""A task is as large as its hours (content 0.10). On 28.10.2026 the methodologist: «6 и 60 часов при генерации ничем
не отличаются». The concept counted the hours; the writer of a task was given them and told nothing of them, and was
told to keep the volume of the first task — so a task of an hour and one of seven were written alike."""

from typing import Any

import pytest
from project_generator_lab.content import writer
from project_generator_lab.content.writer import PROMPTS, material_payload, task_payload
from project_generator_lab.tests.content.test_theory_plan import planned


def drafted(version=None):
    draft = planned("legend", "chapter")
    draft.content_version = writer.CONTENT_VERSION if version is None else version
    hours = {1: 1.0, 2: 4.0, 3: 6.0}
    for task in draft.handoff.tasks:
        task.hours = hours.get(task.number, task.hours)
    return draft


def test_the_writer_of_a_task_is_told_to_measure_the_work_by_the_hours():
    asked = PROMPTS.text("task")
    assert "Объём — по часам задания." in asked
    for band in ("- до 2 ч — одна операция", "- 2–4 ч — результат из двух-трёх частей", "- 4–6 ч — небольшая законченная вещь",
                 "- больше 6 ч (методолог оставил часы выше нормы)"):
        assert band in asked
    # The first task is a model of the style, no longer of the volume.
    assert "держись его стиля и устройства, но не объёма" in asked and "стиля, структуры и объёма" not in asked
    assert "почему объём соответствует часам задания" in asked
    assert "по его часам (tasks[].hours)" in PROMPTS.text("material")
    critic = PROMPTS.text("critic")
    assert "13. Объём по часам" in critic and critic.index("12. Подача теории") < critic.index("13. Объём по часам")
    assert writer.CONTENT_VERSION == "0.11"


def test_a_task_is_written_seeing_the_hours_of_the_others_and_a_material_those_of_its_tasks():
    draft = drafted()
    others: Any = task_payload(draft, 2)["other_tasks"]
    assert [(t["number"], t["hours"]) for t in others] == [(1, 1.0), (3, 6.0)]
    planned: Any = task_payload(draft, 2)["planned_task"]
    assert planned["hours"] == 4.0
    material = draft.materials[0]
    shown: Any = material_payload(draft, material.path)["tasks"]
    assert shown and all(t["hours"] == draft.handoff.task(t["number"]).hours for t in shown)


@pytest.mark.parametrize("version", ["0.9", "0.8", "0.3"])
def test_a_draft_begun_before_sends_what_it_always_sent(version):
    draft = drafted(version)
    others: Any = task_payload(draft, 2)["other_tasks"]
    shown: Any = material_payload(draft, draft.materials[0].path)["tasks"]
    assert others and all("hours" not in t for t in others)
    assert shown and all("hours" not in t for t in shown)
    # «0.10» is a later version than «0.9», not an earlier one.
    assert drafted("0.10").since("0.10") and not draft.since("0.10") and drafted("0.10").since("0.9")
