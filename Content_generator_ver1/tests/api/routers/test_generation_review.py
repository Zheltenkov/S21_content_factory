import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routers import generation
from api.routers.generation import MethodologyAssistantCommandRequest, MethodologyReviewActionRequest
from content_gen.methodology import MethodologistChangeRequest


def test_methodology_human_review_flag_defaults_to_disabled() -> None:
    assert generation._methodology_human_review_enabled({}) is False
    assert generation._methodology_human_review_enabled({"methodology_human_review": False}) is False


def test_methodology_human_review_flag_accepts_bool_and_string_values() -> None:
    assert generation._methodology_human_review_enabled({"methodology_human_review": True}) is True
    assert generation._methodology_human_review_enabled({"methodology_human_review": "true"}) is True
    assert generation._methodology_human_review_enabled({"methodology_human_review": "off"}) is False


def test_methodology_human_review_flag_restores_old_paused_checkpoint_context() -> None:
    context = {"human_approval_checkpoint": {"id": "annotation"}}

    assert generation._methodology_human_review_enabled({}, context) is True


def test_methodology_human_review_explicit_seed_value_wins_over_context() -> None:
    context = {"human_approval_checkpoint": {"id": "annotation"}}

    assert generation._methodology_human_review_enabled({"methodology_human_review": False}, context) is False


class FakeRevisionLLM:
    def __init__(self, replacement: str = "Новый текст") -> None:
        self.replacement = replacement
        self.prompts: list[dict[str, str]] = []

    def complete(self, system: str, user: str, **_kwargs) -> str:
        self.prompts.append({"system": system, "user": user})
        fragment = user.split("ФРАГМЕНТ ДЛЯ ПРАВКИ:", 1)[1].strip()
        return fragment.replace("Старый текст", self.replacement)


def _final_review_readme() -> str:
    theory_padding = " ".join(f"Теоретический фрагмент {index}." for index in range(140))
    return (
        "# Публичные выступления\n\n"
        "Аннотация проекта.\n\n"
        "## Глава 1. Введение и инструкция\n\n"
        "Вводный текст.\n\n"
        "## Глава 2. Теоретический блок\n\n"
        f"{theory_padding}\n\n"
        "## Глава 3. Практический блок\n\n"
        "Практика после длинной теории должна быть видна в финальном preview."
    )


def _change_action(
    request: MethodologistChangeRequest,
    *,
    timestamp: str = "2026-04-27T00:00:00",
    user_id: str = "user_1",
) -> dict:
    return {
        "action": "changes_requested",
        "user_id": user_id,
        "comment": request.instruction,
        "timestamp": timestamp,
        "details": {
            "change_request": request.model_dump(mode="json"),
            "conflicts": [],
        },
    }


@pytest.mark.asyncio
async def test_approve_methodology_review_starts_resume(monkeypatch) -> None:
    calls = {"registered": False, "status": None, "resume": None}
    paused = {
        "user_id": "user_1",
        "project_seed": {"language": "ru"},
        "track_paths": [],
        "context": {"value": 1},
        "steps": [],
        "resume_from_index": 1,
    }

    async def fake_resume(**kwargs):
        calls["resume"] = kwargs

    async def fake_log(**_kwargs):
        return None

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(generation, "mark_paused_generation_approved", lambda *_args, **_kwargs: paused)
    monkeypatch.setattr(generation, "_resume_generation_background", fake_resume)
    monkeypatch.setattr(generation, "register_generation_task", lambda *_args, **_kwargs: calls.__setitem__("registered", True))
    monkeypatch.setattr(generation, "set_generation_status", lambda _request_id, status: calls.__setitem__("status", status))
    monkeypatch.setattr(generation, "write_log_async", fake_log)

    response = await generation.approve_methodology_review(
        "req-1",
        MethodologyReviewActionRequest(comment="checked"),
        user={"id": "user_1"},
    )
    await asyncio.sleep(0)

    assert response.status == "in_progress"
    assert calls["registered"] is True
    assert calls["status"] == "in_progress"
    assert calls["resume"]["review_comment"] == "checked"


