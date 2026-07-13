"""Immutable provenance snapshots for UP row-to-template bindings."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from content_factory.catalog.db import CatalogConnection

from .artifact_templates import load_curriculum_artifact_templates_for_brief

_SNAPSHOT_SCHEMA_VERSION = 1


class TemplateBindingSnapshotError(ValueError):
    """Base error for an invalid or non-reproducible template binding."""


class TemplateBindingVersionMismatchError(TemplateBindingSnapshotError):
    """The planner and persistence layer observed different template revisions."""


class TemplateBindingNotFoundError(TemplateBindingSnapshotError):
    """The bound template is no longer visible to the brief at save time."""


class TemplateBindingIntegrityError(TemplateBindingSnapshotError):
    """A persisted snapshot no longer matches its integrity digest."""


def canonical_template_snapshot_json(snapshot: dict[str, Any]) -> str:
    """Serialize a snapshot deterministically for hashing and persistence."""

    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def template_snapshot_sha256(snapshot: dict[str, Any]) -> str:
    """Return the content identity of a normalized template snapshot."""

    raw = canonical_template_snapshot_json(snapshot).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _resolved_source(template: dict[str, Any]) -> str:
    return "brief" if str(template.get("source") or "").strip().casefold() == "brief" else "global"


def _template_version(template: dict[str, Any]) -> str:
    return str(template.get("updated_at") or template.get("created_at") or "").strip()


def _normalized_template_snapshot(template: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Whitelist only generation-relevant fields into the durable snapshot."""

    scopes: list[dict[str, Any]] = []
    for raw_scope in template.get("scopes") or []:
        if not isinstance(raw_scope, dict):
            continue
        scopes.append(
            {
                "scope_type": str(raw_scope.get("scope_type") or ""),
                "scope_id": raw_scope.get("scope_id"),
                "scope_name": str(raw_scope.get("scope_name") or ""),
                "normalized_scope_name": str(raw_scope.get("normalized_scope_name") or ""),
                "weight": float(raw_scope.get("weight", 1.0) or 1.0),
            }
        )
    return {
        "schema_version": _SNAPSHOT_SCHEMA_VERSION,
        "template_id": int(template["id"]) if template.get("id") is not None else None,
        "proposal_id": int(template["proposal_id"]) if template.get("proposal_id") is not None else None,
        "template_code": str(template.get("code") or ""),
        "template_version": _template_version(template),
        "source": source,
        "repeatable": bool(template.get("repeatable", False)),
        "title": str(template.get("title") or ""),
        "artifact_family": str(template.get("artifact_family") or ""),
        "artifact_description": str(template.get("artifact_description") or ""),
        "project_name_pattern": str(template.get("project_name_pattern") or ""),
        "materials_pattern": str(template.get("materials_pattern") or ""),
        "storytelling_pattern": str(template.get("storytelling_pattern") or ""),
        "validation_criteria": str(template.get("validation_criteria") or ""),
        "priority": int(template.get("priority", 100) or 100),
        "status": str(template.get("status") or "active"),
        "scopes": scopes,
    }


def resolve_template_binding_snapshot(
    con: CatalogConnection,
    *,
    brief_id: int,
    binding: dict[str, Any],
) -> dict[str, Any]:
    """Resolve and freeze the exact visible template referenced by a planner row.

    A non-empty planner version is checked before saving.  This closes the race
    where a methodologist edits a template between planning and persistence.
    """

    code = str(binding.get("template_code") or "").strip()
    source = str(binding.get("source") or "").strip().casefold()
    if not code or source not in {"brief", "global"}:
        raise TemplateBindingSnapshotError("Template binding must contain code and brief/global source")

    template = next(
        (
            candidate
            for candidate in load_curriculum_artifact_templates_for_brief(con, brief_id)
            if str(candidate.get("code") or "").strip() == code and _resolved_source(candidate) == source
        ),
        None,
    )
    if template is None:
        raise TemplateBindingNotFoundError(
            f"Template {source}:{code} is not visible to brief {brief_id} at plan persistence time"
        )

    expected_version = str(binding.get("template_version") or "").strip()
    current_version = _template_version(template)
    if expected_version and expected_version != current_version:
        raise TemplateBindingVersionMismatchError(
            f"Template {source}:{code} changed before plan persistence "
            f"({expected_version!r} != {current_version!r})"
        )

    snapshot = _normalized_template_snapshot(template, source=source)
    # The planner binding remains authoritative for repeatability; disagreement is
    # a contract violation rather than a silently rewritten plan.
    expected_repeatable = bool(binding.get("repeatable", False))
    if expected_repeatable != bool(snapshot["repeatable"]):
        raise TemplateBindingVersionMismatchError(
            f"Template {source}:{code} repeatability changed before plan persistence"
        )
    return snapshot


def decode_and_verify_template_snapshot(snapshot_json: str, expected_sha256: str) -> dict[str, Any]:
    """Parse a stored snapshot and fail closed when its content was corrupted."""

    try:
        snapshot = json.loads(snapshot_json)
    except json.JSONDecodeError as exc:
        raise TemplateBindingIntegrityError("Template snapshot is not valid JSON") from exc
    if not isinstance(snapshot, dict):
        raise TemplateBindingIntegrityError("Template snapshot must be a JSON object")
    actual_sha256 = template_snapshot_sha256(snapshot)
    if actual_sha256 != expected_sha256:
        raise TemplateBindingIntegrityError(
            f"Template snapshot digest mismatch ({actual_sha256} != {expected_sha256})"
        )
    return snapshot
