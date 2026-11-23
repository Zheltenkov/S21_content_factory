from copy import deepcopy
from io import BytesIO
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from content_factory.api.routers import curriculum_builder
from content_factory.catalog.db import CatalogConnection
from content_factory.catalog.pipeline.brief_persistence import save_brief, save_curriculum_plan
from content_factory.catalog.web.routers import intake, up

_PREFIX = "/app/spravochnik"


@pytest.fixture
def downloads(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, dict[str, Any]]:
    plan: dict[str, Any] = {
        "status": "built",
        "csv_primary_header": [f"Колонка {number}" for number in range(1, 23)],
        "csv_secondary_header": [""] * 22,
        "rows": [{"project_name": "=SUM(1,2)", "materials": "Разбор задачи\n#N/A", "row_number": 1,
                  "effort_hours": 12.5, "group_size": "2", "skills_list": "Анализ, Защита"}],
    }
    app = FastAPI()
    app.include_router(up.router)
    app.include_router(intake.router)
    app.dependency_overrides[up.get_conn] = lambda: None
    monkeypatch.setattr(up, "ensure_intake_runtime_schema", lambda *_: None)
    monkeypatch.setattr(intake, "ensure_intake_runtime_schema", lambda *_: None)
    monkeypatch.setattr(up, "get_curriculum_plan", lambda _, key: plan if key == 5 else None)
    monkeypatch.setattr(intake, "get_intake_job", lambda _, key: {"result_payload": {
        "brief_id": 7, "curriculum_plan": plan,
    }} if key == 9 else None)
    return TestClient(app), plan


@pytest.mark.parametrize(("path", "filename"), [
    ("/up/plans/5/xlsx", "curriculum_plan_5.xlsx"), ("/intake/jobs/9/plan.xlsx", "curriculum_plan_brief_7.xlsx"),
])
def test_download_excel(downloads, path, filename):
    client, _ = downloads
    response = client.get(_PREFIX + path)
    assert response.status_code == 200
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    book = load_workbook(BytesIO(response.content))
    sheet = book["Учебный план"]
    assert (sheet.max_row, sheet.max_column, sheet.freeze_panes) == (3, 22, "A3")
    assert sheet["D3"].data_type == "s" and sheet["D3"].value == "=SUM(1,2)"
    assert sheet["J3"].value == "Разбор задачи\n#N/A"
    assert (sheet["M3"].value, sheet["N3"].value) == (2, 12.5)
    assert sheet["T3"].value == "Анализ: 50%, Защита: 50%"


def test_excel_download_from_job_reads_current_draft(downloads, monkeypatch: pytest.MonkeyPatch):
    client, snapshot = downloads
    snapshot.update(plan_id=5, source_policy="revision_with_accepted_skills")
    saved = deepcopy(snapshot)
    saved["rows"][0].update(project_name="После редактирования", group_size="3–4")
    monkeypatch.setattr(intake, "get_curriculum_plan", lambda *_: saved)
    response = client.get(_PREFIX + "/intake/jobs/9/plan.xlsx")
    assert response.status_code == 200
    assert "_draft.xlsx" in response.headers["content-disposition"]
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet["D3"].value == "После редактирования"
    assert sheet["M3"].value == "3–4"
    assert "Черновик УП для обсуждения" in sheet["J3"].value
    assert sheet["J3"].value.endswith("Разбор задачи\n#N/A")


def test_excel_download_errors(downloads):
    client, plan = downloads
    assert client.get(_PREFIX + "/up/plans/777/xlsx").status_code == 404
    assert client.get(_PREFIX + "/intake/jobs/777/plan.xlsx").status_code == 404
    plan["status"] = "invalid"
    assert client.get(_PREFIX + "/up/plans/5/xlsx").status_code == 409
    plan["status"] = "built"
    plan["rows"][0]["materials"] = "я" * 32768
    response = client.get(_PREFIX + "/up/plans/5/xlsx")
    assert response.status_code == 422 and "J3" in response.text
    csv_response = client.get(_PREFIX + "/up/plans/5/csv")
    assert csv_response.status_code == 200 and plan["rows"][0]["materials"] in csv_response.text


def test_builder_offers_last_saved_excel_while_brief_has_pending_work(catalog_conn: CatalogConnection) -> None:
    brief_id = save_brief(catalog_conn, "Проверка оформления выгрузки", {})
    saved = save_curriculum_plan(catalog_conn, brief_id, {
        "rows": [{"row_number": 1, "project_name": "Отчёт о качестве данных", "effort_hours": 4}],
        "status": "draft",
    })
    app = FastAPI()
    app.include_router(curriculum_builder.router)
    app.include_router(up.router)
    app.dependency_overrides[up.get_conn] = lambda: catalog_conn
    client = TestClient(app)
    page = client.get(f"/app/curriculum?brief_id={brief_id}")
    assert page.status_code == 200
    base = f"{_PREFIX}/up/plans/{saved['plan_id']}"
    assert f'href="{base}/csv"' in page.text and f'href="{base}/xlsx"' in page.text
    downloaded = client.get(base + "/xlsx")
    assert downloaded.status_code == 200
    sheet = load_workbook(BytesIO(downloaded.content)).active
    assert "Отчёт о качестве данных" in [cell.value for cell in sheet["D"]]
