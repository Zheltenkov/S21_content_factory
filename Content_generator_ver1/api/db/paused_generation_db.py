"""DB-backed pause/resume storage for methodology review sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from api.utils.logger import get_logger
from content_gen.utils.rubric_export import convert_numpy_types

from .models import PausedGenerationSession
from .paused_generation_codec import hydrate_context, hydrate_steps, serialize_context, serialize_steps
from .session import SessionLocal

logger = get_logger("db.paused_generation")


def save_paused_generation_session(
    request_id: str,
    *,
    user_id: str,
    project_seed: dict[str, Any],
    track_paths: list[str],
    context: dict[str, Any],
    steps: list[Any],
    resume_from_index: int,
    methodology: dict[str, Any] | None = None,
) -> PausedGenerationSession:
    """Create or update a durable paused generation session."""
    db = SessionLocal()
    try:
        session = (
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.request_id == request_id)
            .first()
        )
        if session is None:
            session = PausedGenerationSession(
                request_id=request_id,
                user_id=user_id,
                review_actions=[],
            )
            db.add(session)

        session.user_id = user_id
        session.status = "needs_review"
        session.project_seed = convert_numpy_types(project_seed)
        session.track_paths = list(track_paths or [])
        session.context_payload = convert_numpy_types(serialize_context(context))
        session.steps_payload = convert_numpy_types(serialize_steps(steps))
        session.resume_from_index = int(resume_from_index or 0)
        session.methodology = convert_numpy_types(methodology) if methodology else None
        session.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(session)
        logger.debug("Paused generation session saved: %s", request_id)
        return session
    except Exception:
        db.rollback()
        logger.exception("Failed to save paused generation session: %s", request_id)
        raise
    finally:
        db.close()


def load_paused_generation_session(request_id: str) -> dict[str, Any] | None:
    """Load and hydrate a paused generation session for resume."""
    db = SessionLocal()
    try:
        session = (
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.request_id == request_id)
            .first()
        )
        if session is None or session.status != "needs_review":
            return None
        return _session_to_runtime_dict(session)
    finally:
        db.close()


def mark_paused_generation_approved(
    request_id: str,
    *,
    user_id: str,
    comment: str | None = None,
) -> dict[str, Any] | None:
    """Record reviewer approval and return hydrated runtime session."""
    db = SessionLocal()
    try:
        session = (
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.request_id == request_id)
            .first()
        )
        if session is None or session.status != "needs_review":
            return None
        _append_review_action(
            session,
            "approved",
            user_id=user_id,
            comment=comment,
            details=_human_checkpoint_approval_details(session.context_payload),
        )
        session.status = "approved"
        session.updated_at = datetime.utcnow()
        runtime = _session_to_runtime_dict(session)
        db.commit()
        return runtime
    except Exception:
        db.rollback()
        logger.exception("Failed to approve paused generation session: %s", request_id)
        raise
    finally:
        db.close()


def mark_paused_generation_rejected(
    request_id: str,
    *,
    user_id: str,
    comment: str | None = None,
) -> bool:
    """Record reviewer rejection and clear heavy context payload."""
    db = SessionLocal()
    try:
        session = (
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.request_id == request_id)
            .first()
        )
        if session is None or session.status != "needs_review":
            return False
        _append_review_action(session, "rejected", user_id=user_id, comment=comment)
        session.status = "rejected"
        session.context_payload = None
        session.steps_payload = None
        session.updated_at = datetime.utcnow()
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("Failed to reject paused generation session: %s", request_id)
        raise
    finally:
        db.close()


def record_paused_generation_change_request(
    request_id: str,
    *,
    user_id: str,
    change_request: dict[str, Any],
    conflicts: list[dict[str, Any]] | None = None,
    assistant_command: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Record a bounded methodologist change request without resuming the flow."""
    db = SessionLocal()
    try:
        session = (
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.request_id == request_id)
            .first()
        )
        if session is None or session.status != "needs_review":
            return None
        details = {
            "change_request": convert_numpy_types(change_request),
            "conflicts": convert_numpy_types(conflicts or []),
        }
        if assistant_command:
            details["assistant_command"] = convert_numpy_types(assistant_command)
        _append_review_action(
            session,
            "changes_requested",
            user_id=user_id,
            comment=str(change_request.get("instruction") or ""),
            details=details,
        )
        session.updated_at = datetime.utcnow()
        runtime = _session_to_runtime_dict(session)
        db.commit()
        return runtime
    except Exception:
        db.rollback()
        logger.exception("Failed to record paused generation change request: %s", request_id)
        raise
    finally:
        db.close()


