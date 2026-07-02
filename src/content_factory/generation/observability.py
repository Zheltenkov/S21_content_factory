"""Typed observability contracts for LLM and generation node calls."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FALLBACK_TRACE_REQUIRED_FIELDS = (
    "trace_id",
    "node",
    "fallback_type",
    "reason",
    "quality_risk",
    "visible_to_user",
)
FALLBACK_QUALITY_RISKS = frozenset({"none", "low", "medium", "high"})


def _utc_now_iso() -> str:
    """Return UTC timestamp in the legacy naive ISO format."""
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def stable_input_hash(payload: Any) -> str:
    """Return a deterministic short hash for JSON-like node input payloads."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class TokenUsage(BaseModel):
    """Provider-neutral token usage snapshot."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ValidationTrace(BaseModel):
    """Validation outcome attached to a node or LLM call."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["not_run", "passed", "warning", "failed"] = "not_run"
    issues_count: int = 0
    issues: list[str] = Field(default_factory=list)


class NodeTraceEvent(BaseModel):
    """Minimal reproducible trace for one generation node execution."""

    model_config = ConfigDict(extra="forbid")

    node: str
    input_hash: str
    prompt_version: str | None = None
    model: str | None = None
    latency_ms: float | None = None
    tokens: TokenUsage | None = None
    validation: ValidationTrace = Field(default_factory=ValidationTrace)
    repair_attempts: int = 0
    output_schema: str | None = None
    status: str = "success"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_node_execution(
        cls,
        *,
        node: str,
        inputs: Any,
        latency_ms: float | None,
        status: str,
        issues: list[str] | None = None,
        prompt_version: str | None = None,
        model: str | None = None,
        repair_attempts: int = 0,
        output_schema: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "NodeTraceEvent":
        """Build a trace event from runtime execution data."""
        issue_list = [str(issue) for issue in issues or [] if str(issue)]
        if status == "error":
            validation_status = "failed"
        elif issue_list:
            validation_status = "warning"
        else:
            validation_status = "passed"
        return cls(
            node=node,
            input_hash=stable_input_hash(inputs),
            prompt_version=prompt_version,
            model=model,
            latency_ms=latency_ms,
            validation=ValidationTrace(
                status=validation_status,
                issues_count=len(issue_list),
                issues=issue_list,
            ),
            repair_attempts=max(0, int(repair_attempts or 0)),
            output_schema=output_schema,
            status=status,
            metadata=metadata or {},
        )


class FallbackTraceEvent(BaseModel):
    """Machine-readable trace for deterministic degradation paths."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(default_factory=lambda: f"fb_{uuid.uuid4().hex[:16]}")
    node: str
    fallback_type: str
    reason: str
    quality_risk: Literal["none", "low", "medium", "high"] = "low"
    visible_to_user: bool = False
    input_hash: str | None = None
    trace: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_fallback(
        cls,
        *,
        node: str,
        fallback_type: str,
        reason: str,
        quality_risk: Literal["none", "low", "medium", "high"] = "low",
        visible_to_user: bool = False,
        trace_id: str | None = None,
        inputs: Any | None = None,
        trace: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "FallbackTraceEvent":
        """Build a fallback trace with optional deterministic input hashing."""
        input_hash = stable_input_hash(inputs) if inputs is not None else None
        resolved_trace = trace or {}
        resolved_metadata = metadata or {}
        return cls(
            trace_id=trace_id
            or _fallback_trace_id(
                node=node,
                fallback_type=fallback_type,
                reason=reason,
                input_hash=input_hash,
                trace=resolved_trace,
                metadata=resolved_metadata,
            ),
            node=node,
            fallback_type=fallback_type,
            reason=str(reason),
            quality_risk=quality_risk,
            visible_to_user=visible_to_user,
            input_hash=input_hash,
            trace=resolved_trace,
            metadata=resolved_metadata,
        )


def _fallback_trace_id(
    *,
    node: str,
    fallback_type: str,
    reason: str,
    input_hash: str | None = None,
    trace: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Return a stable fallback event id for repeatable deterministic degradations."""
    payload = {
        "node": node,
        "fallback_type": fallback_type,
        "reason": str(reason),
        "input_hash": input_hash,
        "trace": trace or {},
        "metadata": metadata or {},
    }
    return f"fb_{stable_input_hash(payload)}"


def normalize_fallback_trace_event(event: FallbackTraceEvent | dict[str, Any]) -> FallbackTraceEvent:
    """Normalize legacy fallback dictionaries into the current fallback policy contract."""
    if isinstance(event, FallbackTraceEvent):
        return event

    raw_payload = dict(event or {})
    payload = dict(raw_payload)
    fields = set(FallbackTraceEvent.model_fields)
    extra = {key: payload.pop(key) for key in list(payload.keys()) if key not in fields}
    metadata = dict(payload.get("metadata") or {})
    if extra:
        metadata.setdefault("legacy_extra", extra)
    node = str(payload.get("node") or raw_payload.get("source") or metadata.get("node") or "unknown")
    fallback_type = str(payload.get("fallback_type") or raw_payload.get("type") or "fallback")
    reason = str(payload.get("reason") or fallback_type)
    quality_risk = str(payload.get("quality_risk") or "low")
    if quality_risk not in {"none", "low", "medium", "high"}:
        metadata["legacy_quality_risk"] = quality_risk
        quality_risk = "medium"
    trace = dict(payload.get("trace") or {})
    input_hash = payload.get("input_hash")
    visible_to_user = payload.get("visible_to_user", False)
    if isinstance(visible_to_user, str):
        visible_to_user = visible_to_user.strip().lower() in {"1", "true", "yes", "y", "да"}
    trace_id = str(
        payload.get("trace_id")
        or _fallback_trace_id(
            node=node,
            fallback_type=fallback_type,
            reason=reason,
            input_hash=str(input_hash) if input_hash else None,
            trace=trace,
            metadata=metadata,
        )
    )
    return FallbackTraceEvent(
        trace_id=trace_id,
        node=node,
        fallback_type=fallback_type,
        reason=reason,
        quality_risk=quality_risk,  # type: ignore[arg-type]
        visible_to_user=bool(visible_to_user),
        input_hash=str(input_hash) if input_hash else None,
        trace=trace,
        metadata=metadata,
    )


def fallback_trace_policy_issues(event: dict[str, Any]) -> list[str]:
    """Return contract issues for an already serialized fallback event."""
    issues: list[str] = []
    for field in FALLBACK_TRACE_REQUIRED_FIELDS:
        if field not in event:
            issues.append(f"missing {field}")
    quality_risk = event.get("quality_risk")
    if quality_risk is not None and quality_risk not in FALLBACK_QUALITY_RISKS:
        issues.append(f"invalid quality_risk {quality_risk!r}")
    if "visible_to_user" in event and not isinstance(event.get("visible_to_user"), bool):
        issues.append("visible_to_user must be bool")
    return issues


class CompatibilityEvent(BaseModel):
    """Machine-readable trace for intentional legacy compatibility behavior."""

    model_config = ConfigDict(extra="forbid")

    source: str
    compatibility_type: str
    reason: str
    risk: Literal["none", "low", "medium", "high"] = "low"
    metadata: dict[str, Any] = Field(default_factory=dict)


def record_runtime_fallback_traces(
    runtime: Any,
    events: Iterable[FallbackTraceEvent | dict[str, Any]] | None,
) -> None:
    """Append fallback events to a runtime object that exposes ``fallback_traces``."""
    normalized: list[dict[str, Any]] = []
    for event in events or []:
        if isinstance(event, (FallbackTraceEvent, dict)):
            normalized.append(normalize_fallback_trace_event(event).model_dump(mode="json"))
    if not normalized:
        return

    traces = getattr(runtime, "fallback_traces", None)
    if traces is None:
        runtime.fallback_traces = []
        traces = runtime.fallback_traces
    traces.extend(normalized)
    sink = getattr(runtime, "observability_sink", None)
    if sink is not None and hasattr(sink, "record_fallback_trace"):
        for event in normalized:
            try:
                sink.record_fallback_trace(event)
            except Exception:
                continue


class LLMCallTraceEvent(BaseModel):
    """Reproducible trace for one LLM invocation."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    node: str
    agent: str
    input_hash: str
    prompt_version: str | None = None
    model: str | None = None
    latency_ms: float | None = None
    tokens: TokenUsage | None = None
    output_schema: str | None = Field(default=None, alias="schema")
    validation: ValidationTrace = Field(default_factory=ValidationTrace)
    repair_attempts: int = 0
    status: str = "success"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_llm_call(
        cls,
        *,
        node: str,
        agent: str,
        system: str,
        user: str,
        response_format: Any,
        model: str | None,
        latency_ms: float,
        status: str,
        error: str | None = None,
        tokens: TokenUsage | dict[str, Any] | None = None,
        prompt_version: str | None = None,
        repair_attempts: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> "LLMCallTraceEvent":
        """Build a trace event from an LLM complete call."""
        issues = [error] if error else []
        output_schema = None
        if isinstance(response_format, str):
            output_schema = response_format
        elif isinstance(response_format, dict):
            output_schema = str(
                response_format.get("json_schema", {}).get("name")
                or response_format.get("type")
                or "structured"
            )
        token_snapshot = tokens if isinstance(tokens, TokenUsage) else TokenUsage.model_validate(tokens) if tokens else None
        return cls(
            node=node,
            agent=agent,
            input_hash=stable_input_hash({"system": system, "user": user, "response_format": response_format}),
            prompt_version=prompt_version,
            model=model,
            latency_ms=latency_ms,
            tokens=token_snapshot,
            output_schema=output_schema,
            validation=ValidationTrace(
                status="failed" if error else "not_run",
                issues_count=len(issues),
                issues=issues,
            ),
            repair_attempts=max(0, int(repair_attempts or 0)),
            status=status,
            metadata=metadata or {},
        )


class LLMTraceRecorder:
    """In-memory trace sink for one generation run."""

    def __init__(self, sink: "UnifiedTraceSink | None" = None) -> None:
        self.events: list[dict[str, Any]] = []
        self.sink = sink

    def append(self, event: LLMCallTraceEvent) -> None:
        """Append a JSON-safe event."""
        self.events.append(event.model_dump(mode="json", by_alias=True))
        if self.sink is not None:
            self.sink.record_llm_trace(event)


class PromptRegistryEntry(BaseModel):
    """Versioned prompt identity stored with a trace instead of raw prompt text."""

    model_config = ConfigDict(extra="forbid")

    prompt_id: str
    prompt_version: str
    node: str | None = None
    agent: str | None = None
    prompt_hash: str
    owner: str | None = None
    input_schema: Any | None = None
    output_schema: Any | None = None
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalArtifact(BaseModel):
    """Compact evaluation artifact for node/repair/validator debugging."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    run_id: str | None = None
    user_id: str | None = None
    node: str
    artifact_type: str
    input_hash: str | None = None
    input_preview: Any | None = None
    output_preview: Any | None = None
    validator_report: dict[str, Any] = Field(default_factory=dict)
    diff_after_repair: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now_iso)

    @classmethod
    def from_node_trace(
        cls,
        *,
        run_id: str | None,
        user_id: str | None,
        trace: NodeTraceEvent,
        output_artifact: dict[str, Any] | None = None,
    ) -> "EvalArtifact":
        """Build a compact artifact from a node trace and its emitted outputs."""
        return cls(
            artifact_id=f"{trace.node}:{trace.input_hash}:{trace.status}",
            run_id=run_id,
            user_id=user_id,
            node=trace.node,
            artifact_type="node_output",
            input_hash=trace.input_hash,
            output_preview=output_artifact or {},
            validator_report=trace.validation.model_dump(mode="json"),
            diff_after_repair=(
                {"repair_attempts": trace.repair_attempts}
                if trace.repair_attempts
                else None
            ),
            metadata={"status": trace.status, "schema": trace.output_schema},
        )


