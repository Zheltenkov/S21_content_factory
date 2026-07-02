import json
import shutil
import uuid
from pathlib import Path

from content_gen.evaluation import (
    RegenerationEvalCase,
    RegenerationEvalDataset,
    RegenerationEvalOutput,
    RegenerationEvalThresholds,
    RegenerationEvaluationHarness,
    load_regeneration_eval_dataset,
    load_regeneration_eval_outputs,
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


def _readme(section_21: str = "Старый пример про переговоры.", section_22: str = "Соседний раздел стабилен.") -> str:
    return f"""# Эффективные переговоры

Аннотация проекта.

## Глава 1. Введение и инструкция

Рабочий контекст не должен меняться при точечной правке.

## Глава 2. Теоретический блок

### 2.1. Как выбрать стратегию разговора

{section_21}

### 2.2. Как удержать границу

{section_22}

## Глава 3. Практический блок

### Задание 1. Снять картину переговоров

Собери факты и ограничения.
"""


def _validation_report() -> dict:
    return {
        "selected_sections": [
            {
                "title": "2.1. Как выбрать стратегию разговора",
                "start_line": 9,
                "end_line": 10,
            }
        ],
        "issues": [],
        "failed_patch_count": 0,
    }


def _case(**overrides) -> RegenerationEvalCase:
    data = {
        "id": "regen-section-21",
        "title": "Правка раздела 2.1",
        "original_markdown": _readme(),
        "comments": "Исправь только раздел 2.1.",
        "selected_section_titles": ["2.1. Как выбрать стратегию разговора"],
        "protected_section_titles": ["2.2. Как удержать границу"],
        "original_rubric_report": _report(_item("2.2.1"), _item("2.5.1"), _item("3.1")),
        "thresholds": RegenerationEvalThresholds(require_rubric_comparison=True),
    }
    data.update(overrides)
    return RegenerationEvalCase.model_validate(data)


def test_regeneration_eval_passes_when_only_selected_section_changes() -> None:
    case = _case()
    output = RegenerationEvalOutput(
        case_id=case.id,
        regenerated_markdown=_readme(section_21="Новый пример про переговоры с заказчиком."),
        validation_report=_validation_report(),
        regenerated_rubric_report=_report(_item("2.2.1"), _item("2.5.1"), _item("3.1")),
    )

    result = RegenerationEvaluationHarness().evaluate_case(case, output)

    assert result.passed is True
    assert result.metrics.selected_changed_section_titles == ["2.1. Как выбрать стратегию разговора"]
    assert result.metrics.unscoped_changed_section_titles == []
    assert result.metrics.rubric_total_delta == 0


def test_regeneration_eval_fails_when_neighbour_section_changes() -> None:
    case = _case()
    output = RegenerationEvalOutput(
        case_id=case.id,
        regenerated_markdown=_readme(
            section_21="Новый пример про переговоры с заказчиком.",
            section_22="Соседний раздел случайно переписан.",
        ),
        validation_report=_validation_report(),
        regenerated_rubric_report=_report(_item("2.2.1"), _item("2.5.1"), _item("3.1")),
    )

    result = RegenerationEvaluationHarness().evaluate_case(case, output)

    assert result.passed is False
    assert result.metrics.protected_changed_section_titles == ["2.2. Как удержать границу"]
    assert any("unscoped_changed_sections" in reason for reason in result.reasons)
    assert any("protected sections changed" in reason for reason in result.reasons)


def test_regeneration_eval_fails_on_outline_and_rubric_regression() -> None:
    case = _case()
    regenerated = _readme(section_21="Новый пример про переговоры с заказчиком.").replace(
        "### 2.2. Как удержать границу",
        "### 2.2. Как удержать границу и конфликт",
    )
    output = RegenerationEvalOutput(
        case_id=case.id,
        regenerated_markdown=regenerated,
        validation_report={**_validation_report(), "failed_patch_count": 1},
        regenerated_rubric_report=_report(_item("2.2.1"), _item("2.5.1", score=0), _item("3.1")),
    )

    result = RegenerationEvaluationHarness().evaluate_case(case, output)

    assert result.passed is False
    assert result.metrics.outline_changed is True
    assert result.metrics.failed_patch_count == 1
    assert result.metrics.rubric_total_delta == -1
    assert result.metrics.new_failed_criteria == ["2.5.1"]
    assert any("heading outline changed" in reason for reason in result.reasons)
    assert any("rubric_total_delta" in reason for reason in result.reasons)
    assert any("new_failed_criteria" in reason for reason in result.reasons)


def test_regeneration_eval_dataset_summary_tracks_missing_outputs() -> None:
    dataset = RegenerationEvalDataset(cases=[_case(), _case(id="missing", title="Нет результата")])
    output = RegenerationEvalOutput(
        case_id="regen-section-21",
        regenerated_markdown=_readme(section_21="Новый пример про переговоры с заказчиком."),
        validation_report=_validation_report(),
        regenerated_rubric_report=_report(_item("2.2.1"), _item("2.5.1"), _item("3.1")),
    )

    summary = RegenerationEvaluationHarness().evaluate_dataset(dataset, {output.case_id: output})

    assert summary.total_cases == 2
    assert summary.passed_cases == 1
    assert summary.pass_rate == 0.5
    assert summary.results[1].reasons == ["missing regenerated output"]


def test_regeneration_eval_loaders_resolve_markdown_paths() -> None:
    temp_dir = Path(".pytest_eval_tmp") / uuid.uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=False)
    try:
        (temp_dir / "original.md").write_text(_readme(), encoding="utf-8")
        (temp_dir / "regenerated.md").write_text(
            _readme(section_21="Новый пример про переговоры с заказчиком."),
            encoding="utf-8",
        )
        dataset_path = temp_dir / "regeneration_cases.yaml"
        dataset_path.write_text(
            """
name: regeneration-smoke
version: v1
cases:
  - id: regen-section-21
    title: Правка раздела 2.1
    original_markdown_path: original.md
    selected_section_titles:
      - 2.1. Как выбрать стратегию разговора
""",
            encoding="utf-8",
        )
        outputs_path = temp_dir / "regeneration_outputs.json"
        outputs_path.write_text(
            json.dumps(
                {
                    "outputs": [
                        {
                            "case_id": "regen-section-21",
                            "regenerated_markdown_path": "regenerated.md",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        dataset = load_regeneration_eval_dataset(dataset_path)
        outputs = load_regeneration_eval_outputs(outputs_path)

        assert dataset.name == "regeneration-smoke"
        assert dataset.cases[0].original_markdown.startswith("# Эффективные переговоры")
        assert outputs["regen-section-21"].regenerated_markdown.startswith("# Эффективные переговоры")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_regeneration_eval_output_accepts_model_name_or_model_alias() -> None:
    by_name = RegenerationEvalOutput(
        case_id="case",
        regenerated_markdown="# README",
        model_name="gpt-test",
    )
    by_alias = RegenerationEvalOutput.model_validate(
        {
            "case_id": "case",
            "regenerated_markdown": "# README",
            "model": "gpt-alias",
        }
    )

    assert by_name.model_name == "gpt-test"
    assert by_alias.model_name == "gpt-alias"
