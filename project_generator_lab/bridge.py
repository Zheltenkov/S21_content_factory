"""The one place that knows both modules: an approved concept run becomes a ConceptHandoff.

concept/ and content/ never import each other. To move the content generator elsewhere, or to plug in
another one, only this file and one configure() call in app.py change.
"""

from uuid import UUID

from .concept import runs
from .concept.input_semantics import program_map
from .concept.models import Run, considered
from .concept.skills import gaps as skill_gaps
from .concept.skills import names_of
from .concept.skills import table as skill_lines
from .content.handoff import MAX_GAPS, ConceptHandoff, HandoffUnavailable, PlannedTask, SourceText, TaskHours
from .evaluation.models import Context, Unit

LEVELS = {"zero": "нулевой", "basic": "базовый", "intermediate": "средний", "advanced": "продвинутый"}
STORY = {"none": "", "sjm": "практический (SJM)", "case": "рабочий кейс", "role_play": "ролевая ситуация",
         "project_scenario": "сценарий проекта", "story_arc": "сквозная история"}
RULES = ("program_rules", "platform")
# How much of an attached file the writers get. Requirements and the customer brief bind the content, so they
# get the most; the card shows what was cut, so a cropped requirement is visible, not silent.
LIMITS = {"requirement": 15000, "customer": 15000, "reviewer": 8000}
DEFAULT_LIMIT = 4000
KINDS = {"given": "задано", "proposal": "предложено", "conflict": "противоречие"}


def sources(run: Run) -> list[SourceText]:
    found = []
    for material in run.request.materials:
        # What the methodologist agreed not to consider never reaches the content writers either.
        passages, _ = considered(material, run.request.brief.excluded_sources)
        text = "\n\n".join(f"[{p.location}] {p.text}" for p in passages)
        limit = LIMITS.get(material.role, DEFAULT_LIMIT)
        found.append(SourceText(name=material.name, role=material.role, text=text[:limit], total_chars=len(text)))
    return found


def review_notes(run: Run) -> list[str]:
    """Reviewer findings the methodologist did not dismiss: they stay true for the content too."""
    if not run.review:
        return []
    dismissed = {d.finding_index for d in run.finding_decisions if d.verdict == "dismissed"}
    return [f.message + (f" Предложение: {f.suggestion}" if f.suggestion else "")
            for i, f in enumerate(run.review.findings) if i not in dismissed]


def program_context(run: Run) -> str:
    """The curriculum facts counted by code, as plain sentences for the writer."""
    m = program_map(run.request)
    if not m:
        return "Учебный план не загружен: место проекта в программе неизвестно."
    lines = [f"Блок «{m['block']}»: проект {m['position_in_block']} из {len(m['block_projects'])}"  # type: ignore[arg-type]
             f" (весь план: {m['position']} из {m['total']})."]
    for label, key in (("До него", "previous_project"), ("После него", "next_project")):
        neighbour = m[key]
        if isinstance(neighbour, dict):
            lines.append(f"{label}: «{neighbour['title']}» (блок «{neighbour['block']}»).")
    for label, key in (("Навыки, встречавшиеся раньше", "skills_practised_before"), ("Навыки впервые", "skills_new")):
        if m[key]:
            lines.append(f"{label}: {', '.join(m[key])}.")  # type: ignore[arg-type]
    later = m["skills_used_later"]
    if isinstance(later, list) and later:
        lines.append("Понадобятся дальше: " + "; ".join(f"{x['skill']} — «{x['projects'][0]}»" for x in later) + ".")
    lines.extend(path_lines(run))
    placed = placement(run)
    if placed:
        lines.extend(placed)
    elif m["block_goals"]:
        lines.append(f"Цели блока (на все его проекты): {m['block_goals']}")
    return "\n".join(lines)


def path_lines(run: Run) -> list[str]:
    """The participant's path as agreed at the inputs: what they bring, what is not taught again, what comes next."""
    path = run.input_card.learner_path if run.input_card else None
    if not path:
        return []
    lines = [f"До этого проекта (по УП): {path.before}"]
    if path.builds_on:
        lines.append("Опора из прошлых проектов, заново не учить: " + "; ".join(path.builds_on) + ".")
    lines += [f"Понадобится в «{n.project}»: {n.needs} Здесь: {n.teach_here}" for n in path.next_needs]
    return lines


def goal_texts(run: Run) -> dict[int, str]:
    m = program_map(run.request)
    items = m["block_goal_items"] if m else []
    return {int(g["number"]): str(g["text"]) for g in items} if isinstance(items, list) else {}


OWNERS = {"previous": "закрывает предыдущий проект", "later": "закрывает следующий проект", "shared": "сквозная для блока"}


def placement(run: Run) -> list[str]:
    """How the block goals were placed at the inputs, in words: what this project takes and what it leaves to others."""
    goals = goal_texts(run)
    card = run.input_card
    if not card or not card.block_goals:
        return []
    mine = [goals[g.number] for g in card.block_goals if g.owner == "this" and g.number in goals]
    others = [f"«{goals[g.number]}» — {OWNERS[g.owner]}" + (f" «{g.project}»" if g.project else "")
              for g in card.block_goals if g.owner in OWNERS and g.number in goals]
    lines = [f"Цели блока этого проекта: {'; '.join(mine)}."] if mine else []
    lines += [f"Не учить заново / оставить другим: {'; '.join(others)}."] if others else []
    lines += [f"{p.fact} → {p.so_what}" for p in card.program_reasoning]
    return lines


