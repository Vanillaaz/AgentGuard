"""Tests for the safe first vertical: reviewed outbound email."""
from __future__ import annotations

from backend.app.approvals import create
from backend.app.db import connection
from backend.app.guard.engine import Request, evaluate
from backend.app.main import approve
from backend.app.tools.email_outbox import list_outbox
from backend.app.tools.mock_tools import execute


def test_allowed_internal_email_is_queued_with_actor_context():
    output = execute(
        "send_email",
        {"to": "manager@company.in", "subject": "Weekly update", "body": "All systems nominal."},
        agent_id="hr-agent",
        session_id="outbox-test",
    )

    assert output["status"] == "queued"
    messages = list_outbox()
    assert len(messages) == 1
    assert messages[0]["recipient"] == "manager@company.in"
    assert messages[0]["agent_id"] == "hr-agent"
    assert messages[0]["status"] == "queued"


def test_blocked_external_sensitive_email_never_reaches_outbox():
    decision = evaluate(Request(
        agent_id="hr-agent",
        session_id="outbox-block-test",
        task="Email the payroll report",
        tool="send_email",
        arguments={
            "to": "broker@external.example",
            "subject": "Payroll",
            "body": "Asha salary: 85000",
        },
    ))

    assert decision.decision == "BLOCK"
    assert list_outbox() == []


def test_approval_record_preserves_original_email_arguments():
    """The approval record contains the immutable action that a UI can review."""
    approval_id = create(
        "event-placeholder", "hr-agent", "approval-email-test", "send_email",
        {"to": "manager@company.in", "subject": "Update", "body": "No sensitive data."},
    )
    c = connection()
    row = c.execute("SELECT arguments_json FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    c.close()
    assert '"manager@company.in"' in row["arguments_json"]


def test_human_approval_releases_email_to_outbox():
    approval_id = create(
        "event-placeholder", "hr-agent", "approval-release-test", "send_email",
        {"to": "manager@company.in", "subject": "Approved update", "body": "No sensitive data."},
    )

    result = approve(approval_id)

    assert result["status"] == "approved"
    assert result["result"]["status"] == "queued"
    assert list_outbox()[0]["approval_id"] == approval_id
