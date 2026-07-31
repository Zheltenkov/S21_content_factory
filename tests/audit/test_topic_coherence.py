"""TopicCoherenceChecker: off-topic / contradicting sentences vs the README's declared theme."""

from __future__ import annotations

from pathlib import Path

from content_factory.audit.checker_base import CheckContext
from content_factory.audit.domain import CRITERION_LABELS, AuditSettings, Criterion, Severity, Verdict
from content_factory.audit.ingestion import discover_content_units, load_unit_files
from content_factory.audit.topic_coherence import TopicCoherenceChecker


class _FakeJsonClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.model = "fake-model"
        self.calls = 0
        self.last_call_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost_usd": 0.001}

    def complete_json(self, system_prompt: str, user_prompt: str, max_retries: int = 2) -> object:
        del system_prompt, max_retries
        self.calls += 1
        self.user_prompt = user_prompt
        return self.response


def _settings(tmp_path: Path, project: Path) -> AuditSettings:
    return AuditSettings(input_path=project, output_path=tmp_path / "out", allow_network=False)


def _pm_project(tmp_path: Path) -> Path:
    project = tmp_path / "PjM1_Basics"
    project.mkdir()
    (project / "README.md").write_text(
        "# Управление ИТ-проектами\n\n"
        "Проект знакомит с ролью проджект-менеджера и жизненным циклом ИТ-проекта.\n"
        "Черепахи живут до 150 лет и медленно передвигаются по суше.\n"
        "Собери план проекта и оцени риски.\n",
        encoding="utf-8",
    )
    (project / "check-list.yml").write_text(
        "name: Проекты и процессы в управлении\n"
        "sections:\n"
        "  - questions:\n"
        "      - name: Составлен план проекта\n"
        "      - name: Оценены риски\n",
        encoding="utf-8",
    )
    return project


def _unit(tmp_path: Path):
    project = _pm_project(tmp_path)
    return load_unit_files(discover_content_units(project)[0], max_file_bytes=100000), project


def test_off_topic_sentence_becomes_a_topic_relevance_finding(workspace_tmp_path: Path) -> None:
    unit, project = _unit(workspace_tmp_path)
    client = _FakeJsonClient(
        {"items": [{"quote": "Черепахи живут до 150 лет", "line": 4, "reason": "Про черепах, не про управление проектами", "kind": "off_topic", "confidence": 0.9}]}
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=client)

    findings = TopicCoherenceChecker().check(unit, [], context)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.criterion == Criterion.TOPIC_RELEVANCE
    assert finding.severity == Severity.MINOR and finding.verdict == Verdict.WARNING
    assert finding.extra["issue_type"] == "off_topic"
    assert "Черепахи" in (finding.quote or "")
    assert finding.location is not None and finding.location.line_start == 4
    assert context.model_usage["calls_total"] == 1


def test_contradiction_is_major(workspace_tmp_path: Path) -> None:
    unit, project = _unit(workspace_tmp_path)
    client = _FakeJsonClient(
        {"items": [{"quote": "Управлять проектом не нужно", "line": 3, "reason": "Противоречит теме управления проектами", "kind": "contradiction", "confidence": 0.88}]}
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=client)

    findings = TopicCoherenceChecker().check(unit, [], context)

    assert len(findings) == 1
    assert findings[0].severity == Severity.MAJOR and findings[0].verdict == Verdict.FAIL
    assert findings[0].extra["issue_type"] == "contradiction"


def test_low_confidence_items_are_dropped(workspace_tmp_path: Path) -> None:
    unit, project = _unit(workspace_tmp_path)
    client = _FakeJsonClient(
        {"items": [{"quote": "Черепахи живут до 150 лет", "line": 4, "reason": "borderline", "kind": "off_topic", "confidence": 0.5}]}
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=client)

    assert TopicCoherenceChecker().check(unit, [], context) == []


def test_no_model_client_means_no_findings(workspace_tmp_path: Path) -> None:
    unit, project = _unit(workspace_tmp_path)
    context = CheckContext(_settings(workspace_tmp_path, project))  # no model_client

    assert TopicCoherenceChecker().check(unit, [], context) == []


def test_declared_theme_combines_readme_title_and_checklist(workspace_tmp_path: Path) -> None:
    unit, _project = _unit(workspace_tmp_path)

    theme = TopicCoherenceChecker()._declared_theme(unit)

    assert "Управление ИТ-проектами" in theme  # README H1
    assert "Проекты и процессы в управлении" in theme  # check-list.yml name
    assert "Оценены риски" in theme  # checklist question name


def test_topic_relevance_criterion_is_labeled() -> None:
    from content_factory.audit.web_rendering import _criterion_short_label

    assert CRITERION_LABELS[Criterion.TOPIC_RELEVANCE] == "Соответствие теме"
    assert _criterion_short_label(Criterion.TOPIC_RELEVANCE) == "Тема"
