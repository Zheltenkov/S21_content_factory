"""Typed contracts for deterministic curriculum planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

OccurrenceRole = Literal["primary", "supporting", "reinforcement", "assessment"]
BloomBucket = Literal["know", "can", "skills"]


@dataclass(frozen=True)
class PlanNode:
    """Accepted atomic skill normalized for curriculum planning."""

    tmp_id: str
    name: str
    group: str
    block_key: str
    bloom: int
    outcomes_know: tuple[str, ...]
    outcomes_can: tuple[str, ...]
    outcomes_skills: tuple[str, ...]
    tools: tuple[str, ...]


@dataclass(frozen=True)
class SkillOccurrence:
    """A concrete appearance of a skill in a project.

    A skill may have one primary occurrence and several spiral reinforcement
    occurrences. This is the key difference between a DAG coverage route and a
    curriculum plan.
    """

    node: PlanNode
    role: OccurrenceRole
    touch_index: int = 1
    bloom_bucket: BloomBucket = "can"

    @property
    def is_repeat(self) -> bool:
        return self.touch_index > 1 or self.role in {"reinforcement", "assessment"}


@dataclass
class ProjectBlueprint:
    """Integrative project built around one checked artifact."""

    occurrences: list[SkillOccurrence]
    block_key: str
    artifact: str
    artifact_key: str = ""
    artifact_family: str = "practice"
    artifact_template_code: str = ""
    enrichment: dict[str, str] = field(default_factory=dict)
    title: str = ""
    project_kind: str = "integrative"
    # Explicit classification assigned deterministically at grouping time (slice 3).
    # project_type: lab | project | capstone. policy_area: key into the artifact policy
    # registry (slice 4); "" means unclassified → draft-only, never a silent generic.
    project_type: str = "project"
    policy_area: str = ""
    # Artifact policy contract from the registry (slice 4); None when unclassified.
    artifact_contract: ArtifactContract | None = None
    # Durable binding to the artifact template the project was built from (slice 6a);
    # None when the project uses the policy artifact rather than a template.
    template_binding: TemplateBinding | None = None

    @property
    def primary_occurrences(self) -> list[SkillOccurrence]:
        return [item for item in self.occurrences if item.role == "primary"]

    @property
    def unique_nodes(self) -> list[PlanNode]:
        seen: set[str] = set()
        nodes: list[PlanNode] = []
        for occurrence in self.occurrences:
            if occurrence.node.tmp_id in seen:
                continue
            seen.add(occurrence.node.tmp_id)
            nodes.append(occurrence.node)
        return nodes

    @property
    def node_ids(self) -> list[str]:
        return [node.tmp_id for node in self.unique_nodes]


@dataclass
class CurriculumBlock:
    """A group of related projects with preserved DAG-level ordering."""

    block_keys: tuple[str, ...]
    projects: list[ProjectBlueprint] = field(default_factory=list)
    stage_code: str = ""
    title: str = ""
    goal: str = ""


VerificationMode = Literal["automatic", "manual"]


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One structured, verifiable acceptance condition for a project artifact."""

    subject: str
    check: str
    expected_result: str
    evidence_type: str
    verification_mode: VerificationMode = "manual"
    blocking: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "check": self.check,
            "expected_result": self.expected_result,
            "evidence_type": self.evidence_type,
            "verification_mode": self.verification_mode,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class ArtifactContract:
    """The minimum verifiable result for a project, keyed by policy area.

    The deterministic methodical floor: what must be produced (deliverables), what proves
    it (evidence), how it is accepted (acceptance_criteria), and where it runs. The LLM may
    reword these for the project theme but cannot replace a runnable result with a schema.
    """

    artifact_type: str
    policy_area: str
    deliverables: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    execution_environment: str = ""
    publication_constraints: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_type": self.artifact_type,
            "policy_area": self.policy_area,
            "deliverables": list(self.deliverables),
            "evidence_requirements": list(self.evidence_requirements),
            "acceptance_criteria": [criterion.as_dict() for criterion in self.acceptance_criteria],
            "execution_environment": self.execution_environment,
            "publication_constraints": list(self.publication_constraints),
        }


TemplateSource = Literal["brief", "global", "policy"]


@dataclass(frozen=True)
class TemplateBinding:
    """Durable link from a project to the artifact template it was built from.

    Captures a version snapshot (``template_version``) so a UP version records exactly which
    template revision produced the project, and ``source`` so brief-scoped templates can be
    distinguished from the global catalog. ``repeatable`` marks templates allowed to bind to
    more than one project.
    """

    template_code: str
    template_version: str = ""
    source: TemplateSource = "global"
    repeatable: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "template_code": self.template_code,
            "template_version": self.template_version,
            "source": self.source,
            "repeatable": self.repeatable,
        }


@dataclass(frozen=True)
class WorkloadContract:
    """Canonical UP workload: hours are authoritative, calendar duration is derived.

    Replaces the misleading "total_days" figure (hours / a fixed ``UP_HOURS_PER_DAY``
    constant) that read like a calendar duration. Weeks/months are computed from the
    built UP's actual total hours and the assumed study intensity; ``study_days_per_week``
    is populated only when the brief states it.
    """

    total_hours: int
    hours_per_week: int
    duration_weeks: float
    duration_months: float
    study_days_per_week: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "total_hours": self.total_hours,
            "hours_per_week": self.hours_per_week,
            "duration_weeks": self.duration_weeks,
            "duration_months": self.duration_months,
            "study_days_per_week": self.study_days_per_week,
        }


@dataclass(frozen=True)
class PlanQualityMetrics:
    """Post-generation metrics for methodologist review and regression tests."""

    avg_skills_per_project: float
    avg_outcomes_per_project: float
    single_skill_project_count: int
    overloaded_project_count: int
    core_thread_count: int
    repeated_thread_count: int
    spiral_enabled: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "avg_skills_per_project": self.avg_skills_per_project,
            "avg_outcomes_per_project": self.avg_outcomes_per_project,
            "single_skill_project_count": self.single_skill_project_count,
            "overloaded_project_count": self.overloaded_project_count,
            "core_thread_count": self.core_thread_count,
            "repeated_thread_count": self.repeated_thread_count,
            "spiral_enabled": self.spiral_enabled,
        }
