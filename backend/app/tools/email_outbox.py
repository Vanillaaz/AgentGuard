"""A safe, persistent outbound-email adapter for the first AgentGuard vertical.

This is deliberately an *outbox*, not an SMTP client.  Guard-approved messages
are recorded for delivery by a separately configured enterprise mail connector.
That makes the safety boundary demonstrable without granting this prototype
access to a real mailbox or SMTP credential.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..db import connection


def queue_email(
    to: str,
    subject: str,
    body: str = "",
    attachments: list[Any] | None = None,
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    """Place a reviewed message in the durable delivery outbox."""
    item = {
        "id": str(uuid.uuid4()),
        "recipient": to,
        "subject": subject,
        "body": body,
        "attachments_json": json.dumps(attachments or []),
        "agent_id": agent_id,
        "session_id": session_id,
        "status": "queued",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "approval_id": approval_id,
    }
    c = connection()
    c.execute(
        """INSERT INTO email_outbox
           (id, recipient, subject, body, attachments_json, agent_id, session_id, status, created_at, approval_id)
           VALUES (:id, :recipient, :subject, :body, :attachments_json, :agent_id, :session_id, :status, :created_at, :approval_id)""",
        item,
    )
    c.commit()
    c.close()
    return {
        "type": "email",
        "outbox_id": item["id"],
        "to": to,
        "subject": subject,
        "status": "queued",
        "message": "Email queued in the AgentGuard outbox; no external email was sent.",
    }


def list_outbox(limit: int = 100) -> list[dict[str, Any]]:
    c = connection()
    rows = [dict(row) for row in c.execute(
        "SELECT * FROM email_outbox ORDER BY created_at DESC LIMIT ?", (min(limit, 500),)
    )]
    c.close()
    for row in rows:
        row["attachments"] = json.loads(row.pop("attachments_json"))
    return rows
