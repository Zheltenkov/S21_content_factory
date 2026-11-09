"""Evidence-bound checks and revision records; no model calls or hidden reasoning."""

import hashlib
import json

from . import skills
from .input_semantics import program_map
from .models import (
    PRESENTATION,
    Concept,
    CriterionCheck,
    Evidence,
    Finding,
    InputCard,
    OutcomeCheck,
    Review,
    RevisionChange,
    Run,
    SourceRecord,
    StartRequest,
)

RULES = {"outcomes", "audience", "program", "constraints", "independence", "materials"}
AREAS = RULES - {"independence"}


def system_finding(message: str, suggestion: str, rule: str = "integrity") -> Finding:
    return Finding(severity="blocker", message=message, suggestion=suggestion, rule_id=rule, origin="system")


def source_registry(request: StartRequest, evidence: list[Evidence]) -> list[SourceRecord]:
    """Counts come from parsed inputs and actual selection, never from a model's claim."""
    selected = {e.id for e in evidence}
    # The form as the concept reads it: how the text is to be told is no source of the concept (models.PRESENTATION).
    # Written as before those fields were there, so the fingerprint of a form stored earlier stays what it was.
    form = request.brief.for_concept()
    form_json = request.brief.model_dump_json(exclude=set(PRESENTATION))
    registry = [SourceRecord(
        id="form", name="Параметры методолога", role="requirement",
        sha256=hashlib.sha256(form_json.encode()).hexdigest(), read_status="text_extracted",
        extracted_count=len([v for v in form.values() if v is not None and v != ""]),
        selected_count=sum(e.id.startswith("form.") for e in evidence),
    )]
    if request.curriculum:
        curriculum = request.curriculum
        registry.append(SourceRecord(
            id="curriculum", name=curriculum.filename, role="curriculum", sha256=curriculum.sha256,
            read_status="text_extracted", extracted_count=len(curriculum.rows),
            selected_count=sum(r.id in selected for r in curriculum.rows),
            limitations=[*curriculum.warnings, *curriculum.missing_fields,
                         *([f"Не распознаны колонки: {', '.join(curriculum.unmapped_columns)}"] if curriculum.unmapped_columns else []),
                         "Выбранная запись передана полностью; другие проекты — как карта названий, описаний, ОР и часов."],
        ))
    for material in request.materials:
        registry.append(SourceRecord(
            id=material.id, name=material.name, role=material.role, sha256=material.sha256,
            read_status=material.read_status, extracted_count=len(material.passages),
            selected_count=sum(p.id in selected for p in material.passages),
            unread_locations=material.unread_locations, limitations=material.warnings,
        ))
    return registry


def check_sources(request: StartRequest, evidence: list[Evidence], registry: list[SourceRecord]) -> list[Finding]:
    findings = []
    ids = [e.id for e in evidence]
    if len(ids) != len(set(ids)):
        findings.append(system_finding("Повторяются идентификаторы источников", "Перезагрузите материалы с корректной разметкой."))
    for source in registry:
        if source.read_status == "failed" or (source.id not in {"form", "curriculum"} and not source.extracted_count):
            findings.append(system_finding(f"Не прочитан файл: {source.name}", "Загрузите читаемую версию либо явно удалите файл из комплекта.", "materials"))
        elif source.role in {"requirement", "customer"} and (source.read_status in {"partial", "legacy_unknown"} or source.extracted_count != source.selected_count):
            findings.append(system_finding(f"Обязательный источник прочитан не полностью: {source.name}", "Перезагрузите файл или приложите полную текстовую расшифровку.", "materials"))
    return findings


