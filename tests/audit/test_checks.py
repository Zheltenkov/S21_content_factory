from datetime import datetime, timezone
from pathlib import Path

from content_factory.audit import checks as checks_module
from content_factory.audit.cache import AuditCache
from content_factory.audit.checks import (
    BrokenUrlSyntaxChecker,
    CheckContext,
    ChecklistChecker,
    CurriculumRelevanceChecker,
    FactCheckerPerplexity,
    ImageQualityChecker,
    LanguageCoverageChecker,
    LabelPunctuationChecker,
    LinkChecker,
    LocalConsistencyChecker,
    MarkdownStructureChecker,
    MarketFitChecker,
    ModelRubricChecker,
    ReadmeFactActualityChecker,
    ReadabilityChecker,
    RegionalAvailabilityChecker,
    ResourceAvailabilityChecker,
    RightsAndOriginalityChecker,
    RightsChecker,
    SpellingAndWordingChecker,
    TechFreshnessChecker,
    TechnologyFreshnessChecker,
    default_checkers,
)
from content_factory.audit.dependencies import DependencyCandidate, DependencyMetadata, DependencyRegistryClient
from content_factory.audit.domain import AuditSettings, Criterion, Severity, Verdict
from content_factory.audit.extraction import extract_entities
from content_factory.audit.ingestion import discover_content_units, load_unit_files


def _settings(tmp_path: Path, project: Path) -> AuditSettings:
    return AuditSettings(input_path=project, output_path=tmp_path / "out", allow_network=False)


class _FakeJsonClient:
    def __init__(self, response):
        self.response = response
        self.model = "fake-model"
        self.calls = 0
        self.last_call_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost_usd": 0.001}

    def complete_json(self, system_prompt: str, user_prompt: str, max_retries: int = 2):
        del system_prompt, max_retries
        self.calls += 1
        self.user_prompt = user_prompt
        self.user_prompts = getattr(self, "user_prompts", [])
        self.user_prompts.append(user_prompt)
        return self.response


