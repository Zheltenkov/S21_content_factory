"""Deterministic model fixtures for workflow tests; never used by the application."""

from project_generator_lab.concept.models import (
    CriterionCheck,
    GroundedOutcome,
    GroundedReview,
    InputCard,
    InputClaim,
    StartRequest,
)


def input_card(request: StartRequest) -> InputCard:
    claims = [InputClaim(area="outcomes", text=text, kind="given", evidence_ids=["form.outcomes"], outcome_numbers=[i])
              for i, text in enumerate(filter(str.strip, request.brief.outcomes.splitlines()), 1)]
    for area, key in [("audience", "audience"), ("constraints", "hours"), ("program", "description"), ("materials", "output_package")]:
        claims.append(InputClaim(area=area, text=str(getattr(request.brief, key)), kind="given", evidence_ids=[f"form.{key}"], outcome_numbers=[]))
    return InputCard(summary="Практический разбор интервью с основаниями выводов", claims=claims, questions=[])


def grounded_review() -> GroundedReview:
    return GroundedReview(
        summary="Все результаты связаны с практикой и проверкой", findings=[],
        outcomes=[GroundedOutcome(outcome_number=i, task_numbers=[1], status="confirmed", explanation="Выводы подтверждаются цитатами", evidence_ids=["form.outcomes"], required_action="Выделить факты", planned_action="Сопоставить фрагменты интервью", observable_evidence="Пир сверяет вывод с цитатами", gap="") for i in [1, 2]],
        checks=[CriterionCheck(rule_id=rule, status="confirmed", explanation="Условие учтено в практическом задании", evidence_ids=["form.description"], concept_locations=["tasks.1"]) for rule in ["outcomes", "audience", "program", "constraints", "independence", "materials"]],
    )