def check_input_card(card: InputCard, request: StartRequest, evidence: list[Evidence]) -> list[Finding]:
    findings = []
    allowed = {e.id for e in evidence}
    if {claim.area for claim in card.claims} != AREAS:
        findings.append(system_finding("В карточке раскрыты не все разделы входных данных", "Повторите разбор: нужны ОР, аудитория, ограничения, программа и материалы."))
    # Numbering follows nonempty form lines, exactly as in the designer contract.
    required = set(range(1, len([s for s in request.brief.outcomes.splitlines() if s.strip()]) + 1))
    covered = {n for c in card.claims if c.area == "outcomes" and c.kind == "given" for n in c.outcome_numbers}
    if covered != required:
        findings.append(system_finding("Карточка не подтверждает все ОР выбранного проекта", "Сохраните все результаты из формы с их номерами и основаниями.", "outcomes"))
    for claim in card.claims:
        if set(claim.evidence_ids) - allowed or (claim.kind in {"given", "conflict"} and not claim.evidence_ids):
            bad = sorted(set(claim.evidence_ids) - allowed)
            findings.append(system_finding("Утверждение в карточке не имеет проверяемого основания", f"{claim.text} Неверные ссылки: {bad or 'нет конкретного источника'}"))
        if claim.area == "outcomes" and claim.kind == "given" and "form.outcomes" not in claim.evidence_ids:
            findings.append(system_finding("ОР карточки не связан с текущей формой", "Укажите form.outcomes как основание результатов этого проекта.", "outcomes"))
        if claim.kind == "conflict":
            findings.append(system_finding("Входные требования противоречат друг другу", claim.text, "inputs"))
    for question in card.questions:
        if question.blocking:
            findings.append(system_finding(question.question, question.why, "inputs"))
    unknown = sorted({item.evidence_id for item in card.off_topic} - allowed)
    if unknown:
        findings.append(system_finding("Посторонний фрагмент указан без проверяемого места", f"Неверные ссылки: {unknown}"))
    findings.extend(check_program_place(card, request, allowed))
    return findings


def check_program_place(card: InputCard, request: StartRequest, allowed: set[str]) -> list[Finding]:
    """The block goals are placed one by one on real projects of the block; the reasoning cites real sources."""
    findings: list[Finding] = []
    m = program_map(request)
    goals = m["block_goal_items"] if m else []
    assert isinstance(goals, list)
    if m and goals:
        numbers = [g.number for g in card.block_goals]
        expected = list(range(1, len(goals) + 1))
        if sorted(numbers) != expected:
            findings.append(system_finding("Цели блока разложены не все или не по одному разу",
                                           f"Нужны номера 1–{len(goals)}, каждый один раз; сейчас: {sorted(numbers) or 'нет раскладки'}."))
        projects = m["block_projects"]
        assert isinstance(projects, list)
        titles = {p["title"] for p in projects if not p["current"]}
        wrong = [g.number for g in card.block_goals if g.owner in {"previous", "later"} and g.project not in titles]
        if wrong:
            findings.append(system_finding("Цель блока отдана проекту, которого нет в блоке",
                                           f"Цели {wrong}: название проекта берите точно из block_projects."))
    row = next((r for r in request.curriculum.rows if r.id == request.selected_row_id), None) if request.curriculum else None
    skills = (row.skills if row else "").casefold()
    invented = [s.skill for s in card.doubtful_skills if s.skill.casefold() not in skills]
    if invented:
        findings.append(system_finding("Сомнительный навык не найден в колонке навыков УП", f"Навыки: {invented}"))
    for point in card.program_reasoning:
        if set(point.evidence_ids) - allowed or (point.kind == "given" and not point.evidence_ids):
            findings.append(system_finding("Рассуждение о месте в программе без проверяемого основания", point.fact))
    findings.extend(check_learner_path(card, request, allowed))
    return findings


MAX_SKILLS_COMPARED = 50


