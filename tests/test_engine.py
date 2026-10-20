"""Integration tests for guard/engine.py.

Tests cover: threshold decisions, shadow mode, LLM fail-safe, rate-limit
auto-pause, and hash-chain tamper detection.
"""
from __future__ import annotations

import pytest
from backend.app.db import connection
from backend.app.guard.engine import Request, evaluate, update_agent
from backend.app.guard import engine
from backend.app.guard.ratelimit import reset as reset_rl
from backend.app.audit import verify


# ── basic decision thresholds ─────────────────────────────────────────────────

class TestDecisionThresholds:
    def test_allowed_read_document(self):
        d = evaluate(
            Request(
                agent_id="finance-agent",
                session_id="threshold-1",
                task="Read invoice",
                tool="read_document",
                arguments={"doc_id": "inv-1"},
            )
        )
        assert d.decision == "ALLOW"
        assert d.score < 30

    def test_blocked_unknown_tool(self):
        d = evaluate(
            Request(
                agent_id="finance-agent",
                session_id="threshold-2",
                task="Do something",
                tool="nuke_database",
                arguments={},
            )
        )
        assert d.decision == "BLOCK"
        assert any("not permitted" in r for r in d.reasons)

    def test_escalate_payment_over_limit(self):
        d = evaluate(
            Request(
                agent_id="finance-agent",
                session_id="threshold-3",
                task="Pay invoice",
                tool="make_payment",
                arguments={"vendor": "Acme", "amount": 60_000, "currency": "INR"},
            )
        )
        assert d.decision == "ESCALATE"


# ── shadow mode ───────────────────────────────────────────────────────────────

class TestShadowMode:
    def test_shadow_overrides_block_to_allow(self):
        engine.global_shadow = True
        d = evaluate(
            Request(
                agent_id="finance-agent",
                session_id="shadow-1",
                task="Pay",
                tool="make_payment",
                arguments={"vendor": "Acme", "amount": 500_000, "currency": "INR"},
            )
        )
        assert d.decision == "ALLOW"
        assert d.shadow is True

    def test_shadow_off_blocks_normally(self):
        engine.global_shadow = False
        d = evaluate(
            Request(
                agent_id="finance-agent",
                session_id="shadow-2",
                task="Pay",
                tool="nuke_database",
                arguments={},
            )
        )
        assert d.decision == "BLOCK"
        assert d.shadow is False


# ── fail-safe on LLM error ────────────────────────────────────────────────────

class TestFailSafe:
    def test_llm_error_escalates(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("Simulated LLM failure")

        monkeypatch.setattr("backend.app.guard.engine.intent_check", _boom)
        d = evaluate(
            Request(
                agent_id="finance-agent",
                session_id="failsafe-1",
                task="Pay vendor",
                tool="make_payment",
                arguments={"vendor": "Test", "amount": 1, "currency": "INR"},
            )
        )
        assert d.decision == "ESCALATE"
        assert any("fail-safe" in r.lower() for r in d.reasons)


# ── rate-limit + auto-pause ───────────────────────────────────────────────────

class TestRateLimit:
    def test_rate_limit_triggers_block_and_pause(self):
        reset_rl()
        update_agent("hr-agent", status="active")
        first_block: object = None
        for i in range(25):
            d = evaluate(
                Request(
                    agent_id="hr-agent",
                    session_id="ratelimit-1",
                    task="Read",
                    tool="read_document",
                    arguments={"doc_id": "inv-1"},
                )
            )
            if d.decision == "BLOCK" and first_block is None:
                first_block = d
        assert first_block is not None, "Expected at least one BLOCK in 25 calls"
        assert any("rate limit" in r.lower() for r in first_block.reasons), (
            f"Expected rate-limit reason, got: {first_block.reasons}"
        )

    def test_paused_agent_always_blocks(self):
        update_agent("hr-agent", status="paused")
        d = evaluate(
            Request(
                agent_id="hr-agent",
                session_id="ratelimit-2",
                task="Read",
                tool="read_document",
                arguments={"doc_id": "inv-1"},
            )
        )
        assert d.decision == "BLOCK"


# ── hash-chain tamper detection ───────────────────────────────────────────────

class TestAuditChain:
    def test_clean_chain_is_valid(self):
        result = verify()
        # After conftest isolation with a fresh DB, chain may be empty → valid
        assert result["valid"] is True

    def test_tamper_breaks_chain(self):
        # First ensure at least one event exists
        evaluate(
            Request(
                agent_id="finance-agent",
                session_id="tamper-1",
                task="Read",
                tool="read_document",
                arguments={"doc_id": "inv-1"},
            )
        )
        c = connection()
        row = c.execute("SELECT id FROM events LIMIT 1").fetchone()
        c.execute(
            "UPDATE events SET decision = 'TAMPERED' WHERE id = ?",
            (row["id"],),
        )
        c.commit()
        c.close()
        result = verify()
        assert result["valid"] is False
        assert result["broken_event_id"] is not None
