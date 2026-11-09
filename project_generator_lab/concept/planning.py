"""Evidence selection, bounded design workflow and deterministic completion gates."""

import hashlib
import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal

from ..core.llm import ModelClient
from . import skills
from .collaboration import resolved
from .input_semantics import input_semantics, program_map, program_position
from .models import (
    Concept,
    CurriculumRow,
    Event,
    Evidence,
    Finding,
    GroundedReview,
    InputCard,
    Review,
    Run,
    StartRequest,
    considered,
    loose,
)
from .source_policy import effective_request
from .validation import (
    append_validation_export,
    apply_demo_policy,
    check_input_card,
    check_review,
    concept_locations,
    input_review_findings,
    reconcile_review,
    source_registry,
)
from .workload import budget as workload_budget
from .workload import check as workload_check
from .workload import overflow

PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = "1.2"
# Rule sets are frozen per run like prompts: a resumed run keeps the rules it started with.
RULES = Path(__file__).parent / "config"
RULE_FILES = {"program_rules": "program_rules.md", "platform": "platform.md"}
# The rules a check appeared in: every run from that version on gets it. A version list written by hand silently
# dropped both checks when the rules moved to 0.9 (audit 30.09.2026) — a comparison cannot forget a new version.
WORKLOAD_SINCE = "0.4"
RECONCILE_SINCE = "0.3"
# Plan rows go to the models once, in curriculum_map, from the rules that say so (1.0); a run resumed with older frozen
# rules keeps getting them in evidence as well.
ROWS_ONCE_SINCE = "1.0"
# The designer says which skills of the project each task practises, and code divides the skills between the tasks.
SKILLS_SINCE = "1.1"
# On the scale of a skill's weight, three is «the task practises it»; below that the task only touches it.
PRACTISED = 3
# How far the part of the work a concept gives a skill may be from the plan's share before it is said: ten percent
# points. The difference is rounded to a hundredth of a point before it is compared — 0.3 − 0.2 is not 0.1 in a float.
DRIFT = 10
# From this share of the project on, a skill of the plan has to be practised by some main task, not only touched.
# Below it the demand cannot always be met together with DRIFT: a weight of three pushes a small skill past the plan.
NOTABLE = 0.3
MAP_FIELDS = {"id", "block", "block_goals", "title", "description", "outcomes", "skills", "tools", "software", "hours"}


def map_row(row: CurriculumRow) -> dict[str, object]:
    """A plan row as the models get it in the map of the programme. The project's code and the mark of an exam are
    added only where the plan has them: a plan without them reads exactly as before."""
    return {**row.model_dump(include=MAP_FIELDS), **({"code": row.code} if row.code else {}),
            **({"kind": "exam"} if row.kind == "exam" else {})}


def since(version: str, first: str) -> bool:
    """Rules «0.10» come after «0.9»: versions compare as numbers, not as text."""
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split(".") if x.isdigit())
    return parts(version) >= parts(first)