def check_skill_names(request: StartRequest) -> list[Finding]:
    """The skills of the brief against the platform's catalogue, when the plan came with one (a passport workbook):
    the platform does not accept a project with a skill it does not have. Code names the stranger and the catalogue
    names closest to it; it replaces nothing."""
    catalog = request.curriculum.skill_catalog if request.curriculum else []
    names = skills.names_of(request.brief.skills)
    if not catalog or not names:
        return []
    # A project has a handful of skills: a text of hundreds of «skills» is not looked up name by name.
    strangers = skills.unknown(names[:MAX_SKILLS_COMPARED], catalog)
    if not strangers:
        return []
    return [system_finding("Навыки задания, которых нет в справочнике платформы: " + "; ".join(strangers),
                           "Замените название на справочное или уберите навык: платформа не примет проект с навыком не из справочника.",
                           rule="constraints")]


def check_learner_path(card: InputCard, request: StartRequest, allowed: set[str]) -> list[Finding]:
    """What the next projects need is tied to real later projects of the plan and to real outcomes of this one."""
    path = card.learner_path
    m = program_map(request)
    learner = m.get("learner") if m else None
    if not path or not isinstance(learner, dict):
        return []
    # A result is handed on to a project: an exam ahead is a check, not something to prepare a deliverable for.
    later = {str(r["title"]) for r in learner["later"] if r.get("kind") != "exam"}
    exams = {str(r["title"]) for r in learner["later"] if r.get("kind") == "exam"} - later
    earlier = {str(r["title"]) for r in learner["before"]}
    outcomes = len([x for x in request.brief.outcomes.splitlines() if x.strip()])
    findings: list[Finding] = []
    for need in path.next_needs:
        # A title can repeat in a plan («Публичные выступления» twice): a later project with it is a later project.
        if need.project in later:
            pass
        elif need.project in exams:
            findings.append(system_finding("«Понадобится дальше» ссылается на экзамен, а не на проект",
                                           f"«{need.project}» — экзамен: ему результат проекта не передаётся."))
        elif need.project in earlier:
            findings.append(system_finding("«Понадобится дальше» ссылается на проект, который идёт раньше",
                                           f"«{need.project}» стоит в УП до этого проекта."))
        else:
            findings.append(system_finding("«Понадобится дальше» ссылается на проект, которого нет в УП после этого",
                                           f"«{need.project}»: название берите точно из learner.later."))
        wrong = [n for n in need.outcome_numbers if not 1 <= n <= outcomes]
        if wrong or set(need.evidence_ids) - allowed:
            findings.append(system_finding("«Понадобится дальше» с неверными номерами ОР или ссылками",
                                           f"«{need.project}»: ОР {wrong or '—'}, ссылки {sorted(set(need.evidence_ids) - allowed) or '—'}."))
    return findings


def concept_locations(concept: Concept) -> set[str]:
    # Explicit addresses make each critique navigable and prevent invented task references.
    locations = set(Concept.model_fields)
    locations.update(f"tasks.{i}" for i in range(1, len(concept.tasks) + 1))
    locations.update(f"decisions.{i}" for i in range(1, len(concept.decisions) + 1))
    return locations


def input_review_findings(run: Run) -> list[Finding]:
    """Demo findings remain visible, including any sources that could not be read."""
    registry = source_registry(run.request, run.evidence)
    findings = check_sources(run.request, run.evidence, registry)
    findings.extend(check_skill_names(run.request))
    if run.input_card:
        # Keep exact diagnostics for the model and methodologist; only readiness changes.
        findings.extend(f.model_copy(update={"severity": "warning"})
                        for f in check_input_card(run.input_card, run.request, run.evidence))
    return [f.model_copy(update={"severity": "warning"}) for f in findings]


def apply_demo_policy(run: Run) -> None:
    """Assess quality without blocking a demonstration; never erase the diagnostics."""
    run.demo_mode = True
    for finding in run.input_findings:
        finding.severity = "warning"
    if run.input_card:
        for question in run.input_card.questions:
            question.blocking = False
    if run.concept:
        for question in run.concept.questions:
            question.blocking = False
    if run.review:
        for finding in run.review.findings:
            finding.severity = "warning"


