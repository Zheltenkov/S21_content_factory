from content_factory.generation.agents.practice_generation_service import select_representative_tasks
from content_factory.generation.models.schemas import PracticeTask


def _task(index: int) -> PracticeTask:
    return PracticeTask(
        title=f"Task {index}",
        goal=f"Goal {index}",
        expected_artifact=f"Artifact {index}",
    )


def test_representative_selection_preserves_workflow_boundaries() -> None:
    selected = select_representative_tasks([_task(index) for index in range(1, 7)], 3)

    assert [task.title for task in selected] == ["Task 1", "Task 3", "Task 6"]


def test_representative_selection_does_not_pad_missing_tasks() -> None:
    tasks = [_task(1), _task(2)]

    assert select_representative_tasks(tasks, 3) == tasks
