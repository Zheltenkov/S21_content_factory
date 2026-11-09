"""Versioned input, evidence and design contracts for the workbench."""

import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ..core.contracts import STORED_SCHEMA, Contract
from ..core.generation import GenerationSettings
from .collaboration import Resolution


class Passage(Contract):
    id: str
    location: str
    text: str = Field(max_length=12000)


class Material(Contract):
    id: str
    name: str = Field(max_length=250)
    # requirement and customer are passed whole; the rest is ranked and may be cropped.
    role: Literal["reference", "requirement", "customer", "example", "starter", "reviewer"] = "reference"
    sha256: str
    passages: list[Passage] = Field(max_length=500)
    warnings: list[str] = Field(default_factory=list)
    read_status: Literal["text_extracted", "partial", "failed", "legacy_unknown"] = "legacy_unknown"
    unread_locations: list[str] = Field(default_factory=list)


class CurriculumRow(Contract):
    id: str
    row_number: int
    # An exam is a control point of the programme, not a project: it stays in the participant's path and cannot be
    # chosen for preparation (StartRequest.input_limits).
    kind: Literal["project", "exam"] = "project"
    block: str
    block_goals: str = ""
    program: str = ""
    # The project's name on the platform and in GitLab (D1P1_Terminal).
    code: str = ""
    # The address of the project's repository, as the plan gives it (column «GitLab»); empty — not created yet.
    repository: str = ""
    title: str
    description: str = ""
    # The approved cases of the project, as the plan lists them.
    cases: str = ""
    outcomes: str = ""
    skills: str = ""
    # The skills tab of the passport gives the project a bonus part (a line `Bonus all` or bonus tasks): the plan
    # expects bonus tasks, and the form opens with them allowed.
    bonus: bool = False
    audience: str = ""
    tools: str = ""
    software: str = ""
    storytelling: str = ""
    storytelling_type: str = ""
    format: str = ""
    group_size: str = ""
    hours: str = ""
    materials: str = ""
    checking: str = ""
    raw: dict[str, str]


class SkillTask(Contract):
    """A task's line on the skills tab: «01», «Bonus 02»."""

    label: str = Field(max_length=100)
    # Skill → the part of that skill this task gets; zeros are not kept.
    shares: dict[str, float] = Field(default_factory=dict)


class SkillPart(Contract):
    """The main or the bonus part of a project on the skills tab; each is counted on its own."""

    # Skill → its share in this part of the project (the line `all` or `Bonus all`); zeros are not kept.
    shares: dict[str, float] = Field(default_factory=dict)
    tasks: list[SkillTask] = Field(default_factory=list, max_length=50)


class SkillBlock(Contract):
    """A project's block on the skills tab of the passport, as the tab has it."""

    code: str = Field(max_length=200)
    main: SkillPart = Field(default_factory=SkillPart)
    bonus: SkillPart = Field(default_factory=SkillPart)
    # What in the block does not add up or is not in the catalogue (concept/skills.block_issues).
    issues: list[str] = Field(default_factory=list)


class Curriculum(Contract):
    filename: str
    sha256: str
    rows: list[CurriculumRow] = Field(max_length=200)
    warnings: list[str] = Field(default_factory=list)
    # Column report for the methodologist: which headers fed which form field.
    columns: dict[str, list[str]] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    unmapped_columns: list[str] = Field(default_factory=list)
    # From a passport workbook. The platform's catalogue of skills (tab «Available skills»): a project's skill must be
    # one of these. The tabs that hold skills of this plan's projects, the one read, and its blocks by project code.
    skill_catalog: list[str] = Field(default_factory=list, max_length=1000)
    skill_tabs: list[str] = Field(default_factory=list, max_length=20)
    skills_tab: str = ""
    skill_blocks: list[SkillBlock] = Field(default_factory=list, max_length=200)
    # What the reading of the skills tabs has to tell the methodologist. Kept apart from `warnings`: those go to the
    # models as the limits of the plan as a source, and the sums of other projects' skills are not that.
    skill_notes: list[str] = Field(default_factory=list)


StartLevel = Literal["", "zero", "basic", "intermediate", "advanced"]