def test_checklist_checker_accepts_part_names(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("## Part 1. Работа с утилитой cat\n", encoding="utf-8")
    (project / "check-list.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Part_1.CAT\n"
        "        description: Must check src/cat.c, expected stdout and error handling. Example input is provided.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ChecklistChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].criterion == Criterion.CHECKLIST_ALIGNMENT
    assert findings[0].verdict == Verdict.PASS


def test_checklist_checker_matches_number_and_keyword_across_language(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_UZB.md").write_text("## 1-qism. cat utilitasi bilan ishlash\n", encoding="utf-8")
    (project / "check-list.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Part_1.CAT\n"
        "        description: Must check src/cat.c, expected stdout and error handling. Example input is provided.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ChecklistChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].verdict == Verdict.PASS


def test_checklist_checker_keeps_lexical_weak_match_minor(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("## Part 4. Log generator\n", encoding="utf-8")
    (project / "check-list.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Part_4.File_generator\n"
        "        description: Must check src/log_generator.c, expected output and error handling. Example input is provided.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ChecklistChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].criterion == Criterion.CHECKLIST_ALIGNMENT
    assert findings[0].verdict == Verdict.WARNING
    assert findings[0].severity == Severity.MINOR
    assert findings[0].extra["strong_matched"] == 0
    assert findings[0].extra["weak_matched"] == 1


def test_checklist_checker_flags_missing_expanded_descriptions_as_major(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("## Part 1. Работа с cat\n", encoding="utf-8")
    (project / "check-list.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Part_1.CAT\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ChecklistChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].verdict == Verdict.WARNING
    assert findings[0].severity == Severity.MAJOR
    assert findings[0].extra["description_ratio"] == 0.0
    assert findings[0].extra["incomplete_questions"] == ["Part_1.CAT"]


def test_checklist_checker_keeps_partial_descriptions_minor(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "## Exercise 00 - Terminology\n"
        "## Exercise 01 - Data Preparation\n"
        "## Exercise 02 - UC Update\n",
        encoding="utf-8",
    )
    (project / "check-list.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Exercise 00 - Terminology\n"
        "        description: Terms are compared.\n"
        "      - name: Exercise 01 - Data Preparation\n"
        "        description: Ported from previous projects.\n"
        "      - name: Exercise 02 - UC Update\n"
        "        description: UC is analyzed. The response set must contain request.json, at least 2 responses, expected output and an example.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=3000)

    findings = ChecklistChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].verdict == Verdict.WARNING
    assert findings[0].severity == Severity.MINOR
    assert 0.0 < findings[0].extra["description_ratio"] < 0.8


def test_language_checker_flags_single_language(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text("# Проект\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LanguageCoverageChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_language_checker_passes_when_expected_languages_are_present(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text("# Проект\n", encoding="utf-8")
    (project / "README.md").write_text("# Project\nThis project explains the task for students.\n", encoding="utf-8")
    settings = _settings(workspace_tmp_path, project).model_copy(update={"expected_languages": ("RUS", "ENG")})
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LanguageCoverageChecker().check(unit, [], CheckContext(settings))

    assert findings == []


def test_language_checker_cross_checks_suffix_with_content(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "This project explains how to build and test command line utilities. "
        "Students should read the instructions carefully before starting the task.",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LanguageCoverageChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert any(finding.evidence[0].title == "Несовпадение языка" for finding in findings)
    assert findings[0].extra["expected"] == "RUS"
    assert findings[0].extra["detected"] == "ENG"
    assert findings[0].extra["missing_languages"] == ["ENG", "UZ", "TG"]


def test_readability_checker_does_not_flag_long_lines_without_model(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(f"{'Очень длинный учебный абзац. ' * 20}\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)

    findings = ReadabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_readability_checker_does_not_flag_normal_here_will_be_phrase(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("В этом разделе здесь будет описан порядок настройки сервиса.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ReadabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_readability_checker_flags_real_placeholder_phrase(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("Здесь будет описание проекта.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ReadabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].severity == Severity.MAJOR
    assert findings[0].verdict == Verdict.FAIL


def test_readability_checker_lets_model_decide_long_line_warning(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(f"{'Очень длинный учебный абзац с несколькими мыслями. ' * 16}\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)
    fake_client = _FakeJsonClient(
        {
            "verdict": "warning",
            "severity": "minor",
            "confidence": 0.82,
            "problem_lines": [1],
            "evidence": "Абзац перегружен несколькими действиями и плохо сканируется.",
            "recommendation": "Разбить абзац на короткие пункты.",
        }
    )
    cache = AuditCache.load(workspace_tmp_path / "readability_cache.json")
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client, cache=cache)

    first = ReadabilityChecker().check(unit, [], context)
    second = ReadabilityChecker().check(unit, [], context)

    assert fake_client.calls == 1
    assert first[0].criterion == Criterion.READABILITY
    assert first[0].verdict == Verdict.WARNING
    assert first[0].location is not None
    assert first[0].location.line_start == 1
    assert first[0].prompt_version == "readability_checker:v2"
    assert second[0].extra["cache_hit"] is True
    assert context.model_usage["calls_total"] == 1
    assert context.model_usage["cache_hits"] == 1


def test_spelling_wording_checker_flags_rule_based_editorial_issues(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "Чтобы завершить нажатие нужно нажатием Enter.\n"
        "Данные поступают неупорядоченные.\n"
        "Введите специализацию врача и дата визита.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = SpellingAndWordingChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert {finding.location.line_start for finding in findings if finding.location} == {1, 2, 3}
    assert all(finding.criterion == Criterion.READABILITY for finding in findings)
    assert all(finding.severity == Severity.MINOR for finding in findings)
    assert all(finding.verdict == Verdict.WARNING for finding in findings)
    assert any(finding.extra["issue_type"] == "tautology" for finding in findings)


def test_spelling_wording_checker_uses_model_windows_and_cache(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "\n".join(
            [
                "Учебный проект описывает ввод данных, обработку ошибок и формат вывода.",
                "Пользователь должен ввести специализацию врача, дату визита и выбрать действие.",
                "Система выводит подсказки и сохраняет результат выполнения упражнения.",
            ]
        ),
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    fake_client = _FakeJsonClient(
        {
            "findings": [
                {
                    "line": 2,
                    "issue_type": "case",
                    "quote": "специализацию врача, дату визита",
                    "issue": "Перечисление стоит проверить на падежное согласование.",
                    "suggestion": "Оставить единый падеж для всех элементов перечисления.",
                    "confidence": 0.76,
                }
            ]
        }
    )
    cache = AuditCache.load(workspace_tmp_path / "spelling_cache.json")
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client, cache=cache)

    first = SpellingAndWordingChecker().check(unit, [], context)
    second = SpellingAndWordingChecker().check(unit, [], context)

    assert fake_client.calls == 1
    assert first[0].checker_name == "spelling_wording_checker"
    assert first[0].location is not None
    assert first[0].location.line_start == 2
    assert first[0].prompt_version == "spelling_wording_checker:v1"
    assert second[0].extra["cache_hit"] is True
    assert context.model_usage["calls_total"] == 1
    assert context.model_usage["cache_hits"] == 1
    assert '"line": 2' in fake_client.user_prompt


def test_spelling_wording_checker_rejects_unanchored_model_issue(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "Учебный проект описывает ввод данных, обработку ошибок и формат вывода.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    fake_client = _FakeJsonClient(
        {
            "findings": [
                {
                    "line": 99,
                    "issue_type": "typo",
                    "quote": "несуществующая цитата",
                    "issue": "Модель ошиблась строкой.",
                    "suggestion": "Не должно попасть в отчёт.",
                    "confidence": 0.9,
                }
            ]
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client)

    findings = SpellingAndWordingChecker().check(unit, [], context)

    assert findings == []


def test_local_consistency_checker_flags_sort_direction_conflict(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "### Задание 2. Частые слова\n"
        "Результатом является отсортированный по возрастанию список из K наиболее частых слов.\n"
        "1) Программа считывает строку.\n"
        "1) Программа сортирует результирующий массив по убыванию и возвращает K первых слов.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LocalConsistencyChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].criterion == Criterion.FACTS
    assert findings[0].location is not None
    assert findings[0].location.line_start == 2
    assert findings[0].location.line_end == 4
    assert findings[0].extra["issue_type"] == "sort_direction_conflict"


def test_local_consistency_checker_flags_invalid_word_definition(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text("Словом является любой символ, разделенный пробелами.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LocalConsistencyChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].criterion == Criterion.FACTS
    assert findings[0].extra["issue_type"] == "invalid_definition"
    assert "последовательность символов" in findings[0].recommendation


def test_local_consistency_checker_flags_field_variant_and_table_mismatch(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "The script should return the following fields: **Name**, **Surname**, **Email**.\n"
        "| Name | Surname | Phone |\n"
        "| --- | --- | --- |\n"
        "Use E-mail as a contact field in the final file.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LocalConsistencyChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))
    issue_types = {finding.extra["issue_type"] for finding in findings}

    assert "table_description_mismatch" in issue_types
    assert "field_name_variant" in issue_types


def test_full_audit_includes_local_consistency_checker() -> None:
    checker_names = [checker.name for checker in default_checkers(use_model=True)]

    assert "local_consistency_checker" in checker_names
    assert "readability_checker" not in checker_names
    assert "spelling_wording_checker" in checker_names


def test_resource_availability_checker_flags_unconfirmed_environment_path(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "В виртуальной машине исходные файлы должны лежать в /opt/source21.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ResourceAvailabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].criterion == Criterion.CORRECTNESS
    assert findings[0].severity == Severity.MAJOR
    assert findings[0].extra["issue_type"] == "unconfirmed_environment_path"
    assert findings[0].quote == "/opt/source21"


def test_checklist_checker_emits_atomic_artifact_content_finding(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    materials = project / "materials"
    materials.mkdir()
    (materials / "capture.pcapng").write_bytes(b"GET / HTTP/1.1\r\nHost: example.local\r\n")
    (project / "README.md").write_text(
        "## Task 2\n\nAnalyze the attached pcapng dump.\n",
        encoding="utf-8",
    )
    (project / "check-list.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Task 2. Reverse shell evidence\n"
        "        description: The expected pcapng contains command output from `whoami`.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ChecklistChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    artifact_findings = [
        finding for finding in findings if finding.extra.get("issue_type") == "artifact_missing_expected_text"
    ]
    assert len(artifact_findings) == 1
    assert "whoami" in artifact_findings[0].quote
    assert artifact_findings[0].criterion == Criterion.CHECKLIST_ALIGNMENT


def test_resource_availability_checker_flags_missing_required_pcap(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Open the provided capture `traffic.pcapng` and find suspicious packets.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ResourceAvailabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].verdict == Verdict.FAIL
    assert findings[0].extra["issue_type"] == "missing_local_resource"
    assert findings[0].quote == "traffic.pcapng"


def test_resource_availability_checker_accepts_linked_input_and_output_file(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "В прикреплённом файле [ds.csv](https://drive.google.com/file/d/example/view) есть данные о вакансиях.\n"
        "Разработай скрипт, который открывает файл ds.csv и сохраняет данные в новый файл ds.tsv.\n",
        encoding="utf-8",
    )
    (project / "check-list_RUS.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Работа с файлами\n"
        "        description: >\n"
        "          - Запусти скрипт на приложенном файле ds.csv.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)

    findings = ResourceAvailabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_resource_availability_checker_accepts_existing_resource(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    materials = project / "materials"
    materials.mkdir()
    (materials / "traffic.pcapng").write_text("pcap", encoding="utf-8")
    (project / "README.md").write_text(
        "Open the provided capture `materials/traffic.pcapng` and find suspicious packets.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ResourceAvailabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_resource_availability_checker_flags_dataset_without_artifact(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "Для выполнения задания загрузите датасет клиентов и проанализируйте продажи.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ResourceAvailabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].extra["issue_type"] == "resource_without_artifact"
    assert findings[0].extra["resource_kind"] == "dataset"


def test_resource_availability_checker_ignores_output_artifact(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Create a script and save the result to employees.tsv.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ResourceAvailabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_full_audit_includes_resource_availability_checker() -> None:
    checker_names = [checker.name for checker in default_checkers(use_model=True)]

    assert "resource_availability_checker" in checker_names


def test_technology_checker_creates_actuality_candidate(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("Use Alpine 3.20.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)

    findings = TechnologyFreshnessChecker().check(unit, entities, CheckContext(_settings(workspace_tmp_path, project)))

    assert any(finding.criterion == Criterion.TECHNOLOGY_FRESHNESS for finding in findings)


def test_technology_checker_ignores_makefile_target_instruction(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "check-list.yml").write_text(
        "- The program is built with Makefile with target s21_cat.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)

    findings = TechnologyFreshnessChecker().check(unit, entities, CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_tech_freshness_checker_ignores_exercise_numbers_and_turn_in_labels(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "10.1. [Exercise 06. Sorting a dictionary](#exercise-06-sorting-a-dictionary)\n"
        "- Turn-in directory: `ex00/`.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    fake_client = _FakeJsonClient(
        {
            "verdict": "unknown",
            "severity": "info",
            "confidence": 0.1,
            "support_status": "неизвестно",
            "evidence": "Нет проверяемой версии технологии.",
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), tech_model_client=fake_client)

    findings = TechFreshnessChecker().check(unit, entities, context)

    assert findings == []
    assert fake_client.calls == 0


def test_technology_checker_skips_unknown_without_evidence(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("Use Alpine 3.20.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    context = CheckContext(
        _settings(workspace_tmp_path, project),
        tech_model_client=_FakeJsonClient({"verdict": "unknown", "severity": "info"}),
    )

    findings = TechnologyFreshnessChecker().check(unit, entities, context)

    assert findings == []


def test_technology_checker_skips_low_confidence_unknown_without_sources(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("Use Alpine 3.20.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    context = CheckContext(
        _settings(workspace_tmp_path, project),
        tech_model_client=_FakeJsonClient(
            {
                "verdict": "unknown",
                "severity": "info",
                "confidence": 0.1,
                "support_status": "неизвестно",
                "evidence": "Недостаточно источников для проверки.",
                "recommendation": "Проверить вручную.",
            }
        ),
    )

    findings = TechnologyFreshnessChecker().check(unit, entities, context)

    assert findings == []


def test_market_fit_checker_passes_when_all_business_signals_exist(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    data_dir = project / "data"
    data_dir.mkdir()
    (data_dir / "customers.csv").write_text("id,churn\n1,0\n", encoding="utf-8")
    (project / "README.md").write_text(
        "Проект работает с реальными данными клиентов.\n"
        "Бизнес-задача: снизить отток клиентов банка.\n"
        "Метрика успеха: уменьшить churn и повысить retention.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)

    findings = MarketFitChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].criterion == Criterion.MARKET_FIT
    assert findings[0].verdict == Verdict.PASS
    assert findings[0].extra["market_fit_score"] == 3
    assert findings[0].needs_human_review is False


def test_market_fit_checker_accepts_target_audience_and_business_requirements(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    data_dir = project / "datasets"
    data_dir.mkdir()
    (data_dir / "orders.parquet").write_text("id,total\n1,100\n", encoding="utf-8")
    (project / "README.md").write_text(
        "Целевая аудитория: менеджеры интернет-магазина, которые планируют закупки.\n"
        "Пользовательский сценарий: прогнозировать спрос по историческим данным заказов.\n"
        "Бизнес-требование: сократить время обработки заявок и контролировать SLA.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)

    findings = MarketFitChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].verdict == Verdict.PASS
    assert findings[0].extra["sub_checks"]["real_data"]["present"] is True
    assert findings[0].extra["sub_checks"]["business_context"]["present"] is True
    assert findings[0].extra["sub_checks"]["success_metrics"]["present"] is True


def test_market_fit_checker_flags_missing_success_metrics(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Используется датасет продаж.\n"
        "Бизнес-проблема: заказчик хочет лучше понимать спрос.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)

    findings = MarketFitChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].verdict == Verdict.WARNING
    assert findings[0].severity == Severity.MINOR
    assert findings[0].extra["sub_checks"]["success_metrics"]["present"] is False


def test_market_fit_checker_detects_service_business_context_without_dataset(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "The management of a chain of barbershops decided to implement an online booking system.\n"
        "The objective is to expand the customer base and reduce employee labour costs and manual labour.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)

    findings = MarketFitChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].verdict == Verdict.WARNING
    assert findings[0].severity == Severity.MINOR
    assert findings[0].extra["market_fit_score"] == 1
    assert findings[0].extra["sub_checks"]["business_context"]["present"] is True
    assert findings[0].extra["sub_checks"]["real_data"]["present"] is False
    assert findings[0].extra["sub_checks"]["success_metrics"]["present"] is False


def test_market_fit_checker_does_not_count_generic_technical_data_as_market_fit(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Autotests compare correct output data with expected results.\n"
        "The service exports CSV reports for manual review.\n"
        "Наша игра — многопользовательская.\n"
        "Интеграционные тесты сравнивают результат со стандартным выводом.\n",
        encoding="utf-8",
    )
    (project / "reports.csv").write_text("metric,value\ncoverage,90\n", encoding="utf-8")
    tests_dir = project / "tests" / "fixtures"
    tests_dir.mkdir(parents=True)
    (tests_dir / "expected.csv").write_text("value\n42\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)

    findings = MarketFitChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_market_fit_checker_uses_model_to_refine_weak_signals(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Пользовательский сценарий: аналитики принимают решения по заявкам.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)
    fake_client = _FakeJsonClient(
        {
            "verdict": "pass",
            "severity": "info",
            "confidence": 0.8,
            "real_data": True,
            "business_context": True,
            "success_metrics": True,
            "evidence": "В тексте есть прикладной сценарий, а данные и критерии успеха заданы другими словами.",
            "recommendation": "Действий не требуется.",
        }
    )
    cache = AuditCache.load(workspace_tmp_path / "market_cache.json")
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client, cache=cache)

    first = MarketFitChecker().check(unit, [], context)
    second = MarketFitChecker().check(unit, [], context)

    assert fake_client.calls == 1
    assert first[0].verdict == Verdict.PASS
    assert first[0].extra["market_fit_score"] == 3
    assert first[0].prompt_version == "market_fit_checker:v1"
    assert second[0].extra["cache_hit"] is True


def test_rights_checker_treats_missing_license_as_advisory(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = RightsChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings[0].criterion == Criterion.RIGHTS
    assert findings[0].severity == Severity.INFO
    assert findings[0].verdict == Verdict.WARNING
    assert findings[0].needs_human_review is False


def test_rights_checker_flags_significant_image_without_source(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("![architecture](diagram.png)\n", encoding="utf-8")
    (project / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (project / "diagram.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (480).to_bytes(4, "big")
        + (320).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)

    findings = RightsAndOriginalityChecker().check(unit, entities, CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].severity == Severity.MINOR
    assert findings[0].verdict == Verdict.WARNING
    assert findings[0].needs_human_review is True
    assert findings[0].extra["kind"] == "image_provenance"


def test_rights_checker_ignores_decorative_image(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("![logo](logo.png)\n", encoding="utf-8")
    (project / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (project / "logo.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (48).to_bytes(4, "big")
        + (48).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)

    findings = RightsAndOriginalityChecker().check(unit, entities, CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_rights_checker_flags_dataset_without_license_terms(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (project / "README.md").write_text(
        "Используется датасет продаж с Kaggle: https://kaggle.com/datasets/example/sales.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = RightsAndOriginalityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].extra["kind"] == "dataset_rights"
    assert findings[0].severity == Severity.MINOR
    assert findings[0].needs_human_review is True


def test_rights_checker_uses_registry_dependency_license(workspace_tmp_path: Path, monkeypatch) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    (project / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (project / "package.json").write_text('{"dependencies":{"copyleft-lib":"1.0.0"}}', encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=5000)

    def fake_fetch(self, candidate: DependencyCandidate) -> DependencyMetadata:
        del self
        return DependencyMetadata(
            ecosystem=candidate.ecosystem,
            name=candidate.name,
            latest_version="1.0.0",
            source_url=f"https://registry.npmjs.org/{candidate.name}",
            checked_at=datetime.now(timezone.utc),
            license_spdx="GPL-3.0-only",
        )

    monkeypatch.setattr(DependencyRegistryClient, "fetch", fake_fetch)
    context = CheckContext(AuditSettings(input_path=project, output_path=workspace_tmp_path / "out", allow_network=True))

    findings = RightsAndOriginalityChecker().check(unit, [], context)

    license_findings = [finding for finding in findings if finding.extra["kind"] == "dependency_license"]
    assert len(license_findings) == 1
    assert license_findings[0].criterion == Criterion.RIGHTS
    assert license_findings[0].severity == Severity.CRITICAL
    assert license_findings[0].verdict == Verdict.FAIL
    assert license_findings[0].source == "GPL-3.0-only"
    assert license_findings[0].evidence[0].url == "https://registry.npmjs.org/copyleft-lib"


def test_image_quality_checker_ignores_decorative_small_icons(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("![icon](icon.png)\n", encoding="utf-8")
    (project / "icon.png").write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (32).to_bytes(4, "big")
        + (32).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)

    findings = ImageQualityChecker().check(unit, entities, CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_tech_freshness_checker_uses_sources_and_cache(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("Use Alpine 3.20 for the build image.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    fake_client = _FakeJsonClient(
        {
            "verdict": "warning",
            "severity": "minor",
            "confidence": 0.8,
            "support_status": "устарело",
            "latest_version": "3.22",
            "recommended_version": "3.22",
            "evidence": "Alpine 3.20 уже не последняя стабильная ветка.",
            "sources": [{"title": "Alpine releases", "url": "https://alpinelinux.org/releases/"}],
            "recommendation": "Проверить образ и обновить версию в материалах.",
        }
    )
    cache = AuditCache.load(workspace_tmp_path / "cache.json")
    context = CheckContext(_settings(workspace_tmp_path, project), tech_model_client=fake_client, cache=cache)

    first = TechFreshnessChecker().check(unit, entities, context)
    second = TechFreshnessChecker().check(unit, entities, context)

    assert fake_client.calls == 1
    assert (workspace_tmp_path / "cache.json").exists()
    assert first[0].support_status == "устарело"
    assert first[0].latest_version == "3.22"
    assert first[0].recommended_version == "3.22"
    assert first[0].source == "https://alpinelinux.org/releases/"
    assert first[0].prompt_version == "tech_freshness_checker:v1"
    assert second[0].extra["cache_hit"] is True
    assert context.model_usage["calls_total"] == 1
    assert context.model_usage["cache_hits"] == 1
    assert context.model_usage["total_tokens"] == 15


def test_fact_checker_perplexity_uses_sources_and_cache(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Python 3.10 supports structural pattern matching since the 2021 release.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    fake_client = _FakeJsonClient(
        {
            "verdict": "pass",
            "confidence": 0.9,
            "evidence": "Утверждение подтверждается документацией Python.",
            "sources": [{"title": "Python docs", "url": "https://docs.python.org/3/whatsnew/3.10.html"}],
            "recommendation": "Действий не требуется.",
        }
    )
    cache = AuditCache.load(workspace_tmp_path / "fact_cache.json")
    context = CheckContext(_settings(workspace_tmp_path, project), fact_model_client=fake_client, cache=cache)

    first = FactCheckerPerplexity().check(unit, [], context)
    second = FactCheckerPerplexity().check(unit, [], context)

    assert fake_client.calls == 1
    assert first[0].verdict == Verdict.PASS
    assert first[0].source == "https://docs.python.org/3/whatsnew/3.10.html"
    assert first[0].prompt_version == "fact_checker_perplexity:v1"
    assert second[0].extra["cache_hit"] is True


def test_fact_checker_skips_navigation_and_course_requirements(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "- [Python 3.10 supports structural pattern matching since the 2021 release](#python-310)\n"
        "Read more [here](https://docs.python.org/3/whatsnew/3.10.html).\n"
        "Python scripts should be placed in src according to the project rules.\n"
        "The program is built with Docker and Bash scripts in src.\n"
        "Before starting, clone the project repository.\n"
        "To store data, define a structure in src.\n"
        "We recommend installing the latest Docker version.\n"
        "Your code should follow Google style.\n"
        "| Python 3.10 | should not be checked from a table |\n"
        "Python 3.10 supports structural pattern matching since the 2021 release.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)
    fake_client = _FakeJsonClient(
        {
            "verdict": "pass",
            "confidence": 0.9,
            "evidence": "Утверждение подтверждается документацией Python.",
            "sources": [{"title": "Python docs", "url": "https://docs.python.org/3/whatsnew/3.10.html"}],
            "recommendation": "Действий не требуется.",
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), fact_model_client=fake_client)

    findings = FactCheckerPerplexity().check(unit, [], context)

    assert fake_client.calls == 1
    assert len(findings) == 1
    assert "structural pattern matching" in findings[0].quote
    assert "program is built" not in fake_client.user_prompt
    assert "Read more" not in fake_client.user_prompt
    assert "clone the project" not in fake_client.user_prompt
    assert "define a structure" not in fake_client.user_prompt
    assert "recommend installing" not in fake_client.user_prompt
    assert "Your code" not in fake_client.user_prompt
    assert "should not be checked" not in fake_client.user_prompt


def test_fact_checker_keeps_fact_lines_that_start_with_technical_words(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Use of structural pattern matching was added in Python 3.10.\n"
        "Run-time errors are checked by the Python runtime.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)
    fake_client = _FakeJsonClient(
        {
            "verdict": "pass",
            "confidence": 0.9,
            "evidence": "Утверждение подтверждается документацией Go.",
            "sources": [{"title": "Go docs", "url": "https://go.dev/doc/"}],
            "recommendation": "Действий не требуется.",
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), fact_model_client=fake_client)

    findings = FactCheckerPerplexity().check(unit, [], context)
    prompt_text = "\n".join(fake_client.user_prompts)

    assert fake_client.calls == 2
    assert len(findings) == 2
    assert "Use of structural pattern matching was added in Python 3.10" in prompt_text
    assert "Run-time errors are checked by the Python runtime" in prompt_text


def test_readme_fact_actuality_checker_only_reads_main_and_russian_readme(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Python 3.10 was released in October 2021 and introduced structural pattern matching.\n",
        encoding="utf-8",
    )
    (project / "README_RUS.md").write_text(
        "Python 3.10 поддерживает структурное сопоставление pattern matching с релиза 2021 года.\n",
        encoding="utf-8",
    )
    (project / "README_UZB.md").write_text("Bu fayl maxsus fakt tekshiruviga kirmaydi.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)
    fake_client = _FakeJsonClient(
        {
            "findings": [
                {
                    "claim": "Python 3.10 поддерживает структурное сопоставление pattern matching с релиза 2021 года.",
                    "criterion": "actuality",
                    "verdict": "warning",
                    "severity": "minor",
                    "confidence": 0.82,
                    "file_path": "README_RUS.md",
                    "line_start": 1,
                    "evidence": "Утверждение требует уточнения по версии.",
                    "sources": [{"title": "Python docs", "url": "https://docs.python.org/3/whatsnew/3.10.html"}],
                    "support_status": "поддерживается",
                    "latest_version": "3.14",
                    "recommended_version": "3.14",
                    "recommendation": "Уточнить актуальную версию Python.",
                }
            ]
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), fact_model_client=fake_client)

    findings = ReadmeFactActualityChecker().check(unit, [], context)

    assert fake_client.calls == 2
    assert "README.md" in fake_client.user_prompts[0]
    assert "README_RUS.md" in fake_client.user_prompts[1]
    assert all("README_UZB.md" not in prompt for prompt in fake_client.user_prompts)
    assert findings[0].criterion == Criterion.FACTS
    assert findings[0].source == "https://docs.python.org/3/whatsnew/3.10.html"
    assert findings[0].latest_version == "3.14"


def test_readme_fact_actuality_checker_skips_exercise_options_and_task_requirements(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "## Chapter III\n"
        "REST is an architectural style for distributed systems.\n"
        "Use of structural pattern matching was added in Python 3.10.\n"
        "Run-time errors are checked by the Python runtime.\n"
        "Read more [here](https://go.dev/doc/).\n"
        "The program is built with Docker and Bash scripts in src.\n"
        "Before starting, clone the project repository.\n"
        "To store data, define a structure in src.\n"
        "We recommend installing the latest Docker version.\n"
        "Your code should follow Google style.\n"
        "## Chapter V\n"
        "### Exercise 00 — Terminology\n"
        "1) The ability of a system to increase performance without adding resources.\n"
        "The system should notify clients through Telegram and SMS.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=2000)
    fake_client = _FakeJsonClient({"findings": []})
    context = CheckContext(_settings(workspace_tmp_path, project), fact_model_client=fake_client)

    findings = ReadmeFactActualityChecker().check(unit, [], context)

    assert findings == []
    assert fake_client.calls == 1
    assert "REST is an architectural style" in fake_client.user_prompt
    assert "Use of structural pattern matching was added in Python 3.10" in fake_client.user_prompt
    assert "Run-time errors are checked by the Python runtime" in fake_client.user_prompt
    assert "program is built" not in fake_client.user_prompt
    assert "Read more" not in fake_client.user_prompt
    assert "clone the project" not in fake_client.user_prompt
    assert "define a structure" not in fake_client.user_prompt
    assert "recommend installing" not in fake_client.user_prompt
    assert "Your code" not in fake_client.user_prompt
    assert "without adding resources" not in fake_client.user_prompt
    assert "notify clients" not in fake_client.user_prompt


def test_full_model_audit_includes_readme_fact_checker() -> None:
    checker_names = [checker.name for checker in default_checkers(use_model=True)]

    assert "readme_fact_actuality_checker" in checker_names
    assert "fact_checker_perplexity" in checker_names
    assert "curriculum_relevance_checker" in checker_names
    assert checker_names.index("curriculum_relevance_checker") < checker_names.index("model_rubric_checker")


def test_curriculum_relevance_checker_flags_cpp_style_for_java_without_model(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    src = project / "src"
    src.mkdir()
    (src / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    (project / "README.md").write_text(
        "This Java project should follow the Google C++ Style Guide.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = CurriculumRelevanceChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].criterion == Criterion.CORRECTNESS
    assert findings[0].severity == Severity.MAJOR
    assert findings[0].verdict == Verdict.WARNING
    assert findings[0].extra["issue_type"] == "language_material_conflict"


def test_curriculum_relevance_checker_flags_makefile_for_java_without_model(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    src = project / "src"
    src.mkdir()
    (src / "Main.java").write_text("class Main {}\n", encoding="utf-8")
    (project / "README.md").write_text(
        "The program must be built with Makefile target run.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = CurriculumRelevanceChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].criterion == Criterion.CORRECTNESS
    assert findings[0].severity == Severity.MINOR
    assert findings[0].extra["issue_type"] == "language_tooling_conflict"


def test_curriculum_relevance_checker_flags_cpp_style_for_c_without_model(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    src = project / "src"
    src.mkdir()
    (src / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    (project / "README.md").write_text(
        "Follow the Google C++ Style Guide for formatting rules.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = CurriculumRelevanceChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].criterion == Criterion.CORRECTNESS
    assert findings[0].severity == Severity.MINOR
    assert findings[0].extra["issue_type"] == "language_material_conflict"


def test_curriculum_relevance_checker_drops_model_tool_mentions_without_conflict(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("Follow the Google C++ Style Guide.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    fake_client = _FakeJsonClient(
        {
            "findings": [
                {
                    "criterion": "correctness",
                    "issue_type": "language_material_conflict",
                    "severity": "minor",
                    "verdict": "warning",
                    "confidence": 0.9,
                    "quote": "Follow the Google C++ Style Guide.",
                    "file_path": "README.md",
                    "line_start": 1,
                    "evidence": "Упомянут Google C++ Style Guide.",
                    "recommendation": "Проверить стиль.",
                }
            ]
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client)

    findings = CurriculumRelevanceChecker().check(unit, [], context)

    assert fake_client.calls == 1
    assert findings == []


def test_curriculum_relevance_checker_uses_model_for_missing_key_topic(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Implement a tokenizer and parser for the expression language.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    fake_client = _FakeJsonClient(
        {
            "findings": [
                {
                    "criterion": "correctness",
                    "issue_type": "missing_key_topic",
                    "severity": "minor",
                    "verdict": "warning",
                    "confidence": 0.82,
                    "quote": "Implement a tokenizer and parser",
                    "file_path": "README.md",
                    "line_start": 1,
                    "evidence": "Для задания про разбор выражений не раскрыта тема finite state machine или эквивалентный способ проектирования состояний.",
                    "recommendation": "Добавить методическое пояснение про конечный автомат или явно указать другой ожидаемый подход.",
                }
            ]
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client)

    findings = CurriculumRelevanceChecker().check(unit, [], context)

    assert fake_client.calls == 1
    assert "finite state machine" in fake_client.user_prompt
    assert len(findings) == 1
    assert findings[0].checker_name == "curriculum_relevance_checker"
    assert findings[0].prompt_version == "curriculum_relevance_checker:v1"
    assert findings[0].extra["issue_type"] == "missing_key_topic"


def test_curriculum_relevance_checker_drops_low_confidence_model_methodology(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Implement a tokenizer and parser for the expression language.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    fake_client = _FakeJsonClient(
        {
            "findings": [
                {
                    "criterion": "correctness",
                    "issue_type": "missing_key_topic",
                    "severity": "minor",
                    "verdict": "warning",
                    "confidence": 0.6,
                    "quote": "Implement a tokenizer and parser",
                    "file_path": "README.md",
                    "line_start": 1,
                    "evidence": "Слабая гипотеза без достаточной уверенности.",
                    "recommendation": "Не должно попасть в отчёт.",
                }
            ]
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client)

    findings = CurriculumRelevanceChecker().check(unit, [], context)

    assert fake_client.calls == 1
    assert findings == []


def test_model_rubric_checker_only_keeps_workload_findings(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    fake_client = _FakeJsonClient(
        {
            "findings": [
                {
                    "criterion": "checklist_alignment",
                    "severity": "critical",
                    "verdict": "fail",
                    "confidence": 0.9,
                    "quote": "Проверьте, что ни одно вредоносное ПО не использовалось.",
                    "file_path": "check-list.yml",
                    "line_start": 13,
                    "evidence": "Ложный дубль специализированной проверки.",
                    "recommendation": "Не должно попасть в отчёт.",
                },
                {
                    "criterion": "workload",
                    "severity": "info",
                    "verdict": "unknown",
                    "confidence": 0.5,
                    "evidence": "Нет данных о реальном времени прохождения.",
                    "recommendation": "Собрать данные платформы о трудозатратах.",
                },
            ]
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client)

    findings = ModelRubricChecker().check(unit, [], context)

    assert findings == []


def test_model_rubric_checker_keeps_concrete_workload_findings(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("# Проект\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    fake_client = _FakeJsonClient(
        {
            "findings": [
                {
                    "criterion": "workload",
                    "severity": "minor",
                    "verdict": "warning",
                    "confidence": 0.85,
                    "quote": "Выполните 25 больших заданий за один день.",
                    "file_path": "README.md",
                    "line_start": 1,
                    "evidence": "Трудоёмкость выглядит завышенной для одного учебного дня.",
                    "recommendation": "Разбить задания на несколько этапов или дать ориентир по времени.",
                }
            ]
        }
    )
    context = CheckContext(_settings(workspace_tmp_path, project), model_client=fake_client)

    findings = ModelRubricChecker().check(unit, [], context)

    assert len(findings) == 1
    assert findings[0].criterion == Criterion.WORKLOAD
    assert findings[0].location.file_path == "README.md"


def test_regional_availability_checker_uses_curated_ru_rules(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "The task uses https://blocked.example/api and the ExampleCloud SDK.\n",
        encoding="utf-8",
    )
    (project / "requirements.txt").write_text("examplecloud==1.0.0\n", encoding="utf-8")
    (project / "regional_availability_ru.yml").write_text(
        "rules:\n"
        "  - pattern: blocked.example\n"
        "    target: service\n"
        "    status: unavailable\n"
        "    reason: Сервис недоступен из РФ по кураторской базе.\n"
        "    source: https://kb.example/blocked\n"
        "    updated_at: 2026-06-01\n"
        "  - pattern: examplecloud\n"
        "    target: package\n"
        "    status: limited\n"
        "    reason: SDK требует проверки доступности из РФ.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=5000)
    entities = extract_entities(unit)
    context = CheckContext(_settings(workspace_tmp_path, project))

    findings = RegionalAvailabilityChecker().check(unit, entities, context)

    assert {finding.support_status for finding in findings} == {"недоступно в РФ", "ограничено в РФ"}
    assert all(finding.criterion == Criterion.TECHNOLOGY_FRESHNESS for finding in findings)
    assert any(finding.severity == Severity.MAJOR for finding in findings)
    assert any(finding.source == "https://kb.example/blocked" for finding in findings)


def test_markdown_structure_checker_flags_duplicate_heading_and_anchor(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "# Overview!\n"
        "Text.\n"
        "## Overview\n"
        "More text.\n"
        "## Overview\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = MarkdownStructureChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    issue_types = [finding.extra["issue_type"] for finding in findings]
    assert "duplicate_anchor" in issue_types
    assert "duplicate_heading" in issue_types
    assert all(finding.criterion == Criterion.READABILITY for finding in findings)


def test_markdown_structure_checker_flags_chapter_gap(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "# Chapter I\n"
        "Intro.\n"
        "# Chapter III\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = MarkdownStructureChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].extra["issue_type"] == "chapter_sequence"
    assert "Chapter II" in findings[0].evidence[0].detail


def test_markdown_structure_checker_flags_repeated_manual_numbers(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "1) Первый пункт\n"
        "1) Второй пункт\n"
        "1) Третий пункт\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = MarkdownStructureChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].extra["issue_type"] == "repeated_numbered_list_items"
    assert findings[0].location.line_start == 2


def test_markdown_structure_checker_flags_numbering_reset_inside_block(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "1) Первый пункт\n"
        "2) Второй пункт\n"
        "3) Третий пункт\n"
        "1) Четвёртый пункт\n"
        "2) Пятый пункт\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = MarkdownStructureChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].extra["issue_type"] == "numbered_list_reset"
    assert findings[0].location.line_start == 4


def test_full_audit_runs_markdown_structure_after_broken_url_syntax() -> None:
    checker_names = [checker.name for checker in default_checkers(use_model=False)]

    assert checker_names.index("broken_url_syntax_checker") < checker_names.index("markdown_structure_checker")


def test_label_punctuation_checker_flags_missing_colon_before_next_value(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Input operation \n"
        "> `+`\n"
        "\n"
        "Input right operand\n"
        "> `15`\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LabelPunctuationChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert [finding.quote for finding in findings] == ["Input operation", "Input right operand"]
    assert all(finding.criterion == Criterion.READABILITY for finding in findings)
    assert all(finding.extra["issue_type"] == "missing_label_colon" for finding in findings)
    assert all(finding.needs_human_review is False for finding in findings)


def test_label_punctuation_checker_flags_inline_value_and_ignores_colon(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Output 42\n"
        "Example `Save \\n Ivanov`\n"
        "Result: ok\n"
        "Output: `42`\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LabelPunctuationChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert [finding.quote for finding in findings] == ["Output 42", "Example `Save \\n Ivanov`"]


def test_label_punctuation_checker_does_not_flag_prose_or_yaml(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Output should be printed to stdout.\n"
        "Example of valid input is shown below.\n",
        encoding="utf-8",
    )
    (project / "check-list.yml").write_text(
        "sections:\n"
        "  - questions:\n"
        "      - name: Example\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LabelPunctuationChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert findings == []


def test_full_audit_runs_label_punctuation_after_markdown_structure() -> None:
    checker_names = [checker.name for checker in default_checkers(use_model=False)]

    assert checker_names.index("markdown_structure_checker") < checker_names.index("label_punctuation_checker")


def test_broken_url_syntax_checker_flags_backslash_and_missing_slash(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README_RUS.md").write_text(
        "5 - Сломанная ссылка: https:/\\new.oprosso.net и http:/example.com.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = BrokenUrlSyntaxChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert [finding.quote for finding in findings] == ["https:/\\new.oprosso.net", "http:/example.com"]
    assert all(finding.criterion == Criterion.LINKS for finding in findings)
    assert all(finding.severity == Severity.MAJOR for finding in findings)
    assert all(finding.verdict == Verdict.FAIL for finding in findings)
    assert all(finding.needs_human_review is False for finding in findings)


def test_broken_url_syntax_checker_flags_missing_colon_and_spaces(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text(
        "Broken links: http//example.com, https://new oprosso.net/path, https://example. com/docs.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = BrokenUrlSyntaxChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert {finding.quote for finding in findings} == {
        "http//example.com",
        "https://new oprosso.net/path",
        "https://example. com/docs",
    }


def test_full_audit_runs_broken_url_syntax_before_network_link_checker() -> None:
    checker_names = [checker.name for checker in default_checkers(use_model=False)]

    assert checker_names.index("broken_url_syntax_checker") < checker_names.index("link_checker")


def test_full_audit_runs_new_rules_in_priority_order() -> None:
    checker_names = [checker.name for checker in default_checkers(use_model=False)]
    priority = [
        "broken_url_syntax_checker",
        "markdown_structure_checker",
        "label_punctuation_checker",
        "spelling_wording_checker",
        "local_consistency_checker",
        "checklist_checker",
        "resource_availability_checker",
    ]

    positions = [checker_names.index(checker_name) for checker_name in priority]

    assert positions == sorted(positions)


def test_spelling_checker_scans_drawio_artifacts_for_rule_typos(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("Diagram is attached.\n", encoding="utf-8")
    (project / "network.drawio").write_text(
        '<mxCell value="COMANY\\A.Sidorova" />\n'
        '<mxCell value="COMPANY\\O.Krivov" />\n',
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = SpellingAndWordingChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].quote == "COMANY"
    assert findings[0].location.file_path == "network.drawio"


def test_spelling_checker_flags_asp_net_in_java_project_as_wording_issue(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "AP1_Jv_T04B"
    project.mkdir()
    (project / "README.md").write_text("Recommended materials:\n- ASP.NET.\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = SpellingAndWordingChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].criterion == Criterion.READABILITY
    assert findings[0].quote == "ASP.NET"
    assert findings[0].extra["issue_type"] == "wording"


def test_resource_checker_flags_vm_guide_without_environment_image(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    materials = project / "materials"
    materials.mkdir(parents=True)
    (project / "README.md").write_text("Set up the database environment.\n", encoding="utf-8")
    (materials / "Инструкция по настройке VBox.pdf").write_bytes(b"%PDF-1.4")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = ResourceAvailabilityChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].extra["issue_type"] == "environment_guide_without_image"
    assert findings[0].criterion == Criterion.CORRECTNESS


def test_local_consistency_checker_flags_function_length_range_conflict(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    materials = project / "materials"
    materials.mkdir(parents=True)
    (project / "README.md").write_text(
        "Functions must be compact and take no more than 20-30 lines.\n",
        encoding="utf-8",
    )
    (materials / "principles.md").write_text(
        "Функции должны занимать 40-50 строк кода.\n",
        encoding="utf-8",
    )
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = LocalConsistencyChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    assert len(findings) == 1
    assert findings[0].extra["issue_type"] == "function_length_range_conflict"


def test_curriculum_checker_flags_c_style_and_missing_preprocessor_topic(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "c_intro"
    src = project / "src"
    src.mkdir(parents=True)
    (project / "README.md").write_text(
        "Quest 1. Write C code with GCC and Google C++ Style Guide.\n"
        "Quest 2. Work with int, char and float data types.\n"
        "Quest 3. Continue exercises.\n",
        encoding="utf-8",
    )
    (src / "main.c").write_text("#include <stdio.h>\n#define NMAX 10\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)

    findings = CurriculumRelevanceChecker().check(unit, [], CheckContext(_settings(workspace_tmp_path, project)))

    issue_types = {finding.extra["issue_type"] for finding in findings}
    assert "language_material_conflict" in issue_types
    assert "missing_key_topic" in issue_types


def test_link_checker_blocks_private_ip_before_network(workspace_tmp_path: Path) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("[internal](http://127.0.0.1:9999/secret)\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    settings = _settings(workspace_tmp_path, project).model_copy(update={"allow_network": True})

    findings = LinkChecker().check(unit, entities, CheckContext(settings))

    assert findings[0].verdict == Verdict.UNKNOWN
    assert "Локальные адреса" in findings[0].evidence[0].detail or "Внутренние IP" in findings[0].evidence[0].detail


def test_link_checker_treats_transient_status_as_recheck(workspace_tmp_path: Path, monkeypatch) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("[slow](https://example.com/slow)\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    settings = _settings(workspace_tmp_path, project).model_copy(update={"allow_network": True})
    monkeypatch.setattr(checks_module, "_check_url", lambda *_args: (503, "https://example.com/slow", None))

    findings = LinkChecker().check(unit, entities, CheckContext(settings))

    assert findings[0].severity == Severity.INFO
    assert findings[0].verdict == Verdict.UNKNOWN
    assert "Повторить проверку позже" in findings[0].recommendation


def test_link_checker_does_not_make_first_404_critical(workspace_tmp_path: Path, monkeypatch) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("[missing](https://example.com/missing)\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    settings = _settings(workspace_tmp_path, project).model_copy(update={"allow_network": True})
    monkeypatch.setattr(checks_module, "_check_url", lambda *_args: (404, "https://example.com/missing", None))

    findings = LinkChecker().check(unit, entities, CheckContext(settings))

    assert findings[0].severity == Severity.MAJOR
    assert findings[0].verdict == Verdict.FAIL


def test_link_checker_accepts_oprosso_short_link_redirect(workspace_tmp_path: Path, monkeypatch) -> None:
    project = workspace_tmp_path / "unit"
    project.mkdir()
    (project / "README.md").write_text("[survey](http://opros.so/kAnXy)\n", encoding="utf-8")
    unit = load_unit_files(discover_content_units(project)[0], max_file_bytes=1000)
    entities = extract_entities(unit)
    settings = _settings(workspace_tmp_path, project).model_copy(update={"allow_network": True})
    monkeypatch.setattr(
        checks_module,
        "_check_url",
        lambda *_args: (200, "https://oprosso.ru/p/4cb31ec3f47a4596bc758ea1861fb624", None),
    )

    findings = LinkChecker().check(unit, entities, CheckContext(settings))

    assert findings == []
