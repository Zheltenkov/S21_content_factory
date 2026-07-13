"""Artifact policy registry + Capstone contract (contract epic, slice 4).

The deterministic methodical floor per policy area (from the review's artifact matrix): what
must be produced, what proves it, and how it is accepted. Keyed by the ``policy_area`` set
in slice 3. This is a code registry, not a prompt — the LLM may reword deliverables for the
project theme but cannot replace a runnable result with a schema.

Applied as a post-grouping pass: a classified project's generic artifact/criteria fallback
is replaced with the policy-backed contract; an unclassified project (``policy_area == ""``)
gets no contract and keeps its generic fallback (draft-only, flagged), never a silent
substitution. Template-provided enrichment is left untouched (that is slice 6's concern).

Pure leaf: depends only on the domain dataclasses + the generic detector.
"""

from __future__ import annotations

from .domain import AcceptanceCriterion, ArtifactContract, CurriculumBlock, ProjectBlueprint
from .project_quality import is_generic_artifact


def _ac(subject: str, check: str, expected: str, evidence: str, *, mode: str = "manual") -> AcceptanceCriterion:
    return AcceptanceCriterion(
        subject=subject,
        check=check,
        expected_result=expected,
        evidence_type=evidence,
        verification_mode="automatic" if mode == "automatic" else "manual",
        blocking=True,
    )


#: policy_area -> ArtifactContract template. Order/keys match POLICY_AREA_HINTS (slice 3).
POLICY_REGISTRY: dict[str, ArtifactContract] = {
    "product_creation": ArtifactContract(
        artifact_type="runnable_prototype",
        policy_area="product_creation",
        deliverables=("запускаемый прототип", "репозиторий", "инструкция запуска"),
        evidence_requirements=("демонстрация основного сценария", "скриншот/лог запуска"),
        acceptance_criteria=(
            _ac("прототип", "запускается по инструкции на контрольном входе", "рабочий результат основного сценария", "лог/скриншот запуска"),
            _ac("репозиторий", "содержит исходники и README", "воспроизводимый проект", "ссылка на репозиторий"),
        ),
        execution_environment="локальный запуск / контейнер",
        publication_constraints=("демо не должно требовать закрытых доступов",),
    ),
    "ai_automation": ArtifactContract(
        artifact_type="executable_workflow",
        policy_area="ai_automation",
        deliverables=("исполняемый workflow", "описание входа/выхода", "обработка одного сценария ошибки"),
        evidence_requirements=("журнал запуска", "точка human-in-the-loop"),
        acceptance_criteria=(
            _ac("workflow", "запускается на контрольном входе и сохраняет результат", "корректный выход + журнал", "журнал запуска / экспорт workflow"),
            _ac("ошибки", "один сценарий ошибки обрабатывается контролируемо", "workflow не падает молча", "журнал обработки ошибки"),
        ),
        execution_environment="workflow-раннер / скрипт",
        publication_constraints=("ключи и секреты не в артефакте",),
    ),
    "engineering_discipline": ArtifactContract(
        artifact_type="engineered_repository",
        policy_area="engineering_discipline",
        deliverables=("репозиторий", "тесты", "CI pipeline", "release/tag"),
        evidence_requirements=("зелёный CI-прогон", "воспроизводимая сборка"),
        acceptance_criteria=(
            _ac("CI", "pipeline проходит на коммите", "зелёный прогон тестов и сборки", "ссылка на CI-прогон", mode="automatic"),
            _ac("релиз", "есть release/tag воспроизводимой сборки", "фиксированная версия", "tag/release в репозитории"),
        ),
        execution_environment="CI (GitHub Actions / аналог)",
        publication_constraints=("сборка воспроизводима без ручных шагов",),
    ),
    "operations": ArtifactContract(
        artifact_type="operated_service",
        policy_area="operations",
        deliverables=("развёрнутый сервис", "health-check", "мониторинг/логи", "runbook"),
        evidence_requirements=("health-check отвечает", "evidence backup/restore"),
        acceptance_criteria=(
            _ac("сервис", "развёрнут и отвечает на health-check", "доступный сервис", "ответ health-check / скриншот мониторинга"),
            _ac("эксплуатация", "runbook покрывает инцидент и восстановление", "воспроизводимая процедура", "runbook + evidence backup/restore"),
        ),
        execution_environment="развёрнутое окружение (сервер/контейнер)",
        publication_constraints=("доступ к сервису безопасен",),
    ),
    "marketing_sales": ArtifactContract(
        artifact_type="market_material",
        policy_area="marketing_sales",
        deliverables=("лендинг/материалы", "настроенная аналитика", "тест канала"),
        evidence_requirements=("измеримый результат теста канала",),
        acceptance_criteria=(
            _ac("аналитика", "настроена и фиксирует события", "измеримые метрики канала", "скриншот аналитики / отчёт"),
            _ac("канал", "проведён тест канала с результатом", "измеримый результат", "отчёт теста канала"),
        ),
        execution_environment="лендинг + аналитическая система",
        publication_constraints=(),
    ),
    "monetization": ArtifactContract(
        artifact_type="monetization_model",
        policy_area="monetization",
        deliverables=("тарифы", "unit economics", "сценарий продажи или trial funnel"),
        evidence_requirements=("расчёт unit economics",),
        acceptance_criteria=(
            _ac("экономика", "unit economics посчитана по входным данным", "обоснованная модель", "таблица расчёта"),
            _ac("продажа", "описан сценарий продажи или trial funnel", "воспроизводимый путь к выручке", "документ сценария"),
        ),
        execution_environment="расчётная модель / документ",
        publication_constraints=(),
    ),
    "ai_quality_safety": ArtifactContract(
        artifact_type="ai_quality_harness",
        policy_area="ai_quality_safety",
        deliverables=("eval-набор", "порог качества", "guardrails", "процедура эскалации"),
        evidence_requirements=("журнал ошибок", "результат eval относительно порога"),
        acceptance_criteria=(
            _ac("eval", "прогоняется и сравнивается с порогом качества", "измеримое качество vs порог", "отчёт eval", mode="automatic"),
            _ac("safety", "guardrails и эскалация описаны и срабатывают", "контролируемое поведение на плохом входе", "журнал ошибок + правило эскалации"),
        ),
        execution_environment="eval-раннер",
        publication_constraints=("нет утечки небезопасных ответов в демо",),
    ),
    "capstone": ArtifactContract(
        artifact_type="capstone_release",
        policy_area="capstone",
        deliverables=("работающий MVP", "release", "доступное demo", "метрики запуска", "презентация"),
        evidence_requirements=("публичный запуск/demo", "evidence обратной связи"),
        acceptance_criteria=(
            _ac("MVP", "работает end-to-end и публично доступен как demo", "запущенный MVP с demo", "ссылка на demo + release"),
            _ac("запуск", "собраны метрики запуска и обратная связь", "измеримый результат запуска", "метрики + evidence обратной связи"),
        ),
        execution_environment="публичный запуск / demo-стенд",
        publication_constraints=("demo доступно проверяющему без спец-доступов",),
    ),
}


