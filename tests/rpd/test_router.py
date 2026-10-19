"""РПД-режим (web): загрузка УП-CSV → сборка РПД → скачивание + персист. LLM замокан в None."""

from __future__ import annotations

import base64
import csv
import io
import json
import threading
import time
import zipfile
from collections.abc import Iterator
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
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("DISABLE_AUTH", "true")
    monkeypatch.setattr(rpd, "RPD_ROOT", tmp_path / "rpd")
    monkeypatch.setattr(rpd, "_llm_client", lambda: None)  # без реального LLM
    rpd._UPLOADS.clear()
    rpd._RUNS.clear()
    rpd._JOBS.clear()
    rpd._TASKS.clear()
    rpd._LOADED = False
    app = FastAPI()
    app.include_router(rpd.page_router)
    # Клиент как контекст: иначе anyio закрывает портал после каждого запроса и отменяет фоновую
    # сборку. В uvicorn цикл событий один и живёт всё время работы сервиса.
    with TestClient(app) as test_client:
        yield test_client


def _upload(client: TestClient) -> str:
    resp = client.post("/app/rpd/upload", files={"program": ("up.csv", _up_csv(), "text/csv")})
    assert resp.status_code == 200
    return resp.json()["upload_id"]


def _generate(client: TestClient) -> str:
    """Собирает РПД и дожидается готовности: сборка идёт в фоне, ответ приходит сразу."""
    upload_id = _upload(client)
    resp = client.post("/app/rpd/generate", data={"upload_id": upload_id, "title": "Тестовая программа"})
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    _wait_state(client, run_id, "done")
    return run_id


def test_home_renders(client: TestClient) -> None:
    resp = client.get("/app/rpd")
    assert resp.status_code == 200
    assert "РПД из учебного плана" in resp.text


def test_home_registry_picker_is_a_dropdown(client: TestClient) -> None:
    response = client.get("/app/rpd")

    assert 'id="rpd-registry-select"' in response.text
    assert '<select class="registry-select"' in response.text
    assert 'id="rpd-registry-go"' in response.text
    assert "plans.forEach" in response.text
    assert "document.createElement('option')" in response.text