def check_review(review: Review, run: Run) -> list[Finding]:
    """Reject missing coverage and ungrounded conclusions, without claiming semantic truth."""
    if not run.concept:
        return [system_finding("Нет замысла для проверки", "Повторите подготовку.")]
    findings = []
    allowed = {e.id for e in run.evidence}
    locations = concept_locations(run.concept)
    expected = set(range(1, len([s for s in run.request.brief.outcomes.splitlines() if s.strip()]) + 1))
    outcome_ids = [c.outcome_number for c in review.outcomes]
    if set(outcome_ids) != expected or len(outcome_ids) != len(expected):
        findings.append(system_finding("Проверяющий рассмотрел не все ОР либо повторил один из них", "Повторите проверку каждого результата отдельно.", "outcomes"))
    rule_ids = [c.rule_id for c in review.checks]
    if set(rule_ids) != RULES or len(rule_ids) != len(RULES):
        findings.append(system_finding("Проверяющий пропустил обязательное направление проверки", "Повторите проверку всех шести направлений."))
    evidence_items: list[Finding | CriterionCheck | OutcomeCheck] = [*review.findings, *review.checks, *review.outcomes]
    for item in evidence_items:
        if set(item.evidence_ids) - allowed:
            findings.append(system_finding("Проверяющий сослался на отсутствующий источник", f"Недопустимые evidence_ids: {sorted(set(item.evidence_ids) - allowed)}. Используйте только allowed_evidence_ids."))
    location_items: list[Finding | CriterionCheck] = [*review.findings, *review.checks]
    for item in location_items:
        if set(item.concept_locations) - locations:
            findings.append(system_finding("Проверяющий сослался на отсутствующее место в замысле", f"Недопустимые concept_locations: {sorted(set(item.concept_locations) - locations)}. Используйте только переданный список адресов."))
    for item in review.findings:
        if not item.rule_id or not item.concept_locations or not item.consequence or not item.evidence_ids:
            findings.append(system_finding("Замечание не содержит основания, места или последствий", "Повторите проверку; замечание должно быть проверяемым."))
    for item in review.outcomes:
        tasks = [run.concept.tasks[n - 1] for n in item.task_numbers if 1 <= n <= len(run.concept.tasks)]
        valid = len(tasks) == len(item.task_numbers) and len(set(item.task_numbers)) == len(tasks)
        valid = valid and all(item.outcome_number in t.outcome_numbers and not t.optional for t in tasks)
        if not valid or "form.outcomes" not in item.evidence_ids:
            findings.append(system_finding(f"Неверная связь с заданием или источником у ОР {item.outcome_number}", "Проверяющий должен ссылаться на основные задания этого результата и form.outcomes.", "outcomes"))
        if item.status == "confirmed" and (not tasks or any(not t.action.strip() or not t.deliverable.strip() or not t.peer_check.strip() for t in tasks)):
            findings.append(system_finding(f"ОР {item.outcome_number} подтверждён без действия, сдачи и проверки", "Добавьте наблюдаемую цепочку результата.", "outcomes"))
        if item.status in {"deferred", "not_applicable"}:
            findings.append(system_finding(f"Проверку обязательного ОР {item.outcome_number} нельзя пропустить", "Проверьте замысел сейчас; фактическое обучение проверяется на следующем этапе.", "outcomes"))
    # A problem cannot disappear merely because the model omitted it from findings.
    for rule, status, explanation in [(c.rule_id, c.status, c.explanation) for c in review.checks] + [
        ("outcomes", c.status, c.explanation) for c in review.outcomes
    ]:
        if status in {"problem", "needs_information"} and not any(f.rule_id == rule and f.severity == "blocker" for f in review.findings):
            findings.append(system_finding("Есть неразрешённая проблема в заключении проверяющего", explanation, rule))
    for item in review.checks:
        if item.status == "confirmed" and (not item.evidence_ids or not item.concept_locations):
            findings.append(system_finding("Положительное заключение не подкреплено источником и местом в замысле", item.explanation, item.rule_id))
        if item.rule_id != "materials" and item.status in {"deferred", "not_applicable"}:
            findings.append(system_finding("Обязательное направление проверки пропущено", item.explanation, item.rule_id))
    return findings


