"""Tests for RubricScorer cache context isolation."""

from content_gen.models.criteria_models import CheckMethod, CriteriaItem
from content_gen.utils.validation_cache import ValidationCache
from content_gen.validators.rubric.scorer import RubricScorer


def _item(item_id: str, score: int) -> CriteriaItem:
    return CriteriaItem(
        id=item_id,
        title=item_id,
        description=item_id,
        check_method=CheckMethod.SCRIPT,
        score=score,
        comments=[],
    )


def test_rubric_cache_depends_on_learning_outcomes(monkeypatch, mock_llm_client):
    scorer = RubricScorer(language="ru", llm_client=mock_llm_client)
    local_cache = ValidationCache(max_size=10, ttl=3600)
    monkeypatch.setattr("content_gen.validators.rubric.scorer.get_cache", lambda: local_cache)

    calls = {"section2": 0}

    scorer.section1_checker.check = lambda _md: [_item("1.1", 1)]

    def _section2(_md, learning_outcomes):
        calls["section2"] += 1
        return [_item("2.1", 1 if "A" in (learning_outcomes or []) else 0)]

    scorer.section2_checker.check = _section2
    scorer.section3_checker.check = lambda _md: [_item("3.1", 1)]
    scorer.section4_checker.check = lambda _md: [_item("4.1", 1)]

    md = "# test"
    r1 = scorer.score(md, learning_outcomes=["A"], use_cache=True)
    r2 = scorer.score(md, learning_outcomes=["B"], use_cache=True)
    r3 = scorer.score(md, learning_outcomes=["A"], use_cache=True)

    assert r1.total != r2.total
    assert r3.total == r1.total
    assert calls["section2"] == 2

