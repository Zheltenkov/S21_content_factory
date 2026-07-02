import json
import shutil
import uuid
from pathlib import Path

from content_gen.evaluation import EvaluationHarness, load_generated_outputs, load_golden_dataset
from content_gen.evaluation.metrics import extract_tool_mentions
from content_gen.evaluation.models import (
    EvalThresholds,
    GeneratedProjectOutput,
    GoldenDataset,
    GoldenProjectCase,
    GoldenProjectExpectations,
)
from content_gen.models.criteria_models import CheckMethod, CriteriaItem, CriteriaReport


def _item(item_id: str, score: int = 1) -> CriteriaItem:
    return CriteriaItem(
        id=item_id,
        title=item_id,
        description=item_id,
        check_method=CheckMethod.SCRIPT,
        score=score,
        comments=[] if score else ["failed"],
    )


def _report(*items: CriteriaItem) -> CriteriaReport:
    criteria = list(items)
    return CriteriaReport(
        items=criteria,
        total=sum(item.score for item in criteria),
        max_score=len(criteria),
        summary={},
    )


def _readme(extra: str = "") -> str:
    return (
        "# План рисков\n\n"
        "Ты собираешь проектный план рисков для команды.\n\n"
        "## Содержание\n\n"
        "- [Глава 1](#глава-1)\n"
        "- [Глава 2](#глава-2)\n"
        "- [Глава 3](#глава-3)\n\n"
        "## Глава 1. Введение и инструкция\n\n"
        "Рабочий контекст проекта связан с рисками команды.\n\n"
        "## Глава 2. Теоретический блок\n\n"
        "Команда использует Miro и Google Sheets для анализа рисков.\n\n"
        "## Глава 3. Практический блок\n\n"
        "### Задание 1. Карта рисков\n\n"
        "**Что нужно сделать:**\n"
        "Ситуация: команда готовит карту рисков.\n"
        "Цель: собрать карту рисков.\n"
        "Подход: выпиши риск, причину и действие.\n\n"
        "**Что должно получиться:**\n"
        "- Таблица рисков готова.\n"
        "- Указан владелец риска.\n"
        "- Есть действие команды.\n\n"
        "**Формат сдачи:** файл `Project/part-03/task-01/risks.md`.\n\n"
        "### Задание 2. План митигирования\n\n"
        "**Что нужно сделать:**\n"
        "Ситуация: команда выбирает реакции на риски.\n"
        "Цель: собрать план митигирования.\n"
        "Подход: зафиксируй реакцию и срок.\n\n"
        "**Что должно получиться:**\n"
        "- План готов.\n"
        "- Указаны сроки.\n"
        "- Есть ответственный.\n\n"
        "**Формат сдачи:** файл `Project/part-03/task-02/mitigation.md`.\n\n"
        f"{extra}"
    )


def _case(**overrides) -> GoldenProjectCase:
    expectations = GoldenProjectExpectations(
        required_task_count=2,
        required_task_titles=["Карта рисков", "План митигирования"],
        required_tools=["Miro", "Google Sheets"],
        allowed_tools=["Miro", "Google Sheets", "Git"],
        required_criteria_ids=["2.2.1", "2.5.1"],
        rubric_thresholds=EvalThresholds(
            min_score_ratio=0.75,
            min_structure_pass_rate=0.75,
            min_practice_atomicity=0.75,
            min_didactics_compliance=0.75,
            max_hallucinated_tools=0,
            max_fallback_count=1,
            max_fallback_policy_violations=0,
            max_retry_count=2,
        ),
    )
    data = {
        "id": "pjm-risk",
        "title": "План рисков",
        "seed": {"language": "ru", "learning_outcomes": ["Оценивать риски"]},
        "expectations": expectations,
    }
    data.update(overrides)
    return GoldenProjectCase.model_validate(data)


def test_evaluation_harness_passes_matching_output() -> None:
    case = _case()
    output = GeneratedProjectOutput(
        case_id=case.id,
        markdown=_readme(),
        model="gpt-test",
        rubric_report=_report(
            _item("1.1"),
            _item("2.1.1"),
            _item("2.2.1"),
            _item("2.3.1"),
            _item("2.4.1"),
            _item("2.5.1"),
            _item("3.1"),
            _item("4.1"),
        ),
        fallback_count=1,
        retry_count=1,
    )

    result = EvaluationHarness().evaluate_case(case, output)

    assert result.passed is True
    assert result.model_name == "gpt-test"
    assert result.metrics.task_count == 2
    assert result.metrics.hallucinated_tools == []
    assert result.metrics.practice_atomicity == 1.0


