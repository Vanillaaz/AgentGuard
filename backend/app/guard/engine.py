"""Core guard evaluation engine.

``evaluate(request)`` is the single entry point used by both the HTTP
front-door (FastAPI) and the MCP front-door.  It runs the full pipeline:
kill-switch → rate-limit → deterministic rules → optional LLM check →
risk scoring → decision.  Every call writes a hash-chained audit event and
broadcasts it over the WebSocket.
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

from ..audit import write_event
from ..db import connection
from .policy import load_policy
from .rules import RULES, Finding
from .ratelimit import check as rate_check
from .llm import intent_check
from .risk import score as compute_score, decide

# ── module-level mutable state ────────────────────────────────────────────────
session_risk: dict[str, int] = {}
global_shadow: bool = False
event_listener: Callable[[dict], None] | None = None


# ── Pydantic models ───────────────────────────────────────────────────────────

class Request(BaseModel):
    """An incoming tool-call intercepted by AgentGuard."""

    agent_id: str
    session_id: str
    task: str  # the original user task for this session
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    recent_context: list[Any] = Field(default_factory=list)


class Decision(BaseModel):
    """The result returned to the caller after guard evaluation."""

    decision: str          # ALLOW | BLOCK | ESCALATE
    score: int             # 0-100 risk score
    reasons: list[str]
    triggered_rules: list[dict]
    llm_verdict: dict | None = None
    shadow: bool = False   # True ⟹ outcome was overridden to ALLOW
    event_id: str = ""
    session_drift: int = 0 # 0-100 cumulative behavioral drift
    ephemeral_token: dict | None = None # Temporary scoped credentials


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_agent_row(agent_id: str) -> dict:
    """Return the agents row, creating it with defaults if absent."""
    c = connection()
    c.execute(
        "INSERT OR IGNORE INTO agents(agent_id, status) VALUES (?, ?)",
        (agent_id, "active"),
    )
    row = c.execute(
        "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    c.commit()
    c.close()
    return dict(row)


def update_agent(agent_id: str, **vals: Any) -> None:
    """Upsert agent columns; used by scenarios to reset state."""
    c = connection()
    c.execute(
        "INSERT OR IGNORE INTO agents(agent_id, status) VALUES (?, ?)",
        (agent_id, "active"),
    )
    setters = ", ".join(f"{k} = ?" for k in vals)
    c.execute(
        f"UPDATE agents SET {setters} WHERE agent_id = ?",
        (*vals.values(), agent_id),
    )
    c.commit()
    c.close()


# Keep the private alias so existing imports keep working
_update = update_agent


# ── main pipeline ─────────────────────────────────────────────────────────────

def evaluate(request: "Request | dict") -> Decision:
    """Run the full guard pipeline and return a Decision.

    Steps (short-circuit on hard BLOCK):
    1. Kill-switch / agent status
    2. Rate-limit (auto-pauses on breach)
    3. Deterministic policy rules
    4. LLM intent check (only when needed)
    5. Risk scoring
    6. Shadow-mode override
    7. Audit log + WebSocket broadcast
    """
    r = Request.model_validate(request)
    p = load_policy()
    agent_cfg = p.agents.get(r.agent_id)

    # Fall back to permissive defaults for unknown agents so unknown agents
    # still get rate-limited (we use a sentinel object with default limits).
    if agent_cfg is None:
        from .policy import Agent as _Agent
        agent_cfg = _Agent()

    state = _get_agent_row(r.agent_id)
    findings: list[Finding] = []
    llm_result: dict | None = None

    # ── 1. kill switch ────────────────────────────────────────────────────────
    if state["status"] in ("paused", "killed"):
        findings = [
            Finding(
                "agent_status",
                "block",
                100,
                f"Agent is {state['status']} – all calls blocked",
            )
        ]

    # ── 2. rate limit ─────────────────────────────────────────────────────────
    elif not rate_check(
        r.agent_id,
        agent_cfg.rate_limit.calls,
        agent_cfg.rate_limit.per_seconds,
    ):
        findings = [
            Finding(
                "rate_limit",
                "block",
                100,
                "Rate limit exceeded; agent auto-paused",
            )
        ]
        update_agent(r.agent_id, status="paused")

    # ── 3. deterministic rules ────────────────────────────────────────────────
    else:
        recent_avg = _recent_payment_average(r.agent_id)
        for rule in RULES:
            findings.extend(rule(r.tool, r.arguments, agent_cfg, p, recent_avg))

        # ── 4. LLM intent check (only when warranted) ─────────────────────────
        args_text = str(r.arguments).lower()
        sensitive = any(
            kw in args_text for kw in ("email", "salary", "phone", "@", "aadhaar")
        )
        needs_llm = (
            any(f.verdict == "escalate" for f in findings)
            or r.tool in ("send_email", "make_payment", "delete_record")
            or sensitive
        )
        if needs_llm:
            try:
                verdict = intent_check(r.task, r.tool, r.arguments)
                llm_result = verdict.model_dump()
                if not verdict.intent_match:
                    findings.append(
                        Finding(
                            "intent_drift",
                            "escalate",
                            verdict.drift_score,
                            verdict.reason,
                        )
                    )
            except Exception:
                # Fail-safe: treat LLM errors as escalation triggers
                findings.append(
                    Finding(
                        "llm_unavailable",
                        "escalate",
                        50,
                        "LLM context check failed – fail-safe escalation applied",
                    )
                )

    # ── 5. risk scoring ───────────────────────────────────────────────────────
    sess_risk = session_risk.get(r.session_id, 0)
    risk_value = compute_score(findings, session_risk=sess_risk)
    outcome = decide(findings, risk_value, p.thresholds.escalate, p.thresholds.block)

    # ── 5b. cumulative behavioral drift tracking ──────────────────────────────
    if any(f.severity >= 20 for f in findings) or (llm_result and not llm_result.get("intent_match", True)):
        session_risk[r.session_id] = min(100, sess_risk + 10)
    current_drift = session_risk.get(r.session_id, 0)

    # ── 6. shadow mode & ephemeral credential downscaling ─────────────────────
    is_shadow = bool(global_shadow or state.get("shadow", 0))
    actual_decision = "ALLOW" if is_shadow else outcome

    ephemeral_token: dict | None = None
    if actual_decision == "ALLOW":
        import hashlib, time as _time
        _seed = f"{r.agent_id}:{r.tool}:{_time.time()}"
        _hash = hashlib.sha256(_seed.encode()).hexdigest()[:16]
        ephemeral_token = {
            "token": f"ag_sts_{_hash}",
            "scope": f"{r.tool}:execute",
            "ttl_seconds": 60,
            "status": "VALID_DOWN_SCOPED",
        }

    # ── 7. audit + broadcast ──────────────────────────────────────────────────
    reasons = [f.reason for f in findings] or ["No policy concerns found"]
    event_id = write_event(
        r.agent_id,
        r.session_id,
        r.tool,
        r.arguments,
        outcome,
        risk_value,
        reasons,
        is_shadow,
    )

    update_agent(
        r.agent_id,
        calls=state["calls"] + 1,
        blocked=state["blocked"] + (1 if outcome == "BLOCK" else 0),
    )

    result = Decision(
        decision=actual_decision,
        score=risk_value,
        reasons=reasons,
        triggered_rules=[f.__dict__ for f in findings],
        llm_verdict=llm_result,
        shadow=is_shadow,
        event_id=event_id,
        session_drift=current_drift,
        ephemeral_token=ephemeral_token,
    )

    if event_listener is not None:
        try:
            event_listener(
                {
                    "type": "decision",
                    "event_id": event_id,
                    **result.model_dump(),
                    "agent_id": r.agent_id,
                    "session_id": r.session_id,
                    "tool": r.tool,
                    "arguments": r.arguments,
                    "timestamp": __import__("datetime").datetime.now(
                        __import__("datetime").timezone.utc
                    ).isoformat(),
                }
            )
        except Exception:
            pass  # never let broadcast errors break the guard

    return result


def _recent_payment_average(agent_id: str) -> float:
    """Compute the average of recent approved payments for the agent (for
    the unusual_amount rule).  Falls back to 0 if there are no prior payments.
    """
    try:
        c = connection()
        rows = c.execute(
            """SELECT arguments_json FROM events
               WHERE agent_id = ? AND tool = 'make_payment' AND decision = 'ALLOW'
               ORDER BY rowid DESC LIMIT 10""",
            (agent_id,),
        ).fetchall()
        c.close()
        amounts = []
        for row in rows:
            import json
            args = json.loads(row["arguments_json"])
            if "amount" in args:
                amounts.append(float(args["amount"]))
        return sum(amounts) / len(amounts) if amounts else 0.0
    except Exception:
        return 0.0
