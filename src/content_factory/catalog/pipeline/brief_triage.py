"""Candidate confidence, gray-zone council, and triage for stage_brief_to_catalog.

The decision side of the brief pipeline: score per-candidate ``_confidence`` from
evidence + resolution, run the multi-model council over gray-zone candidates, apply the
auto-accept policy, and triage each candidate into accepted/needs_review with reasons,
plus roll-up metrics. Extracted from ``stage_brief_to_catalog`` as a leaf (imports only
sibling leaves + models + config); the stage module re-imports the helpers it still
calls internally (``_confidence``/``_is_for_resolve``) and the public council/triage/
metrics entrypoints used by the intake viewer.
"""

from __future__ import annotations

from typing import Any

from . import config
from .brief_coverage import is_catalog_match_safe
from .models import Evidence, SkillCandidate
from .skill_names import has_observable_action


def _confidence(cand: SkillCandidate, evidence: list[Evidence]) -> float:
    evs = [e for e in evidence if e.id in cand.evidence_ids]
    fw = any(e.source_type in ("framework", "syllabus") for e in evs)
    evidence_confidence = min(min(0.5 + 0.2 * len(evs), 0.95) + (0.1 if fw else 0.0), 0.97)
    match_confidence = 0.0
    if cand.resolution in {"matched", "alias"}:
        match_confidence = 0.98
    elif cand.resolution == "fuzzy":
        match_confidence = min(max((cand.match_score or 0.0) / 100.0, 0.55), 0.93)
    elif cand.resolution == "new":
        match_confidence = 0.5
    return round(max(evidence_confidence if evs else 0.0, match_confidence), 2)


# --------- эвристическая панель по серой зоне + триаж ---------
# NB: this is NOT a real multi-model jury. Model names select a FIXED deterministic voting
# rule (no model is actually called), so it is a heuristic panel. run_council /
# select_council_candidates are kept as compatibility aliases; the honest names are
# run_heuristic_panel / select_heuristic_panel_candidates.
def _heuristic_vote(model: str, cand: SkillCandidate) -> int:
    """A fixed deterministic vote keyed by model name — a heuristic, not a model call."""
    n = len(set(cand.evidence_ids))
    if model.startswith("openai"):
        return 1
    if model.startswith("anthropic"):
        return 1 if n >= 2 else 0
    return 0 if (cand.resolution == "new" and cand.bloom >= 4) else 1


def _needs_panel(cand: SkillCandidate) -> bool:
    return not (cand.resolution in ("matched", "alias") and cand.confidence >= config.TAU_CONFIDENCE)


def _is_for_resolve(cand: SkillCandidate) -> bool:
    return cand.entity_type == "skill" and cand.atomicity == "atomic"


def select_council_candidates(cands: list[SkillCandidate]) -> list[SkillCandidate]:
    return [cand for cand in cands if _is_for_resolve(cand) and _needs_panel(cand)]


def run_heuristic_panel(cands: list[SkillCandidate]) -> dict[str, int]:
    """Run the deterministic heuristic panel over gray-zone candidates.

    Despite the ``council``/``MODEL_PANEL`` naming, no model is called: each "vote" is a
    fixed rule keyed by model name (see ``_heuristic_vote``). The panel adjusts confidence
    for candidates that are not confidently matched. The ``council_*`` fields/metric keys
    are kept for backward compatibility with the DB/UI.
    """
    panel_candidates = select_council_candidates(cands)
    if config.USE_COUNCIL:
        for cand in panel_candidates:
            votes = [_heuristic_vote(model, cand) for model in config.MODEL_PANEL]
            cand.council_ran = True
            cand.council_agreement = round(sum(votes) / len(votes), 2)
            cand.confidence = round(0.6 * cand.confidence + 0.4 * cand.council_agreement, 2)
    return {
        "sent_to_council": len(panel_candidates),
        "council_executed": len([cand for cand in cands if cand.council_ran]),
    }


#: Compatibility alias — the honest name is run_heuristic_panel (see its docstring).
run_council = run_heuristic_panel


