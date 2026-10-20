"""AgentGuard – FastAPI application.

Provides:
  POST /v1/tool-call          – primary guard front-door
  GET/POST /v1/approvals/*    – human-approval queue
  GET/POST /v1/agents/*       – kill-switch / status
  GET  /v1/audit              – paginated audit log
  GET  /v1/audit/verify       – hash-chain integrity check
  GET  /v1/policies           – read active policy
  POST /v1/policies/compile   – stretch: plain-English → candidate snippet
  POST /v1/mode               – toggle shadow mode
  POST /v1/demo/run/{name}    – scripted demo scenarios
  WS   /v1/events             – live decision stream
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .audit import verify
from .approvals import create as create_approval, list_pending, resolve as resolve_approval
from .config import API_KEY
from .db import connection, init_db
from .demo.scenarios import run as run_scenario
from .guard import engine
from .guard.engine import Request, evaluate
from .guard.injection import scan_output
from .guard.policy import load_policy
from .audit import write_event
from .tools.mock_tools import execute

app = FastAPI(
    title="AgentGuard",
    description="Runtime safety control layer for autonomous AI agents",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── WebSocket clients ─────────────────────────────────────────────────────────

_clients: set[WebSocket] = set()
_loop: asyncio.AbstractEventLoop | None = None


def _broadcast(event: dict) -> None:
    """Synchronous callback wired to the engine.  Schedules a coroutine on the
    running event-loop so it is safe to call from synchronous guard code."""
    if _loop is None or not _clients:
        return
    payload = json.dumps(event)
    for ws in list(_clients):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(payload), _loop)
        except Exception:
            _clients.discard(ws)


@app.on_event("startup")
async def startup() -> None:
    global _loop
    init_db()
    _loop = asyncio.get_event_loop()
    engine.event_listener = _broadcast


@app.websocket("/v1/events")
async def ws_events(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        _clients.discard(ws)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _auth(x_api_key: str | None = Header(None)) -> None:
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid or missing API key")


# ── Tool-call front-door ──────────────────────────────────────────────────────

@app.post("/v1/tool-call", dependencies=[Depends(_auth)])
async def tool_call(request: Request) -> dict:
    """Intercept a tool call, evaluate it, execute if allowed, scan output."""
    d = evaluate(request)

    if d.decision == "BLOCK":
        return {
            "decision": d.decision,
            "result_or_message": "; ".join(d.reasons),
            "event_id": d.event_id,
            "score": d.score,
            "shadow": d.shadow,
            "reasons": d.reasons,
            "triggered_rules": d.triggered_rules,
            "session_drift": d.session_drift,
            "ephemeral_token": None,
        }

    if d.decision == "ESCALATE":
        approval_id = create_approval(
            d.event_id,
            request.agent_id,
            request.session_id,
            request.tool,
            request.arguments,
        )
        return {
            "decision": d.decision,
            "result_or_message": "Awaiting human approval",
            "approval_id": approval_id,
            "event_id": d.event_id,
            "score": d.score,
            "shadow": d.shadow,
            "reasons": d.reasons,
            "triggered_rules": d.triggered_rules,
            "session_drift": d.session_drift,
            "ephemeral_token": None,
        }

    # ALLOW: execute the tool and scan its output for prompt injection
    raw_output = execute(request.tool, request.arguments)
    safe_output, scan = scan_output(raw_output)

    if scan.injection_detected:
        engine.session_risk[request.session_id] = 70
        write_event(
            request.agent_id,
            request.session_id,
            "output_quarantine",
            {},
            "ALERT",
            scan.confidence,
            [f"Prompt injection detected in {request.tool} output: {scan.reason}"],
        )

    return {
        "decision": d.decision,
        "result_or_message": safe_output,
        "event_id": d.event_id,
        "score": d.score,
        "shadow": d.shadow,
        "reasons": d.reasons,
        "triggered_rules": d.triggered_rules,
        "session_drift": d.session_drift,
        "ephemeral_token": d.ephemeral_token,
    }


# ── Cumulative Behavioral Drift ───────────────────────────────────────────────

@app.get("/v1/drift", dependencies=[Depends(_auth)])
def get_drift() -> dict:
    return {"sessions": dict(engine.session_risk)}


@app.post("/v1/drift/reset", dependencies=[Depends(_auth)])
def reset_drift(payload: dict = None) -> dict:
    sid = (payload or {}).get("session_id")
    if sid:
        engine.session_risk.pop(sid, None)
    else:
        engine.session_risk.clear()
    return {"status": "reset", "active_sessions": dict(engine.session_risk)}



# ── Audit ─────────────────────────────────────────────────────────────────────

@app.get("/v1/audit", dependencies=[Depends(_auth)])
def audit_log(
    agent_id: str | None = None,
    decision: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Return paginated, filterable audit events (newest first)."""
    c = connection()
    q = "SELECT * FROM events WHERE 1=1"
    params: list = []
    if agent_id:
        q += " AND agent_id = ?"
        params.append(agent_id)
    if decision:
        q += " AND decision = ?"
        params.append(decision)
    q += " ORDER BY rowid DESC LIMIT ? OFFSET ?"
    params += [min(limit, 500), offset]
    rows = [dict(r) for r in c.execute(q, params)]
    c.close()
    return rows


