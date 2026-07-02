"""Triage of findings into two tiers for the reviewer: "fix first" vs "review".

Cuts perceived noise without losing coverage: high-precision deterministic
checkers and actionable (major/critical) findings go to the "fix" tier; the rest
(minor stylistic / low-confidence) go to "review". Same-issue findings are
grouped by (unit, criterion, issue_type) so a reviewer sees distinct problems,
not every repeated line.

Pure, offline, no dependencies beyond the domain model.
"""

from __future__ import annotations

from collections import defaultdict

# Deterministic / high-precision modules: trust their output to the fix tier.
HIGH_PRECISION_CHECKERS = {
    "broken_url_syntax_checker",
    "label_punctuation_checker",
    "local_consistency_checker",
    "markdown_structure_checker",
    "checklist_checker",
    "cross_file_consistency_checker",
    "course_material_relevance_checker",
    "curriculum_relevance_checker",
    "link_checker",
    "local_link_checker",
}
ACTIONABLE_SEVERITIES = {"critical", "major"}


def _sev(finding) -> str:
    s = getattr(finding, "severity", "")
    return getattr(s, "value", s) or ""


def _crit(finding) -> str:
    c = getattr(finding, "criterion", "")
    return getattr(c, "value", c) or ""


def _issue_type(finding) -> str:
    extra = getattr(finding, "extra", {}) or {}
    return str(extra.get("issue_type") or "")


def is_fix_tier(finding) -> bool:
    """A finding belongs to the 'fix first' tier."""

    return _sev(finding) in ACTIONABLE_SEVERITIES or getattr(finding, "checker_name", "") in HIGH_PRECISION_CHECKERS


def _group_count(findings) -> int:
    groups = {(getattr(f, "unit_id", ""), _crit(f), _issue_type(f)) for f in findings}
    return len(groups)


def triage_findings(findings) -> dict:
    """Splits findings into 'fix' and 'review' tiers with grouped counts."""

    fix = [f for f in findings if is_fix_tier(f)]
    review = [f for f in findings if not is_fix_tier(f)]
    by_sev: dict = defaultdict(int)
    for f in fix:
        by_sev[_sev(f)] += 1
    return {
        "total": len(findings),
        "fix": len(fix),
        "fix_groups": _group_count(fix),
        "review": len(review),
        "review_groups": _group_count(review),
        "fix_by_severity": dict(sorted(by_sev.items())),
        "fix_items": fix,
        "review_items": review,
    }
