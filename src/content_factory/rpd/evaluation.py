"""Deterministic evaluation artifact for RPD pilots and regression monitoring."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .build import RpdBuild


class RpdRoleMetrics(BaseModel):
    """Aggregated latency, reliability, usage and cost for one model role."""

    calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    cache_hits: int = 0
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)


class RpdEvaluationArtifact(BaseModel):
    """Versioned metrics that can be aggregated across a real-methodologist pilot."""

    schema_version: int = 1
    evaluated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    program_title: str
    template_profile_id: str
    template_sha256: str
    source_sha256: str
    revision: int
    ready: bool
    blocking_count: int
    warning_count: int
    issues_by_code: dict[str, int]
    total_sections: int
    reviewed_sections: int
    edited_sections: int
    generated_sections: int
    reviewed_generated_sections: int
    accepted_generated_without_edits: int
    accepted_without_edits_rate: float | None
    manual_dependency_sections: int
    llm_calls: int
    llm_failed_calls: int
    llm_cache_hits: int
    llm_total_latency_ms: int
    llm_total_tokens: int
    llm_total_cost_usd: float
    llm_by_role: dict[str, RpdRoleMetrics]


def evaluate_rpd_build(build: RpdBuild) -> RpdEvaluationArtifact:
    """Calculate stable pilot metrics without another model call."""
    reviewed = [section for section in build.sections if section.done]
    edited = [
        section
        for section in build.sections
        if section.initial_content_hash and section.content_hash() != section.initial_content_hash
    ]
    generated = [
        section
        for section in build.sections
        if section.category == "generate" and "[Будет сгенерировано]" not in section.paragraphs
    ]
    reviewed_generated = [section for section in generated if section.done]
    accepted_without_edits = [
        section
        for section in reviewed_generated
        if section.initial_content_hash and section.content_hash() == section.initial_content_hash
    ]
    acceptance_rate = (
        len(accepted_without_edits) / len(reviewed_generated)
        if reviewed_generated
        else None
    )
    issues = Counter(issue.code for issue in build.quality.issues)
    llm_by_role = _aggregate_llm_events(build)
    llm_calls = sum(metrics.calls for metrics in llm_by_role.values())
    llm_failed_calls = sum(metrics.failed_calls for metrics in llm_by_role.values())
    llm_cache_hits = sum(metrics.cache_hits for metrics in llm_by_role.values())
    llm_total_latency_ms = sum(metrics.latency_ms for metrics in llm_by_role.values())
    llm_total_tokens = sum(metrics.total_tokens for metrics in llm_by_role.values())
    llm_total_cost_usd = sum(metrics.cost_usd for metrics in llm_by_role.values())

    return RpdEvaluationArtifact(
        program_title=build.program_title,
        template_profile_id=build.template_profile_id,
        template_sha256=build.generation_trace.template_sha256,
        source_sha256=build.generation_trace.source_sha256,
        revision=build.revision,
        ready=build.quality.ready,
        blocking_count=build.quality.blocking_count,
        warning_count=build.quality.warning_count,
        issues_by_code=dict(sorted(issues.items())),
        total_sections=len(build.sections),
        reviewed_sections=len(reviewed),
        edited_sections=len(edited),
        generated_sections=len(generated),
        reviewed_generated_sections=len(reviewed_generated),
        accepted_generated_without_edits=len(accepted_without_edits),
        accepted_without_edits_rate=acceptance_rate,
        manual_dependency_sections=sum(section.category in {"gap", "platform"} for section in build.sections),
        llm_calls=llm_calls,
        llm_failed_calls=llm_failed_calls,
        llm_cache_hits=llm_cache_hits,
        llm_total_latency_ms=llm_total_latency_ms,
        llm_total_tokens=llm_total_tokens,
        llm_total_cost_usd=round(llm_total_cost_usd, 8),
        llm_by_role=llm_by_role,
    )


def _aggregate_llm_events(build: RpdBuild) -> dict[str, RpdRoleMetrics]:
    result: dict[str, RpdRoleMetrics] = {}
    roles = sorted({event.role for event in build.generation_trace.llm_events})
    for role in roles:
        events = [event for event in build.generation_trace.llm_events if event.role == role]
        result[role] = RpdRoleMetrics(
            calls=len(events),
            successful_calls=sum(event.success for event in events),
            failed_calls=sum(not event.success for event in events),
            cache_hits=sum(event.cache_hit for event in events),
            latency_ms=sum(event.elapsed_ms for event in events),
            prompt_tokens=sum(event.prompt_tokens or 0 for event in events),
            completion_tokens=sum(event.completion_tokens or 0 for event in events),
            total_tokens=sum(event.total_tokens or 0 for event in events),
            cost_usd=round(sum(event.cost_usd or 0.0 for event in events), 8),
            providers=sorted({event.provider for event in events if event.provider}),
            models=sorted({event.model for event in events if event.model}),
        )
    return result


__all__ = ["RpdEvaluationArtifact", "RpdRoleMetrics", "evaluate_rpd_build"]
