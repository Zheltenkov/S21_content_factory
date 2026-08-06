"""РПД-режим (web): загрузка УП-CSV → сборка РПД → скачивание + персист. LLM замокан в None."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from content_factory.api.routers import rpd

_HEADER = [
    "Тематический блок", "Цели блока", "№", "Название контентной единицы (проекта)", "Краткое описание",
    "Обр. результаты — что узнает (ЗНАТЬ)", "Обр. результаты — что умеет (УМЕТЬ)", "Обр. результаты — какой навык (НАВЫКИ)",
    "Необходимое ПО", "Формат", "Трудоёмкость, астр. часы", "Список навыков (развесовка)", "Количество p2p проверок",
]


def _up_csv() -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_HEADER)
    writer.writerow(["Модуль тест", "Цель", "1", "Проект Альфа", "описание", "знает основы", "умеет применять", "навык", "Python", "индивидуальный", "36", "Навык А: 30%", "1"])
    return buffer.getvalue().encode("utf-8-sig")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("DISABLE_AUTH", "true")
    monkeypatch.setattr(rpd, "RPD_ROOT", tmp_path / "rpd")
    monkeypatch.setattr(rpd, "_llm_client", lambda: None)  # без реального LLM
    rpd._UPLOADS.clear()
    rpd._RUNS.clear()
    rpd._LOADED = False
    app = FastAPI()
    app.include_router(rpd.page_router)
    return TestClient(app)


def _upload(client: TestClient) -> str:
    resp = client.post("/app/rpd/upload", files={"program": ("up.csv", _up_csv(), "text/csv")})
    assert resp.status_code == 200
    return resp.json()["upload_id"]


def _generate(client: TestClient) -> str:
    upload_id = _upload(client)
    resp = client.post("/app/rpd/generate", data={"upload_id": upload_id, "title": "Тестовая программа"})
    assert resp.status_code == 200
    return resp.json()["run_id"]


def test_home_renders(client: TestClient) -> None:
    resp = client.get("/app/rpd")
    assert resp.status_code == 200
    assert "РПД из учебного плана" in resp.text


def test_upload_requires_a_csv(client: TestClient) -> None:
    assert client.post("/app/rpd/upload").status_code == 400
    bad = client.post("/app/rpd/upload", files={"program": ("x.docx", b"hi", "application/octet-stream")})
    assert bad.status_code == 400


def test_generate_builds_a_run_and_view_shows_editor(client: TestClient) -> None:
    run_id = _generate(client)
    view = client.get(f"/app/rpd/{run_id}")
    assert view.status_code == 200
    assert "Сохранить раздел" in view.text  # редактор по разделам
    assert "Учебный план" in view.text  # навигатор разделов
    assert "Скачать черновик DOCX" in view.text
    assert "Финальный экспорт заблокирован" in view.text


def _paragraphs_section_index() -> int:
    from content_factory.rpd import RpdBuild

    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    return next(i for i, s in enumerate(build.sections) if s.kind == "paragraphs")


def test_edit_prose_section_persists_and_exports(client: TestClient) -> None:
    run_id = _generate(client)
    index = _paragraphs_section_index()
    saved = client.post(
        f"/app/rpd/{run_id}/section/{index}",
        data={"text": "УникальныйТекстПравкиМетодиста", "revision": "0"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    view = client.get(f"/app/rpd/{run_id}?s={index}")
    assert "УникальныйТекстПравкиМетодиста" in view.text  # правка видна в редакторе
    dl = client.get(f"/app/rpd/{run_id}/download?draft=true")
    assert dl.status_code == 200
    with zipfile.ZipFile(io.BytesIO(dl.content)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "УникальныйТекстПравкиМетодиста" in xml  # и в экспортированном DOCX


def _table_section_index() -> int:
    from content_factory.rpd import RpdBuild

    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    return next(i for i, s in enumerate(build.sections) if s.kind == "table")


def test_edit_table_rows_and_done_flag(client: TestClient) -> None:
    import json

    run_id = _generate(client)
    index = _table_section_index()
    payload = json.dumps([["1", "НовыйМодульИзРедактора", "36", "", "36", "", "Проект"]])
    saved = client.post(
        f"/app/rpd/{run_id}/section/{index}",
        data={"table_json": payload, "done": "on", "revision": "0"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    view = client.get(f"/app/rpd/{run_id}?s={index}")
    assert "НовыйМодульИзРедактора" in view.text
    assert "checked" in view.text and "✓" in view.text  # раздел отмечен готовым
    dl = client.get(f"/app/rpd/{run_id}/download?draft=true")
    with zipfile.ZipFile(io.BytesIO(dl.content)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "НовыйМодульИзРедактора" in xml  # правка таблицы в экспортном DOCX


def test_generate_with_bad_upload_id_is_rejected(client: TestClient) -> None:
    assert client.post("/app/rpd/generate", data={"upload_id": "nope"}).status_code == 400


def test_download_returns_a_valid_docx_from_up(client: TestClient) -> None:
    run_id = _generate(client)
    resp = client.get(f"/app/rpd/{run_id}/download?draft=true")
    assert resp.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(resp.content))
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "Модуль тест" in xml  # содержание УП в учебном плане
    assert "273-ФЗ" in xml  # boilerplate ФОРМЫ сохранён
    assert "Будет сгенерировано" in xml  # LLM=None → проза-плейсхолдер


def test_final_download_is_blocked_but_draft_remains_available(client: TestClient) -> None:
    run_id = _generate(client)

    final = client.get(f"/app/rpd/{run_id}/download")
    draft = client.get(f"/app/rpd/{run_id}/download?draft=true")

    assert final.status_code == 409
    assert final.json()["blocking_count"] > 0
    assert final.json()["draft_url"].endswith("?draft=true")
    assert draft.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(draft.content))


def test_document_metadata_persists_and_reaches_draft_cover(client: TestClient) -> None:
    run_id = _generate(client)
    metadata = {
        "revision": "0",
        "organization_name": "АНО ДПО Проверка",
        "review_body": "Методический совет",
        "protocol_number": "5",
        "protocol_date": "01.08.2026",
        "leader_position": "Директор",
        "leader_name": "Иванов И.И.",
        "order_number": "9",
        "order_date": "02.08.2026",
        "audience": "Специалисты",
        "delivery_mode": "очно-заочная",
        "final_attestation": "защита проекта",
        "issued_document": "удостоверение",
        "city": "Москва",
        "year": "2026",
    }

    saved = client.post(f"/app/rpd/{run_id}/metadata", data=metadata, follow_redirects=False)
    draft = client.get(f"/app/rpd/{run_id}/download?draft=true")

    assert saved.status_code == 303
    with zipfile.ZipFile(io.BytesIO(draft.content)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "АНО ДПО Проверка" in xml
    assert "Тестовая программа" in xml


def test_stale_editor_revision_is_rejected(client: TestClient) -> None:
    run_id = _generate(client)
    index = _paragraphs_section_index()

    first = client.post(
        f"/app/rpd/{run_id}/section/{index}",
        data={"text": "Первая версия", "revision": "0"},
        follow_redirects=False,
    )
    stale = client.post(
        f"/app/rpd/{run_id}/section/{index}",
        data={"text": "Потерянная версия", "revision": "0"},
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert stale.status_code == 409
    assert stale.headers["content-type"].startswith("text/html")
    assert "Другая вкладка" in stale.text
    assert f'/app/rpd/{run_id}' in stale.text
    assert "Потерянная версия" not in client.get(f"/app/rpd/{run_id}?s={index}").text


def test_evaluation_artifact_is_persisted_and_available(client: TestClient) -> None:
    run_id = _generate(client)

    response = client.get(f"/app/rpd/{run_id}/evaluation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["template_profile_id"] == "dpo_pk_v1"
    assert len(payload["source_sha256"]) == 64
    assert payload["llm_calls"] == 0
    assert next(iter(rpd.RPD_ROOT.rglob("evaluation.json"))).exists()
    assert "Метрики JSON" in client.get(f"/app/rpd/{run_id}").text


def test_delete_removes_the_run(client: TestClient) -> None:
    run_id = _generate(client)
    assert client.get(f"/app/rpd/{run_id}").status_code == 200
    deleted = client.post(f"/app/rpd/{run_id}/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert client.get(f"/app/rpd/{run_id}").status_code == 404


def test_runs_survive_reload_and_list_in_recent(client: TestClient) -> None:
    run_id = _generate(client)
    rpd._RUNS.clear()
    rpd._LOADED = False
    home = client.get("/app/rpd")
    assert home.status_code == 200
    assert "Недавние РПД" in home.text
    assert client.get(f"/app/rpd/{run_id}").status_code == 200