# Fields of the brief that the concept does not read: it decides which tasks prove the outcomes, not the voice they are
# told in nor the address the project will be kept at, and what it designs must not change with either. The content
# stage reads them (bridge.handoff).
PRESENTATION = ("style_profile", "series_story", "repository", "generation", "collaboration_mode")


class Brief(Contract):
    # Legacy/API clients keep explicit review; the intake UI opts into assisted editing.
    collaboration_mode: Literal["review", "assisted"] = "review"
    curriculum_policy: Literal["authoritative", "context_only", "ignore"] = "authoritative"
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    direction: str = ""
    # Legacy free-form program context; new briefs use program_outcomes and customer_requirements.
    program: str = ""
    program_outcomes: str = Field(default="", max_length=12000)
    customer_requirements: str = Field(default="", max_length=12000)
    block: str = ""
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=10, max_length=12000)
    audience: str = Field(max_length=2000)
    start_level: StartLevel = ""
    start_skills: str = Field(default="", max_length=4000)
    # How the programme names its learners; empty means «участник».
    learner_term: str = Field(default="", max_length=40)
    format: str = "individual"
    group_size: int | None = Field(default=None, ge=2, le=100)
    hours: float | None = Field(default=None, gt=0, le=10000)
    tasks_count: int | None = Field(default=None, ge=1, le=20)
    outcomes: str = Field(min_length=5, max_length=12000)
    skills: str = ""
    tools: str = ""
    software: str = ""
    storytelling_type: str = "none"
    storytelling: str = ""
    # How the text is to be told, for the content stage only (PRESENTATION): "" — the assistant proposes and says why,
    # quest — the text quest of a bassin, legend — one working legend and plain tasks.
    style_profile: Literal["", "quest", "legend"] = ""
    # The story that runs through the projects of a series, in the methodologist's own words: who the character is,
    # what has happened before this project. The assistant talks it over when the content begins.
    series_story: str = Field(default="", max_length=6000)
    # The address of the project's repository in GitLab (the plan's column «GitLab»), when it is known. For the content
    # stage only (PRESENTATION): the files of the project are named by their paths from its root.
    repository: str = Field(default="", max_length=500)
    student_result: str = ""
    checking: str = ""
    constraints: str = ""
    output_package: str = ""
    references: str = ""
    notes: str = ""
    # Methodologist's verdict on the previous concept: which tasks to keep, replace or rework.
    concept_feedback: str = Field(default="", max_length=6000)
    # Parts of attached files the methodologist agreed not to consider, one per line: «<id>: «текст» — почему» cuts the
    # text out of the fragment, «<id> — почему» drops the fragment. Code applies it to everything the models read.
    excluded_sources: str = Field(default="", max_length=12000)
    # The methodologist's placement of the block goals, when it differs from the interpreter's: «N — этот проект», one per line.
    block_scope: str = Field(default="", max_length=6000)
    diagrams: bool = False
    tables: bool = False
    formulas: bool = False
    bonus: bool = False

    @model_validator(mode="after")
    def group_contract(self) -> "Brief":
        if self.format == "group" and self.group_size is None:
            raise ValueError("Укажите размер группы")
        return self

    def for_concept(self) -> dict[str, Any]:
        """The brief as the models of the concept read it: without the fields that say how the text is told."""
        return self.model_dump(exclude=set(PRESENTATION))


class StartRequest(Contract):
    request_id: UUID
    brief: Brief
    curriculum: Curriculum | None = None
    selected_row_id: str = ""
    materials: list[Material] = Field(default_factory=list, max_length=12)
    parent_run_id: UUID | None = None
    resolved_questions: list[Resolution] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def input_limits(self) -> "StartRequest":
        # Reject oversized context rather than silently discarding uploaded evidence.
        # Fields left at their defaults are not counted: a run stored before a field was added must not outgrow
        # the limit by gaining it (every stored run passes through here when it is read).
        if len(self.model_dump_json(exclude_defaults=True)) > 800000:
            raise ValueError("Слишком большой комплект. Разделите материалы на несколько проектов.")
        if self.curriculum and self.brief.curriculum_policy != "ignore":
            row = next((r for r in self.curriculum.rows if r.id == self.selected_row_id), None)
            if row is None:
                raise ValueError("Выберите проект из загруженного УП")
            if row.kind == "exam":
                raise ValueError(f"«{row.title}» в УП — экзамен, а не проект: проект для него не готовится. Выберите проект.")
        if len({m.id for m in self.materials}) != len(self.materials):
            raise ValueError("В комплекте повторяется один и тот же файл")
        return self