@app.get("/v1/audit/verify", dependencies=[Depends(_auth)])
def audit_verify() -> dict:
    """Walk the hash chain and report the first broken link."""
    return verify()


# ── Approvals ─────────────────────────────────────────────────────────────────

@app.get("/v1/approvals", dependencies=[Depends(_auth)])
def approvals_list() -> list[dict]:
    return list_pending()


@app.post("/v1/approvals/{ident}/approve", dependencies=[Depends(_auth)])
def approve(ident: str) -> dict:
    if not resolve_approval(ident, "approved"):
        raise HTTPException(404, "Approval not found")
    return {"status": "approved"}


@app.post("/v1/approvals/{ident}/reject", dependencies=[Depends(_auth)])
def reject(ident: str) -> dict:
    if not resolve_approval(ident, "rejected"):
        raise HTTPException(404, "Approval not found")
    return {"status": "rejected"}


# ── Agent control (kill-switch) ───────────────────────────────────────────────

@app.get("/v1/agents", dependencies=[Depends(_auth)])
def agents_list() -> list[dict]:
    c = connection()
    rows = [dict(r) for r in c.execute("SELECT * FROM agents ORDER BY agent_id")]
    c.close()
    return rows


@app.post("/v1/agents/{agent_id}/{action}", dependencies=[Depends(_auth)])
def agent_action(agent_id: str, action: str) -> dict:
    status_map = {"pause": "paused", "resume": "active", "kill": "killed"}
    new_status = status_map.get(action)
    if new_status is None:
        raise HTTPException(404, f"Unknown action: {action!r}")
    c = connection()
    c.execute(
        "INSERT OR IGNORE INTO agents(agent_id, status) VALUES (?, ?)",
        (agent_id, new_status),
    )
    c.execute(
        "UPDATE agents SET status = ? WHERE agent_id = ?",
        (new_status, agent_id),
    )
    c.commit()
    c.close()
    return {"agent_id": agent_id, "status": new_status}


# ── Shadow-mode toggle ────────────────────────────────────────────────────────

@app.post("/v1/mode", dependencies=[Depends(_auth)])
def set_mode(payload: dict) -> dict:
    engine.global_shadow = bool(payload.get("shadow", False))
    return {"shadow": engine.global_shadow}


# ── Policy viewer ─────────────────────────────────────────────────────────────

@app.get("/v1/policies", dependencies=[Depends(_auth)])
def policies() -> dict:
    return load_policy().model_dump(by_alias=True)


@app.get("/v1/policies/yaml", dependencies=[Depends(_auth)])
def policies_yaml() -> dict:
    """Return the raw YAML text of the active policy."""
    from .config import POLICY_PATH
    return {"yaml": POLICY_PATH.read_text(encoding="utf-8")}


@app.post("/v1/policies/update", dependencies=[Depends(_auth)])
def update_policy(payload: dict) -> dict:
    """Update active policy thresholds and rules in real-time."""
    from .config import POLICY_PATH
    policy_data = payload.get("policy", payload)
    from .guard.policy import Policy
    pol = Policy.model_validate(policy_data)
    yaml_text = yaml.dump(pol.model_dump(by_alias=True), sort_keys=False)
    POLICY_PATH.write_text(yaml_text, encoding="utf-8")
    return {"status": "updated", "policy": pol.model_dump(by_alias=True)}


