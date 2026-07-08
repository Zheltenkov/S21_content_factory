from __future__ import annotations

from typing import Any

from content_factory.catalog.viewer.intake_jobs import (
    create_intake_job,
    get_intake_job,
    get_intake_job_brief_id,
    list_recent_intake_jobs,
    update_intake_job,
)


def test_intake_job_lifecycle_hydrates_payload_and_labels(catalog_conn: Any) -> None:
    job_id = create_intake_job(
        catalog_conn,
        source_kind="text",
        source_name=None,
        file_path=None,
        brief_text="brief",
        use_council=True,
    )

    update_intake_job(
        catalog_conn,
        job_id,
        status="succeeded",
        current_stage="completed",
        progress_note="done",
        result_payload={"brief_id": 42, "row_count": 3},
        mark_started=True,
        mark_finished=True,
    )

    job, brief_id = get_intake_job_brief_id(catalog_conn, job_id)

    assert brief_id == 42
    assert job is not None
    assert job["result_payload"] == {"brief_id": 42, "row_count": 3}
    assert job["status_label"] == "Готово"
    assert job["current_stage_label"] == "Завершено"


def test_list_recent_intake_jobs_hydrates_status_labels(catalog_conn: Any) -> None:
    first_id = create_intake_job(
        catalog_conn,
        source_kind="text",
        source_name="first",
        file_path=None,
        brief_text="brief-1",
        use_council=False,
    )
    second_id = create_intake_job(
        catalog_conn,
        source_kind="file",
        source_name="brief.txt",
        file_path=None,
        brief_text="brief-2",
        use_council=False,
    )
    update_intake_job(catalog_conn, first_id, status="running", current_stage="search")

    jobs = list_recent_intake_jobs(catalog_conn, limit=2)

    assert [item["id"] for item in jobs] == [second_id, first_id]
    assert jobs[0]["status_label"] == "В очереди"
    assert jobs[1]["status_label"] == "Обрабатывается"
    assert jobs[1]["current_stage_label"] == "Поиск evidence по серой зоне"


def test_get_intake_job_handles_malformed_payload(catalog_conn: Any) -> None:
    job_id = create_intake_job(
        catalog_conn,
        source_kind="text",
        source_name=None,
        file_path=None,
        brief_text="brief",
        use_council=False,
    )
    catalog_conn.execute("UPDATE intake_job SET result_payload = '{bad-json' WHERE id = ?", (job_id,))
    catalog_conn.commit()

    job = get_intake_job(catalog_conn, job_id)

    assert job is not None
    assert job["result_payload"] is None