def test_registry_list_exposes_meaningful_plan_names(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.app.dependency_overrides[rpd.get_conn] = lambda: object()
    monkeypatch.setattr(
        rpd,
        "list_curriculum_plans",
        lambda _conn, limit=50: [
            {
                "id": 7,
                "title": "Черновик учебного плана",
                "brief_role": "Предприниматель",
                "brief_domain": "Искусственный интеллект",
                "total_hours": 72,
            },
            {"id": 8, "title": "Управление продуктом", "total_hours": 36},
        ],
    )

    response = client.get("/app/rpd/registry")

    assert response.status_code == 200
    assert response.json() == {
        "plans": [
            {"id": 7, "title": "Предприниматель · Искусственный интеллект", "hours": 72},
            {"id": 8, "title": "Управление продуктом", "hours": 36},
        ]
    }


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
    assert "Проверить готовность" in view.text  # по умолчанию — плашка, не стена замечаний
    assert "Финальный экспорт заблокирован" not in view.text
    checked = client.get(f"/app/rpd/{run_id}?check=1")  # валидация — по явной кнопке
    assert "Финальный экспорт заблокирован" in checked.text


def test_passport_section_uses_the_table_editor(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    index = next(i for i, section in enumerate(build.sections) if section.category == "passport")

    view = client.get(f"/app/rpd/{run_id}?s={index}")

    assert 'id="edit-table"' in view.text
    assert "Требования к поступающим" in view.text
    assert "Форма итоговой аттестации" in view.text
    assert 'class="prosein"' not in view.text


def test_module_evidence_uploads_every_assignment_and_control_into_docx(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    index = next(i for i, section in enumerate(build.sections) if section.category == "up_modules")
    assert len(build.module_evidence) == 2

    view = client.get(f"/app/rpd/{run_id}?s={index}")
    assert "Скриншоты заданий и контролей" in view.text
    assert 'class="module-program"' in view.text
    assert "Паспорт учебного модуля" in view.text
    assert "Темы и виды работ" in view.text
    assert 'class="module-evidence-area"' in view.text
    assert 'name="module_blocks_json"' in view.text
    assert view.text.index('class="module-evidence-area"') < view.text.index(
        "Скриншоты заданий и контролей"
    )
    assert view.text.count('name="screenshot"') == 2
    assert "Приложено 0 из 2" in view.text

    first, second = build.module_evidence
    invalid = client.post(
        f"/app/rpd/{run_id}/evidence/{first.evidence_id}",
        data={"revision": "0"},
        files={"screenshot": ("screen.txt", b"not-an-image", "text/plain")},
    )
    assert invalid.status_code == 400

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2gZAAAAAASUVORK5CYII="
    )
    uploaded_first = client.post(
        f"/app/rpd/{run_id}/evidence/{first.evidence_id}",
        data={"revision": "0"},
        files={"screenshot": ("assignment.png", png, "image/png")},
        follow_redirects=False,
    )
    assert uploaded_first.status_code == 303
    preview = client.get(f"/app/rpd/{run_id}/evidence/{first.evidence_id}")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"

    stale = client.post(
        f"/app/rpd/{run_id}/evidence/{second.evidence_id}",
        data={"revision": "0"},
        files={"screenshot": ("control.png", png, "image/png")},
    )
    assert stale.status_code == 409
    assert "Черновик уже изменён" in stale.text

    uploaded_second = client.post(
        f"/app/rpd/{run_id}/evidence/{second.evidence_id}",
        data={"revision": "1"},
        files={"screenshot": ("control.png", png, "image/png")},
        follow_redirects=False,
    )
    assert uploaded_second.status_code == 303
    after = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    assert all(item.status == "uploaded" for item in after.module_evidence)
    assert not any(issue.code == "module_platform_screenshots_missing" for issue in after.quality.issues)

    draft = client.get(f"/app/rpd/{run_id}/download?draft=true")
    assert draft.status_code == 200
    with zipfile.ZipFile(io.BytesIO(draft.content)) as archive:
        names = archive.namelist()
        document_xml = archive.read("word/document.xml").decode("utf-8", errors="ignore")
    assert any(name.startswith("word/media/") for name in names)
    assert "RPD_EVIDENCE" not in document_xml


def test_module_program_editor_saves_passport_and_topics_without_touching_evidence(
    client: TestClient,
) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    index = next(i for i, section in enumerate(build.sections) if section.category == "up_modules")
    block = build.sections[index].module_blocks[0]
    passport_rows = [list(row) for row in block.passport_rows]
    topics_rows = [list(row) for row in block.topics_rows]
    passport_rows[1][1] = "Уточнённая цель модуля"
    topics_rows[0][1] = "Уточнённая тема и краткое содержание"
    payload = {"1": {"passport": passport_rows, "topics": topics_rows}}

    saved = client.post(
        f"/app/rpd/{run_id}/section/{index}",
        data={
            "revision": str(build.revision),
            "module_blocks_json": json.dumps(payload, ensure_ascii=False),
        },
        follow_redirects=False,
    )

    assert saved.status_code == 303
    after = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    updated = after.sections[index].module_blocks[0]
    assert updated.passport_rows[1][1] == "Уточнённая цель модуля"
    assert updated.topics_rows[0][1] == "Уточнённая тема и краткое содержание"
    assert len(updated.evidence_rows) == 2
    assert all("RPD_EVIDENCE" in row[0] for row in updated.evidence_rows)


def test_assessment_sections_use_template_text_and_control_uploads(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    passport_index = next(i for i, section in enumerate(build.sections) if section.number == "4.3")
    example_index = next(i for i, section in enumerate(build.sections) if section.number == "4.4")

    passport_view = client.get(f"/app/rpd/{run_id}?s={passport_index}")
    assert "заранее опубликованы без ключей" in passport_view.text
    assert "Ключи должны быть в отдельном файле" in passport_view.text
    assert 'class="prosein"' in passport_view.text  # форма ДПО теперь редактируема

    example_view = client.get(f"/app/rpd/{run_id}?s={example_index}")
    assert "Скриншоты примеров оценки по модулям" in example_view.text
    assert "Приложено 0 из 1" in example_view.text
    assert example_view.text.count('name="screenshot"') == 1
    assert f'name="return_section" value="{example_index}"' in example_view.text
    assert "RPD_EVIDENCE" not in example_view.text
    assert 'class="prosein"' not in example_view.text

    control = next(item for item in build.module_evidence if item.kind == "control")
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2gZAAAAAASUVORK5CYII="
    )
    uploaded = client.post(
        f"/app/rpd/{run_id}/evidence/{control.evidence_id}",
        data={"revision": "0", "return_section": str(example_index)},
        files={"screenshot": ("control.png", png, "image/png")},
        follow_redirects=False,
    )

    assert uploaded.status_code == 303
    assert uploaded.headers["location"].endswith(f"?s={example_index}")


def test_competency_section_exposes_pipeline_status_and_assigned_code(
    client: TestClient,
) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    index = next(i for i, section in enumerate(build.sections) if section.category == "up_comp")

    view = client.get(f"/app/rpd/{run_id}?s={index}")

    assert "Контур компетенций" in view.text
    assert "безопасный fallback" in view.text
    assert "покрытие навыков УП: 100%" in view.text
    assert "ПК-1" in view.text
    assert "Основание требуется заполнить и подтвердить методисту" in view.text


def test_internal_professional_activity_uses_the_table_editor(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    index = next(
        i for i, section in enumerate(build.sections) if section.category == "professional_activity"
    )

    view = client.get(f"/app/rpd/{run_id}?s={index}")

    assert view.status_code == 200
    assert 'id="edit-table"' in view.text
    assert "Позиция" in view.text
    assert "Область деятельности" in view.text
    assert "Профессиональная задача" in view.text
    assert "Необходимые умения" in view.text
    assert 'class="prosein"' not in view.text


def test_current_and_final_assessment_use_template_table_editors(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))

    for category, expected_header in (
        ("current_control", "Форма контроля"),
        ("final_attestation", "Проверяемый результат"),
    ):
        index = next(i for i, section in enumerate(build.sections) if section.category == category)
        view = client.get(f"/app/rpd/{run_id}?s={index}")
        assert view.status_code == 200
        assert 'id="edit-table"' in view.text
        assert expected_header in view.text
        assert 'class="prosein"' not in view.text


def test_organizational_conditions_use_three_template_table_editors(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))

    for category, expected_header in (
        ("methodological_support", "Реквизиты / ссылка"),
        ("material_conditions", "Оборудование и ПО"),
        ("staffing", "Ф.И.О. / должность"),
    ):
        index = next(i for i, section in enumerate(build.sections) if section.category == category)
        view = client.get(f"/app/rpd/{run_id}?s={index}")
        assert view.status_code == 200
        assert 'id="edit-table"' in view.text
        assert expected_header in view.text
        assert 'class="prosein"' not in view.text


def test_normative_section_is_editable_with_verbatim_fallback(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    before = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    index = next(i for i, section in enumerate(before.sections) if section.number == "1.2")

    view = client.get(f"/app/rpd/{run_id}?s={index}")
    assert view.status_code == 200
    assert "273-ФЗ" in view.text and "24.03.2025 № 266" in view.text
    assert 'class="prosein"' in view.text  # форма ДПО редактируема
    assert 'data-readonly="template"' not in view.text

    # Без правок экспорт держит дословную нормативку шаблона.
    untouched = client.get(f"/app/rpd/{run_id}/download?draft=true")
    xml_untouched = zipfile.ZipFile(io.BytesIO(untouched.content)).read("word/document.xml").decode("utf-8", "ignore")
    assert "273-ФЗ" in xml_untouched

    # Явная правка методиста применяется и попадает в экспорт.
    saved = client.post(
        f"/app/rpd/{run_id}/section/{index}",
        data={"text": "Переформулированные нормативные основания организации", "done": "on", "revision": "0"},
        follow_redirects=False,
    )
    after = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    assert saved.status_code == 303
    assert after.sections[index].paragraphs == ["Переформулированные нормативные основания организации"]
    assert after.sections[index].done is True

    edited = client.get(f"/app/rpd/{run_id}/download?draft=true")
    xml_edited = zipfile.ZipFile(io.BytesIO(edited.content)).read("word/document.xml").decode("utf-8", "ignore")
    assert "Переформулированные нормативные основания организации" in xml_edited


def test_mandatory_boilerplate_sections_are_visible_and_editable(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))

    assert len(build.sections) == 21
    for number, expected_text in (
        ("5.4", "электронная образовательная среда"),
        ("5.5", "доступные форматы учебных материалов"),
    ):
        index = next(i for i, section in enumerate(build.sections) if section.number == number)
        view = client.get(f"/app/rpd/{run_id}?s={index}")

        assert view.status_code == 200
        assert expected_text in view.text
        assert 'class="prosein"' in view.text  # прозаическая форма ДПО редактируема
        assert 'data-readonly="template"' not in view.text

    quality_index = next(i for i, section in enumerate(build.sections) if section.number == "6")
    quality_view = client.get(f"/app/rpd/{run_id}?s={quality_index}")

    assert quality_view.status_code == 200
    assert 'id="edit-table"' in quality_view.text  # табличная форма ДПО редактируема
    assert "Направление оценки" in quality_view.text
    assert "Достижение результатов" in quality_view.text
    assert "Актуальность содержания" in quality_view.text
    assert 'data-readonly="template"' not in quality_view.text


def test_program_developers_uses_the_template_table_editor(client: TestClient) -> None:
    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    index = next(
        i for i, section in enumerate(build.sections) if section.category == "program_developers"
    )

    view = client.get(f"/app/rpd/{run_id}?s={index}")

    assert view.status_code == 200
    assert 'id="edit-table"' in view.text
    assert "Ученая степень / квалификация" in view.text
    assert "Руководитель программы" in view.text
    assert "Эксперт / рецензент" in view.text
    assert 'class="prosein"' not in view.text


def test_passport_table_and_metadata_form_stay_synchronized(client: TestClient) -> None:
    import json

    run_id = _generate(client)
    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    index = next(i for i, section in enumerate(build.sections) if section.category == "passport")
    passport = build.sections[index]
    rows = [list(row) for row in passport.table_rows]
    for row in rows:
        if row[0] == "Категория слушателей":
            row[1] = "Основатели технологических стартапов"

    saved_table = client.post(
        f"/app/rpd/{run_id}/section/{index}",
        data={"table_json": json.dumps(rows, ensure_ascii=False), "revision": "0"},
        follow_redirects=False,
    )
    after_table = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))

    assert saved_table.status_code == 303
    assert after_table.document_fields.audience == "Основатели технологических стартапов"

    metadata = {
        "revision": "1",
        "organization_name": "АНО ДПО Проверка",
        "review_body": "Методический совет",
        "protocol_number": "5",
        "protocol_date": "01.08.2026",
        "leader_position": "Директор",
        "leader_name": "Иванов И.И.",
        "order_number": "9",
        "order_date": "02.08.2026",
        "audience": "Руководители продуктовых команд",
        "admission_requirements": "Наличие или получение СПО и/или ВО",
        "delivery_mode": "очно-заочная с применением ДОТ",
        "duration": "9 недель",
        "study_mode": "не более 8 академических часов в неделю",
        "final_attestation": "защита итогового проекта",
        "issued_document": "удостоверение о повышении квалификации",
        "city": "Москва",
        "year": "2026",
    }
    saved_metadata = client.post(
        f"/app/rpd/{run_id}/metadata",
        data=metadata,
        follow_redirects=False,
    )
    after_metadata = rpd.RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    passport_values = {
        row[0]: row[1]
        for section in after_metadata.sections
        if section.category == "passport"
        for row in section.table_rows
    }

    assert saved_metadata.status_code == 303
    assert passport_values["Категория слушателей"] == "Руководители продуктовых команд"
    assert passport_values["Продолжительность"] == "9 недель"
    schedule = next(section for section in after_metadata.sections if section.category == "up_grafik")
    assert schedule.table_rows[0][1] == "9-я неделя"
    assert schedule.table_rows[0][4] == "Защита итогового проекта"
    assert after_metadata.schedule_trace is not None
    assert after_metadata.schedule_trace.status == "passport_duration"
    final_attestation = next(
        section for section in after_metadata.sections if section.category == "final_attestation"
    )
    final_values = {row[0]: row[1] for row in final_attestation.table_rows}
    assert final_values["Форма"] == "Защита итогового проекта"
    assert after_metadata.assessment_trace is not None
    assert after_metadata.assessment_trace.final_control_form == "Защита итогового проекта"