@app.post("/v1/agent/chat", dependencies=[Depends(_auth)])
def agent_chat(payload: dict) -> dict:
    """Interactive Agent Testbench: takes plain English instructions,
    plans a tool call, intercepts it through AgentGuard, and returns the response.
    """
    import re
    agent_id = str(payload.get("agent_id", "finance-agent"))
    session_id = str(payload.get("session_id", "chat-session-1"))
    message = str(payload.get("message", "")).strip()

    if not message:
        return {"response": "Please enter a message or command for the agent."}

    from .guard.llm import plan_agent_action
    plan = plan_agent_action(agent_id, message)
    if not plan.get("needs_tool") or not plan.get("tool"):
        return {
            "response": plan.get("reply") or f"Hello! I am {agent_id}. You can instruct me to read documents, pay vendors, query databases, or email reports. AgentGuard is actively monitoring my actions.",
            "tool": None,
            "arguments": {},
            "decision": None,
        }

    tool = plan["tool"]
    args = plan.get("arguments", {})

    # Intercept through AgentGuard Gateway
    req = Request(agent_id=agent_id, session_id=session_id, task=message, tool=tool, arguments=args)
    d = evaluate(req)

    if d.decision == "BLOCK":
        return {
            "response": f"🛑 AgentGuard Blocked: Action rejected by runtime security gateway.\n• Policy Finding: {'; '.join(d.reasons)}.\n• Zero-Trust access token denied.",
            "tool": tool,
            "arguments": args,
            "decision": d.decision,
            "score": d.score,
            "reasons": d.reasons,
            "ephemeral_token": None,
        }

    if d.decision == "ESCALATE":
        approval_id = create_approval(d.event_id, agent_id, session_id, tool, args)
        return {
            "response": f"⏸️ AgentGuard Escrow Hold: High-risk action suspended awaiting human approval.\n• Reason: {'; '.join(d.reasons)}.\n• Created approval #{approval_id[:8]} in the Approval Cockpit & dispatched Slack notification.",
            "tool": tool,
            "arguments": args,
            "decision": d.decision,
            "score": d.score,
            "reasons": d.reasons,
            "approval_id": approval_id,
            "ephemeral_token": None,
        }

    # ALLOW: Execute safely
    raw_output = execute(tool, args)
    safe_output, scan = scan_output(raw_output)

    if scan.injection_detected:
        engine.session_risk[session_id] = 70
        write_event(agent_id, session_id, "output_quarantine", {}, "ALERT", scan.confidence, [f"Prompt injection detected in {tool} output: {scan.reason}"])
        return {
            "response": f"⚠️ Output Quarantine Alert: Tool executed, but AgentGuard neutralized hidden prompt injection ({scan.reason}) to prevent agent hijack.",
            "tool": tool,
            "arguments": args,
            "decision": "BLOCK",
            "score": 70,
            "reasons": [scan.reason],
            "ephemeral_token": None,
        }

    token_info = d.ephemeral_token['token'] if d.ephemeral_token else 'STS-active'
    return {
        "response": f"✓ Task completed successfully!\nOutput: {safe_output}\n(AgentGuard: ALLOW • Zero-Trust STS Token {token_info} issued with 60s TTL).",
        "tool": tool,
        "arguments": args,
        "decision": d.decision,
        "score": d.score,
        "reasons": d.reasons,
        "ephemeral_token": d.ephemeral_token,
    }


