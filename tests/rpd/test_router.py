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


def test_generate_builds_a_run_and_view_shows_stats(client: TestClient) -> None:
    run_id = _generate(client)
    view = client.get(f"/app/rpd/{run_id}")
    assert view.status_code == 200
    assert "Модулей" in view.text and "Скачать РПД" in view.text


def test_generate_with_bad_upload_id_is_rejected(client: TestClient) -> None:
    assert client.post("/app/rpd/generate", data={"upload_id": "nope"}).status_code == 400


def test_download_returns_a_valid_docx_from_up(client: TestClient) -> None:
    run_id = _generate(client)
    resp = client.get(f"/app/rpd/{run_id}/download")
    assert resp.status_code == 200
    assert zipfile.is_zipfile(io.BytesIO(resp.content))
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "Модуль тест" in xml  # содержание УП в учебном плане
    assert "273-ФЗ" in xml  # boilerplate ФОРМЫ сохранён
    assert "Будет сгенерировано" in xml  # LLM=None → проза-плейсхолдер


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
