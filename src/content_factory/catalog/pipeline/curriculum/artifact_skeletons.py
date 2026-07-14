"""Universal artifact skeletons keyed by observable learner activity.

Skeletons are domain-neutral methodological minima. They define which evidence makes an
activity assessable without assuming a product lifecycle, technology stack, or subject
domain. Profile policies may refine them, while brief templates provide themed wording.
"""

from __future__ import annotations

from .domain import AcceptanceCriterion, ActivityArchetype, ArtifactContract, ProjectBlueprint

SKELETON_VERSION = "activity-artifact-skeleton/v1"


def _criterion(
    subject: str,
    check: str,
    expected: str,
    evidence: str,
    *,
    automatic: bool = False,
) -> AcceptanceCriterion:
    return AcceptanceCriterion(
        subject=subject,
        check=check,
        expected_result=expected,
        evidence_type=evidence,
        verification_mode="automatic" if automatic else "manual",
        blocking=True,
    )


ARCHETYPE_SKELETONS: dict[ActivityArchetype, ArtifactContract] = {
    "investigate": ArtifactContract(
        artifact_type="evidence_report",
        policy_area="",
        activity_archetype="investigate",
        deliverables=("описание метода", "исходные наблюдения или данные", "анализ и вывод"),
        evidence_requirements=("прослеживаемая связь вывода с данными",),
        acceptance_criteria=(
            _criterion(
                "метод",
                "описывает источник, процедуру сбора и ограничения",
                "исследование можно повторить или проверить",
                "описание метода + исходные данные",
            ),
            _criterion(
                "вывод",
                "связан с конкретными наблюдениями или расчётами",
                "вывод подтверждается представленными данными",
                "таблица/журнал анализа",
            ),
        ),
        execution_environment="среда сбора и анализа данных",
        composition_version=SKELETON_VERSION,
    ),
    "design": ArtifactContract(
        artifact_type="design_package",
        policy_area="",
        activity_archetype="design",
        deliverables=("модель или спецификация решения", "обоснование проектных решений"),
        evidence_requirements=("проверка требований и ограничений",),
        acceptance_criteria=(
            _criterion(
                "спецификация",
                "покрывает заявленные требования и ограничения",
                "решение непротиворечиво и проверяемо",
                "модель/схема + трассировка требований",
            ),
            _criterion(
                "решения",
                "содержат альтернативы и обоснованный выбор",
                "ключевые компромиссы явно зафиксированы",
                "decision log",
            ),
        ),
        execution_environment="среда моделирования или проектирования",
        composition_version=SKELETON_VERSION,
    ),
    "construct": ArtifactContract(
        artifact_type="working_implementation",
        policy_area="",
        activity_archetype="construct",
        deliverables=("работающая реализация", "инструкция воспроизведения", "демонстрационный сценарий"),
        evidence_requirements=("результат контрольного запуска",),
        acceptance_criteria=(
            _criterion(
                "реализация",
                "воспроизводится по инструкции на контрольном входе",
                "получен ожидаемый наблюдаемый результат",
                "артефакт + журнал/запись запуска",
            ),
            _criterion(
                "основной сценарий",
                "демонстрирует заявленную функцию или свойство",
                "результат можно проверить независимо",
                "демонстрация + ожидаемый результат",
            ),
        ),
        execution_environment="целевая среда выполнения",
        composition_version=SKELETON_VERSION,
    ),
    "operate": ArtifactContract(
        artifact_type="operational_result",
        policy_area="",
        activity_archetype="operate",
        deliverables=("работающий объект эксплуатации", "операционная инструкция", "наблюдаемость состояния"),
        evidence_requirements=("проверка штатного режима", "проверка одного отказного сценария"),
        acceptance_criteria=(
            _criterion(
                "штатный режим",
                "объект работает и его состояние наблюдаемо",
                "целевой режим подтверждён измерением",
                "журнал/чек-лист состояния",
            ),
            _criterion(
                "восстановление",
                "оператор выполняет процедуру для заданного отклонения",
                "система возвращается в допустимое состояние",
                "runbook + журнал выполнения",
            ),
        ),
        execution_environment="целевая операционная среда",
        composition_version=SKELETON_VERSION,
    ),
    "decide": ArtifactContract(
        artifact_type="decision_case",
        policy_area="",
        activity_archetype="decide",
        deliverables=("варианты решения", "явные критерии выбора", "обоснованная рекомендация"),
        evidence_requirements=("исходные данные для сравнения", "след принятого решения"),
        acceptance_criteria=(
            _criterion(
                "сравнение",
                "варианты оценены по заранее заданным критериям",
                "различия и компромиссы наблюдаемы",
                "матрица решения/расчёт",
            ),
            _criterion(
                "рекомендация",
                "следует из сравнения и учитывает ограничения",
                "выбор можно защитить по представленным данным",
                "decision record",
            ),
        ),
        execution_environment="контекст принятия решения",
        composition_version=SKELETON_VERSION,
    ),
    "perform": ArtifactContract(
        artifact_type="recorded_performance",
        policy_area="",
        activity_archetype="perform",
        deliverables=("выполненный сценарий деятельности", "результат или запись выполнения", "рубрика оценки"),
        evidence_requirements=("наблюдаемое выполнение по сценарию",),
        acceptance_criteria=(
            _criterion(
                "выполнение",
                "проходит заданный сценарий с наблюдаемым результатом",
                "ключевые действия выполнены в требуемой последовательности",
                "запись/протокол наблюдения",
            ),
            _criterion(
                "качество",
                "оценено по явной рубрике",
                "достигнут минимальный уровень по каждому блокирующему критерию",
                "заполненная рубрика",
            ),
        ),
        execution_environment="реальная или симулированная рабочая ситуация",
        composition_version=SKELETON_VERSION,
    ),
}


