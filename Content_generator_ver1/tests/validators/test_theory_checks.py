from content_gen.models.schemas import TheoryPart
from content_gen.validators.theory_checks import TheoryChecks


def _body_without_explicit_definitions() -> str:
    return (
        "Ты оцениваешь стоимость сервиса и считаешь регулярные расходы команды. "
        "В смете важно видеть ежемесячные платежи, годовой бюджет и пределы расходов. "
        "Так проще понять, где риски для проекта и какие решения можно отложить. "
        "Если команда не считает ограничения заранее, деньги заканчиваются слишком быстро. "
        "Тогда приходится срочно урезать объём проекта и переносить сроки. "
    ) * 6


def _body_with_unbold_definition() -> str:
    return (
        "Подписка — это регулярный платёж за использование сервиса. "
        "Смета — это перечень плановых расходов проекта на период. "
        "Ты смотришь, как эти понятия влияют на сроки, риски и объём работы команды. "
    ) * 8


def test_theory_checks_treats_missing_definitions_as_soft_issue():
    checks = TheoryChecks()
    part = TheoryPart(
        title="Платные сервисы и их стоимость",
        body=_body_without_explicit_definitions(),
        example="В 2024 году команда сократила расходы после пересмотра подписок.",
        bridge_questions=["Как ты проверишь стоимость сервисов в своем проекте?"],
    )

    result = checks.check([part, part, part])

    assert result.passed is True
    assert not result.hard_issues
    assert any(issue.criterion_id == "2.4.4" and issue.severity == "soft" for issue in result.soft_issues)
    assert any(issue.message.startswith("Раздел 2.1 'Платные сервисы") for issue in result.soft_issues)


def test_theory_checks_accepts_single_unbold_definition_without_hard_fail():
    checks = TheoryChecks()
    part = TheoryPart(
        title="Создание сметы расходов",
        body=_body_with_unbold_definition(),
        example="Компания пересчитала бюджет и отказалась от лишних инструментов.",
        bridge_questions=["Как ты оформил бы смету для своей команды?"],
    )

    result = checks.check([part, part, part])

    assert result.passed is True
    assert not result.hard_issues
