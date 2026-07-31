"""ProfanityChecker: deterministic obscene-word detection (obfuscation-tolerant) + LLM fallback."""

from __future__ import annotations

from pathlib import Path

from content_factory.audit.checker_base import CheckContext
from content_factory.audit.domain import CRITERION_LABELS, AuditSettings, Criterion, Severity, Verdict
from content_factory.audit.ingestion import discover_content_units, load_unit_files
from content_factory.audit.profanity import ProfanityChecker


class _FakeJsonClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.model = "fake-model"
        self.last_call_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost_usd": 0.001}

    def complete_json(self, system_prompt: str, user_prompt: str, max_retries: int = 2) -> object:
        del system_prompt, user_prompt, max_retries
        return self.response


def _settings(tmp_path: Path, project: Path) -> AuditSettings:
    return AuditSettings(input_path=project, output_path=tmp_path / "out", allow_network=False)


def _unit_with_readme(tmp_path: Path, body: str):
    project = tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(body, encoding="utf-8")
    return load_unit_files(discover_content_units(project)[0], max_file_bytes=100000), project


def _profanity(findings) -> list:
    return [f for f in findings if f.criterion == Criterion.PROFANITY]


def test_plain_obscene_word_is_flagged(workspace_tmp_path: Path) -> None:
    unit, project = _unit_with_readme(workspace_tmp_path, "# Проект\nИди нахуй отсюда.\n")

    findings = _profanity(ProfanityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project))))

    assert len(findings) == 1
    assert findings[0].severity == Severity.MAJOR and findings[0].verdict == Verdict.FAIL
    assert findings[0].extra["source"] == "rule"
    assert findings[0].location is not None and findings[0].location.line_start == 2


def test_obfuscation_symbol_and_latin_homoglyph_are_caught(workspace_tmp_path: Path) -> None:
    # punctuation between letters + latin homoglyph 'x' for cyrillic 'х'
    unit, project = _unit_with_readme(workspace_tmp_path, "# T\nтакой х.у.й тут\nещё xуё тут\n")

    findings = _profanity(ProfanityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project))))

    assert len(findings) >= 2


def test_legit_lookalike_words_are_not_flagged(workspace_tmp_path: Path) -> None:
    # boundary before the root: страхуй/психуй/мудрый must NOT match
    unit, project = _unit_with_readme(
        workspace_tmp_path, "# Проект\nЗастрахуй риски, не психуй, будь мудрый.\nПодстрахуй команду.\n"
    )

    findings = _profanity(ProfanityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project))))

    assert findings == []


def test_clean_text_has_no_findings(workspace_tmp_path: Path) -> None:
    unit, project = _unit_with_readme(workspace_tmp_path, "# Проект\nОбычный текст без ругани.\n")

    assert _profanity(ProfanityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))) == []


def test_model_fallback_catches_disguised_and_filters_low_confidence(workspace_tmp_path: Path) -> None:
    unit, project = _unit_with_readme(workspace_tmp_path, "# Проект\nЗдесь всё прилично.\n")
    client = _FakeJsonClient(
        {
            "items": [
                {"quote": "х у й через пробелы", "line": 2, "reason": "разрядка пробелами", "confidence": 0.9},
                {"quote": "сомнительное", "line": 2, "reason": "borderline", "confidence": 0.4},
            ]
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=client)

    findings = _profanity(ProfanityChecker().check(unit, [], context))

    assert len(findings) == 1  # low-confidence item dropped
    assert findings[0].extra["source"] == "model"
    assert context.model_usage["calls_total"] == 1


def test_profanity_criterion_is_labeled() -> None:
    from content_factory.audit.web_rendering import _criterion_short_label

    assert CRITERION_LABELS[Criterion.PROFANITY] == "Недопустимая лексика"
    assert _criterion_short_label(Criterion.PROFANITY) == "Лексика"