class Evidence(Contract):
    id: str
    source: str
    location: str
    role: str
    text: str


class Decision(Contract):
    title: str
    explanation: str
    kind: Literal["given", "proposal", "conflict"]
    evidence_ids: list[str]


class Question(Contract):
    question: str
    why: str
    blocking: bool
    key: str = Field(default="", max_length=120)
    depends_on: list[str] = Field(default_factory=list, max_length=12)


class TaskWorkload(Contract):
    """Where the task's hours go; checked by code against configurable norms."""

    reading: float = Field(ge=0)
    setup: float = Field(ge=0)
    doing: float = Field(ge=0)
    debugging: float = Field(ge=0)
    submission: float = Field(ge=0)


# The scale of a skill's weight in a task. Five steps: on the real tabs of a passport three could not express a
# quarter of the columns (a skill that is 65% one task), ten are shares in tenths again (05.10.2026).
SKILL_WEIGHTS = (1, 5)


class TaskPlan(Contract):
    title: str
    action: str
    outcome_numbers: list[int]
    deliverable: str
    peer_check: str
    independent_choice: str
    support: str
    evidence_ids: list[str]
    hours: float = Field(gt=0)
    optional: bool = False
    workload: TaskWorkload | None = None
    # Numbers of the block goals (input_card.block_goals, owner=this) the task closes; checked by code like outcomes.
    block_goal_numbers: list[int] = Field(default_factory=list)
    # What the task builds on from earlier projects and what of its result a later project uses.
    program_link: str = Field(default="", max_length=600)
    # Which skills of the project the task practises, and how much: 1 — touches it … 5 — the task is mostly about it.
    # A judgement of the designer; the shares are counted from the weights by code (concept/skills.spread).
    skills: dict[str, int] = Field(default_factory=dict, max_length=30)

    @field_validator("skills", mode="before")
    @classmethod
    def practised(cls, value: object) -> object:
        """«No skills» has several spellings and each means what it says: null or an empty list for the field, a zero
        or null for a skill the task does not practise. Nothing else is read as something it is not: «true» is not a
        weight of one, and a skill named twice has no weight to take."""
        if value is None or value == []:
            return {}
        if not isinstance(value, dict):
            return value
        if len({str(name).strip() for name in value}) < len(value) or any(isinstance(weight, bool) for weight in value.values()):
            raise ValueError("навык назван дважды или его вес — не число")
        return {name: weight for name, weight in value.items() if weight not in (0, None)}

    @field_validator("skills")
    @classmethod
    def on_the_scale(cls, value: dict[str, int]) -> dict[str, int]:
        off = [f"{name}: {weight}" for name, weight in value.items() if not SKILL_WEIGHTS[0] <= weight <= SKILL_WEIGHTS[1] or not name.strip()]
        if off:
            raise ValueError(f"вес навыка — целое от {SKILL_WEIGHTS[0]} до {SKILL_WEIGHTS[1]}: {', '.join(off[:5])}")
        return value


class Concept(Contract):
    title: str
    program_summary: str
    project_role: str
    student_result: str
    prerequisites: list[str]
    later_use: list[str]
    out_of_scope: list[str]
    tasks: list[TaskPlan] = Field(min_length=1, max_length=20)
    decisions: list[Decision] = Field(min_length=1)
    questions: list[Question]
    material_gaps: list[str]


class Finding(Contract):
    severity: Literal["blocker", "warning"]
    message: str
    suggestion: str
    rule_id: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    concept_locations: list[str] = Field(default_factory=list)
    consequence: str = ""
    origin: Literal["model", "system"] = "model"


class InputClaim(Contract):
    area: Literal["outcomes", "audience", "constraints", "program", "materials"]
    text: str = Field(min_length=1)
    kind: Literal["given", "proposal", "conflict", "unknown"]
    evidence_ids: list[str]
    outcome_numbers: list[int]