def unresolved_blockers(run: Run) -> list[Finding]:
    latest = {d.finding_index: d.verdict for d in run.finding_decisions}
    dismissed = {i for i, verdict in latest.items() if verdict == "dismissed"}
    if not run.review:
        return []
    return [f for i, f in enumerate(run.review.findings) if f.severity == "blocker" and (f.origin == "system" or i not in dismissed)]


def reconcile_review(review: Review, run: Run) -> None:
    """Reconcile observable structural contradictions, not semantic teaching quality."""
    if not run.concept:
        return
    allowed = {e.id for e in run.evidence}
    addresses = concept_locations(run.concept)
    expected = set(range(1, len([s for s in run.request.brief.outcomes.splitlines() if s.strip()]) + 1))
    for outcome in review.outcomes:
        tasks = [run.concept.tasks[n - 1] for n in outcome.task_numbers if 1 <= n <= len(run.concept.tasks)]
        linked = bool(tasks) and len(tasks) == len(set(outcome.task_numbers)) == len(outcome.task_numbers)
        linked = linked and all(outcome.outcome_number in t.outcome_numbers and not t.optional for t in tasks)
        grounded = "form.outcomes" in outcome.evidence_ids and set(outcome.evidence_ids) <= allowed
        comparison = all(s.strip() for s in (outcome.required_action, outcome.planned_action, outcome.observable_evidence))
        unique = sum(c.outcome_number == outcome.outcome_number for c in review.outcomes) == 1
        # A positive label cannot coexist with an explicit gap or broken traceability.
        if outcome.status == "confirmed" and (not linked or not grounded or not comparison or not unique or outcome.gap.strip()):
            reason = outcome.gap.strip() or "Не подтверждена связка ОР, основного задания, источника и наблюдения пира."
            outcome.status = "needs_information"
            outcome.gap = reason
            outcome.explanation = f"Соответствие не подтверждено: {reason}"
            review.findings.append(Finding(severity="warning", origin="system", rule_id="outcomes",
                                           message=f"ОР {outcome.outcome_number}: положительный статус требует уточнения",
                                           suggestion=reason, evidence_ids=["form.outcomes"] if "form.outcomes" in allowed else [],
                                           concept_locations=[f"tasks.{n}" for n in outcome.task_numbers if f"tasks.{n}" in addresses],
                                           consequence="Общий вывод не должен подтверждать этот результат без основания."))
    confirmed = {o.outcome_number for o in review.outcomes if o.status == "confirmed"} & expected
    pending = expected - confirmed
    for check in review.checks:
        relevant = [f.message for f in review.findings if f.rule_id == check.rule_id and f.severity == "blocker"]
        reasons = relevant[:3]
        if check.rule_id == "outcomes" and pending:
            reasons = [f"Не подтверждены ОР: {', '.join(map(str, sorted(pending)))}.", *reasons]
        if not check.evidence_ids or set(check.evidence_ids) - allowed or not check.concept_locations or set(check.concept_locations) - addresses:
            reasons.append("Уточнить основания и адреса в замысле.")
        if check.status == "confirmed" and reasons:
            check.status = "needs_information"
            check.explanation = " ".join(reasons)
    # Original model wording remains in review_attempts; the displayed summary uses actual statuses.
    labels = {"outcomes": "ОР", "audience": "подготовка участника", "program": "контекст программы",
              "constraints": "условия", "independence": "самостоятельность и P2P", "materials": "материалы"}
    attention = [labels[c.rule_id] for c in review.checks if c.status in {"problem", "needs_information"}]
    lines = [f"На уровне замысла подтверждено ОР: {len(confirmed)} из {len(expected)}."]
    if pending:
        lines.append(f"Не подтверждены ОР: {', '.join(map(str, sorted(pending)))}.")
    if attention:
        lines.append(f"Нужно обсудить: {', '.join(attention)}.")
    if any(c.rule_id == "materials" and c.status == "deferred" for c in review.checks):
        lines.append("Готовность будущих материалов проверяется на следующем этапе.")
    lines.append(f"Замечаний для обсуждения: {len(review.findings)}. В демонстрационном режиме они не блокируют согласование.")
    review.summary = " ".join(lines)


