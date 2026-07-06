"""Тесты жюри: медиана/уверенность, эскалация, mock-фолбэк."""

from content_factory.generation.evaluation.didactic.dimensions import DIMENSIONS
from content_factory.generation.evaluation.didactic.jury import (
    LLMJuryBackend,
    MockJuryBackend,
    judge_dimension,
    jury_score_dimension,
)
from content_factory.generation.evaluation.didactic.models import JurorVerdict
from content_factory.generation.evaluation.didactic.signals import collect_signals

_COHERENCE = next(d for d in DIMENSIONS if d.id == "coherence")
_DEBATE_ROLES = {"critic": "B", "defender": "C", "judge": "A"}


class _StubBackend:
    """Детерминированный бэкенд: фиксированные баллы per-model, дискуссия отдаёт 4.0."""

    def __init__(self, per_model: dict[str, float]) -> None:
        self._per_model = per_model

    def score_one(self, model, dim, signals, learning_outcomes):
        return JurorVerdict(score=self._per_model[model], rationale=f"{model}", evidence=[f"ev-{model}"])

    def debate(self, dim, signals, jury_scores, debate_roles, learning_outcomes):
        return 4.0, "debated", [{"role": "judge", "model": "A", "points": "ok"}]


def _signals() -> dict:
    return collect_signals("# Проект\n\nТекст.")


def test_jury_score_dimension_median_and_confidence() -> None:
    backend = _StubBackend({"A": 2.0, "B": 4.0, "C": 3.0})
    score = jury_score_dimension(_COHERENCE, ["A", "B", "C"], _signals(), [], backend)
    assert score.score == 3.0
    assert score.confidence == 0.59  # 1 − pstdev([2,4,3])/2
    assert score.per_model == {"A": 2.0, "B": 4.0, "C": 3.0}
    assert not score.escalated


def test_judge_dimension_escalates_below_floor() -> None:
    backend = _StubBackend({"A": 2.0, "B": 2.0, "C": 2.0})
    score = judge_dimension(
        _COHERENCE, ["A", "B", "C"], _signals(), [], backend,
        floor=3.0, abstain_confidence=0.55, debate_on_escalate=True, debate_roles=_DEBATE_ROLES,
    )
    assert score.escalated
    assert "ниже пола" in score.escalate_reason
    assert score.score == 4.0  # результат дискуссии
    assert score.debate_transcript


def test_judge_dimension_escalates_on_low_confidence() -> None:
    backend = _StubBackend({"A": 1.0, "B": 5.0})  # median 3, spread → confidence 0
    score = judge_dimension(
        _COHERENCE, ["A", "B"], _signals(), [], backend,
        floor=3.0, abstain_confidence=0.55, debate_on_escalate=True, debate_roles=_DEBATE_ROLES,
    )
    assert score.escalated
    assert "разброс жюри" in score.escalate_reason


def test_judge_dimension_no_escalation_when_confident_and_above_floor() -> None:
    backend = _StubBackend({"A": 4.0, "B": 4.0, "C": 4.0})
    score = judge_dimension(
        _COHERENCE, ["A", "B", "C"], _signals(), [], backend,
        floor=3.0, abstain_confidence=0.55, debate_on_escalate=True, debate_roles=_DEBATE_ROLES,
    )
    assert not score.escalated
    assert score.score == 4.0


class _BoomGateway:
    def complete_structured(self, **kwargs):
        raise RuntimeError("no api key")


class _OkGateway:
    def complete_structured(self, *, output_model, system, user, **kwargs):
        return output_model(score=4.7, rationale="ok", evidence=["цитата"])


def test_llm_backend_falls_back_to_mock_on_failure() -> None:
    backend = LLMJuryBackend("# Проект\n\nТекст.", gateway_factory=lambda model: _BoomGateway())
    verdict = backend.score_one("openai/gpt-5.4", _COHERENCE, _signals(), [])
    assert isinstance(verdict, JurorVerdict)
    assert 1.0 <= verdict.score <= 5.0


def test_llm_backend_uses_and_clamps_llm_verdict() -> None:
    backend = LLMJuryBackend("# Проект\n\nТекст.", gateway_factory=lambda model: _OkGateway())
    verdict = backend.score_one("openai/gpt-5.4", _COHERENCE, _signals(), [])
    assert verdict.score == 4.7
    assert verdict.evidence == ["цитата"]


def test_mock_backend_debate_weighs_strict_over_lenient() -> None:
    backend = MockJuryBackend()
    final, rationale, transcript = backend.debate(
        _COHERENCE, _signals(), {"strict": 2.0, "lenient": 4.0}, _DEBATE_ROLES, [],
    )
    assert final == round(2.0 * 0.6 + 4.0 * 0.4, 2)
    assert rationale
    assert [row["role"] for row in transcript] == ["critic", "defender", "judge"]
