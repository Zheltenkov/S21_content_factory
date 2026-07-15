"""A blocking PracticeCritic finding must say WHAT is wrong, not just how many."""

from __future__ import annotations

from content_factory.generation.node_services import PracticeNodeService


class _Issue:
    """Critic issues reach the node as objects on some paths, dicts on others."""

    def __init__(self, severity: str, message: str, suggestion: str = "", task_index: int | None = None) -> None:
        self.severity = severity
        self.message = message
        self.suggestion = suggestion
        self.task_index = task_index


def test_blocking_finding_is_described_with_task_and_suggestion() -> None:
    issues = [
        {"severity": "info", "message": "стилистика"},
        {
            "severity": "critical",
            "task_index": 2,
            "message": "Задача не проверяет заявленный навык",
            "suggestion": "Добавить проверяемый критерий приёмки",
        },
    ]

    described = PracticeNodeService._describe_critic_issues(issues)

    assert "Задача не проверяет заявленный навык" in described
    assert "задача 2" in described
    assert "Добавить проверяемый критерий приёмки" in described
    assert "стилистика" not in described  # non-blocking findings are not failure reasons


def test_object_shaped_issues_are_described_too() -> None:
    described = PracticeNodeService._describe_critic_issues(
        [_Issue("hard", "Нет входных данных", "Приложить датасет", 1)]
    )

    assert described == "задача 1: Нет входных данных → Приложить датасет"


def test_only_non_blocking_findings_describe_to_nothing() -> None:
    assert PracticeNodeService._describe_critic_issues([{"severity": "info", "message": "мелочь"}]) == ""
    assert PracticeNodeService._has_blocking_critic_issues([{"severity": "info", "message": "мелочь"}]) is False


def test_blocking_detection_still_flags_every_blocking_severity() -> None:
    for severity in ("critical", "hard", "error"):
        assert PracticeNodeService._has_blocking_critic_issues([{"severity": severity, "message": "x"}]) is True