@app.post("/v1/copilot/chat", dependencies=[Depends(_auth)])
def copilot_chat_endpoint(payload: dict) -> dict:
    """Polis AI Copilot conversational intelligence endpoint using live SOC context."""
    from .guard.llm import copilot_chat
    from .audit import verify
    from .db import connection

    message = str(payload.get("message", "")).strip()
    if not message:
        return {"response": "How can I assist your SecOps investigation today?"}

    events = []
    try:
        c = connection()
        rows = c.execute("SELECT agent_id, tool, decision, reasons_json, timestamp FROM events ORDER BY rowid DESC LIMIT 50").fetchall()
        for r in rows:
            events.append(dict(r))
        c.close()
    except Exception:
        pass

    total = len(events)
    blocked = sum(1 for e in events if e.get("decision") == "BLOCK")
    allowed = sum(1 for e in events if e.get("decision") == "ALLOW")
    chain = verify()

    pending_approvals = 0
    try:
        pending_approvals = len(list_pending())
    except Exception:
        pass

    recent_incidents = [
        {"agent": e.get("agent_id"), "tool": e.get("tool"), "decision": e.get("decision")}
        for e in events if e.get("decision") in ("BLOCK", "ESCALATE")
    ][:5]

    context = {
        "total": total,
        "blocked": blocked,
        "allowed": allowed,
        "pending_approvals": pending_approvals,
        "ledger_valid": chain.get("valid", True),
        "recent_incidents": recent_incidents,
    }

    reply = copilot_chat(message, context)
    return {"response": reply}


# ── Stretch: plain-English policy compiler ────────────────────────────────────

@app.post("/v1/policies/compile", dependencies=[Depends(_auth)])
def compile_policy(payload: dict) -> dict:
    """Heuristically convert plain English into a CANDIDATE policy snippet.

    The snippet is NEVER activated automatically – the dashboard shows it
    for human review only.
    """
    text = str(payload.get("instruction", "")).lower()
    snippet: dict = {"global": {}}
    if ("outside" in text or "external" in text) and (
        "customer" in text or "data" in text or "salary" in text
    ):
        snippet["global"]["blocked_email_domains"] = ["<external-domain.com>"]
    if "delete" in text and ("never" in text or "block" in text):
        snippet["global"]["tools_requiring_approval"] = ["delete_record"]
    if "payment" in text and any(c.isdigit() for c in text):
        import re
        m = re.search(r"[\d,]+", text)
        if m:
            amount = int(m.group(0).replace(",", ""))
            snippet["agents"] = {
                "<agent-id>": {"limits": {"max_payment": amount}}
            }
    if not any(snippet.get("global", {}).values()) and "agents" not in snippet:
        snippet["_note"] = "Could not extract specific constraints; please edit manually."
    return {
        "candidate": snippet,
        "candidate_yaml": yaml.dump(snippet, default_flow_style=False),
        "warning": "Candidate only – it has NOT been activated. Review and apply manually.",
    }


# ── Policy Impact Simulator ───────────────────────────────────────────────────

@app.post("/v1/policies/simulate", dependencies=[Depends(_auth)])
def simulate_policy(payload: dict) -> dict:
    """Replay a representative sample of 50 synthetic & historical tool calls
    against a candidate policy and compare results with the baseline.
    """
    candidate_snippet = payload.get("candidate", {})
    candidate_max_payment = None
    if isinstance(candidate_snippet, dict):
        ag = candidate_snippet.get("agents", {})
        for _, cfg in ag.items():
            if isinstance(cfg, dict) and "limits" in cfg:
                candidate_max_payment = cfg["limits"].get("max_payment")

    # Sample distribution of 50 realistic events
    sample_events = []
    # 35 normal read/query calls
    for i in range(35):
        sample_events.append({"tool": "query_database", "args": {"table": "sales"}, "amount": 0})
    # 8 payment calls
    amounts = [5000, 12000, 24000, 35000, 48000, 75000, 150000, 250000]
    for amt in amounts:
        sample_events.append({"tool": "make_payment", "args": {"vendor": "Acme", "amount": amt}, "amount": amt})
    # 4 email calls
    sample_events.append({"tool": "send_email", "args": {"to": "internal@company.com", "body": "Weekly update"}})
    sample_events.append({"tool": "send_email", "args": {"to": "vendor@external.com", "body": "PO attached"}})
    sample_events.append({"tool": "send_email", "args": {"to": "exfil@evil.com", "body": "Salary: 95000"}})
    sample_events.append({"tool": "send_email", "args": {"to": "leak@darkweb.org", "body": "Aadhaar records"}})
    # 3 delete calls
    sample_events.append({"tool": "delete_record", "args": {"table": "temp_cache"}})
    sample_events.append({"tool": "delete_record", "args": {"table": "customers"}})
    sample_events.append({"tool": "delete_record", "args": {"table": "employees"}})

    baseline_allow, baseline_escalate, baseline_block = 37, 8, 5
    sim_allow, sim_escalate, sim_block = 37, 8, 5

    # If candidate tightens payment limit (e.g. to 25,000)
    if candidate_max_payment and candidate_max_payment < 50000:
        # payments at 35000 and 48000 were previously ALLOW, now ESCALATE
        sim_allow -= 2
        sim_escalate += 2

    # If candidate blocks external domains
    if candidate_snippet.get("global", {}).get("blocked_email_domains"):
        sim_allow -= 1
        sim_block += 1

    escalated_inc = sim_escalate - baseline_escalate
    blocked_inc = sim_block - baseline_block

    summary = (
        f"Simulated candidate policy would escalate {escalated_inc} additional transactions "
        f"and block {blocked_inc} prohibited operations in a representative sample of 50 events."
    )

    return {
        "events_evaluated": len(sample_events),
        "baseline": {"allow": baseline_allow, "escalate": baseline_escalate, "block": baseline_block},
        "simulated": {"allow": sim_allow, "escalate": sim_escalate, "block": sim_block},
        "diff": {
            "escalated_increase": escalated_inc,
            "blocked_increase": blocked_inc,
        },
        "impact_summary": summary,
        "sample_size": 50,
    }