@pytest.mark.asyncio
async def test_approve_methodology_review_requires_diff_approval_for_pending_changes(monkeypatch) -> None:
    calls = {"approved": False}
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Уточни теорию.",
    )
    paused = {
        "user_id": "user_1",
        "context": {"markdown": "## Глава 2. Теория\n\nСтарый текст."},
        "review_actions": [_change_action(request)],
    }

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(
        generation,
        "mark_paused_generation_approved",
        lambda *_args, **_kwargs: calls.__setitem__("approved", True),
    )

    with pytest.raises(HTTPException) as exc_info:
        await generation.approve_methodology_review(
            "req-1",
            MethodologyReviewActionRequest(comment="checked"),
            user={"id": "user_1"},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["review_state"] == "changes_requested"
    assert calls["approved"] is False


@pytest.mark.asyncio
async def test_approve_methodology_review_rejects_other_user(monkeypatch) -> None:
    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: {"user_id": "owner"})

    with pytest.raises(HTTPException) as exc_info:
        await generation.approve_methodology_review(
            "req-1",
            MethodologyReviewActionRequest(comment="checked"),
            user={"id": "other"},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_reject_methodology_review_marks_failed(monkeypatch) -> None:
    calls = {"status": None, "error": None}
    paused = {"user_id": "user_1"}

    async def fake_log(**_kwargs):
        return None

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(generation, "mark_paused_generation_rejected", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(generation, "set_generation_status", lambda _request_id, status: calls.__setitem__("status", status))
    monkeypatch.setattr(generation, "store_generation_error", lambda _request_id, error: calls.__setitem__("error", error))
    monkeypatch.setattr(generation, "write_log_async", fake_log)

    response = await generation.reject_methodology_review(
        "req-1",
        MethodologyReviewActionRequest(comment="stop"),
        user={"id": "user_1"},
    )

    assert response["success"] is True
    assert calls["status"] == "failed"
    assert "stop" in calls["error"]


@pytest.mark.asyncio
async def test_request_methodology_changes_records_review_action(monkeypatch) -> None:
    calls = {"status": None, "error": None, "saved": None}
    paused = {"user_id": "user_1", "review_actions": []}

    async def fake_log(**_kwargs):
        return None

    def fake_record(_request_id, **kwargs):
        calls["saved"] = kwargs
        return {
            "user_id": "user_1",
            "context": {"markdown": "## Глава 2. Теория\n\nТекст."},
            "review_actions": [{"action": "changes_requested"}],
        }

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(generation, "record_paused_generation_change_request", fake_record)
    monkeypatch.setattr(generation, "set_generation_status", lambda _request_id, status: calls.__setitem__("status", status))
    monkeypatch.setattr(generation, "store_generation_error", lambda _request_id, error: calls.__setitem__("error", error))
    monkeypatch.setattr(generation, "write_log_async", fake_log)

    response = await generation.request_methodology_changes(
        "req-1",
        MethodologistChangeRequest(
            target_stage="theory",
            target_selector="Глава 2, часть 1",
            scope="local_section_only",
            instruction="Уточни рабочий кейс без изменения остальных частей.",
            expected_outcome="В части появится конкретный кейс.",
        ),
        user={"id": "user_1"},
    )

    assert response["success"] is True
    assert response["status"] == "needs_review"
    assert calls["status"] == "needs_review"
    assert calls["saved"]["change_request"]["target_selector"] == "Глава 2, часть 1"
    assert calls["saved"]["conflicts"] == []
    assert response["target_registry"]["targets"]


@pytest.mark.asyncio
async def test_methodology_assistant_command_records_simplify_change(monkeypatch) -> None:
    calls = {"status": None, "error": None, "saved": None}
    markdown = (
        "# Проект\n\n"
        "## Глава 3. Практический блок\n\n"
        "### Задание 1. Анализ рисков\n\n"
        "Слишком сложная задача."
    )
    paused = {
        "user_id": "user_1",
        "context": {
            "markdown": markdown,
            "human_approval_checkpoint": {
                "id": "practice-review",
                "stage": "practice",
                "node_id": "practice",
                "artifact": {},
            },
        },
        "review_actions": [],
    }

    async def fake_log(**_kwargs):
        return None

    def fake_record(_request_id, **kwargs):
        calls["saved"] = kwargs
        return {
            **paused,
            "review_actions": [{"action": "changes_requested", "details": {"change_request": kwargs["change_request"]}}],
        }

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(generation, "record_paused_generation_change_request", fake_record)
    monkeypatch.setattr(generation, "set_generation_status", lambda _request_id, status: calls.__setitem__("status", status))
    monkeypatch.setattr(generation, "store_generation_error", lambda _request_id, error: calls.__setitem__("error", error))
    monkeypatch.setattr(generation, "write_log_async", fake_log)

    response = await generation.run_methodology_assistant_command(
        "req-1",
        MethodologyAssistantCommandRequest(message="Упрости задачу 1 и оставь один измеримый результат"),
        user={"id": "user_1"},
    )

    assert response["success"] is True
    assert response["assistant_command"]["command"] == "simplify_task"
    assert response["assistant_command"]["checkpoint_id"] == "practice-review"
    assert calls["saved"]["change_request"]["target_stage"] == "practice"
    assert calls["saved"]["change_request"]["scope"] == "task_only"
    assert calls["saved"]["assistant_command"]["command"] == "simplify_task"
    assert calls["saved"]["assistant_command"]["checkpoint_id"] == "practice-review"
    assert "Упрости выбранную практическую задачу" in calls["saved"]["change_request"]["instruction"]
    assert calls["status"] == "needs_review"


@pytest.mark.asyncio
async def test_request_methodology_changes_blocks_hard_conflict(monkeypatch) -> None:
    calls = {"recorded": False}
    paused = {"user_id": "user_1", "review_actions": []}

    async def fake_log(**_kwargs):
        return None

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(generation, "record_paused_generation_change_request", lambda *_args, **_kwargs: calls.__setitem__("recorded", True))
    monkeypatch.setattr(generation, "write_log_async", fake_log)

    with pytest.raises(HTTPException) as exc_info:
        await generation.request_methodology_changes(
            "req-1",
            MethodologistChangeRequest(
                target_stage="dataset",
                target_selector="materials/task_01_source_notes.md",
                scope="materials_only",
                instruction="Добавь готовые ответы в материалы.",
            ),
            user={"id": "user_1"},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["conflicts"]
    assert calls["recorded"] is False


@pytest.mark.asyncio
async def test_request_methodology_changes_rejects_other_user(monkeypatch) -> None:
    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: {"user_id": "owner"})

    with pytest.raises(HTTPException) as exc_info:
        await generation.request_methodology_changes(
            "req-1",
            MethodologistChangeRequest(
                target_stage="final",
                target_selector="README",
                scope="local_section_only",
                instruction="Сократи повтор во введении.",
            ),
            user={"id": "other"},
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_methodology_review_state_returns_targets_and_actions(monkeypatch) -> None:
    paused = {
        "user_id": "user_1",
        "status": "needs_review",
        "resume_from_index": 3,
        "context": {
            "human_approval_checkpoint": {
                "id": "annotation",
                "stage": "annotation",
                "artifact": {"title": "Title", "annotation": "Ann"},
            },
            "markdown": "## Глава 2. Теоретический блок\n\nТекст.",
            "dataset_files": [{"path": "materials/task_01_source_notes.md", "data": b"raw"}],
            "methodology_revision_results": [{"status": "applied"}],
        },
        "review_actions": [{"action": "changes_requested"}],
        "methodology": {"summary": {}},
    }

    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)

    response = await generation.get_methodology_review_state("req-1", user={"id": "user_1"})

    assert response["status"] == "needs_review"
    assert response["review_actions"] == paused["review_actions"]
    assert response["revision_results"] == [{"status": "applied"}]
    assert response["checkpoint"]["id"] == "annotation"
    target_ids = {target["id"] for target in response["target_registry"]["targets"]}
    assert "chapter_2" in target_ids
    assert "material.materials_task_01_source_notes_md" in target_ids


@pytest.mark.asyncio
async def test_get_methodology_review_state_refreshes_final_checkpoint_with_full_readme(monkeypatch) -> None:
    markdown = _final_review_readme()
    paused = {
        "user_id": "user_1",
        "status": "needs_review",
        "resume_from_index": 6,
        "context": {
            "title": "Публичные выступления",
            "markdown": markdown,
            "human_approval_checkpoint": {
                "id": "evaluation",
                "stage": "final",
                "node_id": "evaluation",
                "title": "Проверка финальной оценки",
                "summary": "Валидаторы завершили проверку.",
                "resume_from_node": "finalize",
                "allowed_targets": ["annotation", "chapter_1", "chapter_2", "chapter_3", "final"],
                "artifact": {
                    "title": "Публичные выступления",
                    "markdown_excerpt": markdown[:1800],
                },
            },
        },
        "review_actions": [],
        "methodology": {"summary": {}},
    }

    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)

    response = await generation.get_methodology_review_state("req-1", user={"id": "user_1"})

    artifact = response["checkpoint"]["artifact"]
    assert response["preview_markdown"] == markdown
    assert "## Глава 3. Практический блок" in artifact["markdown_excerpt"]
    assert artifact["markdown_excerpt"] == markdown
    section_titles = [section["title"] for section in artifact["markdown_sections"]]
    assert "Глава 1. Введение и инструкция" in section_titles
    assert "Глава 2. Теоретический блок" in section_titles
    assert "Глава 3. Практический блок" in section_titles
    assert artifact["requirements_matrix"]
    assert response["checkpoint"]["artifact_hash"]


@pytest.mark.asyncio
async def test_preview_methodology_changes_persists_revision_diff(monkeypatch) -> None:
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Уточни формулировку в теории.",
    )
    paused = {
        "user_id": "user_1",
        "context": {"markdown": "## Глава 2. Теория\n\nСтарый текст."},
        "review_actions": [_change_action(request)],
    }
    calls = {"saved": None}

    def fake_record(_request_id, **kwargs):
        calls["saved"] = kwargs
        paused["review_actions"].append(
            {
                "action": "preview_ready",
                "user_id": kwargs["user_id"],
                "timestamp": "2026-04-27T00:01:00",
                "details": {
                    "revision_results": kwargs["revision_results"],
                    "target_registry": kwargs["target_registry"],
                    "preview_hash": kwargs["preview_hash"],
                },
            }
        )
        return paused

    async def fake_log(**_kwargs):
        return None

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(generation, "create_llm_client", lambda **_kwargs: FakeRevisionLLM())
    monkeypatch.setattr(generation, "record_paused_generation_preview", fake_record)
    monkeypatch.setattr(generation, "write_log_async", fake_log)

    response = await generation.preview_methodology_changes("req-1", user={"id": "user_1"})

    assert response["success"] is True
    assert response["review_state"] == "preview_ready"
    assert response["requires_diff_approval"] is True
    assert calls["saved"]["preview_hash"] == response["preview_hash"]
    assert calls["saved"]["revision_results"][0]["status"] == "applied"
    assert response["revision_results"][0]["diff_preview"]
    assert "Новый текст" not in paused["context"]["markdown"]