def _paragraphs_section_index() -> int:
    from content_factory.rpd import RpdBuild

    build_path = next(iter(rpd.RPD_ROOT.rglob("build.json")))
    build = RpdBuild.model_validate_json(build_path.read_text(encoding="utf-8"))
    return next(i for i, s in enumerate(build.sections) if s.kind == "paragraphs" and s.editable)


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
    return next(i for i, s in enumerate(build.sections) if s.category == "up_plan")


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


# --------------------------------------------------------------------------- фоновая сборка


def _wait_state(client: TestClient, run_id: str, expected: str, tries: int = 100) -> dict[str, object]:
    """Опрашивает статус, пока сборка не придёт в ожидаемое состояние."""
    for _ in range(tries):
        payload = client.get(f"/app/rpd/{run_id}/status").json()
        if payload.get("state") == expected:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"состояние {expected!r} не наступило; последнее — {payload!r}")


@pytest.fixture
def held_build(monkeypatch: pytest.MonkeyPatch):
    """Останавливает сборку на входе, чтобы поймать её незавершённой."""
    release = threading.Event()
    entered = threading.Event()
    original = rpd._build_run

    def slow(csv_path, run_dir, program_title):  # type: ignore[no-untyped-def]
        entered.set()
        release.wait(10)
        return original(csv_path, run_dir, program_title)

    monkeypatch.setattr(rpd, "_build_run", slow)
    release.entered = entered  # type: ignore[attr-defined]
    return release


