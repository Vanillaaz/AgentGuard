from __future__ import annotations
from .rules import Finding


def score(findings: list[Finding], session_risk: int = 0) -> int:
    """Combine finding severities and session risk into a 0-100 score.

    The result is the max of all individual finding severities plus a
    session-risk boost (capped at 100).  This means one high-severity rule
    dominates, and elevated session risk nudges borderline calls toward
    escalation.
    """
    base = max([f.severity for f in findings], default=0)
    return min(100, base + min(session_risk, 30))


def decide(findings: list[Finding], value: int, escalate: int, block: int) -> str:
    """Turn a numeric score and rule verdicts into ALLOW / ESCALATE / BLOCK."""
    if any(f.verdict == "block" for f in findings) or value >= block:
        return "BLOCK"
    if any(f.verdict == "escalate" for f in findings) or value >= escalate:
        return "ESCALATE"
    return "ALLOW"