def record_paused_generation_preview(
    request_id: str,
    *,
    user_id: str,
    revision_results: list[dict[str, Any]],
    target_registry: dict[str, Any],
    preview_hash: str,
    preview_context: dict[str, Any] | None = None,
    preview_markdown: str | None = None,
) -> dict[str, Any] | None:
    """Persist a dry-run diff preview for audit and explicit approval."""
    db = SessionLocal()
    try:
        session = (
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.request_id == request_id)
            .first()
        )
        if session is None or session.status != "needs_review":
            return None
        _append_review_action(
            session,
            "preview_ready",
            user_id=user_id,
            details={
                "revision_results": convert_numpy_types(revision_results),
                "target_registry": convert_numpy_types(target_registry),
                "preview_hash": preview_hash,
                "preview_context_payload": (
                    convert_numpy_types(serialize_context(preview_context))
                    if preview_context is not None
                    else None
                ),
                "preview_markdown": preview_markdown or "",
            },
        )
        session.updated_at = datetime.utcnow()
        runtime = _session_to_runtime_dict(session)
        db.commit()
        return runtime
    except Exception:
        db.rollback()
        logger.exception("Failed to record paused generation preview: %s", request_id)
        raise
    finally:
        db.close()


def mark_paused_generation_diff_approved(
    request_id: str,
    *,
    user_id: str,
    approved_action_ids: list[str],
    preview_hash: str,
    comment: str | None = None,
) -> dict[str, Any] | None:
    """Record explicit approval of the latest preview diff."""
    db = SessionLocal()
    try:
        session = (
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.request_id == request_id)
            .first()
        )
        if session is None or session.status != "needs_review":
            return None
        preview_details = _latest_review_action_details(session.review_actions or [], "preview_ready")
        if (
            preview_details
            and str(preview_details.get("preview_hash") or "") == str(preview_hash or "")
            and isinstance(preview_details.get("preview_context_payload"), dict)
        ):
            # Commit the exact previewed context so resume continues from the accepted diff,
            # instead of asking the LLM to reproduce the same scoped edit again.
            session.context_payload = preview_details["preview_context_payload"]
        _append_review_action(
            session,
            "diff_approved",
            user_id=user_id,
            comment=comment,
            details={
                "approved_action_ids": list(approved_action_ids or []),
                "preview_hash": preview_hash,
            },
        )
        session.updated_at = datetime.utcnow()
        runtime = _session_to_runtime_dict(session)
        db.commit()
        return runtime
    except Exception:
        db.rollback()
        logger.exception("Failed to approve paused generation diff: %s", request_id)
        raise
    finally:
        db.close()


def mark_paused_generation_completed(request_id: str) -> None:
    """Mark pause session as completed and drop heavy resume payload."""
    db = SessionLocal()
    try:
        session = (
            db.query(PausedGenerationSession)
            .filter(PausedGenerationSession.request_id == request_id)
            .first()
        )
        if session is None:
            return
        session.status = "completed"
        session.context_payload = None
        session.steps_payload = None
        session.updated_at = datetime.utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to complete paused generation session: %s", request_id)
        raise
    finally:
        db.close()


def _session_to_runtime_dict(session: PausedGenerationSession) -> dict[str, Any]:
    return {
        "request_id": session.request_id,
        "user_id": session.user_id,
        "project_seed": session.project_seed or {},
        "track_paths": session.track_paths or [],
        "context": hydrate_context(session.context_payload or {}),
        "steps": hydrate_steps(session.steps_payload or []),
        "resume_from_index": session.resume_from_index or 0,
        "methodology": session.methodology,
        "review_actions": list(session.review_actions or []),
        "status": session.status,
    }


def _append_review_action(
    session: PausedGenerationSession,
    action: str,
    *,
    user_id: str,
    comment: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    actions = list(session.review_actions or [])
    actions.append(
        {
            "action": action,
            "user_id": user_id,
            "comment": comment or "",
            "timestamp": datetime.utcnow().isoformat(),
            "details": details or {},
        }
    )
    session.review_actions = actions


def _latest_review_action_details(
    actions: list[dict[str, Any]],
    action: str,
) -> dict[str, Any] | None:
    for item in reversed(actions or []):
        if not isinstance(item, dict) or item.get("action") != action:
            continue
        details = item.get("details")
        return details if isinstance(details, dict) else {}
    return None


def _human_checkpoint_approval_details(context_payload: dict[str, Any] | None) -> dict[str, Any]:
    try:
        context = hydrate_context(context_payload or {})
    except Exception:
        logger.debug("Failed to hydrate checkpoint approval details", exc_info=True)
        return {}
    checkpoint = context.get("human_approval_checkpoint") if isinstance(context, dict) else None
    if not isinstance(checkpoint, dict):
        return {}
    checkpoint_id = str(checkpoint.get("id") or "")
    checkpoint_hash = str(checkpoint.get("artifact_hash") or "")
    details: dict[str, Any] = {
        "checkpoint": convert_numpy_types(checkpoint),
    }
    if checkpoint_id:
        details["checkpoint_id"] = checkpoint_id
    if checkpoint_hash:
        details["checkpoint_hash"] = checkpoint_hash
    if checkpoint.get("stage"):
        details["checkpoint_stage"] = str(checkpoint.get("stage"))
    return details