def test_generate_answers_before_the_build_finishes(client: TestClient, held_build) -> None:
    upload_id = _upload(client)

    resp = client.post("/app/rpd/generate", data={"upload_id": upload_id, "title": "Тестовая программа"})

    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    assert resp.json()["status_url"] == f"/app/rpd/{run_id}/status"
    assert held_build.entered.wait(5), "сборка не началась"
    assert client.get(f"/app/rpd/{run_id}/status").json()["state"] == "running"
    held_build.set()
    assert _wait_state(client, run_id, "done")["redirect"] == f"/app/rpd/{run_id}"


def test_status_of_a_finished_build_keeps_reporting_done(client: TestClient) -> None:
    run_id = _generate(client)

    payload = client.get(f"/app/rpd/{run_id}/status").json()

    assert payload["state"] == "done"
    assert payload["redirect"] == f"/app/rpd/{run_id}"


def test_open_run_while_it_builds_shows_waiting_page_not_404(client: TestClient, held_build) -> None:
    upload_id = _upload(client)
    run_id = client.post("/app/rpd/generate", data={"upload_id": upload_id, "title": "Тестовая"}).json()["run_id"]
    assert held_build.entered.wait(5)

    page = client.get(f"/app/rpd/{run_id}")

    assert page.status_code == 200
    assert "Собираем РПД" in page.text
    assert f"/app/rpd/{run_id}/status" in page.text  # страница сама следит за ходом
    held_build.set()
    _wait_state(client, run_id, "done")