def revision_changes(previous: Run, request: StartRequest) -> list[RevisionChange]:
    """Keep the approved snapshot immutable; a child run records actual input differences."""
    before = previous.request.model_dump(mode="json", exclude={"request_id", "parent_run_id"})
    after = request.model_dump(mode="json", exclude={"request_id", "parent_run_id"})
    changes = []
    for key in sorted(before["brief"]):
        # How the text is told is not an input of the concept (models.PRESENTATION): not a difference for its models.
        if key not in PRESENTATION and before["brief"][key] != after["brief"][key]:
            changes.append(RevisionChange(location=f"brief.{key}", before=str(before["brief"][key]), after=str(after["brief"][key])))
    for key in ("curriculum", "selected_row_id", "materials"):
        if before[key] != after[key]:
            changes.append(RevisionChange(location=key, before=json.dumps(before[key], ensure_ascii=False), after=json.dumps(after[key], ensure_ascii=False)))
    return changes


def append_validation_export(run: Run) -> str:
    lines = ["## Понимание входных данных"]
    if run.input_card:
        lines.append(run.input_card.summary)
        for claim in run.input_card.claims:
            lines.append(f"- [{claim.kind}] {claim.text}\n  Основания: {', '.join(claim.evidence_ids) or 'Не заданы'}")
        lines.extend(f"- Вопрос: {q.question} — {q.why}" for q in run.input_card.questions)
    lines.extend(f"- Нужно уточнить: {f.message} — {f.suggestion}" for f in run.input_findings)
    lines.append("## Реестр источников")
    for source in run.source_registry:
        lines.append(f"- {source.name}: {source.read_status}; передано {source.selected_count} из {source.extracted_count}; SHA-256 {source.sha256}")
        lines.extend(f"  - {s}" for s in source.unread_locations + source.limitations)
    if run.review:
        lines.append("## Подтверждение образовательных результатов")
        for outcome in run.review.outcomes:
            lines.append(f"- ОР {outcome.outcome_number}: {outcome.status}; задания {outcome.task_numbers}. {outcome.explanation}. Источники: {', '.join(outcome.evidence_ids)}")
            if outcome.required_action:
                lines.append(f"  Требуется: {outcome.required_action}\n  Запланировано: {outcome.planned_action}\n  Что наблюдает пир: {outcome.observable_evidence}\n  Пробел: {outcome.gap or 'Не указан'}")
        lines.append("## Направления проверки")
        lines.extend(f"- {c.rule_id}: {c.status}. {c.explanation}. Источники: {', '.join(c.evidence_ids)}; места: {', '.join(c.concept_locations)}" for c in run.review.checks)
        for i, finding in enumerate(run.review.findings):
            lines.append(f"- Замечание {i + 1}: правило {finding.rule_id}; основания {finding.evidence_ids}; места {finding.concept_locations}; последствия: {finding.consequence}")
    if run.request.parent_run_id:
        lines.extend(["## Изменения версии", f"Предыдущий запуск: {run.request.parent_run_id}"])
        lines.extend(f"- {c.location}\n  Было: {c.before}\n  Стало: {c.after}" for c in run.changes)
    if run.finding_decisions:
        lines.append("## Решения методолога по замечаниям")
        lines.extend(f"- Замечание {d.finding_index + 1}: {d.verdict}. {d.reason} ({d.at})" for d in run.finding_decisions)
    lines.append(f"Входы согласованы: {run.input_approved_at or 'нет'}. Замысел согласован: {run.approved_at or 'нет'}.")
    return "\n\n".join(lines)