class OffTopic(Contract):
    """A fragment of the inputs that does not belong to the project, as the interpreter sees it; the methodologist decides."""

    evidence_id: str
    quote: str = Field(min_length=1, max_length=300)
    why: str = Field(min_length=1, max_length=400)
    # The whole unrelated fragment verbatim, all its lines: a file fragment often mixes it with text that matters.
    text: str = Field(default="", max_length=3000)


class GoalPlacement(Contract):
    """Which project of the block closes one of the block's goals: the goals are written for the whole block."""

    number: int = Field(ge=1)
    owner: Literal["previous", "this", "later", "shared", "unclear"]
    # The block project that closes it, exactly as in the plan; empty for this project, shared and unclear.
    project: str = ""
    why: str = Field(min_length=1, max_length=400)


class ProgramPoint(Contract):
    """One step of reasoning about the place in the programme: a fact of the plan and what it means for the tasks."""

    fact: str = Field(min_length=1, max_length=400)
    so_what: str = Field(min_length=1, max_length=500)
    kind: Literal["given", "proposal"]
    evidence_ids: list[str] = Field(default_factory=list)


class NextNeed(Contract):
    """What a later project of the plan will ask of the participant, and what this project must teach for it."""

    # A later project, its title exactly as in the plan.
    project: str = Field(min_length=1)
    # What the participant will do there that relies on this project.
    needs: str = Field(min_length=1, max_length=300)
    # What this project must teach or leave as a result so that it works there.
    teach_here: str = Field(min_length=1, max_length=300)
    # Outcomes of this project that cover it.
    outcome_numbers: list[int] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class LearnerPath(Contract):
    """The participant's path by the plan: what they have gone through before this project and what the next
    projects will need from it. Written by the interpreter from the facts code collected; corrected by the methodologist."""

    # A short summary: outcomes, skills and tools of the earlier projects, as abilities, not as a list of titles.
    before: str = Field(min_length=1, max_length=900)
    # Concrete results or abilities of earlier projects this project relies on, each with its project: not taught again.
    builds_on: list[str] = Field(default_factory=list, max_length=6)
    next_needs: list[NextNeed] = Field(default_factory=list, max_length=5)


class DoubtfulSkill(Contract):
    """A skill in the plan's column that does not fit the project or the block: possibly an error in the plan."""

    skill: str
    why: str = Field(min_length=1, max_length=300)


class InputCard(Contract):
    # In the order the interpreter decides them (its prompt goes step by step): what is unrelated first, so no claim is
    # built on it; the questions after everything that asks for one (an unrelated fragment, a doubtful skill, an empty
    # start level); the summary last, when it is known what is still unknown. The model writes JSON in schema order.
    off_topic: list[OffTopic] = Field(default_factory=list, max_length=12)
    claims: list[InputClaim] = Field(min_length=5, max_length=80)
    block_goals: list[GoalPlacement] = Field(default_factory=list, max_length=40)
    doubtful_skills: list[DoubtfulSkill] = Field(default_factory=list, max_length=10)
    # Since 0.9 the place in the programme is this path; program_reasoning stays readable in older runs.
    learner_path: LearnerPath | None = None
    program_reasoning: list[ProgramPoint] = Field(default_factory=list, max_length=6)
    questions: list[Question]
    summary: str


# One entry per «не учитывать» decision: an id, then optionally «text» — which may span lines — then a reason.
EXCLUSION = re.compile(r"^[ \t]*[-•*]?[ \t]*([^\s:«]+):?(?:[^\n«]*«(.*?)»)?[^\n]*", re.M | re.S)


def exclusions(text: str) -> dict[str, list[str | None]]:
    """Brief.excluded_sources as {fragment id: texts to cut}. An entry is «<id>: «текст» — почему» — cut that text out of
    the fragment — or «<id> — почему» — drop the whole fragment (None)."""
    found: dict[str, list[str | None]] = {}
    for entry in EXCLUSION.finditer(text):
        quoted = entry.group(2)
        found.setdefault(entry.group(1), []).append(quoted.strip() if quoted is not None else None)
    return found


def loose(fragment: str) -> re.Pattern[str]:
    """The fragment with any run of whitespace matching any other: a model copying a quote may rewrap lines."""
    return re.compile(r"\s+".join(re.escape(word) for word in fragment.split()))


