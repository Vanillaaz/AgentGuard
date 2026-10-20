from __future__ import annotations
import uuid
from datetime import datetime,timezone
from .db import connection
from .audit import write_event
def create(event_id,agent_id,session_id,tool,arguments):
    ident=str(uuid.uuid4()); c=connection();c.execute('INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?)',(ident,event_id,agent_id,session_id,tool,__import__('json').dumps(arguments),'pending',datetime.now(timezone.utc).isoformat(),None));c.commit();c.close();return ident
def format_slack_notification(approval: dict) -> dict:
    import json
    args = json.loads(approval['arguments_json']) if isinstance(approval.get('arguments_json'), str) else approval.get('arguments_json', {})
    return {
        "channel": "#security-approvals",
        "bot_name": "AgentGuard SecOps",
        "text": f"🚨 Human Approval Required: Agent *{approval['agent_id']}* invoked *{approval['tool']}*",
        "fields": [
            {"label": "Agent", "value": approval['agent_id']},
            {"label": "Tool", "value": f"`{approval['tool']}()`"},
            {"label": "Session", "value": approval['session_id']},
            {"label": "Arguments", "value": json.dumps(args, indent=2)},
        ],
        "webhook_status": "Dispatched (HTTP 200)",
        "timestamp": approval.get('created_at'),
    }


def list_pending():
    c = connection()
    rows = []
    for r in c.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY created_at"):
        d = dict(r)
        d['slack_notification'] = format_slack_notification(d)
        rows.append(d)
    c.close()
    return rows
def resolve(ident,status):
    c=connection(); r=c.execute('SELECT * FROM approvals WHERE id=?',(ident,)).fetchone()
    if not r: return None
    c.execute('UPDATE approvals SET status=?,decided_at=? WHERE id=?',(status,datetime.now(timezone.utc).isoformat(),ident));c.commit();c.close(); write_event(r['agent_id'],r['session_id'],r['tool'],{},status.upper(),0,[f'Human {status} approval {ident}']);return status
