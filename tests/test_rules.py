"""Unit tests for guard/rules.py.

Each rule is tested independently with crafted arguments so failures are
pinpointed to individual rules.  The policy fixture loads the real
``policies/default.yaml``.
"""
from __future__ import annotations

import pytest
from backend.app.guard.policy import load_policy
from backend.app.guard.rules import (
    Finding,
    approval_required_tool,
    bulk_action,
    external_data_exfiltration,
    payment_over_limit,
    tool_not_allowed,
    unusual_amount,
)


@pytest.fixture(scope="module")
def policy():
    return load_policy()


@pytest.fixture(scope="module")
def fin(policy):
    return policy.agents["finance-agent"]


@pytest.fixture(scope="module")
def hr(policy):
    return policy.agents["hr-agent"]


# ── tool_not_allowed ──────────────────────────────────────────────────────────

class TestToolNotAllowed:
    def test_blocked_tool_returns_block_finding(self, fin, policy):
        findings = tool_not_allowed("delete_record", {}, fin, policy)
        assert len(findings) == 1
        assert findings[0].verdict == "block"
        assert findings[0].rule_id == "tool_not_allowed"

    def test_allowed_tool_returns_empty(self, fin, policy):
        assert tool_not_allowed("read_document", {}, fin, policy) == []

    def test_allowed_tool_for_hr(self, hr, policy):
        assert tool_not_allowed("send_email", {}, hr, policy) == []

    def test_make_payment_blocked_for_hr(self, hr, policy):
        findings = tool_not_allowed("make_payment", {}, hr, policy)
        assert findings[0].verdict == "block"


# ── payment_over_limit ────────────────────────────────────────────────────────

class TestPaymentOverLimit:
    def test_under_limit_is_empty(self, fin, policy):
        assert payment_over_limit("make_payment", {"amount": 1_000}, fin, policy) == []

    def test_over_max_payment_escalates(self, fin, policy):
        findings = payment_over_limit("make_payment", {"amount": 60_000}, fin, policy)
        assert findings[0].verdict == "escalate"
        assert findings[0].rule_id == "payment_over_limit"

    def test_over_hard_max_blocks(self, fin, policy):
        findings = payment_over_limit("make_payment", {"amount": 300_000}, fin, policy)
        assert findings[0].verdict == "block"

    def test_exactly_at_max_payment_is_empty(self, fin, policy):
        # equal to max_payment should NOT escalate (strictly greater)
        assert payment_over_limit("make_payment", {"amount": 50_000}, fin, policy) == []

    def test_non_payment_tool_is_empty(self, fin, policy):
        assert payment_over_limit("read_document", {"amount": 9_999_999}, fin, policy) == []


# ── external_data_exfiltration ────────────────────────────────────────────────

class TestExternalDataExfiltration:
    def test_blocked_domain_blocks(self, hr, policy):
        findings = external_data_exfiltration(
            "send_email",
            {"to": "someone@evil.com", "body": "hello"},
            hr,
            policy,
        )
        assert findings[0].verdict == "block"

    def test_sensitive_body_to_external_blocks(self, hr, policy):
        findings = external_data_exfiltration(
            "send_email",
            {"to": "x@random-external.com", "body": "salary: 85000"},
            hr,
            policy,
        )
        assert findings[0].verdict == "block"
        assert findings[0].rule_id == "external_data_exfiltration"

    def test_internal_email_is_empty(self, hr, policy):
        assert (
            external_data_exfiltration(
                "send_email",
                {"to": "boss@company.in", "body": "salary: 85000"},
                hr,
                policy,
            )
            == []
        )

    def test_non_email_tool_is_empty(self, hr, policy):
        assert (
            external_data_exfiltration("make_payment", {"amount": 1}, hr, policy)
            == []
        )


# ── approval_required_tool ────────────────────────────────────────────────────

class TestApprovalRequired:
    def test_delete_record_escalates(self, hr, policy):
        findings = approval_required_tool("delete_record", {}, hr, policy)
        assert findings[0].verdict == "escalate"

    def test_other_tool_is_empty(self, hr, policy):
        assert approval_required_tool("query_database", {}, hr, policy) == []


# ── bulk_action ───────────────────────────────────────────────────────────────

class TestBulkAction:
    def test_large_record_count_escalates(self, hr, policy):
        findings = bulk_action(
            "query_database", {"limit": 101}, hr, policy
        )
        assert findings[0].verdict == "escalate"
        assert findings[0].rule_id == "bulk_action"

    def test_small_count_is_empty(self, hr, policy):
        assert bulk_action("query_database", {"limit": 5}, hr, policy) == []

    def test_non_db_tool_is_empty(self, hr, policy):
        assert bulk_action("send_email", {"limit": 9999}, hr, policy) == []


# ── unusual_amount ────────────────────────────────────────────────────────────

class TestUnusualAmount:
    def test_5x_average_escalates(self, fin, policy):
        findings = unusual_amount(
            "make_payment", {"amount": 600}, fin, policy, recent_average=100
        )
        assert findings[0].verdict == "escalate"

    def test_under_5x_is_empty(self, fin, policy):
        assert (
            unusual_amount("make_payment", {"amount": 499}, fin, policy, recent_average=100)
            == []
        )

    def test_zero_average_skips_rule(self, fin, policy):
        # No history means no baseline → rule should not fire
        assert unusual_amount("make_payment", {"amount": 999_999}, fin, policy, recent_average=0) == []

    def test_non_payment_tool_is_empty(self, fin, policy):
        assert unusual_amount("read_document", {"amount": 9999}, fin, policy, recent_average=10) == []