def without_excluded(text: str, cuts: list[str | None]) -> tuple[str, list[str]]:
    """The fragment's text with the excluded parts cut out ("" when nothing is left), and the parts not found verbatim.
    A part that is not found is not cut: a wrong quote must never remove the text around it."""
    missing: list[str] = []
    for cut in cuts:
        if cut is None:
            return "", missing
        match = loose(cut).search(text) if cut.split() else None
        if match is None:
            missing.append(cut)
            continue
        text = text[:match.start()] + text[match.end():]
    return re.sub(r"\n{3,}", "\n\n", text).strip(), missing


def considered(material: "Material", excluded: str) -> tuple[list["Passage"], list[str]]:
    """The file's fragments without what the methodologist excluded, and what to tell about it."""
    rules = exclusions(excluded)
    kept: list[Passage] = []
    notes: list[str] = []
    for passage in material.passages:
        if passage.id not in rules:
            kept.append(passage)
            continue
        text, missing = without_excluded(passage.text, rules[passage.id])
        if not text:
            notes.append(f"{material.name}, {passage.location}: фрагмент не учитывается целиком по решению методолога.")
        elif text != passage.text:
            notes.append(f"{material.name}, {passage.location}: по решению методолога вырезано {len(passage.text) - len(text)} "
                         f"из {len(passage.text)} знаков, остальной текст учитывается.")
        notes += [f"{material.name}, {passage.location}: текст «{m[:80]}» не найден дословно — не исключён." for m in missing]
        if text:
            kept.append(passage.model_copy(update={"text": text}))
    return kept, notes


class SourceRecord(Contract):
    id: str
    name: str
    role: str
    sha256: str
    read_status: Literal["text_extracted", "partial", "failed", "legacy_unknown"]
    extracted_count: int
    selected_count: int
    unread_locations: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


CheckStatus = Literal["confirmed", "problem", "needs_information", "deferred", "not_applicable"]
RuleId = Literal["outcomes", "audience", "program", "constraints", "independence", "materials"]


class OutcomeCheck(Contract):
    outcome_number: int = Field(ge=1)
    task_numbers: list[int]
    status: CheckStatus
    explanation: str = Field(min_length=1)
    evidence_ids: list[str]
    required_action: str = ""
    planned_action: str = ""
    observable_evidence: str = ""
    gap: str = ""


class GroundedOutcome(OutcomeCheck):
    """Require an explicit action comparison in new model responses only."""

    required_action: str = Field(min_length=1)
    planned_action: str = Field(min_length=1)
    observable_evidence: str = Field(min_length=1)
    gap: str


class CriterionCheck(Contract):
    rule_id: RuleId
    status: CheckStatus
    explanation: str = Field(min_length=1)
    evidence_ids: list[str]
    concept_locations: list[str]


class GroundedFinding(Contract):
    severity: Literal["blocker", "warning"]
    rule_id: RuleId
    message: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    evidence_ids: list[str]
    concept_locations: list[str]
    consequence: str = Field(min_length=1)


class GroundedReview(Contract):
    """New model responses are strict; persisted legacy reviews remain readable.

    In the order the reviewer decides them: each outcome, then the six directions, then the findings that every
    problem there needs, then the summary of all that. Summary first made the model promise before it checked."""

    outcomes: list[GroundedOutcome]
    checks: list[CriterionCheck] = Field(min_length=6, max_length=6)
    findings: list[GroundedFinding]
    summary: str


class Review(Contract):
    summary: str
    findings: list[Finding]
    outcomes: list[OutcomeCheck] = Field(default_factory=list)
    checks: list[CriterionCheck] = Field(default_factory=list)


class RevisionChange(Contract):
    location: str
    before: str
    after: str


class FindingDecision(Contract):
    finding_index: int = Field(ge=0)
    verdict: Literal["confirmed", "dismissed"]
    reason: str = Field(min_length=5, max_length=2000)
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class FeedbackRequest(Contract):
    finding_index: int = Field(ge=0)
    verdict: Literal["confirmed", "dismissed"]
    reason: str = Field(min_length=5, max_length=2000)