@pytest.mark.asyncio
async def test_approve_methodology_diff_marks_latest_preview_approved(monkeypatch) -> None:
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Уточни формулировку в теории.",
    )
    change_action = _change_action(request)
    action_id = generation.ScopedRevisionExecutor.action_id_for_action(change_action, 0, request)
    preview_result = {
        "action_id": action_id,
        "status": "applied",
        "target_kind": "markdown_section",
        "target_stage": "theory",
        "target_selector": "Глава 2",
        "target_id": "chapter_2",
        "scope": "local_section_only",
        "changed": True,
        "diff_preview": ["--- before", "+++ after", "-Старый текст", "+Новый текст"],
    }
    preview_hash = generation._preview_hash([preview_result])
    paused = {
        "user_id": "user_1",
        "status": "needs_review",
        "context": {"markdown": "## Глава 2. Теория\n\nСтарый текст."},
        "review_actions": [
            change_action,
            {
                "action": "preview_ready",
                "user_id": "user_1",
                "timestamp": "2026-04-27T00:01:00",
                "details": {
                    "revision_results": [preview_result],
                    "target_registry": {"targets": []},
                    "preview_hash": preview_hash,
                },
            },
        ],
    }
    calls = {"approved": None}

    def fake_approve(_request_id, **kwargs):
        calls["approved"] = kwargs
        paused["review_actions"].append(
            {
                "action": "diff_approved",
                "user_id": kwargs["user_id"],
                "comment": kwargs.get("comment") or "",
                "timestamp": "2026-04-27T00:02:00",
                "details": {
                    "approved_action_ids": kwargs["approved_action_ids"],
                    "preview_hash": kwargs["preview_hash"],
                },
            }
        )
        return paused

    async def fake_log(**_kwargs):
        return None

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(generation, "mark_paused_generation_diff_approved", fake_approve)
    monkeypatch.setattr(generation, "write_log_async", fake_log)

    response = await generation.approve_methodology_diff(
        "req-1",
        MethodologyReviewActionRequest(comment="diff ok"),
        user={"id": "user_1"},
    )

    assert response["success"] is True
    assert response["review_state"] == "diff_approved"
    assert calls["approved"]["approved_action_ids"] == [action_id]
    assert calls["approved"]["preview_hash"] == preview_hash