EXPERIMENT_EXTENSION = ArtifactContract(
    artifact_type="experiment_extension",
    policy_area="",
    deliverables=("гипотеза", "протокол эксперимента", "результаты измерений"),
    evidence_requirements=("контроль условий и исходные измерения",),
    acceptance_criteria=(
        _criterion(
            "протокол",
            "фиксирует гипотезу, условия, переменные и способ измерения",
            "эксперимент воспроизводим",
            "протокол эксперимента",
        ),
        _criterion(
            "результат",
            "сопоставлен с гипотезой по заранее заданной метрике",
            "вывод следует из измерений",
            "данные + расчёт метрики",
        ),
    ),
    composition_version=SKELETON_VERSION,
)

INTEGRATIVE_EXTENSION = ArtifactContract(
    artifact_type="integrative_extension",
    policy_area="",
    deliverables=("сквозной результат", "карта интеграции частей", "итоговая демонстрация"),
    evidence_requirements=("end-to-end проверка",),
    acceptance_criteria=(
        _criterion(
            "интеграция",
            "ключевые части работают совместно в сквозном сценарии",
            "получен целевой end-to-end результат",
            "демонстрация + журнал проверки",
        ),
    ),
    composition_version=SKELETON_VERSION,
)


def _unique(values: tuple[str, ...], additions: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*values, *additions)))


def _criterion_key(criterion: AcceptanceCriterion) -> tuple[str, str, str, str]:
    return (
        criterion.subject.casefold(),
        criterion.check.casefold(),
        criterion.expected_result.casefold(),
        criterion.evidence_type.casefold(),
    )


def _extend(base: ArtifactContract, extension: ArtifactContract) -> ArtifactContract:
    criteria = list(base.acceptance_criteria)
    seen = {_criterion_key(criterion) for criterion in criteria}
    for criterion in extension.acceptance_criteria:
        if _criterion_key(criterion) not in seen:
            criteria.append(criterion)
            seen.add(_criterion_key(criterion))
    return ArtifactContract(
        artifact_type=base.artifact_type,
        policy_area=base.policy_area,
        activity_archetype=base.activity_archetype,
        deliverables=_unique(base.deliverables, extension.deliverables),
        evidence_requirements=_unique(base.evidence_requirements, extension.evidence_requirements),
        acceptance_criteria=tuple(criteria),
        execution_environment=base.execution_environment,
        publication_constraints=_unique(
            base.publication_constraints,
            extension.publication_constraints,
        ),
        composition_version=SKELETON_VERSION,
    )


def build_archetype_skeleton(project: ProjectBlueprint) -> ArtifactContract | None:
    """Return the universal skeleton plus explicit activity modifiers, if classified."""
    if not project.activity_archetype:
        return None
    contract = ARCHETYPE_SKELETONS.get(project.activity_archetype)
    if contract is None:
        return None
    if "experiment" in project.activity_archetype_modifiers:
        contract = _extend(contract, EXPERIMENT_EXTENSION)
    if "integrative" in project.activity_archetype_modifiers:
        contract = _extend(contract, INTEGRATIVE_EXTENSION)
    return contract