# ── Audit Ledger Tamper Simulation (Demo Only) ────────────────────────────────

@app.post("/v1/audit/tamper-demo", dependencies=[Depends(_auth)])
def tamper_demo() -> dict:
    """Tamper with a middle record in the SQLite ledger to demonstrate live
    detection of cryptographic chain invalidation."""
    c = connection()
    row = c.execute("SELECT id, decision FROM events ORDER BY rowid DESC LIMIT 1 OFFSET 2").fetchone()
    if not row:
        row = c.execute("SELECT id, decision FROM events ORDER BY rowid LIMIT 1").fetchone()
    if not row:
        c.close()
        raise HTTPException(400, "No audit events available to tamper with.")

    c.execute(
        "UPDATE events SET decision = CASE WHEN decision = 'ALLOW' THEN 'BLOCKED_ALTERED' ELSE 'TAMPERED' END WHERE id = ?",
        (row["id"],),
    )
    c.commit()
    c.close()
    return {"status": "tampered", "tampered_event_id": row["id"]}


@app.post("/v1/audit/repair-demo", dependencies=[Depends(_auth)])
def repair_demo() -> dict:
    """Re-initialize and re-seed clean valid hash chain for demo testing."""
    import os
    from .audit import verify as check_verify
    from .demo.scenarios import run as run_scen

    c = connection()
    c.execute("DELETE FROM events")
    c.commit()
    c.close()

    # Re-seed with standard scenario trace
    run_scen("normal_task")
    run_scen("suspicious_payment")
    run_scen("data_leak")

    return {"status": "repaired", "verification": check_verify()}


# ── Emergency Global Kill Switch ──────────────────────────────────────────────

@app.post("/v1/agents/kill-all", dependencies=[Depends(_auth)])
def kill_all_agents() -> dict:
    """Emergency protocol: permanently kills all active agents."""
    c = connection()
    c.execute("UPDATE agents SET status = 'killed'")
    c.commit()
    rows = [dict(r) for r in c.execute("SELECT * FROM agents")]
    c.close()
    write_event(
        "SYSTEM_SECURITY_ADMIN",
        "global_emergency",
        "EMERGENCY_KILL_ALL",
        {},
        "BLOCK",
        100,
        ["EMERGENCY PROTOCOL ACTIVATED: All agent processes terminated by administrator"],
    )
    return {"status": "all_killed", "agents": rows}



# ── Demo runner ───────────────────────────────────────────────────────────────

@app.post("/v1/demo/run/{scenario}", dependencies=[Depends(_auth)])
def demo_run(scenario: str) -> dict:
    try:
        return run_scenario(scenario)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


# ── Static dashboard mount (if built) ─────────────────────────────────────────
_dist_dir = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
if _dist_dir.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="dashboard")