def evaluation_context(run_id: str) -> Context:
    """The evaluator receives complete permitted source text, never a canceled curriculum row."""
    import json

    from .concept.source_policy import effective_request

    run = runs.load_run(UUID(run_id))
    request = effective_request(run.request)
    units = [Unit(id="input:brief", title="Исходные требования", kind="inputs",
                  text=request.brief.model_dump_json(exclude={"generation", "collaboration_mode"}))]
    if request.curriculum:
        units.append(Unit(id="input:curriculum", title="Разрешённый контекст учебного плана", kind="inputs",
                          text=request.curriculum.model_dump_json()))
    if run.concept:
        units.append(Unit(id="input:concept", title="Согласованный замысел", kind="inputs", text=run.concept.model_dump_json()))
    limitations = []
    active_sources = {"form"}
    if request.curriculum:
        active_sources.add("curriculum")
    if not run.source_registry and (request.materials or request.curriculum):
        limitations.append("У старого запуска нет реестра чтения источников; полнота извлечения не подтверждена.")
    for i, material in enumerate(request.materials):
        passages, _ = considered(material, request.brief.excluded_sources)
        # Excluding every readable passage cancels the source. A failed unread source
        # remains active until explicitly removed; lack of passages is not an exclusion.
        if passages or not material.passages:
            active_sources.add(material.id)
        units.append(Unit(id=f"input:attachment:{i}", title=material.name, kind="inputs",
                          text="\n\n".join(f"[{p.location}] {p.text}" for p in passages)))
    for source in run.source_registry:
        if source.id in active_sources and source.read_status != "text_extracted":
            limitations.append(f"Источник «{source.name}»: чтение {source.read_status}; полнота не подтверждена.")
    units.append(Unit(id="input:registry", title="Реестр чтения источников", kind="inputs",
                      text=json.dumps([{**s.model_dump(), "used_in_check": s.id in active_sources}
                                       for s in run.source_registry], ensure_ascii=False)))
    return Context(units=units, limitations=limitations, curriculum_available=request.curriculum is not None, full_sources=True)


def handoff(run_id: str) -> ConceptHandoff:
    run = runs.load_run(UUID(run_id))
    if run.status != "approved" or not run.concept or not run.approved_at:
        raise HandoffUnavailable("Контент готовится по согласованному замыслу. Сначала согласуйте замысел.")
    brief, concept = run.request.brief, run.concept
    goals = goal_texts(run)
    outcomes = [line.strip() for line in brief.outcomes.splitlines() if line.strip()]
    return ConceptHandoff(
        generation=brief.generation, collaboration_mode=brief.collaboration_mode,
        run_id=run.id, approved_at=run.approved_at, concept_version=run.prompt_version,
        title=concept.title or brief.title, description=brief.description, outcomes=outcomes,
        audience=brief.audience, start_level=LEVELS.get(brief.start_level, "не указан"), start_skills=brief.start_skills,
        learner_term=brief.learner_term or "участник", hours=brief.hours, format=brief.format, group_size=brief.group_size,
        skills=brief.skills, skill_table=skill_lines(run.skill_block) if run.skill_block else [],
        skill_gaps=skill_gaps(run.skill_block, names_of(brief.skills))[:MAX_GAPS] if run.skill_block else [],
        tools=", ".join(x for x in (brief.tools, brief.software) if x),
        storytelling_type=STORY.get(brief.storytelling_type, brief.storytelling_type), storytelling=brief.storytelling,
        style_profile=brief.style_profile, series_story=brief.series_story, repository=brief.repository.strip(),
        student_result=concept.student_result or brief.student_result, checking=brief.checking,
        output_package=brief.output_package, constraints=brief.constraints, references=brief.references, notes=brief.notes,
        program_context=program_context(run), program_summary=concept.program_summary, project_role=concept.project_role,
        prerequisites=concept.prerequisites, later_use=concept.later_use, out_of_scope=concept.out_of_scope,
        material_gaps=concept.material_gaps,
        program_outcomes=brief.program_outcomes or brief.program, customer_requirements=brief.customer_requirements,
        diagrams=brief.diagrams, tables=brief.tables, formulas=brief.formulas,
        tasks=[PlannedTask(number=i, title=t.title, action=t.action, deliverable=t.deliverable, peer_check=t.peer_check,
                           independent_choice=t.independent_choice, support=t.support, hours=t.hours,
                           outcome_numbers=t.outcome_numbers, optional=t.optional, program_link=t.program_link,
                           block_goals=[goals[n] for n in t.block_goal_numbers if n in goals],
                           workload=TaskHours(**t.workload.model_dump()) if t.workload else None)
               for i, t in enumerate(concept.tasks, 1)],
        decisions=[f"{KINDS.get(d.kind, d.kind)}: {d.title} — {d.explanation}" for d in concept.decisions],
        open_questions=[q.question + (f" ({q.why})" if q.why else "") for q in concept.questions],
        review_notes=review_notes(run), sources=sources(run),
        rules={name: run.prompt_snapshots[name] for name in RULES if name in run.prompt_snapshots},
        freshness=[f"{x.topic}: {x.finding}" + (f" Что сделать: {x.suggestion}" if x.suggestion else "")
                   + (f" Источник: {x.sources[0]}" if x.sources else "")
                   for x in (run.freshness.items if run.freshness else [])]
        + [f"Идея: {x.idea}" + (f" Источник: {x.sources[0]}" if x.sources else "") for x in (run.freshness.ideas if run.freshness else [])],
    )