def build_artifact_contract(project: ProjectBlueprint) -> ArtifactContract | None:
    """Return the policy contract for a classified project, or None when unclassified."""
    return POLICY_REGISTRY.get(project.policy_area or "")


def render_artifact_line(contract: ArtifactContract) -> str:
    """A specific, policy-backed artifact description replacing the generic fallback."""
    deliverables = ", ".join(contract.deliverables)
    evidence = ", ".join(contract.evidence_requirements)
    line = f"Проект сдаётся как {deliverables}"
    if evidence:
        line += f"; доказательство: {evidence}"
    return line


def render_acceptance_text(contract: ArtifactContract) -> str:
    """Render the acceptance criteria as a checkable, project-specific criteria block."""
    lines = ["Критерии приёмки:"]
    for criterion in contract.acceptance_criteria:
        mode = "авто" if criterion.verification_mode == "automatic" else "ручная проверка"
        lines.append(
            f"- {criterion.subject}: {criterion.check} → {criterion.expected_result} "
            f"({criterion.evidence_type}, {mode})"
        )
    return "\n".join(lines)


def apply_artifact_contracts(blocks: list[CurriculumBlock]) -> None:
    """Attach policy contracts and replace generic fallbacks for classified projects.

    Only the generic artifact/criteria fallbacks are replaced; template-provided enrichment
    and unclassified projects are left untouched (draft-only, flagged elsewhere).
    """
    for block in blocks:
        for project in block.projects:
            contract = build_artifact_contract(project)
            project.artifact_contract = contract
            if contract is None:
                continue
            if is_generic_artifact(project.artifact):
                project.artifact = render_artifact_line(contract)
            if not project.enrichment.get("validation_criteria"):
                project.enrichment["validation_criteria"] = render_acceptance_text(contract)
