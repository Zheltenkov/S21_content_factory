"""Pure state derivation for methodology review sessions."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from content_gen.methodology import (
    MethodologistChangeRequest,
    ScopedRevisionExecutor,
    build_section_target_registry,
)
from content_gen.methodology.state_machine import MethodologyRuntimeState

from .methodology_review_artifacts import (
    context_preview_markdown,
    is_final_checkpoint_payload,
    refresh_checkpoint_artifact,
)

JsonDict = dict[str, Any]


def preview_hash(revision_results: list[JsonDict]) -> str:
    """Stable hash of currently previewed scoped revisions."""
    payload = json.dumps(revision_results or [], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def current_review_action_slice(review_actions: list[JsonDict]) -> tuple[int, list[JsonDict]]:
    """Return the active pause cycle and its original action index offset."""
    start_index = 0
    for index, action in enumerate(review_actions or []):
        if isinstance(action, dict) and action.get("action") in {"approved", "rejected"}:
            start_index = index + 1
    return start_index, list(review_actions or [])[start_index:]


def change_action_ids(review_actions: list[JsonDict], *, start_index: int = 0) -> list[str]:
    """Return stable action ids for scoped change requests."""
    ids: list[str] = []
    for offset, action in enumerate(review_actions or []):
        if not isinstance(action, dict) or action.get("action") != "changes_requested":
            continue
        details = action.get("details") if isinstance(action.get("details"), dict) else {}
        payload = details.get("change_request") if isinstance(details, dict) else None
        if not isinstance(payload, dict):
            continue
        try:
            request = MethodologistChangeRequest.model_validate(payload)
        except Exception:
            continue
        index = start_index + offset
        ids.append(ScopedRevisionExecutor.action_id_for_action(action, index, request))
    return ids


def revision_results_for_action_ids(
    revision_results: list[JsonDict],
    action_ids: list[str],
) -> list[JsonDict]:
    """Filter revision results to active change request ids."""
    action_id_set = {str(action_id) for action_id in action_ids if action_id}
    if not action_id_set:
        return [item for item in revision_results or [] if isinstance(item, dict)]
    return [
        item
        for item in revision_results or []
        if isinstance(item, dict) and str(item.get("action_id") or "") in action_id_set
    ]


def latest_review_action(review_actions: list[JsonDict], action_name: str) -> JsonDict | None:
    """Return the latest action with the supplied action name."""
    for action in reversed(review_actions or []):
        if isinstance(action, dict) and action.get("action") == action_name:
            return action
    return None


def build_methodology_review_state(paused_session: JsonDict) -> JsonDict:
    """Derive review state from immutable audit actions stored in paused-session."""
    review_actions = list(paused_session.get("review_actions") or [])
    active_start_index, active_review_actions = current_review_action_slice(review_actions)
    context = paused_session.get("context") or {}
    if is_final_checkpoint_payload(context.get("human_approval_checkpoint")):
        refresh_checkpoint_artifact(context)
    target_registry = build_section_target_registry(context).model_dump(mode="json")
    pending_change_ids = change_action_ids(active_review_actions, start_index=active_start_index)

    latest_preview = latest_review_action(active_review_actions, "preview_ready")
    latest_preview_details = latest_preview.get("details") if isinstance(latest_preview, dict) else {}
    if not isinstance(latest_preview_details, dict):
        latest_preview_details = {}
    preview_results = revision_results_for_action_ids(
        latest_preview_details.get("revision_results") or context.get("methodology_revision_results") or [],
        pending_change_ids,
    )
    if isinstance(latest_preview_details.get("target_registry"), dict):
        target_registry = latest_preview_details["target_registry"]
    preview_markdown = str(latest_preview_details.get("preview_markdown") or "")
    preview_action_ids = [
        str(item.get("action_id"))
        for item in preview_results
        if isinstance(item, dict) and item.get("action_id")
    ]
    preview_hash_value = str(latest_preview_details.get("preview_hash") or preview_hash(preview_results))
    preview_has_rejections = any(
        isinstance(item, dict) and item.get("status") == "rejected" for item in preview_results
    )

    approved_set: set[str] = set()
    for action in active_review_actions:
        if not isinstance(action, dict) or action.get("action") != "diff_approved":
            continue
        details = action.get("details") if isinstance(action.get("details"), dict) else {}
        approved_set.update(str(item) for item in details.get("approved_action_ids") or [] if item)
    approved_ids = [action_id for action_id in pending_change_ids if action_id in approved_set]

    pending_set = set(pending_change_ids)
    previewed_set = set(preview_action_ids)
    approved_set = set(approved_ids)
    unapproved_ids = [action_id for action_id in pending_change_ids if action_id not in approved_set]
    unapproved_set = set(unapproved_ids)
    diff_approvable_action_ids = [action_id for action_id in unapproved_ids if action_id in previewed_set]
    if not pending_set:
        review_state = "no_changes"
    elif not unapproved_set:
        review_state = "diff_approved"
    elif latest_preview and unapproved_set.issubset(previewed_set):
        review_state = "preview_ready"
    else:
        review_state = "changes_requested"

    checkpoint = context.get("human_approval_checkpoint")
    preview_context_payload = latest_preview_details.get("preview_context_payload")
    if review_state == "preview_ready" and isinstance(preview_context_payload, dict):
        preview_checkpoint = preview_context_payload.get("human_approval_checkpoint")
        if isinstance(preview_checkpoint, dict):
            checkpoint = preview_checkpoint

    return {
        "request_id": paused_session.get("request_id"),
        "status": paused_session.get("status", "needs_review"),
        "resume_from_index": paused_session.get("resume_from_index", 0),
        "review_state": review_state,
        "runtime_state": (
            MethodologyRuntimeState.CHANGES_REQUESTED.value
            if review_state in {"changes_requested", "preview_ready"}
            else MethodologyRuntimeState.NEEDS_REVIEW.value
        ),
        "requires_diff_approval": bool(unapproved_set),
        "pending_change_ids": pending_change_ids,
        "preview_action_ids": preview_action_ids,
        "approved_action_ids": approved_ids,
        "diff_approvable_action_ids": diff_approvable_action_ids,
        "preview_hash": preview_hash_value,
        "preview_has_rejections": preview_has_rejections,
        "review_actions": review_actions,
        "revision_results": preview_results,
        "preview_markdown": preview_markdown or context_preview_markdown(context),
        "target_registry": target_registry,
        "checkpoint": checkpoint,
        "methodology": paused_session.get("methodology"),
    }
