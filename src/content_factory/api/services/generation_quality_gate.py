"""Final deterministic quality gate for generated project artifacts."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any

_BLOCKING_SEVERITIES = {"critical", "hard", "error"}
_MOJIBAKE_PATTERNS = (
    re.compile(r"(?:Ð.|Ñ.){2,}"),
    re.compile(r"â(?:€|€™|€œ|€\x9d|€“|€”|†|‡)"),
)


@dataclass(frozen=True)
class QualityGateFinding:
    """One release-blocking defect with a stable machine-readable code."""

    code: str
    message: str
    severity: str = "hard"


def collect_blocking_quality_findings(result: Any, markdown: str) -> list[QualityGateFinding]:
    """Collect unresolved structural, critic, and text-integrity failures."""

    findings = collect_text_integrity_findings(markdown)
    report = getattr(result, "report_json", {})
    report_issues = report.get("issues") if isinstance(report, dict) else []
    for issue in report_issues or []:
        payload = _issue_payload(issue)
        severity = str(payload.get("severity") or "").strip().lower()
        if severity in _BLOCKING_SEVERITIES:
            findings.append(
                QualityGateFinding(
                    code=str(payload.get("code") or payload.get("criterion_id") or "quality.blocking_issue"),
                    message=str(payload.get("message") or issue),
                    severity=severity,
                )
            )

    critic_issues = getattr(result, "practice_critic_issues", None) or []
    for issue in critic_issues:
        payload = _issue_payload(issue)
        severity = str(payload.get("severity") or "").strip().lower()
        if severity in _BLOCKING_SEVERITIES:
            findings.append(
                QualityGateFinding(
                    code=f"practice_critic.{payload.get('kind') or 'blocking_issue'}",
                    message=str(payload.get("message") or issue),
                    severity=severity,
                )
            )
    return _deduplicate_findings(findings)


def collect_text_integrity_findings(markdown: str) -> list[QualityGateFinding]:
    """Reject replacement characters, mojibake, and unsafe control bytes."""

    findings: list[QualityGateFinding] = []
    if "\ufffd" in markdown:
        findings.append(
            QualityGateFinding(
                code="text.replacement_character",
                message="README contains the Unicode replacement character U+FFFD.",
            )
        )
    if any(pattern.search(markdown) for pattern in _MOJIBAKE_PATTERNS):
        findings.append(
            QualityGateFinding(
                code="text.mojibake",
                message="README contains byte-decoding mojibake sequences.",
            )
        )
    if any(ord(char) < 32 and char not in "\n\r\t" for char in markdown):
        findings.append(
            QualityGateFinding(
                code="text.control_character",
                message="README contains unsupported control characters.",
            )
        )
    return findings


def _issue_payload(issue: Any) -> dict[str, Any]:
    if isinstance(issue, dict):
        return issue
    if hasattr(issue, "model_dump"):
        payload = issue.model_dump(mode="json")
        return payload if isinstance(payload, dict) else {}
    if hasattr(issue, "__dict__"):
        return dict(issue.__dict__)
    if not isinstance(issue, str):
        return {}
    for parser in (json.loads, ast.literal_eval):
        try:
            payload = parser(issue)
        except (TypeError, ValueError, SyntaxError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _deduplicate_findings(findings: list[QualityGateFinding]) -> list[QualityGateFinding]:
    unique: dict[tuple[str, str], QualityGateFinding] = {}
    for finding in findings:
        unique[(finding.code, finding.message)] = finding
    return list(unique.values())