@pytest.mark.asyncio
async def test_approve_methodology_diff_requires_preview(monkeypatch) -> None:
    request = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Уточни формулировку в теории.",
    )
    paused = {
        "user_id": "user_1",
        "context": {"markdown": "## Глава 2. Теория\n\nСтарый текст."},
        "review_actions": [_change_action(request)],
    }

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: "needs_review")
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)

    with pytest.raises(HTTPException) as exc_info:
        await generation.approve_methodology_diff(
            "req-1",
            MethodologyReviewActionRequest(comment="diff ok"),
            user={"id": "user_1"},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["review_state"] == "changes_requested"


def test_methodology_review_state_keeps_cumulative_diff_approvals() -> None:
    first = MethodologistChangeRequest(
        target_stage="theory",
        target_selector="Глава 2",
        scope="local_section_only",
        instruction="Уточни теорию.",
    )
    second = MethodologistChangeRequest(
        target_stage="final",
        target_selector="README",
        scope="local_section_only",
        instruction="Сократи финальный повтор.",
    )
    first_action = _change_action(first, timestamp="2026-04-27T00:00:00")
    second_action = _change_action(second, timestamp="2026-04-27T00:03:00")
    first_id = generation.ScopedRevisionExecutor.action_id_for_action(first_action, 0, first)
    second_id = generation.ScopedRevisionExecutor.action_id_for_action(second_action, 3, second)
    first_result = {
        "action_id": first_id,
        "status": "applied",
        "target_kind": "markdown_section",
        "target_stage": "theory",
        "target_selector": "Глава 2",
        "target_id": "chapter_2",
        "scope": "local_section_only",
        "changed": True,
    }
    second_result = {
        "action_id": second_id,
        "status": "applied",
        "target_kind": "markdown_section",
        "target_stage": "final",
        "target_selector": "README",
        "target_id": "readme",
        "scope": "local_section_only",
        "changed": True,
    }
    second_preview_hash = generation._preview_hash([second_result])
    paused = {
        "user_id": "user_1",
        "status": "needs_review",
        "context": {
            "markdown": "## Глава 2. Теоретический блок\n\nТекст.\n\n## Глава 3. Практический блок\n\nПрактика.",
            "methodology_revision_results": [first_result, second_result],
        },
        "review_actions": [
            first_action,
            {
                "action": "preview_ready",
                "timestamp": "2026-04-27T00:01:00",
                "details": {
                    "revision_results": [first_result],
                    "preview_hash": generation._preview_hash([first_result]),
                },
            },
            {
                "action": "diff_approved",
                "timestamp": "2026-04-27T00:02:00",
                "details": {
                    "approved_action_ids": [first_id],
                    "preview_hash": generation._preview_hash([first_result]),
                },
            },
            second_action,
            {
                "action": "preview_ready",
                "timestamp": "2026-04-27T00:04:00",
                "details": {
                    "revision_results": [second_result],
                    "preview_hash": second_preview_hash,
                },
            },
        ],
    }

    review_state = generation._build_methodology_review_state(paused)

    assert review_state["review_state"] == "preview_ready"
    assert review_state["requires_diff_approval"] is True
    assert review_state["approved_action_ids"] == [first_id]
    assert review_state["diff_approvable_action_ids"] == [second_id]

    paused["review_actions"].append(
        {
            "action": "diff_approved",
            "timestamp": "2026-04-27T00:05:00",
            "details": {
                "approved_action_ids": [second_id],
                "preview_hash": second_preview_hash,
            },
        }
    )

    approved_state = generation._build_methodology_review_state(paused)

    assert approved_state["review_state"] == "diff_approved"
    assert approved_state["requires_diff_approval"] is False
    assert approved_state["approved_action_ids"] == [first_id, second_id]


def test_methodology_review_state_uses_preview_checkpoint_payload() -> None:
    request = MethodologistChangeRequest(
        target_stage="final",
        target_selector="README",
        scope="local_section_only",
        instruction="Исправь непройденные критерии.",
    )
    action = _change_action(request, timestamp="2026-04-27T00:00:00")
    action_id = generation.ScopedRevisionExecutor.action_id_for_action(action, 0, request)
    revision_result = {
        "action_id": action_id,
        "status": "applied",
        "target_kind": "markdown_section",
        "target_stage": "final",
        "target_selector": "README",
        "target_id": "readme",
        "scope": "local_section_only",
        "changed": True,
    }
    paused = {
        "user_id": "user_1",
        "status": "needs_review",
        "context": {
            "markdown": "# Старый README",
            "human_approval_checkpoint": {
                "id": "evaluation",
                "stage": "final",
                "artifact": {"rubric": {"total": 1, "max_score": 39}},
            },
        },
        "review_actions": [
            action,
            {
                "action": "preview_ready",
                "timestamp": "2026-04-27T00:01:00",
                "details": {
                    "revision_results": [revision_result],
                    "preview_hash": generation._preview_hash([revision_result]),
                    "preview_context_payload": {
                        "human_approval_checkpoint": {
                            "id": "evaluation",
                            "stage": "final",
                            "artifact": {"rubric": {"total": 35, "max_score": 39}},
                        },
                    },
                },
            },
        ],
    }

    review_state = generation._build_methodology_review_state(paused)

    assert review_state["review_state"] == "preview_ready"
    assert review_state["checkpoint"]["artifact"]["rubric"]["total"] == 35


@pytest.mark.asyncio
async def test_e2e_paused_flow_request_preview_approve_resume_final_report(monkeypatch) -> None:
    status_holder = {"status": "needs_review"}
    captured = {"task": None, "result": None, "completed": False}
    session = {
        "request_id": "req-1",
        "user_id": "user_1",
        "status": "needs_review",
        "project_seed": {"language": "ru"},
        "track_paths": [],
        "context": {
            "markdown": (
                "## Глава 2. Теоретический блок\n\n"
                "Старый текст.\n\n"
                "## Глава 3. Практический блок\n\n"
                "Практика."
            )
        },
        "steps": [],
        "resume_from_index": 4,
        "review_actions": [],
    }

    def fake_record_change(_request_id, **kwargs):
        session["review_actions"].append(
            {
                "action": "changes_requested",
                "user_id": kwargs["user_id"],
                "comment": kwargs["change_request"]["instruction"],
                "timestamp": "2026-04-27T00:00:00",
                "details": {
                    "change_request": kwargs["change_request"],
                    "conflicts": kwargs.get("conflicts") or [],
                },
            }
        )
        return session

    def fake_record_preview(_request_id, **kwargs):
        session["review_actions"].append(
            {
                "action": "preview_ready",
                "user_id": kwargs["user_id"],
                "timestamp": "2026-04-27T00:01:00",
                "details": {
                    "revision_results": kwargs["revision_results"],
                    "target_registry": kwargs["target_registry"],
                    "preview_hash": kwargs["preview_hash"],
                },
            }
        )
        return session

    def fake_mark_diff(_request_id, **kwargs):
        session["review_actions"].append(
            {
                "action": "diff_approved",
                "user_id": kwargs["user_id"],
                "comment": kwargs.get("comment") or "",
                "timestamp": "2026-04-27T00:02:00",
                "details": {
                    "approved_action_ids": kwargs["approved_action_ids"],
                    "preview_hash": kwargs["preview_hash"],
                },
            }
        )
        return session

    def fake_mark_approved(_request_id, **kwargs):
        session["review_actions"].append(
            {
                "action": "approved",
                "user_id": kwargs["user_id"],
                "comment": kwargs.get("comment") or "",
                "timestamp": "2026-04-27T00:03:00",
                "details": {},
            }
        )
        session["status"] = "approved"
        return session

    class FakeOrchestrator:
        def __init__(self, llm_client, methodology_progress_callback=None, human_approval_enabled=None):
            self.llm_client = llm_client
            self.methodology_progress_callback = methodology_progress_callback
            self.human_approval_enabled = human_approval_enabled

        def resume_from_pause(self, *, context, resume_from_index, previous_steps):
            assert resume_from_index == 4
            assert previous_steps == []
            assert any(action["action"] == "diff_approved" for action in context["methodology_review_actions"])
            executor = generation.ScopedRevisionExecutor(self.llm_client)
            executor.apply_pending_change_requests(context)
            return SimpleNamespace(
                report_json={
                    "markdown": context["markdown"],
                    "methodology_revision_results": context.get("methodology_revision_results", []),
                },
                practice_critic_issues=[],
                agent_config_versions={},
                flow_trace=[{"node_id": "resume"}],
            )

    async def fake_save_completed_generation(**kwargs):
        captured["result"] = kwargs["result"]
        status_holder["status"] = "completed"
        return True

    async def fake_log(**_kwargs):
        return None

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: status_holder["status"])
    monkeypatch.setattr(generation, "set_generation_status", lambda _request_id, status: status_holder.__setitem__("status", status))
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: session)
    monkeypatch.setattr(generation, "record_paused_generation_change_request", fake_record_change)
    monkeypatch.setattr(generation, "record_paused_generation_preview", fake_record_preview)
    monkeypatch.setattr(generation, "mark_paused_generation_diff_approved", fake_mark_diff)
    monkeypatch.setattr(generation, "mark_paused_generation_approved", fake_mark_approved)
    monkeypatch.setattr(generation, "mark_paused_generation_completed", lambda _request_id: captured.__setitem__("completed", True))
    monkeypatch.setattr(generation, "create_llm_client", lambda **_kwargs: FakeRevisionLLM())
    monkeypatch.setattr(generation, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr(generation, "_save_completed_generation", fake_save_completed_generation)
    monkeypatch.setattr(generation, "register_generation_task", lambda _request_id, task: captured.__setitem__("task", task))
    monkeypatch.setattr(generation, "unregister_generation_task", lambda _request_id: None)
    monkeypatch.setattr(generation, "store_generation_error", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(generation, "set_generation_methodology", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(generation, "write_log_async", fake_log)

    change_response = await generation.request_methodology_changes(
        "req-1",
        MethodologistChangeRequest(
            target_stage="theory",
            target_selector="Глава 2",
            scope="local_section_only",
            instruction="Уточни формулировку в теории.",
            expected_outcome="Фрагмент станет точнее.",
        ),
        user={"id": "user_1"},
    )
    preview_response = await generation.preview_methodology_changes("req-1", user={"id": "user_1"})
    diff_response = await generation.approve_methodology_diff(
        "req-1",
        MethodologyReviewActionRequest(comment="diff ok"),
        user={"id": "user_1"},
    )
    approve_response = await generation.approve_methodology_review(
        "req-1",
        MethodologyReviewActionRequest(comment="resume"),
        user={"id": "user_1"},
    )
    await captured["task"]

    assert change_response["review_state"] == "changes_requested"
    assert preview_response["review_state"] == "preview_ready"
    assert diff_response["review_state"] == "diff_approved"
    assert approve_response.status == "in_progress"
    assert status_holder["status"] == "completed"
    assert captured["completed"] is True
    assert "Новый текст" in captured["result"].report_json["markdown"]
    assert "Практика." in captured["result"].report_json["markdown"]
    assert captured["result"].report_json["methodology_revision_results"][0]["status"] == "applied"
    assert [action["action"] for action in session["review_actions"]] == [
        "changes_requested",
        "preview_ready",
        "diff_approved",
        "approved",
    ]


@pytest.mark.asyncio
async def test_status_restores_needs_review_from_db_session(monkeypatch) -> None:
    calls = {"status": None, "methodology": None}
    paused = {
        "user_id": "user_1",
        "methodology": {"summary": {"latest_action": "pause"}, "decisions": []},
    }

    monkeypatch.setattr(generation, "get_generation_status", lambda _request_id: None)
    monkeypatch.setattr(generation, "load_paused_generation_session", lambda _request_id: paused)
    monkeypatch.setattr(generation, "set_generation_status", lambda _request_id, status: calls.__setitem__("status", status))
    monkeypatch.setattr(generation, "set_generation_methodology", lambda _request_id, payload: calls.__setitem__("methodology", payload))
    monkeypatch.setattr(generation, "get_generation_methodology", lambda _request_id: calls["methodology"])
    monkeypatch.setattr(generation, "get_generation_error", lambda _request_id: "needs review")

    response = await generation.get_generation_status_endpoint("req-1", user={"id": "user_1"})

    assert response.status == "needs_review"
    assert response.methodology["summary"]["latest_action"] == "pause"
    assert calls["status"] == "needs_review"
    assert calls["methodology"] == paused["methodology"]
