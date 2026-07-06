"""Жюри моделей: оценка дименшена панелью LLM + эскалация в дискуссию.

Бэкенд жюри абстрагирован (`JuryBackend`): продовый `LLMJuryBackend` зовёт несколько
Polza-моделей через пиннингованный шлюз с mock-фолбэком на сбой джурора; `MockJuryBackend`
детерминирован (для тестов и оффлайна без ключа). Оркестрация (`jury_score_dimension`,
`judge_dimension`) — backend-агностична.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ....platform.llm.gateway import LLMGateway
from ...config.didactic_config import (
    DEBATE_MD_CHARS,
    JUROR_MD_CHARS,
    JURY_PROVIDER,
)
from .dimensions import Dimension
from .models import DidacticDimensionScore, JurorVerdict
from .signals import DidacticSignals


def _clamp(value: float) -> float:
    return max(1.0, min(5.0, value))


# --- Mock-судья: детерминированный балл из объективных сигналов ---

def _base_mock_score(dim_id: str, s: DidacticSignals) -> tuple[float, str, list[str]]:
    """Базовый эвристический балл 1–5 + объяснение из реальных сигналов (порт прототипа)."""
    rep, ndup = s["repetition_ratio"], s["near_dup"]
    broken, match = s["broken_tables"], s["diagram_match_avg"]
    if dim_id == "naturalness":
        return (
            5 - min(3.2, rep * 18 + ndup * 0.06),
            "Самоповторы: одни и те же связки-болванки дословно.",
            [f"{ndup} почти-дублей предложений", f"repetition_ratio={rep}"],
        )
    if dim_id == "coherence":
        return (
            5 - min(2.8, broken * 1.0 + ndup * 0.04 + (1 if match < 0.2 else 0)),
            "Разрывы потока: сломанная таблица и диаграммы не по теме.",
            [f"{broken} разваленных таблиц", f"диаграммы вне темы (match={match})"],
        )
    if dim_id == "cognitive_load":
        return (
            5 - min(2.7, rep * 15 + ndup * 0.05),
            "Повторы раздувают объём без смысла — лишняя нагрузка.",
            [f"{ndup} дубль-предложений"],
        )
    if dim_id == "example_quality":
        return (
            3.7 if s["example_count"] >= 3 else 2.5,
            "Примеры есть, но слабо привязаны к кейсу проекта.",
            [f"примеров: {s['example_count']} (обобщённые)"],
        )
    if dim_id == "scaffolding":
        return (
            3.1,
            "Теория местами generic, опора под практику размыта.",
            ["диаграммы-болванки не отражают теорию"],
        )
    if dim_id == "school_tone":
        return (
            4.2 if s["directive_hits"] == 0 else 3.0,
            "Тон p2p выдержан: правила, не готовые ответы.",
            [f"директив: {s['directive_hits']}"],
        )
    return 3.0, "—", []


# Линзы mock-моделей: разная строгость по дименшену → жюри расходится, как реальные модели.
MOCK_LENS: dict[str, dict[str, float]] = {
    "openai/gpt-5.4": {"naturalness": -0.5, "coherence": -0.3, "scaffolding": -0.9,
                       "example_quality": -1.5, "cognitive_load": -0.3, "school_tone": 0.0},
    "deepseek/deepseek-v4": {"naturalness": -0.2, "coherence": -0.5, "scaffolding": 0.1,
                             "example_quality": -0.3, "cognitive_load": -0.1, "school_tone": -0.2},
    "google/gemini-3.1-pro": {"naturalness": 0.3, "coherence": 0.2, "scaffolding": 0.4,
                              "example_quality": 1.1, "cognitive_load": 0.3, "school_tone": 0.3},
}


def mock_verdict(model: str, dim: Dimension, signals: DidacticSignals) -> JurorVerdict:
    """Детерминированный вердикт джурора из сигналов + линза модели."""
    base, rationale, evidence = _base_mock_score(dim.id, signals)
    bias = MOCK_LENS.get(model, {}).get(dim.id, 0.0)
    return JurorVerdict(score=round(_clamp(base + bias), 2), rationale=rationale, evidence=evidence)


# --- Backend жюри ---

class JuryBackend(Protocol):
    """Источник вердиктов жюри и результата дискуссии (DI-шов для тестов)."""

    def score_one(
        self, model: str, dim: Dimension, signals: DidacticSignals, learning_outcomes: list[str]
    ) -> JurorVerdict:
        ...

    def debate(
        self,
        dim: Dimension,
        signals: DidacticSignals,
        jury_scores: dict[str, float],
        debate_roles: dict[str, str],
        learning_outcomes: list[str],
    ) -> tuple[float, str, list[dict[str, object]]]:
        ...


class MockJuryBackend:
    """Детерминированный бэкенд без LLM (тесты, оффлайн, фолбэк)."""

    def score_one(
        self, model: str, dim: Dimension, signals: DidacticSignals, learning_outcomes: list[str]
    ) -> JurorVerdict:
        return mock_verdict(model, dim, signals)

    def debate(
        self,
        dim: Dimension,
        signals: DidacticSignals,
        jury_scores: dict[str, float],
        debate_roles: dict[str, str],
        learning_outcomes: list[str],
    ) -> tuple[float, str, list[dict[str, object]]]:
        # Критик = строгая модель, защитник = мягкая, судья ближе к критику.
        strict = min(jury_scores, key=lambda m: jury_scores[m])
        lenient = max(jury_scores, key=lambda m: jury_scores[m])
        _, crit_rationale, crit_evidence = _base_mock_score(dim.id, signals)
        final = round(jury_scores[strict] * 0.6 + jury_scores[lenient] * 0.4, 2)
        rationale = (
            f"После спора критика (повторы/разрывы) перевешивает формальную полноту. {crit_rationale}"
        )
        transcript: list[dict[str, object]] = [
            {"role": "critic", "model": strict, "points": crit_evidence or [crit_rationale]},
            {"role": "defender", "model": lenient, "points": ["формальные требования соблюдены; блок заполнен"]},
            {"role": "judge", "model": debate_roles.get("judge", strict), "points": rationale},
        ]
        return final, rationale, transcript


class _DebatePoints(BaseModel):
    model_config = ConfigDict(extra="ignore")
    points: list[str] = Field(default_factory=list)


class _DebateVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")
    score: float
    rationale: str = ""


GatewayFactory = Callable[[str], LLMGateway]


def default_gateway_factory(model: str) -> LLMGateway:
    """Пиннингованный Polza-шлюз под конкретную модель жюри."""
    return LLMGateway(provider=JURY_PROVIDER, model=model, strict_provider=True, default_role="critic")


class LLMJuryBackend:
    """Продовый бэкенд: несколько Polza-моделей; на сбой джурора — mock-фолбэк."""

    def __init__(
        self,
        md: str,
        *,
        gateway_factory: GatewayFactory = default_gateway_factory,
        mock: MockJuryBackend | None = None,
    ) -> None:
        self._md = md
        self._gateway_factory = gateway_factory
        self._mock = mock or MockJuryBackend()

    def score_one(
        self, model: str, dim: Dimension, signals: DidacticSignals, learning_outcomes: list[str]
    ) -> JurorVerdict:
        system = (
            "Ты — методист школы проектного p2p-обучения. Оцени README по ОДНОМУ дидактическому "
            "критерию. Сначала рассуждение, потом балл 1–5 (5=отлично). Формальное наличие блока "
            "не равно качеству."
        )
        user = (
            f"КРИТЕРИЙ: {dim.title}\nВОПРОС: {dim.question}\n"
            f"ЗУНы: {learning_outcomes or '—'}\n"
            f"ОБЪЕКТИВНЫЕ СИГНАЛЫ: {dict(signals)}\n\n"
            f"README (между <<< >>>):\n<<<\n{self._md[:JUROR_MD_CHARS]}\n>>>"
        )
        try:
            verdict = self._gateway_factory(model).complete_structured(
                output_model=JurorVerdict, system=system, user=user, llm_role="critic"
            )
            verdict.score = _clamp(float(verdict.score))
            return verdict
        except Exception:
            return self._mock.score_one(model, dim, signals, learning_outcomes)

    def debate(
        self,
        dim: Dimension,
        signals: DidacticSignals,
        jury_scores: dict[str, float],
        debate_roles: dict[str, str],
        learning_outcomes: list[str],
    ) -> tuple[float, str, list[dict[str, object]]]:
        base = f"Критерий: {dim.title}. Вопрос: {dim.question}. README:\n{self._md[:DEBATE_MD_CHARS]}"
        try:
            critic_gw = self._gateway_factory(debate_roles["critic"])
            crit = critic_gw.complete_structured(
                output_model=_DebatePoints,
                system="Ты CRITIC: жёстко найди дидактические слабости по критерию.",
                user=base,
                llm_role="critic",
            )
            defender_gw = self._gateway_factory(debate_roles["defender"])
            deff = defender_gw.complete_structured(
                output_model=_DebatePoints,
                system="Ты DEFENDER: ответь критику, отметь сильное.",
                user=f"{base}\nКритика: {crit.points}",
                llm_role="critic",
            )
            judge_gw = self._gateway_factory(debate_roles["judge"])
            verdict = judge_gw.complete_structured(
                output_model=_DebateVerdict,
                system="Ты JUDGE: взвесь обе стороны, дай балл 1–5.",
                user=f"{base}\nCRITIC: {crit.points}\nDEFENDER: {deff.points}",
                llm_role="critic",
            )
            transcript: list[dict[str, object]] = [
                {"role": "critic", "model": debate_roles["critic"], "points": crit.points},
                {"role": "defender", "model": debate_roles["defender"], "points": deff.points},
                {"role": "judge", "model": debate_roles["judge"], "points": verdict.rationale},
            ]
            return _clamp(float(verdict.score)), verdict.rationale, transcript
        except Exception:
            return self._mock.debate(dim, signals, jury_scores, debate_roles, learning_outcomes)


# --- Оркестрация (backend-агностична) ---

def jury_score_dimension(
    dim: Dimension,
    models: list[str],
    signals: DidacticSignals,
    learning_outcomes: list[str],
    backend: JuryBackend,
) -> DidacticDimensionScore:
    """Панель оценивает дименшен → медиана + уверенность из разброса."""
    per_model: dict[str, float] = {}
    rationales: list[str] = []
    evidence: list[str] = []
    for model in models:
        verdict = backend.score_one(model, dim, signals, learning_outcomes)
        per_model[model] = round(_clamp(float(verdict.score)), 2)
        if verdict.rationale:
            rationales.append(verdict.rationale)
        evidence.extend(verdict.evidence)

    scores = list(per_model.values())
    median = round(statistics.median(scores), 2) if scores else 0.0
    spread = statistics.pstdev(scores) if len(scores) > 1 else 0.0
    confidence = round(max(0.0, 1.0 - spread / 2.0), 2)

    unique_evidence: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        if item not in seen:
            seen.add(item)
            unique_evidence.append(item)

    return DidacticDimensionScore(
        dimension=dim.id,
        title=dim.title,
        score=median,
        confidence=confidence,
        per_model=per_model,
        rationale=rationales[0] if rationales else "",
        evidence=unique_evidence[:4],
    )


def judge_dimension(
    dim: Dimension,
    models: list[str],
    signals: DidacticSignals,
    learning_outcomes: list[str],
    backend: JuryBackend,
    *,
    floor: float,
    abstain_confidence: float,
    debate_on_escalate: bool,
    debate_roles: dict[str, str],
) -> DidacticDimensionScore:
    """Жюри + эскалация в дискуссию на спорном дименшене."""
    score = jury_score_dimension(dim, models, signals, learning_outcomes, backend)
    low_conf = score.confidence < abstain_confidence
    below = score.score < floor
    if debate_on_escalate and (low_conf or below) and score.per_model:
        score.escalated = True
        reasons = []
        if low_conf:
            reasons.append("разброс жюри")
        if below:
            reasons.append("ниже пола")
        score.escalate_reason = " + ".join(reasons)
        debate_score, debate_rationale, transcript = backend.debate(
            dim, signals, score.per_model, debate_roles, learning_outcomes
        )
        score.score = round(debate_score, 2)
        score.rationale = debate_rationale
        score.debate_transcript = transcript
    return score