def test_failed_build_is_reported_as_error_not_silence(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(csv_path, run_dir, program_title):  # type: ignore[no-untyped-def]
        raise RuntimeError("УП не разобран")

    monkeypatch.setattr(rpd, "_build_run", boom)
    upload_id = _upload(client)

    run_id = client.post("/app/rpd/generate", data={"upload_id": upload_id, "title": "Тестовая"}).json()["run_id"]

    payload = _wait_state(client, run_id, "error")
    assert "УП не разобран" in payload["error"]


def test_status_of_an_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/app/rpd/" + "0" * 32 + "/status").status_code == 404


def test_build_from_registry_also_answers_before_it_finishes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.app.dependency_overrides[rpd.get_conn] = lambda: object()
    monkeypatch.setattr(rpd, "get_curriculum_plan", lambda conn, plan_id: {"plan_id": plan_id, "title": "УП из Реестра"})
    monkeypatch.setattr(rpd, "from_registry_plan", lambda plan: _parse_up_fixture())

    resp = client.post("/app/rpd/registry/7")

    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    _wait_state(client, run_id, "done")
    client.app.dependency_overrides.clear()


def _parse_up_fixture():
    """УП из тестового CSV — источник для сборки из Реестра."""
    import tempfile

    with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as handle:
        handle.write(_up_csv())
        path = Path(handle.name)
    return rpd.parse_up_csv(path, title="УП из Реестра")
