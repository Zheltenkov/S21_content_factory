"""Opt-in semantic quality evaluation against a real configured LLM.

Run explicitly with ``RUN_RPD_LLM_EVAL=1 pytest tests/rpd/test_quality_live_llm.py -s``.
The default suite skips it to avoid hidden network calls, cost and nondeterminism.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest

from content_factory.platform.llm.factory import create_llm_client
from content_factory.rpd.quality import preflight_up_plan
from content_factory.rpd.up_models import UpPlan, from_registry_plan

pytestmark = [pytest.mark.integration, pytest.mark.slow]


@dataclass(frozen=True)
class _EvalCase:
    name: str
    title: str
    block: str
    project: str
    description: str
    expected_consistent: bool


_CASES = (
    _EvalCase(
        "ai_business_match",
        "ИИ для предпринимателей",
        "Проверка бизнес-гипотез с ИИ",
        "AI-assisted customer discovery",
        "Интервью, анализ рынка и проверка ценностного предложения с помощью LLM",
        True,
    ),
    _EvalCase(
        "english_marketing_match",
        "Английский язык для маркетологов",
        "Marketing English A2",
        "Present a campaign in English",
        "Vocabulary, grammar and speaking practice for marketing teams",
        True,
    ),
    _EvalCase(
        "ai_vs_english_mismatch",
        "ИИ для предпринимателей",
        "Marketing English A2",
        "Present a campaign in English",
        "Vocabulary, grammar and speaking practice for marketing teams",
        False,
    ),
    _EvalCase(
        "safety_vs_design_mismatch",
        "Промышленная безопасность опасных объектов",
        "Визуальный дизайн",
        "Создание рекламного плаката",
        "Композиция, типографика, цвет и подготовка макета к печати",
        False,
    ),
)


def _to_up(case: _EvalCase) -> UpPlan:
    return from_registry_plan(
        {
            "title": case.title,
            "summary": {"total_hours": 16},
            "rows": [
                {
                    "block_title": case.block,
                    "project_name": case.project,
                    "project_summary": case.description,
                    "outcomes_can": case.description,
                    "effort_hours": 16,
                }
            ],
        }
    )


def test_live_llm_title_content_consistency_eval() -> None:
    """Require useful recall/specificity on a tiny, versioned semantic smoke set."""
    if os.getenv("RUN_RPD_LLM_EVAL") != "1":
        pytest.skip("set RUN_RPD_LLM_EVAL=1 to spend LLM quota on the semantic RPD eval")

    client = create_llm_client(
        provider=os.getenv("RPD_LLM_EVAL_PROVIDER") or None,
        model=os.getenv("RPD_LLM_EVAL_MODEL") or None,
        default_role="validator",
        enable_cache=False,
        max_retries=1,
        timeout_seconds=60,
        run_id="rpd-semantic-eval-v1",
    )
    predictions: list[bool] = []
    for case in _CASES:
        issues = preflight_up_plan(_to_up(case), client)
        assert not any(issue.code == "semantic_preflight_unavailable" for issue in issues), case.name
        mismatch = any(issue.code == "source_title_content_mismatch" for issue in issues)
        predictions.append(not mismatch)

    expected = [case.expected_consistent for case in _CASES]
    correct = sum(predicted == target for predicted, target in zip(predictions, expected, strict=True))
    mismatch_indexes = [index for index, target in enumerate(expected) if not target]
    match_indexes = [index for index, target in enumerate(expected) if target]
    mismatch_recall = sum(not predictions[index] for index in mismatch_indexes) / len(mismatch_indexes)
    match_specificity = sum(predictions[index] for index in match_indexes) / len(match_indexes)
    by_case = dict(zip((case.name for case in _CASES), predictions, strict=True))

    assert correct / len(_CASES) >= 0.75, by_case
    assert mismatch_recall >= 0.5, by_case
    assert match_specificity >= 0.5, by_case
