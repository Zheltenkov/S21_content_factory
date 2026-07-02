from content_gen.models.criteria_models import CheckMethod, CriteriaItem, CriteriaReport

from api.services import methodology_review_artifacts as artifacts


def _report() -> CriteriaReport:
    return CriteriaReport(
        items=[
            CriteriaItem(
                id="1.1",
                title="Структура",
                description="README содержит базовую структуру.",
                check_method=CheckMethod.SCRIPT,
                score=1,
            ),
            CriteriaItem(
                id="2.4.3",
                title="Объем теории",
                description="Теория соответствует ожидаемому объему.",
                check_method=CheckMethod.SCRIPT,
                score=0,
            ),
        ],
        total=1,
        max_score=2,
        summary={"1": 1, "2": 0},
    )


def test_refresh_checkpoint_artifact_recalculates_empty_evaluation_rubric(monkeypatch) -> None:
    calls = {}

    class FakeRubricScorer:
        def __init__(self, *, language, llm_client):
            calls["language"] = language
            calls["llm_client"] = llm_client

        def score_document(self, document, *, learning_outcomes=None, use_cache=False):
            calls["learning_outcomes"] = learning_outcomes
            calls["use_cache"] = use_cache
            return _report()

    monkeypatch.setattr(artifacts, "RubricScorer", FakeRubricScorer)
    context = {
        "seed": {"language": "ru", "learning_outcomes": ["Оценивать риски"]},
        "markdown": (
            "# Планирование рисков\n\n"
            "## Глава 1. Введение и инструкция\n\nТекст.\n\n"
            "## Глава 2. Теоретический блок\n\n### 2.1. Риск как рабочая рамка\n\nТекст.\n\n"
            "## Глава 3. Практический блок\n\n### Задание 1. Собрать карту рисков\n\nТекст.\n"
        ),
        "issues": ["Нужно уточнить теорию"],
        "rubric_json": {},
        "human_approval_checkpoint": {
            "id": "evaluation",
            "stage": "final",
            "node_id": "evaluation",
            "artifact": {"rubric": {"items": [{"id": "old", "score": 0}]}},
        },
    }

    artifacts.refresh_checkpoint_artifact(context)

    artifact = context["human_approval_checkpoint"]["artifact"]
    assert calls == {
        "language": "ru",
        "llm_client": None,
        "learning_outcomes": ["Оценивать риски"],
        "use_cache": False,
    }
    assert context["rubric_json"]["total"] == 1
    assert context["rubric_json"]["max_score"] == 2
    assert context["rubric_json"]["source_markdown_hash"]
    assert artifact["rubric"] == context["rubric_json"]
    assert artifact["rubric_score"] == "1 / 2"
    assert artifact["rubric_failed_count"] == 1
    assert artifact["issues_count"] == 1


def test_refresh_checkpoint_artifact_reuses_current_rubric_without_rescoring(monkeypatch) -> None:
    markdown = "# README\n\n## Глава 1. Введение и инструкция\n\nТекст.\n"
    current_hash = artifacts._rubric_markdown_hash(markdown)

    class FailingRubricScorer:
        def __init__(self, **_kwargs):
            raise AssertionError("rubric scorer must not be called for current rubric")

    monkeypatch.setattr(artifacts, "RubricScorer", FailingRubricScorer)
    context = {
        "markdown": markdown,
        "rubric_json": {
            "total": 2,
            "max_score": 2,
            "source_markdown_hash": current_hash,
            "items": [{"id": "1.1", "score": 1}, {"id": "1.2", "score": 1}],
        },
        "human_approval_checkpoint": {
            "id": "evaluation",
            "stage": "final",
            "node_id": "evaluation",
            "artifact": {"rubric": {"items": []}},
        },
    }

    artifacts.refresh_checkpoint_artifact(context)

    artifact = context["human_approval_checkpoint"]["artifact"]
    assert artifact["rubric_score"] == "2 / 2"
    assert artifact["rubric_failed_count"] == 0
