from __future__ import annotations

from content_factory.api.routers.curriculum_projects import _project_items, _runs_by_project


def test_learning_project_items_include_run_history_and_active_gate() -> None:
    run_history = _runs_by_project(
        [
            {
                "pipeline_run_id": "pipe_active",
                "source_plan_id": 10,
                "plan_row_id": 100,
                "status": "needs_review",
                "updated_at": "2026-07-08T10:10:00",
            },
            {
                "pipeline_run_id": "pipe_done",
                "source_plan_id": 10,
                "plan_row_id": 100,
                "status": "completed",
                "updated_at": "2026-07-08T09:00:00",
            },
        ]
    )

    projects = _project_items(
        curriculum={
            "blocks": [
                {
                    "name": "Блок 1. Данные",
                    "projects": [
                        {
                            "title": "Анализ датасета",
                            "order": 1,
                            "plan_row_id": 100,
                            "project_index": 1,
                        }
                    ],
                }
            ]
        },
        plan_snapshot={
            "source_plan_id": 10,
            "plan_version": "v1:abc",
            "plan_hash": "abc",
        },
        readiness={"ready": True, "blockers": []},
        run_history=run_history,
    )

    assert projects[0]["generation_status"] == "needs_review"
    assert projects[0]["generation"]["pipeline_run_id"] == "pipe_active"
    assert projects[0]["generation_runs_count"] == 2
    assert [run["pipeline_run_id"] for run in projects[0]["generation_history"]] == ["pipe_active", "pipe_done"]
    assert projects[0]["can_generate"] is False