class WorkloadBudget(Contract):
    """Deterministic budget computed from hours and editable norms, never by the model."""

    total_hours: float | None = None
    start_level: str = ""
    requested_tasks: int | None = None
    min_task_hours: float
    max_task_hours: float
    tasks_min: int | None = None
    tasks_max: int | None = None
    tasks_recommended: int | None = None
    # The norm «не больше стольких основных заданий в проекте», when the norms have one.
    main_tasks_max: int | None = None
    overhead_min_share: float
    first_task_setup_min: float
    needs_setup: bool = False
    norms_status: str = ""
    notes: list[str] = Field(default_factory=list)


class Event(Contract):
    stage: str
    status: Literal["running", "done", "failed"]
    message: str
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    elapsed_ms: int | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    schema_repairs: int = 0


class FreshItem(Contract):
    """One time-dependent statement of the concept, checked by a web-search model."""

    topic: str = Field(min_length=1, max_length=300)
    where: str = Field(default="", max_length=200)
    status: Literal["current", "outdated", "unclear"]
    finding: str = Field(min_length=1, max_length=1200)
    suggestion: str = Field(default="", max_length=800)
    source_numbers: list[int] = Field(default_factory=list, max_length=8)


class FreshIdea(Contract):
    idea: str = Field(min_length=1, max_length=600)
    why: str = Field(default="", max_length=800)
    source_numbers: list[int] = Field(default_factory=list, max_length=8)


class SearchAnswer(Contract):
    """What the search model returns; source numbers are the [n] marks of its own citations."""

    summary: str = Field(min_length=1, max_length=1200)
    items: list[FreshItem] = Field(default_factory=list, max_length=10)
    ideas: list[FreshIdea] = Field(default_factory=list, max_length=3)


class CheckedItem(FreshItem):
    sources: list[str] = Field(default_factory=list)


class CheckedIdea(FreshIdea):
    sources: list[str] = Field(default_factory=list)


class Freshness(Contract):
    """Stored result of a freshness check. Only links the provider actually returned are kept."""

    checked_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model: str
    summary: str
    items: list[CheckedItem] = Field(default_factory=list)
    ideas: list[CheckedIdea] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    # Sources that did not open when code tried them, from the server. Kept in `sources` all the same:
    # the report is advisory, and a dropped link takes the ground of its verdict with it.
    unreachable: list[str] = Field(default_factory=list)
    # [n] marks that pointed outside the returned sources: counted and dropped, never shown as links.
    dropped_references: int = 0
    elapsed_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class Run(Contract):
    id: str
    # The release shape that wrote the file (core.contracts.STORED_SCHEMA): a rollback reads newer files by it.
    schema_version: int = STORED_SCHEMA
    demo_mode: bool = True
    owner_id: str = "local"
    fingerprint: str
    status: Literal["running", "input_review", "review", "approved", "failed", "interrupted"] = "running"
    request: StartRequest
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    prompt_version: str = "0.1"
    prompt_snapshots: dict[str, str] = Field(default_factory=dict)
    events: list[Event] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    context_warnings: list[str] = Field(default_factory=list)
    concept: Concept | None = None
    review: Review | None = None
    error: str = ""
    approved_at: str | None = None
    input_card: InputCard | None = None
    input_findings: list[Finding] = Field(default_factory=list)
    source_registry: list[SourceRecord] = Field(default_factory=list)
    input_approved_at: str | None = None
    changes: list[RevisionChange] = Field(default_factory=list)
    finding_decisions: list[FindingDecision] = Field(default_factory=list)
    baseline_concept: Concept | None = None
    baseline_approved_at: str | None = None
    input_attempts: list[InputCard] = Field(default_factory=list)
    review_attempts: list[Review] = Field(default_factory=list)
    workload_budget: WorkloadBudget | None = None
    # The methodologist kept hours more than one project holds by the norms (concept/workload.overflow): the tasks will be
    # longer than the norm. Until then the design does not start (29.10.2026).
    hours_kept: bool = False
    # The skills of the project divided between the tasks of the concept, as a block of the passport's skills tab:
    # counted by code when the concept is checked (rules 1.1), shown on the page and written out with the result.
    skill_block: SkillBlock | None = None
    # The methodologist asked to go on from the chat: after interpretation the design starts at once,
    # unless the new card raises an important question.
    auto_continue: bool = False
    freshness: Freshness | None = None
