"""Serialization helpers for durable paused generation sessions."""

from __future__ import annotations

import base64
import importlib
from dataclasses import asdict, is_dataclass
from typing import Any

from pydantic import BaseModel
from pydantic_core import PydanticSerializationError

from content_gen.observability import CompatibilityEvent, LLMTraceRecorder, ObservabilityExporter, UnifiedTraceSink
from content_gen.workflow.flow_runner import FlowExecutionStep

_TYPE_KEY = "__paused_type__"
_DATA_KEY = "data"
_DROP_VALUE = object()
_NON_SERIALIZABLE_CONTEXT_KEYS = {"observability_sink"}
_NON_SERIALIZABLE_TYPES = (UnifiedTraceSink, LLMTraceRecorder, ObservabilityExporter)


def serialize_value(value: Any) -> Any:
    """Serialize known runtime values into JSON-compatible structures."""
    if isinstance(value, _NON_SERIALIZABLE_TYPES):
        return _DROP_VALUE
    if isinstance(value, BaseModel):
        exclude = set(_NON_SERIALIZABLE_CONTEXT_KEYS)
        try:
            payload = value.model_dump(mode="json", exclude=exclude)
        except (PydanticSerializationError, UnicodeDecodeError):
            payload = value.model_dump(mode="python", exclude=exclude)
        return {
            _TYPE_KEY: f"{value.__class__.__module__}:{value.__class__.__qualname__}",
            _DATA_KEY: serialize_value(payload),
        }
    if isinstance(value, bytes):
        return {
            _TYPE_KEY: "builtins:bytes",
            _DATA_KEY: base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, FlowExecutionStep):
        return {
            _TYPE_KEY: "content_gen.agents.flow:FlowExecutionStep",
            _DATA_KEY: value.as_dict(0) | {"step_index": None},
        }
    if is_dataclass(value):
        return serialize_value(asdict(value))
    if isinstance(value, dict):
        serialized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in _NON_SERIALIZABLE_CONTEXT_KEYS:
                continue
            serialized_item = serialize_value(item)
            if serialized_item is _DROP_VALUE:
                continue
            serialized[str(key)] = serialized_item
        return serialized
    if isinstance(value, (list, tuple)):
        serialized_items = [serialize_value(item) for item in value]
        return [item for item in serialized_items if item is not _DROP_VALUE]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def hydrate_value(value: Any) -> Any:
    """Hydrate JSON-compatible paused state back into allowed runtime objects."""
    return _hydrate_value(value, [])


def _hydrate_value(value: Any, events: list[CompatibilityEvent]) -> Any:
    """Hydrate a value while collecting legacy compatibility fallbacks."""
    if isinstance(value, list):
        return [_hydrate_value(item, events) for item in value]
    if not isinstance(value, dict):
        return value

    type_name = value.get(_TYPE_KEY)
    if not type_name:
        return {key: _hydrate_value(item, events) for key, item in value.items()}

    data = _hydrate_value(value.get(_DATA_KEY), events)
    if type_name == "builtins:bytes":
        try:
            return base64.b64decode(str(value.get(_DATA_KEY) or ""))
        except Exception as exc:
            events.append(
                CompatibilityEvent(
                    source="paused_generation_codec",
                    compatibility_type="bytes_decode_failed",
                    reason=str(exc),
                    risk="medium",
                    metadata={"type_name": type_name},
                )
            )
            return b""
    if type_name in {
        "content_gen.agents.flow:FlowExecutionStep",
        "content_gen.workflow.flow_runner:FlowExecutionStep",
    }:
        payload = dict(data)
        payload.pop("step_index", None)
        try:
            return FlowExecutionStep(**payload)
        except Exception as exc:
            events.append(
                CompatibilityEvent(
                    source="paused_generation_codec",
                    compatibility_type="flow_step_hydration_failed",
                    reason=str(exc),
                    risk="medium",
                    metadata={"type_name": type_name},
                )
            )
            return payload

    model_cls = _resolve_pydantic_model(type_name)
    if model_cls is None:
        events.append(
            CompatibilityEvent(
                source="paused_generation_codec",
                compatibility_type="unknown_paused_type",
                reason=f"unsupported stored type: {type_name}",
                risk="medium",
                metadata={"type_name": type_name},
            )
        )
        return data
    try:
        return model_cls.model_validate(data)
    except Exception as exc:
        events.append(
            CompatibilityEvent(
                source="paused_generation_codec",
                compatibility_type="pydantic_model_validation_failed",
                reason=str(exc),
                risk="medium",
                metadata={"type_name": type_name},
            )
        )
        return data


def serialize_context(context: dict[str, Any]) -> dict[str, Any]:
    """Serialize mutable flow context for durable pause/resume."""
    safe_context = {
        key: value
        for key, value in context.items()
        if key not in _NON_SERIALIZABLE_CONTEXT_KEYS
    }
    serialized = serialize_value(safe_context)
    return serialized if isinstance(serialized, dict) else {}


def hydrate_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Hydrate a mutable flow context and restore its state pointer."""
    events: list[CompatibilityEvent] = []
    context = _hydrate_value(payload, events)
    if isinstance(context, dict):
        context.setdefault("compatibility_events", []).extend(
            event.model_dump(mode="json") for event in events
        )
    state = context.get("state") if isinstance(context, dict) else None
    if hasattr(state, "sync_from_context"):
        state.sync_from_context(context)
    return context


def serialize_steps(steps: list[Any]) -> list[dict[str, Any]]:
    """Serialize FlowExecutionStep values for DB storage."""
    serialized: list[dict[str, Any]] = []
    for step in steps or []:
        if isinstance(step, FlowExecutionStep):
            serialized.append(step.as_dict(len(serialized)))
        elif isinstance(step, dict):
            serialized.append(dict(step))
    return serialized


def hydrate_steps(payload: list[dict[str, Any]] | None) -> list[FlowExecutionStep]:
    """Hydrate DB step payload into FlowExecutionStep values."""
    steps: list[FlowExecutionStep] = []
    for item in payload or []:
        data = dict(item)
        data.pop("step_index", None)
        steps.append(FlowExecutionStep(**data))
    return steps


def _resolve_pydantic_model(type_name: str) -> type[BaseModel] | None:
    """Resolve a content_gen Pydantic model from a stored type name."""
    module_name, _, qualname = type_name.partition(":")
    if not module_name.startswith("content_gen."):
        return None
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return None

    obj: Any = module
    for part in qualname.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    if isinstance(obj, type) and issubclass(obj, BaseModel):
        return obj
    return None
