from types import SimpleNamespace

import pytest

from content_factory.api.services.generation_quality_gate import collect_blocking_quality_findings
from content_factory.api.services.generation_result_persistence import GenerationResultPersister


def test_quality_gate_detects_serialized_hard_issue_and_mojibake() -> None:
    result = SimpleNamespace(
        report_json={
            "issues": ["{'severity': 'hard', 'code': 'practice.count', 'message': 'count mismatch'}"]
        },
        practice_critic_issues=[],
    )

    findings = collect_blocking_quality_findings(result, "Текст � Ð¢ÐµÑÑ")

    assert {item.code for item in findings} == {
        "practice.count",
        "text.mojibake",
        "text.replacement_character",
    }


def test_quality_gate_allows_soft_findings() -> None:
    result = SimpleNamespace(
        report_json={"issues": [{"severity": "soft", "message": "minor wording"}]},
        practice_critic_issues=[{"severity": "minor", "message": "optional"}],
    )

    assert collect_blocking_quality_findings(result, "Корректный текст") == []


@pytest.mark.asyncio
async def test_result_persister_does_not_store_blocked_result() -> None:
    statuses: list[str] = []
    errors: list[str] = []
    stored: list[object] = []

    async def log_writer(**_kwargs) -> None:
        return None

    persister = GenerationResultPersister(
        status_setter=lambda _request_id, status: statuses.append(status),
        error_store=lambda _request_id, error: errors.append(error),
        result_store=lambda *_args, **_kwargs: stored.append(object()),
        result_saver=lambda *_args, **_kwargs: None,
        log_writer=log_writer,
    )
    result = SimpleNamespace(
        report_json={
            "markdown": "# README",
            "issues": [{"severity": "hard", "code": "practice.count", "message": "mismatch"}],
        },
        practice_critic_issues=[],
    )

    saved = await persister.save_completed_generation(
        request_id="req-quality",
        user_id="u1",
        project_seed_payload={},
        result=result,
    )

    assert saved is False
    assert statuses == ["failed"]
    assert errors and "practice.count" in errors[0]
    assert stored == []