class UnifiedTraceEvent(BaseModel):
    """Normalized event contract across node, LLM, fallback and compatibility traces."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    run_id: str | None = None
    user_id: str | None = None
    event_type: Literal["node", "llm", "fallback", "compatibility", "eval"] = "node"
    trace_id: str | None = None
    node: str | None = None
    agent: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    input_hash: str | None = None
    latency_ms: float | None = None
    tokens: TokenUsage | None = None
    cost_usd: float | None = None
    output_schema: str | None = Field(default=None, alias="schema")
    validation: ValidationTrace = Field(default_factory=ValidationTrace)
    fallback: dict[str, Any] | None = None
    status: str = "success"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now_iso)


class ObservabilityExporter:
    """Best-effort exporter interface; exporter failures must not break generation."""

    def emit(self, event: UnifiedTraceEvent) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def flush(self) -> None:
        """Flush buffered exporter data when supported."""


class OpenTelemetryObservabilityExporter(ObservabilityExporter):
    """Optional OpenTelemetry span exporter using the active global provider."""

    def __init__(self, service_name: str = "content-generator") -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.trace import Status, StatusCode
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"OpenTelemetry is unavailable: {exc}") from exc
        self._trace = trace
        self._status_cls = Status
        self._status_code_cls = StatusCode
        self._tracer = trace.get_tracer(service_name)

    def emit(self, event: UnifiedTraceEvent) -> None:
        name = ".".join(part for part in ["generation", event.event_type, event.node] if part)
        with self._tracer.start_as_current_span(name) as span:
            for key, value in _otel_attributes(event).items():
                span.set_attribute(key, value)
            if event.status in {"error", "failed"}:
                span.set_status(self._status_cls(self._status_code_cls.ERROR, event.status))


class LangfuseObservabilityExporter(ObservabilityExporter):
    """Optional Langfuse exporter for LLM traces, prompts and eval artifacts."""

    def __init__(self) -> None:
        try:
            from langfuse import Langfuse
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Langfuse is unavailable: {exc}") from exc
        self._client = Langfuse()

    def emit(self, event: UnifiedTraceEvent) -> None:
        as_type = "generation" if event.event_type == "llm" else "span"
        payload = event.model_dump(mode="json", by_alias=True)
        with self._client.start_as_current_observation(
            name=f"{event.event_type}:{event.node or 'run'}",
            as_type=as_type,
            input={"input_hash": event.input_hash},
            metadata=payload.get("metadata") or {},
            version=event.prompt_version,
            model=event.model if as_type == "generation" else None,
        ) as observation:
            update: dict[str, Any] = {"output": payload}
            if event.tokens:
                update["usage_details"] = {
                    "input": event.tokens.prompt_tokens,
                    "output": event.tokens.completion_tokens,
                    "total": event.tokens.total_tokens,
                }
            if event.cost_usd is not None:
                update["cost_details"] = {"total": event.cost_usd}
            observation.update(**update)

    def flush(self) -> None:
        flush = getattr(self._client, "flush", None)
        if callable(flush):
            flush()


class UnifiedTraceSink:
    """Run-scoped sink that links node, LLM, fallback and eval artifacts."""

    def __init__(
        self,
        *,
        run_id: str | None = None,
        user_id: str | None = None,
        exporters: list[ObservabilityExporter] | None = None,
    ) -> None:
        self.run_id = run_id
        self.user_id = user_id
        self.events: list[dict[str, Any]] = []
        self.eval_artifacts: list[dict[str, Any]] = []
        self.prompt_registry: dict[str, dict[str, Any]] = {}
        self.exporters = exporters or []

    def record_node_trace(
        self,
        trace: NodeTraceEvent | dict[str, Any],
        *,
        output_artifact: dict[str, Any] | None = None,
    ) -> None:
        """Record a node event and a compact eval artifact."""
        event = trace if isinstance(trace, NodeTraceEvent) else NodeTraceEvent.model_validate(trace)
        unified = UnifiedTraceEvent(
            run_id=self.run_id,
            user_id=self.user_id,
            event_type="node",
            node=event.node,
            model=event.model,
            prompt_version=event.prompt_version,
            input_hash=event.input_hash,
            latency_ms=event.latency_ms,
            tokens=event.tokens,
            output_schema=event.output_schema,
            validation=event.validation,
            status=event.status,
            metadata=event.metadata | {"repair_attempts": event.repair_attempts},
        )
        self._append(unified)
        artifact = EvalArtifact.from_node_trace(
            run_id=self.run_id,
            user_id=self.user_id,
            trace=event,
            output_artifact=output_artifact,
        )
        self.eval_artifacts.append(artifact.model_dump(mode="json"))

    def record_llm_trace(self, trace: LLMCallTraceEvent | dict[str, Any]) -> None:
        """Record an LLM call event and prompt registry entry."""
        event = trace if isinstance(trace, LLMCallTraceEvent) else LLMCallTraceEvent.model_validate(trace)
        cost = _safe_float(event.metadata.get("cost_usd"))
        unified = UnifiedTraceEvent(
            run_id=self.run_id,
            user_id=self.user_id,
            event_type="llm",
            node=event.node,
            agent=event.agent,
            model=event.model,
            prompt_version=event.prompt_version,
            input_hash=event.input_hash,
            latency_ms=event.latency_ms,
            tokens=event.tokens,
            cost_usd=cost,
            output_schema=event.output_schema,
            validation=event.validation,
            status=event.status,
            metadata=event.metadata | {"repair_attempts": event.repair_attempts},
        )
        self._register_prompt(unified)
        self._append(unified)

    def record_fallback_trace(self, trace: FallbackTraceEvent | dict[str, Any]) -> None:
        """Record a deterministic fallback event."""
        event = normalize_fallback_trace_event(trace)
        unified = UnifiedTraceEvent(
            run_id=self.run_id,
            user_id=self.user_id,
            event_type="fallback",
            trace_id=event.trace_id,
            node=event.node,
            input_hash=event.input_hash,
            fallback={
                "trace_id": event.trace_id,
                "type": event.fallback_type,
                "reason": event.reason,
                "quality_risk": event.quality_risk,
                "visible_to_user": event.visible_to_user,
                "trace": event.trace,
            },
            status="fallback",
            metadata=event.metadata,
        )
        self._append(unified)

    def record_compatibility_event(self, event: CompatibilityEvent | dict[str, Any]) -> None:
        """Record an intentional legacy compatibility branch."""
        if isinstance(event, CompatibilityEvent):
            compatibility = event
        else:
            payload = dict(event)
            payload.setdefault("reason", payload.get("compatibility_type") or "compatibility")
            payload.setdefault("risk", "low")
            payload.setdefault("metadata", {})
            compatibility = CompatibilityEvent.model_validate(payload)
        self._append(
            UnifiedTraceEvent(
                run_id=self.run_id,
                user_id=self.user_id,
                event_type="compatibility",
                node=compatibility.source,
                status="compatibility",
                metadata={
                    "compatibility_type": compatibility.compatibility_type,
                    "reason": compatibility.reason,
                    "risk": compatibility.risk,
                    **compatibility.metadata,
                },
            )
        )

    def report(self) -> dict[str, Any]:
        """Return a JSON-safe run observability bundle for DB/report storage."""
        return {
            "run_id": self.run_id,
            "user_id": self.user_id,
            "events": list(self.events),
            "eval_artifacts": list(self.eval_artifacts),
            "prompt_registry": list(self.prompt_registry.values()),
        }

    def flush(self) -> None:
        """Flush optional exporters."""
        for exporter in self.exporters:
            try:
                exporter.flush()
            except Exception:
                continue

    def _append(self, event: UnifiedTraceEvent) -> None:
        payload = event.model_dump(mode="json", by_alias=True)
        self.events.append(payload)
        for exporter in self.exporters:
            try:
                exporter.emit(event)
            except Exception:
                continue

    def _register_prompt(self, event: UnifiedTraceEvent) -> None:
        if not event.prompt_version:
            return
        metadata = event.metadata or {}
        prompt_id = str(
            metadata.get("prompt_id")
            or f"{event.node or 'generation'}:{event.agent or 'agent'}:{event.prompt_version}"
        )
        if prompt_id in self.prompt_registry:
            return
        entry = PromptRegistryEntry(
            prompt_id=prompt_id,
            prompt_version=event.prompt_version,
            node=event.node,
            agent=event.agent,
            prompt_hash=str(metadata.get("prompt_hash") or event.input_hash or ""),
            owner=metadata.get("prompt_owner"),
            input_schema=metadata.get("prompt_input_schema"),
            output_schema=metadata.get("prompt_output_schema") or event.output_schema,
            source=str(metadata.get("prompt_source") or "ObservedLLMClient"),
            metadata={
                "model": event.model,
                "trace_prompt_version": event.prompt_version,
            },
        )
        self.prompt_registry[prompt_id] = entry.model_dump(mode="json")


def build_default_observability_exporters() -> list[ObservabilityExporter]:
    """Build optional exporters from env without making them runtime requirements."""
    requested = {
        value.strip().lower()
        for value in os.getenv("OBSERVABILITY_EXPORTERS", "").split(",")
        if value.strip()
    }
    exporters: list[ObservabilityExporter] = []
    if "otel" in requested or os.getenv("OTEL_ENABLED", "").lower() == "true":
        try:
            exporters.append(OpenTelemetryObservabilityExporter())
        except RuntimeError:
            pass
    if "langfuse" in requested or os.getenv("LANGFUSE_ENABLED", "").lower() == "true":
        try:
            exporters.append(LangfuseObservabilityExporter())
        except RuntimeError:
            pass
    return exporters


def build_unified_observability_report(
    *,
    run_id: str | None,
    user_id: str | None,
    node_traces: Iterable[dict[str, Any]] | None = None,
    llm_traces: Iterable[dict[str, Any]] | None = None,
    fallback_traces: Iterable[dict[str, Any]] | None = None,
    compatibility_events: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a unified report from legacy trace lists when no live sink exists."""
    sink = UnifiedTraceSink(run_id=run_id, user_id=user_id)
    for event in node_traces or []:
        sink.record_node_trace(event)
    for event in llm_traces or []:
        sink.record_llm_trace(event)
    for event in fallback_traces or []:
        sink.record_fallback_trace(event)
    for event in compatibility_events or []:
        sink.record_compatibility_event(event)
    return sink.report()


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _otel_attributes(event: UnifiedTraceEvent) -> dict[str, str | int | float | bool]:
    payload = event.model_dump(mode="json", by_alias=True)
    attrs: dict[str, str | int | float | bool] = {}
    for key in (
        "run_id",
        "user_id",
        "event_type",
        "node",
        "agent",
        "model",
        "prompt_version",
        "input_hash",
        "latency_ms",
        "cost_usd",
        "schema",
        "status",
    ):
        value = payload.get(key)
        if value is not None:
            attrs[f"content_generator.{key}"] = value
    if event.tokens:
        if event.tokens.prompt_tokens is not None:
            attrs["gen_ai.usage.input_tokens"] = event.tokens.prompt_tokens
        if event.tokens.completion_tokens is not None:
            attrs["gen_ai.usage.output_tokens"] = event.tokens.completion_tokens
        if event.tokens.total_tokens is not None:
            attrs["gen_ai.usage.total_tokens"] = event.tokens.total_tokens
    attrs["content_generator.validation.status"] = event.validation.status
    attrs["content_generator.validation.issues_count"] = event.validation.issues_count
    return attrs