def fingerprint(request: StartRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"request_id"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def build_evidence(request: StartRequest) -> tuple[list[Evidence], list[str]]:
    request = effective_request(request)
    evidence: list[Evidence] = []
    warnings: list[str] = []
    for key, value in request.brief.for_concept().items():
        if value is not None and value != "":
            evidence.append(Evidence(id=f"form.{key}", source="Параметры методолога", location=key, role="requirement", text=str(value)))
    if request.curriculum:
        for row in request.curriculum.rows:
            # Every row visible in the program map is a valid, inspectable source.
            content = row.raw if row.id == request.selected_row_id else {k: v for k, v in map_row(row).items() if k != "id"}
            evidence.append(Evidence(id=row.id, source=request.curriculum.filename, location=f"Запись {row.row_number}", role="curriculum", text=json.dumps(content, ensure_ascii=False)))
    query = set(re.findall(r"\w{3,}", f"{request.brief.title} {request.brief.description} {request.brief.outcomes}".casefold()))
    for material in request.materials:
        # The methodologist's «не учитывать» comes first: excluded text is not ranked, selected or shown to a model.
        kept, notes = considered(material, request.brief.excluded_sources)
        warnings.extend(notes)
        # Keep all required passages. For references, rank exact token overlap without pretending this is semantic search.
        passages = kept
        if material.role not in {"requirement", "customer"}:
            passages = sorted(passages, key=lambda p: -len(query.intersection(re.findall(r"\w{3,}", p.text.casefold()))))[:10]
        if len(passages) < len(kept):
            warnings.append(f"{material.name}: отобрано {len(passages)} из {len(kept)} фрагментов по словам из задачи. Остальные не переданы модели.")
        warnings.extend(f"{material.name}: {message}" for message in material.warnings)
        evidence.extend(Evidence(id=p.id, source=material.name, location=p.location, role=material.role, text=p.text) for p in passages)
    if sum(len(e.text) for e in evidence) > 110000:
        raise ValueError("Отобранный контекст больше 110 000 символов. Уточните комплект материалов; обязательные требования не были обрезаны.")
    return evidence, warnings


def context_payload(run: Run) -> dict[str, object]:
    request = effective_request(run.request)
    curriculum: list[dict[str, object]] = []
    if request.curriculum:
        # Keep each project's outcomes in the map; do not silently crop distant dependencies.
        # Skills and tools too: what the participant has already used is read from them (since 0.9).
        curriculum = [map_row(r) for r in request.curriculum.rows]
    payload: dict[str, object] = {
        "resolved_questions": [a.model_dump() for a in request.resolved_questions],
        "source_policy": request.brief.curriculum_policy,
        "brief": request.brief.for_concept(), "selected_row_id": request.selected_row_id,
        "input_semantics": input_semantics(request),
        "curriculum_map": curriculum, "evidence": model_evidence(run),
        "coverage_warnings": run.context_warnings,
        "source_registry": [s.model_dump() for s in run.source_registry],
        "input_card": run.input_card.model_dump() if run.input_card else None,
        "input_approved_at": run.input_approved_at,
        "input_warnings": [f.model_dump() for f in run.input_findings],
        "input_review_policy": "Демонстрационный режим на всех этапах: замечания не останавливают работу и не подтверждают спорные факты. Непрочитанные источники остаются неизвестными; не выдумывай их содержание. Условия без основания оставляй вопросами или явными предложениями. Используй только allowed_evidence_ids.",
        "revision_changes": [c.location for c in run.changes],
        "previous_concept": as_given(run.baseline_concept, run) if run.baseline_concept else None,
        "previous_approved_at": run.baseline_approved_at,
        "allowed_evidence_ids": [e.id for e in run.evidence],
        # Fixed programme rules and platform limits written by methodologists; the model applies, never edits them.
        "fixed_rules": {name: run.prompt_snapshots[name] for name in RULE_FILES if name in run.prompt_snapshots},
        # Computed by code and placed last: current state the model must not recompute or override.
        "computed_state": {
            "program_position": program_position(request),
            # Block neighbours, hours and repeated skills, counted from the plan: the facts behind project_role.
            "program_map": compact(program_map(request)),
            "workload_budget": (run.workload_budget or workload_budget(request.brief)).model_dump(),
            # Said only when it is so: the methodologist kept more hours than a project holds, and the tasks are longer.
            **({"hours_kept": "Методолог оставил трудоёмкость больше, чем помещается в проект по нормам: основных заданий "
                              "столько, сколько позволяет workload_budget.main_tasks_max, каждое длиннее верхней нормы; часы "
                              "распредели по объёму работы."} if run.hours_kept else {}),
            "learner_term": request.brief.learner_term or "участник",
        },
    }
    project = skills.planned(request)
    # For the designer and the reviewer: the interpreter's rules do not speak of it, and it reads the form itself.
    if since(run.prompt_version, SKILLS_SINCE) and project.names and run.input_card:
        # The names as code compares them, and the shares the plan states for them — so that a task names a skill
        # exactly and the designer sees which skills the plan keeps for which part. The bonus part is given only when
        # there can be one: the plan states it and the form allows bonus tasks.
        stated = project.stated
        state = payload["computed_state"]
        assert isinstance(state, dict)
        state["project_skills"] = {"names": project.names, **({"main": stated.main.shares} if stated and stated.main.shares else {}),
                                   **({"bonus": stated.bonus.shares} if stated and stated.bonus.shares and request.brief.bonus else {})}
    if len(json.dumps(payload, ensure_ascii=False)) > 180000:
        raise ValueError("Карта программы и материалы слишком велики для одного запуска прототипа. Выделите программу меньшего объёма.")
    return payload


IN_MAP = "Строка УП: её поля — в curriculum_map под тем же id."


def as_given(concept: Concept, run: Run) -> dict[str, object]:
    """A concept as the models of this run get it. Rules before 1.1 know nothing of the skills of a task: a run that
    keeps them gets the concept without that field, as it always did."""
    dump = concept.model_dump()
    if not since(run.prompt_version, SKILLS_SINCE):
        for task in dump["tasks"]:
            del task["skills"]
    return dump


def model_evidence(run: Run) -> list[dict[str, object]]:
    """Evidence as the models get it. A plan row other than the project's own carries the same fields as its
    curriculum_map entry: they are sent once, in the map, and the row stays here by id, so it can still be cited
    (audit 30.09.2026: every row of the plan went to each model twice). The project's own row keeps its full text:
    it has columns the map leaves out. Run.evidence itself stays complete — checks read it."""
    selected, once = run.request.selected_row_id, since(run.prompt_version, ROWS_ONCE_SINCE)
    return [{**e.model_dump(), "text": IN_MAP} if once and e.role == "curriculum" and e.id != selected else e.model_dump()
            for e in run.evidence]


def named(entry: dict[str, object]) -> dict[str, object]:
    """A project of the path by id and title; an exam says that it is one."""
    return {"id": entry["id"], "title": entry["title"], **({"kind": "exam"} if entry.get("kind") == "exam" else {})}


def compact(m: dict[str, object] | None) -> dict[str, object] | None:
    """The map as the models get it: the earlier projects' outcomes are already in curriculum_map, so the learner's
    path names those projects without repeating them. Twice the same text cost 25k characters on a 24-project plan
    and pushed a 48-project plan over the limit (audit 30.09.2026); the chat card keeps the full list."""
    learner = m.get("learner") if m else None
    if not m or not isinstance(learner, dict):
        return m
    # Outcomes, skills and tools of each project are in curriculum_map: here only what code adds — which projects come
    # before and after, and the tool counts.
    return {**m, "learner": {
        "before": [named(p) for p in learner["before"]],
        "later": [named(p) for p in learner["later"]],
        "tools_before": [{"tool": t["tool"], "projects": len(t["projects"]), "first": t["projects"][0]} for t in learner["tools_before"]],
        "tools_here": learner["tools_here"], "tools_ahead": learner["tools_ahead"],
        "meaning": f"{learner['meaning']} ОР, навыки и инструменты каждого проекта — в curriculum_map по id."}}


def with_extra(payload: dict[str, object], **extra: object) -> dict[str, object]:
    """Add call-specific data while keeping the computed state last, where the model reads it as current."""
    state = payload["computed_state"]
    return {**{k: v for k, v in payload.items() if k != "computed_state"}, **extra, "computed_state": state}


def check_block_goals(concept: Concept, request: StartRequest, card: InputCard | None) -> list[Finding]:
    """The goals the card gave to this project are closed by main tasks, like outcomes; goals of an earlier project are
    built on, not taught again. Numbers follow program_map.block_goal_items."""
    m = program_map(request)
    items = m["block_goal_items"] if m else []
    if not card or not card.block_goals or not isinstance(items, list):
        return []
    text = {int(g["number"]): str(g["text"]) for g in items}
    owner = {g.number: g for g in card.block_goals}
    findings: list[Finding] = []

    def block(message: str, suggestion: str) -> None:
        findings.append(Finding(severity="blocker", message=message, suggestion=suggestion))

    covered = {n for t in concept.tasks if not t.optional for n in t.block_goal_numbers}
    missing = [n for n, g in owner.items() if g.owner == "this" and n not in covered]
    if missing:
        block("Не закрыты цели блока, отданные этому проекту: " + "; ".join(f"«{text.get(n, n)}»" for n in missing),
              "Добавить действие и проверку в основное задание или согласовать, что цель закрывает другой проект блока.")
    for i, task in enumerate(concept.tasks, 1):
        unknown = [n for n in task.block_goal_numbers if n not in owner]
        if unknown:
            block(f"Задание {i} ссылается на несуществующие цели блока {unknown}", "Использовать номера целей из раскладки.")
        for n in task.block_goal_numbers:
            goal = owner.get(n)
            if goal and goal.owner == "previous":
                block(f"Задание {i} заново учит цель предыдущего проекта «{goal.project}»: «{text.get(n, n)}»",
                      "Опереться на результат предыдущего проекта, а не повторять его.")
    return findings


def percent(share: float) -> str:
    """A share as percent, as exact as shares are kept: 0.425 is «42,5», not «42» — what is shown is what is compared."""
    return skills.said(share * 100)  # six significant digits: what a float adds to the product is dropped


def check_task_skills(divided: skills.Divided, project: skills.Planned) -> list[Finding]:
    """The skills of the project against the tasks that practise them. The sums need no check — code counted them;
    what is checked is the judgement: a skill nobody practises, a task that practises nothing, a name that is not the
    project's."""
    findings: list[Finding] = []

    def say(severity: Literal["blocker", "warning"], message: str, suggestion: str) -> None:
        findings.append(Finding(severity=severity, message=message, suggestion=suggestion))

    def named(names: Iterable[str]) -> str:
        return ", ".join(f"«{name}»" for name in names)

    for number, unknown in divided.strangers.items():
        say("blocker", f"Задание {number} называет навыки не из навыков проекта: {named(unknown)}",
            "Использовать названия из поля «Навыки» буквально: замысел новых навыков не добавляет.")
    for number, again in divided.twice.items():
        say("blocker", f"Задание {number} называет один навык дважды, в разном написании: {named(again)} — учтён первый вес",
            "Оставить у навыка один вес.")
    stated = project.stated
    if project.differs:
        say("warning", "Доли навыков в проекте посчитаны из весов заданий, а не взяты из УП: в поле «Навыки» другой набор навыков, "
                       "чем в блоке проекта на вкладке развесовки",
            "Вернуть в поле «Навыки» набор из УП либо исправить блок проекта на вкладке развесовки паспорта.")
    main = divided.block.main
    for part, many, of_part, given in ((main, "Основные", "основная", stated.main.shares if stated else {}),
                                       (divided.block.bonus, "Бонусные", "бонусная", stated.bonus.shares if stated else {})):
        practised = list(dict.fromkeys(skill for task in part.tasks for skill in task.shares))
        required = list(given) if stated else project.names if part is main else []
        missing = [skill for skill in required if skill not in practised]
        if missing and part is main:
            # The main part is what every participant does: a skill of the project nobody earns there is not earned.
            say("blocker", f"Навыки проекта не отрабатывает ни одно основное задание: {named(missing)}",
                "Добавить действие и сдачу по навыку в задание либо убрать навык из поля «Навыки».")
        elif missing and part.tasks:
            say("warning", f"Навыки проекта не отрабатывает ни одно бонусное задание: {named(missing)}",
                "Добавить действие и сдачу по навыку в задание либо убрать навык из поля «Навыки».")
        elif missing:
            say("warning", f"В УП у проекта есть бонусная часть развесовки ({named(missing)}), а бонусных заданий в замысле нет",
                "Разрешить бонус в параметрах задания либо убрать строку Bonus all у проекта на вкладке развесовки паспорта.")
        if not part.tasks:
            continue
        outside = [skill for skill in practised if skill not in part.shares]
        if outside and given:
            say("warning", f"{many} задания отрабатывают навыки, у которых в этой части развесовки УП нет доли: {named(outside)}",
                "Убрать навык у заданий этой части или добавить его долю на вкладку развесовки паспорта.")
        elif outside:
            say("warning", f"Доля навыка в проекте меньше сотой и округлилась до нуля ({of_part} часть): {named(outside)}",
                "Убрать навык у задания или дать ему больший вес.")
        if given and not skills.whole(sum(given.values())):
            say("warning", f"Доли навыков проекта на вкладке развесовки ({of_part} часть) в сумме дают "
                           f"{skills.said(sum(given.values()))}, а не 1: платформа такой блок не примет",
                "Исправить строку all на вкладке развесовки паспорта.")
    # What the weights say of the main part and no sum shows (both seen in the first run on a model, 05.10.2026).
    # A skill every main task only touches is on the list and is not practised. With the plan's shares that is said
    # of a skill the plan makes a notable part of the project: one it gives a twentieth is rightly only touched.
    given = stated.main.shares if stated else {}
    touched = [skill for skill, weight in divided.peak.items() if weight < PRACTISED and (not given or round(given.get(skill, 0), 4) >= NOTABLE)]
    if touched:
        say("warning", f"Навыков проекта основные задания только касаются (вес ниже {PRACTISED} в каждом задании): " + ", ".join(
            f"«{skill}»" + (f" — доля в УП {skills.said(given[skill])}" if given else "") for skill in touched),
            "Сделать навык заметной частью хотя бы одного задания либо убрать его из поля «Навыки».")
    # With the plan's shares the plan is the yardstick: a concept that gives a skill another part of the work is
    # another project than the plan's — though the block is right, the plan's shares go into it.
    drifted = [f"«{skill}» — {percent(divided.weighed.get(skill, 0))}% при {percent(share)}% в УП" for skill, share in given.items()
               if skill in divided.peak and round(abs(divided.weighed.get(skill, 0) - share) * 10000) >= DRIFT * 100]
    if drifted:
        say("warning", "По весам и часам заданий навыки занимают другую часть работы проекта, чем в УП: " + "; ".join(drifted),
            "Проверить веса заданий или доли в строке all паспорта: в блок развесовки идут доли из УП.")
    if divided.idle:
        say("warning", "Задания, которые не отрабатывают ни одного навыка проекта: " + ", ".join(map(str, divided.idle))
            + ". За них участник не получит баллов навыков", "Указать навык, который отрабатывает задание, или объединить его с соседним.")
    return findings


def check_concept(concept: Concept, request: StartRequest, evidence: list[Evidence]) -> list[Finding]:
    findings: list[Finding] = []

    def block(message: str, suggestion: str) -> None:
        findings.append(Finding(severity="blocker", message=message, suggestion=suggestion))

    allowed = {e.id for e in evidence}
    used = {ref for decision in concept.decisions for ref in decision.evidence_ids}
    used.update(ref for task in concept.tasks for ref in task.evidence_ids)
    if used - allowed:
        block("В обоснованиях есть несуществующие источники", "Повторить подготовку с корректными ссылками на входные данные.")
    if any(d.kind in {"given", "conflict"} and not d.evidence_ids for d in concept.decisions):
        block("Факт или противоречие указаны без основания", "Указать источник либо обозначить вывод как предложение.")
    outcomes = [line for line in request.brief.outcomes.splitlines() if line.strip()]
    required = set(range(1, len(outcomes) + 1))
    covered = {n for task in concept.tasks if not task.optional for n in task.outcome_numbers}
    if required - covered:
        block(f"Не покрыты учебные результаты: {', '.join(map(str, sorted(required - covered)))}", "Добавить действие и проверку для каждого результата.")
    if any(n not in required for task in concept.tasks for n in task.outcome_numbers):
        block("Задание ссылается на отсутствующий учебный результат", "Использовать номера результатов из формы.")
    if request.brief.tasks_count and len([t for t in concept.tasks if not t.optional]) != request.brief.tasks_count:
        block("Число основных заданий отличается от заданного", "Согласовать число заданий с параметрами методолога.")
    if not request.brief.bonus and any(t.optional for t in concept.tasks):
        block("Добавлен не запрошенный бонус", "Убрать бонус либо разрешить его в параметрах.")
    # Bonus tasks are extra for faster learners: the project hours are filled by the main tasks only, as in
    # workload.check. Counting the bonus here flagged a plan that fills the hours exactly (audit 30.09.2026).
    if request.brief.hours and sum(t.hours for t in concept.tasks if not t.optional) > request.brief.hours + .01:
        block("План превышает заданную трудоёмкость", "Сократить объём работы либо уточнить трудоёмкость.")
    for question in concept.questions:
        if question.blocking:
            block(question.question, question.why)
    if any(d.kind == "conflict" for d in concept.decisions) and not any(q.blocking for q in concept.questions):
        block("Есть неразрешённое противоречие", "Уточнить противоречащие требования перед согласованием.")
    return findings


async def prepare(run: Run, save: Callable[[Run], None], client: ModelClient, reviewer: ModelClient | None = None) -> None:
    """Reviewer may be a different model family; without one the main client reviews."""
    stage = "context"
    review_client = reviewer or client

    def event(status: str, message: str) -> None:
        run.events.append(Event(stage=stage, status=status, message=message))
        save(run)

    try:
        # Freeze all prompts before the first call; resuming never changes agreed inputs.
        if not run.input_approved_at:
            run.prompt_version = PROMPT_VERSION
            run.prompt_snapshots = {name: (PROMPTS / f"{name}.md").read_text(encoding="utf-8") for name in ("interpreter", "designer", "reviewer")}
            run.prompt_snapshots.update({name: (RULES / file).read_text(encoding="utf-8") for name, file in RULE_FILES.items()})
            run.workload_budget = workload_budget(run.request.brief)
            event("running", "Проверяем чтение источников и собираем контекст всей программы.")
            run.source_registry = source_registry(run.request, [])
            save(run)
            run.evidence, run.context_warnings = build_evidence(run.request)
            run.source_registry = source_registry(run.request, run.evidence)
            run.input_findings = input_review_findings(run)
            payload = context_payload(run)
            card, metrics = await client.generate(InputCard, run.prompt_snapshots["interpreter"], json.dumps(payload, ensure_ascii=False))
            run.input_card = card
            run.input_attempts.append(card.model_copy(deep=True))
            issues = check_input_card(card, run.request, run.evidence)
            repairable = [f for f in issues if f.rule_id in {"integrity", "outcomes"}]
            if repairable:
                # One bounded repair of the output contract; never invent answers to unknown inputs.
                run.events.append(Event(stage="context", status="done", message="Первый разбор сохранён; исправляем неточные ссылки и полноту карточки.", **metrics.__dict__))
                save(run)
                event("running", "Одна повторная попытка уточнения ссылок карточки.")
                card, metrics = await client.generate(InputCard, run.prompt_snapshots["interpreter"], json.dumps(with_extra(payload, previous_card=card.model_dump(), repair_errors=[f.model_dump() for f in repairable], repair_instruction="Исправь только перечисленные ошибки ссылок и покрытия. IDs бери буквально из evidence, без wildcard. Сохрани неизвестные условия, вопросы и противоречия; не выдумывай ответы."), ensure_ascii=False))
                run.input_card = card
                run.input_attempts.append(card.model_copy(deep=True))
                issues = check_input_card(card, run.request, run.evidence)
            # A fragment the chat cannot point to is not offered for exclusion; input_attempts keep the model's own answer.
            texts = {e.id: e.text for e in run.evidence}
            located = []
            for item in card.off_topic:
                source = texts.get(item.evidence_id)
                # The chat cuts exactly this text later, so it must be found where the card says it is.
                if source is None:
                    continue
                if not (item.text and loose(item.text).search(source)):
                    if not loose(item.quote).search(source):
                        continue
                    item.text = item.quote
                located.append(item)
            card.off_topic = located
            # Keep model attempts for diagnosis, but do not ask an unchanged, already resolved question again.
            card.questions = [q for q in card.questions if not resolved(q.question, q.key, run.request.brief.model_dump(),
                                                                        run.request.resolved_questions)]
            unresolved_blocking = any(q.blocking for q in card.questions)
            run.input_findings = input_review_findings(run)
            apply_demo_policy(run)
            run.status = "input_review"
            run.events.append(Event(stage="context", status="done", message="Карточка входов подготовлена. Проверьте её перед созданием замысла.", **metrics.__dict__))
            save(run)
            # Demo policy clears blocking on the card itself; the model's original verdict is kept in the attempts.
            # Hours more than one project holds are the methodologist's to decide, whatever the card says: the run
            # waits for that at the inputs even when it was asked to go straight on (concept/route.py).
            if not (run.auto_continue and not unresolved_blocking) or (
                    not run.hours_kept and overflow(run.workload_budget) is not None):
                return
            # The methodologist already asked to go on; an important new question would still stop here.
            run.input_approved_at = run.events[-1].at
            run.status = "running"
            run.events.append(Event(stage="inputs_approval", status="done", message="Входы приняты: методолог попросил продолжить, важных вопросов в новом разборе нет."))
            save(run)
        if not run.input_card:
            raise ValueError("Сначала подготовьте карточку входов.")
        plan = run.workload_budget or workload_budget(run.request.brief)
        run.workload_budget = plan
        payload = context_payload(run)
        if run.concept and not run.review:
            # Resumed after an interruption at review: the finished design is kept, not regenerated.
            concept = run.concept
        else:
            stage = "design"
            event("running", "Проектируем итоговый результат, задания и способы проверки.")
            concept, metrics = await client.generate(Concept, run.prompt_snapshots["designer"], json.dumps(payload, ensure_ascii=False))
            if not since(run.prompt_version, SKILLS_SINCE):
                # The answer's schema lists the field for every run; rules that never asked for it get nothing kept of it.
                for task in concept.tasks:
                    task.skills = {}
            concept.questions = [q for q in concept.questions if not resolved(q.question, q.key, run.request.brief.model_dump(),
                                                                             run.request.resolved_questions)]
            run.concept = concept
            if run.baseline_concept:
                from .models import RevisionChange

                before, after = run.baseline_concept.model_dump(), concept.model_dump()
                run.changes.extend(RevisionChange(location=f"concept.{key}", before=json.dumps(before[key], ensure_ascii=False), after=json.dumps(after[key], ensure_ascii=False)) for key in before if before[key] != after[key])
            run.events.append(Event(stage=stage, status="done", message="Замысел подготовлен. Предложения отделены от заданных условий.", **metrics.__dict__))
            save(run)
        stage = "review"
        event("running", "Отдельный вызов модели проверяет требования, самостоятельность и выполнимость.")
        report, metrics = await review_client.generate(GroundedReview, run.prompt_snapshots["reviewer"], json.dumps(with_extra(payload, concept=as_given(concept, run), concept_locations=sorted(concept_locations(concept))), ensure_ascii=False))
        review = Review.model_validate(report.model_dump())
        run.review_attempts.append(review.model_copy(deep=True))
        review_issues = check_review(review, run)
        repairable = [f for f in review_issues if f.rule_id == "integrity"]
        if repairable:
            run.events.append(Event(stage=stage, status="done", message="Первое заключение сохранено; исправляем адреса источников и мест в замысле.", **metrics.__dict__))
            save(run)
            event("running", "Одна повторная попытка исправления ссылок проверяющего.")
            report, metrics = await review_client.generate(GroundedReview, run.prompt_snapshots["reviewer"], json.dumps(with_extra(payload, concept=as_given(concept, run), concept_locations=sorted(concept_locations(concept)), previous_review=review.model_dump(), repair_errors=[f.model_dump() for f in repairable], repair_instruction="Исправь только ссылки и полноту формата. evidence_ids только из allowed_evidence_ids; concept_locations только из списка concept_locations. Не путай эти списки, не добавляй поля audience или constraints в concept_locations. Сохрани найденные проблемы и статусы, не смягчай заключение ради прохождения проверки."), ensure_ascii=False))
            review = Review.model_validate(report.model_dump())
            run.review_attempts.append(review.model_copy(deep=True))
            review_issues = check_review(review, run)
        # Retain deterministic diagnostics, then apply the same demo policy to all findings.
        deterministic = check_concept(concept, run.request, run.evidence) + check_block_goals(concept, run.request, run.input_card)
        project = skills.planned(run.request)
        if since(run.prompt_version, SKILLS_SINCE) and project.names:
            # Skills are arithmetic too: the designer weighs, code divides, and what it divided is kept with the run.
            divided = skills.divide(concept, project)
            run.skill_block = divided.block
            deterministic.extend(check_task_skills(divided, project))
        for finding in deterministic:
            finding.origin = "system"
            finding.rule_id = "concept_integrity"
        # Hours are arithmetic: the calculator, not the reviewer, decides whether they add up.
        if since(run.prompt_version, WORKLOAD_SINCE):
            deterministic.extend(workload_check(concept, run.request.brief, plan))
        review.findings.extend(deterministic + review_issues)
        if since(run.prompt_version, RECONCILE_SINCE):
            reconcile_review(review, run)
        run.review = review
        apply_demo_policy(run)
        run.status = "review"
        run.events.append(Event(stage=stage, status="done", message="Проверка завершена. Решение о согласовании остаётся за методологом.", **metrics.__dict__))
        save(run)
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Не удалось завершить подготовку. Повторите запуск."
        event("failed", run.error)


def readable_export(run: Run) -> str:
    lines = [f"# Замысел проекта — {run.request.brief.title}", f"Статус: {run.status} · Версия правил: {run.prompt_version}", ""]
    if run.concept:
        c = run.concept
        for title, text in [("Контекст программы", c.program_summary), ("Место проекта", c.project_role), ("Итоговый результат", c.student_result)]:
            lines.extend([f"## {title}", text, ""])
        for title, items in [("Предполагаемая подготовка", c.prerequisites), ("Использование дальше", c.later_use), ("За пределами проекта", c.out_of_scope), ("Что подготовить к созданию контента", c.material_gaps)]:
            lines.extend([f"## {title}", *[f"- {item}" for item in items], ""])
        for i, task in enumerate(c.tasks, 1):
            lines.extend([f"## {i}. {task.title}", task.action, f"Результат: {task.deliverable}", f"Проверка: {task.peer_check}", f"Самостоятельность: {task.independent_choice}", f"Поддержка: {task.support}", f"Часы: {task.hours}" + (
                f" (чтение {task.workload.reading}, настройка {task.workload.setup}, выполнение {task.workload.doing}, "
                f"ошибки и повторы {task.workload.debugging}, сдача {task.workload.submission})" if task.workload else ""), ""])
        if run.skill_block:
            head, *rest = skills.table(run.skill_block)
            lines.extend(["## Развесовка навыков по заданиям", "Как на вкладке развесовки паспорта: строки блока переносятся без пересчёта.",
                          "\n".join("| " + " | ".join(line) + " |" for line in (head, ["---"] * len(head), *rest)), ""])
        lines.append("## Обоснования решений")
        for d in c.decisions:
            lines.extend([f"### {d.title} ({d.kind})", d.explanation, f"Основания: {', '.join(d.evidence_ids) or 'Предложение системы'}", ""])
        lines.extend(["## Вопросы", *[f"- {q.question} — {q.why}" for q in c.questions]])
    if run.review:
        lines.extend(["## Проверка", run.review.summary, *[f"- [{f.severity}] {f.message} — {f.suggestion}" for f in run.review.findings]])
    lines.extend(["", "## Охват материалов", *[f"- {w}" for w in run.context_warnings], "", "## Источники"])
    lines.extend(f"- {e.id}: {e.source}, {e.location}" for e in run.evidence)
    return "\n\n".join(lines) + "\n\n" + append_validation_export(run)