def test_evaluation_harness_fails_hallucinated_tools_and_thresholds() -> None:
    case = _case()
    output = GeneratedProjectOutput(
        case_id=case.id,
        markdown=_readme(extra="Для реализации используй Docker и Python."),
        rubric_report=_report(
            _item("1.1"),
            _item("2.1.1"),
            _item("2.2.1"),
            _item("2.3.1"),
            _item("2.5.1"),
            _item("3.1", score=0),
            _item("4.1"),
        ),
        fallback_count=3,
    )

    result = EvaluationHarness().evaluate_case(case, output)

    assert result.passed is False
    assert "Docker" in result.metrics.hallucinated_tools
    assert "Python" in result.metrics.hallucinated_tools
    assert any("hallucinated_tools" in reason for reason in result.reasons)
    assert any("fallback_count" in reason for reason in result.reasons)


def test_evaluation_harness_fails_invalid_fallback_policy_event() -> None:
    case = _case()
    output = GeneratedProjectOutput(
        case_id=case.id,
        markdown=_readme(),
        rubric_report=_report(_item("1.1"), _item("2.2.1"), _item("2.5.1"), _item("3.1")),
        fallback_traces=[{"node": "practice", "fallback_type": "legacy_short_event"}],
    )

    result = EvaluationHarness().evaluate_case(case, output)

    assert result.passed is False
    assert any("fallback_policy_violations" in reason for reason in result.reasons)
    assert any("missing trace_id" in item for item in result.metrics.fallback_policy_violations)


def test_harness_uses_scorer_when_output_has_no_rubric_report() -> None:
    class FakeScorer:
        def score_document(self, _document, learning_outcomes=None, use_cache=False):
            self.learning_outcomes = learning_outcomes
            return _report(_item("1.1"), _item("2.2.1"), _item("2.5.1"), _item("3.1"))

    fake = FakeScorer()
    case = _case()
    output = GeneratedProjectOutput(case_id=case.id, markdown=_readme())

    result = EvaluationHarness(scorer_factory=lambda _language: fake).evaluate_case(case, output)

    assert result.error is None
    assert fake.learning_outcomes == ["Оценивать риски"]


def test_dataset_default_thresholds_apply_when_case_does_not_override() -> None:
    dataset = GoldenDataset(
        name="thresholds",
        version="v1",
        defaults=EvalThresholds(min_score_ratio=0.9),
        cases=[
            GoldenProjectCase(
                id="pjm-risk",
                title="План рисков",
                expectations=GoldenProjectExpectations(required_task_count=2),
            )
        ],
    )
    output = GeneratedProjectOutput(
        case_id="pjm-risk",
        markdown=_readme(),
        rubric_report=_report(_item("1.1"), _item("2.5.1", score=0)),
    )

    summary = EvaluationHarness().evaluate_dataset(dataset, {"pjm-risk": output})

    assert summary.pass_rate == 0.0
    assert any("score_ratio" in reason for reason in summary.results[0].reasons)


def test_dataset_loaders_resolve_yaml_and_markdown_path() -> None:
    temp_dir = Path(".pytest_eval_tmp") / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        dataset_path = temp_dir / "golden.yaml"
        dataset_path.write_text(
            """
name: test-dataset
version: v1
cases:
  - id: pjm-risk
    title: План рисков
    expectations:
      required_task_count: 2
""",
            encoding="utf-8",
        )
        markdown_path = temp_dir / "readme.md"
        markdown_path.write_text(_readme(), encoding="utf-8")
        outputs_path = temp_dir / "outputs.json"
        outputs_path.write_text(
            json.dumps({"outputs": [{"case_id": "pjm-risk", "markdown_path": "readme.md"}]}, ensure_ascii=False),
            encoding="utf-8",
        )

        dataset = load_golden_dataset(dataset_path)
        outputs = load_generated_outputs(outputs_path)

        assert isinstance(dataset, GoldenDataset)
        assert dataset.cases[0].id == "pjm-risk"
        assert outputs["pjm-risk"].markdown.startswith("# План рисков")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_extract_tool_mentions_uses_word_boundaries() -> None:
    mentions = extract_tool_mentions(
        "SQL нужен, но NoSQLTool не должен считаться SQL. Также есть Miro.",
        vocabulary={"SQL", "Miro"},
    )

    assert mentions == ["Miro", "SQL"]
