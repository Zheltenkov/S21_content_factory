"""Перевод архива через API: опись, план, прогон.

Две проверки здесь главные, и обе про деньги и про чужие данные: опись не должна звать модель,
а чужой ``plan_id`` не должен открываться. Остальное — про частичный успех: архив собирается
даже тогда, когда часть файлов не удалась.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest
from fastapi import HTTPException

from content_factory.api.routers import archive_translation as module
from content_factory.api.utils.result_cache import _translation_jobs
from content_factory.generation.models.schemas import ProjectSeed


class _FakeLlm:
    """Переводит, приписывая ``[en]``; считает запросы."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str, **kwargs: object) -> str:  # noqa: ARG002
        self.calls += 1
        marker = "Входные данные:\n"
        payload = json.loads(user[user.index(marker) + len(marker) :])
        return json.dumps(
            {"translations": {f["id"]: f"[en] {f['text']}" for f in payload["fragments"]}},
            ensure_ascii=False,
        )


class _ForbiddenLlm:
    """Любое обращение к модели — провал теста."""

    def complete(self, system: str, user: str, **kwargs: object) -> str:  # noqa: ARG002
        raise AssertionError("опись не должна звать модель")


def _archive(files: dict[str, bytes | str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload.encode("utf-8") if isinstance(payload, str) else payload)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _clean_plans():
    module._PLANS.clear()
    yield
    module._PLANS.clear()


# -------------------------------------------------------------------------------------- опись

def test_the_inventory_never_calls_the_model(monkeypatch) -> None:
    """Опись считается вхолостую: иначе методист платил бы ещё до того, что увидел смету."""
    monkeypatch.setattr(module, "create_llm_client", lambda **kwargs: _ForbiddenLlm())
    content = _archive({"README.md": "Привет мир", "logo.png": b"\x89PNG"})

    members, entries, estimate = module.inspect_archive(content)

    assert [m.path for m in members] == ["README.md", "logo.png"]
    assert {e.path: e.selected for e in entries} == {"README.md": True, "logo.png": False}
    assert estimate.files == 1
    assert estimate.usd > 0


def test_an_archive_with_a_path_outside_is_refused() -> None:
    with pytest.raises(HTTPException) as info:
        module.inspect_archive(_archive({"../снаружи.md": "текст"}))

    assert info.value.status_code == 400


def test_an_empty_archive_is_refused() -> None:
    with pytest.raises(HTTPException) as info:
        module.inspect_archive(_archive({}))

    assert info.value.status_code == 400


# --------------------------------------------------------------------------------------- план

def test_a_plan_opens_only_for_its_owner() -> None:
    """Угаданный plan_id иначе отдавал бы опись чужого архива вместе с именами файлов."""
    members, entries, _estimate = module.inspect_archive(_archive({"a.md": "Привет"}))
    plan_id = module.remember_plan(
        user_id="user-1", filename="course.zip", members=members, entries=entries
    )

    assert module.plan_for_user(plan_id, "user-1")["filename"] == "course.zip"
    with pytest.raises(HTTPException) as info:
        module.plan_for_user(plan_id, "user-2")

    assert info.value.status_code == 404


def test_a_stale_plan_is_forgotten(monkeypatch) -> None:
    members, entries, _estimate = module.inspect_archive(_archive({"a.md": "Привет"}))
    plan_id = module.remember_plan(
        user_id="user-1", filename="course.zip", members=members, entries=entries
    )
    monkeypatch.setattr(module, "PLAN_TTL_SECONDS", -1)

    with pytest.raises(HTTPException):
        module.plan_for_user(plan_id, "user-1")


def test_only_the_newest_plans_are_kept() -> None:
    """План держит распакованный архив в памяти, поэтому их число ограничено."""
    members, entries, _estimate = module.inspect_archive(_archive({"a.md": "Привет"}))
    ids = [
        module.remember_plan(
            user_id="user-1", filename=f"{index}.zip", members=members, entries=entries
        )
        for index in range(module.MAX_PLANS + 3)
    ]

    assert len(module._PLANS) == module.MAX_PLANS
    with pytest.raises(HTTPException):
        module.plan_for_user(ids[0], "user-1")
    assert module.plan_for_user(ids[-1], "user-1")


def test_a_path_that_is_not_in_the_archive_is_an_error() -> None:
    """Иначе вернулся бы архив без ожидаемого файла и без объяснения."""
    _members, entries, _estimate = module.inspect_archive(_archive({"a.md": "Привет"}))

    with pytest.raises(HTTPException) as info:
        module.selected_paths(entries, ["b.md"])

    assert "b.md" in info.value.detail


def test_without_a_list_everything_selected_is_taken() -> None:
    _members, entries, _estimate = module.inspect_archive(
        _archive({"a.md": "Привет", "logo.png": b"\x89PNG"})
    )

    assert module.selected_paths(entries, None) == ["a.md"]


# -------------------------------------------------------------------------------------- прогон

def _run(monkeypatch, tmp_path, files: dict[str, bytes | str], paths=None, llm=None) -> dict:
    llm = llm or _FakeLlm()
    monkeypatch.setattr(module, "create_llm_client", lambda **kwargs: llm)
    monkeypatch.setattr(module, "_write_translation_artifact", _store(tmp_path))

    members, entries, _estimate = module.inspect_archive(_archive(files))
    plan = {
        "user_id": "user-1",
        "filename": "course.zip",
        "members": members,
        "entries": entries,
        "created_at": 0.0,
    }
    module.run_archive_translation(
        request_id="req-arch",
        user_id="user-1",
        plan=plan,
        target_language="en",
        paths=module.selected_paths(entries, paths),
        seed=ProjectSeed(language="en", project_type="individual", project_description="тест"),
    )
    return _translation_jobs["req-arch"]


def _store(tmp_path):
    def write(request_id: str, filename: str, content: bytes) -> str:
        target = tmp_path / request_id
        target.mkdir(parents=True, exist_ok=True)
        (target / filename).write_bytes(content)
        return filename

    return write


def test_the_result_is_an_archive_with_the_same_names(monkeypatch, tmp_path) -> None:
    job = _run(monkeypatch, tmp_path, {"README.md": "Привет мир", "logo.png": b"\x89PNG"})

    assert job["status"] == "completed", job.get("error")
    stored = tmp_path / "req-arch" / job["result_links"]["zip"]
    with zipfile.ZipFile(stored) as archive:
        assert archive.read("README.md").decode("utf-8") == "[en] Привет мир"
        assert archive.read("logo.png") == b"\x89PNG", "непереведённое переносится побайтно"


def test_the_report_travels_inside_the_result(monkeypatch, tmp_path) -> None:
    job = _run(monkeypatch, tmp_path, {"README.md": "Привет", "logo.png": b"\x89PNG"})

    stored = tmp_path / "req-arch" / job["result_links"]["zip"]
    with zipfile.ZipFile(stored) as archive:
        report = archive.read("ПЕРЕВОД-ОТЧЁТ.md").decode("utf-8")

    assert "README.md" in report
    assert "logo.png" in report
    assert "формат не переводится" in report


def test_an_unticked_file_costs_nothing(monkeypatch, tmp_path) -> None:
    llm = _FakeLlm()
    job = _run(
        monkeypatch,
        tmp_path,
        {"a.md": "Первый", "b.md": "Второй"},
        paths=["a.md"],
        llm=llm,
    )

    assert llm.calls == 1, "снятый файл не должен стоить запроса"
    stored = tmp_path / "req-arch" / job["result_links"]["zip"]
    with zipfile.ZipFile(stored) as archive:
        assert archive.read("b.md").decode("utf-8") == "Второй"


def test_a_finished_run_forgets_its_plan(monkeypatch, tmp_path) -> None:
    """Распакованный архив лежит в памяти — держать его после прогона незачем."""
    monkeypatch.setattr(module, "create_llm_client", lambda **kwargs: _FakeLlm())
    monkeypatch.setattr(module, "_write_translation_artifact", _store(tmp_path))

    members, entries, _estimate = module.inspect_archive(_archive({"a.md": "Привет"}))
    plan_id = module.remember_plan(
        user_id="user-1", filename="course.zip", members=members, entries=entries
    )
    module.run_archive_translation(
        request_id="req-arch",
        user_id="user-1",
        plan=module.plan_for_user(plan_id, "user-1"),
        target_language="en",
        paths=["a.md"],
        seed=ProjectSeed(language="en", project_type="individual", project_description="тест"),
    )

    assert plan_id not in module._PLANS


def test_a_run_that_cannot_start_reports_failure(monkeypatch, tmp_path) -> None:
    def angry(**kwargs: object):
        raise RuntimeError("шлюз недоступен")

    monkeypatch.setattr(module, "create_llm_client", angry)
    monkeypatch.setattr(module, "_write_translation_artifact", _store(tmp_path))

    members, entries, _estimate = module.inspect_archive(_archive({"a.md": "Привет"}))
    module.run_archive_translation(
        request_id="req-arch",
        user_id="user-1",
        plan={"user_id": "user-1", "filename": "course.zip", "members": members,
              "entries": entries, "created_at": 0.0},
        target_language="en",
        paths=["a.md"],
        seed=ProjectSeed(language="en", project_type="individual", project_description="тест"),
    )

    job = _translation_jobs["req-arch"]
    assert job["status"] == "failed"
    assert "шлюз недоступен" in job["error"]


# ------------------------------------------------------------------- сквозь HTTP

def _client():
    from fastapi.testclient import TestClient

    from content_factory.api.dependencies import get_current_user
    from content_factory.api.main import app

    app.dependency_overrides[get_current_user] = lambda: {"id": "user-1", "username": "u"}
    return TestClient(app), app


def test_inspect_and_start_work_over_http(monkeypatch, tmp_path) -> None:
    """Сквозная проверка: опись отдаётся по маршруту, прогон стартует сразу и доходит до конца."""
    llm = _FakeLlm()
    monkeypatch.setattr(module, "create_llm_client", lambda **kwargs: llm)
    monkeypatch.setattr(module, "_write_translation_artifact", _store(tmp_path))

    client, app = _client()
    try:
        archive = _archive({"README.md": "Привет мир", "ci/test.sh": "# Сборка\nmake\n"})
        response = client.post(
            "/api/v1/translate/archive/inspect",
            files={"file": ("course.zip", archive, "application/zip")},
        )
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["source_language"] == "ru"
        assert {e["path"] for e in plan["entries"]} == {"README.md", "ci/test.sh"}
        assert plan["totals"]["files"] == 2

        started = client.post(
            f"/api/v1/translate/archive/{plan['plan_id']}/start",
            data={"target_language": "en", "paths": json.dumps(["README.md"])},
        )
        assert started.status_code == 200, started.text
        request_id = started.json()["request_id"]
    finally:
        app.dependency_overrides.clear()

    job = _translation_jobs[request_id]
    assert job["status"] == "completed", job.get("error")
    assert llm.calls == 1, "переведён только отмеченный файл"


def test_a_file_that_is_not_an_archive_is_refused_over_http() -> None:
    client, app = _client()
    try:
        response = client.post(
            "/api/v1/translate/archive/inspect",
            files={"file": ("notes.txt", b"not an archive", "text/plain")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "zip" in response.json()["detail"]


def test_an_unknown_plan_is_not_found_over_http() -> None:
    client, app = _client()
    try:
        response = client.post(
            "/api/v1/translate/archive/00000000-0000-0000-0000-000000000000/start",
            data={"target_language": "en"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