def _meets_auto_accept_policy(cand: SkillCandidate, spec: dict[str, Any] | None = None) -> bool:
    artifact_type = str((spec or {}).get("artifact_type") or "").strip()
    if not has_observable_action(cand.name):
        return False
    # Новый skill в program_brief не публикуем автоматически: сначала нужен human check, иначе каталог быстро загрязняется.
    if (
        artifact_type in {"program_brief", "mixed"}
        and cand.resolution == "new"
        and not config.AUTO_ACCEPT_NEW_FOR_PROGRAM_BRIEF
    ):
        return False
    if not is_catalog_match_safe(cand, spec):
        return False
    return (
        cand.council_agreement is not None
        and cand.confidence >= config.AUTO_ACCEPT_CONFIDENCE
        and cand.council_agreement >= config.AUTO_ACCEPT_COUNCIL_AGREEMENT
    )


def triage_candidates(cands: list[SkillCandidate], spec: dict[str, Any] | None = None) -> None:
    artifact_type = str((spec or {}).get("artifact_type") or "").strip()
    for c in cands:
        if not _is_for_resolve(c):
            continue
        r = list(dict.fromkeys(c.reasons or []))
        n = len(set(c.evidence_ids))
        if c.resolution == "new":
            r.append("novel_skill")
            if artifact_type in {"program_brief", "mixed"} and not config.AUTO_ACCEPT_NEW_FOR_PROGRAM_BRIEF:
                r.append("program_brief_publication_guardrail")
        if c.resolution == "fuzzy":
            r.append("fuzzy_match_ambiguous")
        if c.resolution in {"matched", "alias", "fuzzy"} and not is_catalog_match_safe(c, spec):
            r.append("catalog_match_suspicious")
        if not has_observable_action(c.name):
            r.append("missing_observable_action")
        if c.confidence < config.TAU_CONFIDENCE:
            r.append("low_confidence")
        if n < config.MIN_SOURCES and c.resolution not in {"matched", "alias"}:
            r.append("single_source")
        if c.council_ran and c.council_agreement is not None and c.council_agreement < config.COUNCIL_AGREE_OK:
            r.append("council_split")
        r = list(dict.fromkeys(r))
        if not r and _meets_auto_accept_policy(c, spec):
            c.decision = "accepted"
            c.reasons = ["auto_accept_policy"]
            continue
        if c.resolution in {"matched", "alias"}:
            r = [reason for reason in r if reason not in {"novel_skill", "single_source", "fuzzy_match_ambiguous"}]
        c.decision = "accepted" if not r else "needs_review"
        c.reasons = r


def build_candidate_metrics(cands: list[SkillCandidate]) -> dict[str, int]:
    resolved_candidates = [cand for cand in cands if _is_for_resolve(cand)]
    return {
        "total_candidates": len(cands),
        "atomic_skill_candidates": len(resolved_candidates),
        "composite_candidates": len([cand for cand in cands if cand.atomicity == "composite"]),
        "non_skill_candidates": len([cand for cand in cands if cand.atomicity == "non_skill"]),
        "auto_accepted": len([cand for cand in resolved_candidates if not cand.council_ran and cand.decision == "accepted"]),
        "sent_to_council": len([cand for cand in resolved_candidates if cand.council_ran]),
        "accepted_after_council": len([cand for cand in resolved_candidates if cand.council_ran and cand.decision == "accepted"]),
        "review_after_council": len([cand for cand in resolved_candidates if cand.council_ran and cand.decision == "needs_review"]),
        "needs_review_total": len([cand for cand in cands if cand.decision == "needs_review"]),
        "accepted_total": len([cand for cand in resolved_candidates if cand.decision == "accepted"]),
        "matched_total": len([cand for cand in resolved_candidates if cand.resolution == "matched"]),
        "alias_total": len([cand for cand in resolved_candidates if cand.resolution == "alias"]),
        "fuzzy_total": len([cand for cand in resolved_candidates if cand.resolution == "fuzzy"]),
        "new_total": len([cand for cand in resolved_candidates if cand.resolution == "new"]),
    }
