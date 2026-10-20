"""End-to-end scenario tests.

Each demo scenario must produce the expected final decision so the
dashboard demo buttons are guaranteed to work.
"""
from __future__ import annotations

import pytest
from backend.app.demo.scenarios import run


class TestScenarios:
    def test_normal_task_all_allow(self):
        result = run("normal_task")
        decisions = [a["decision"] for a in result["actions"]]
        assert all(d == "ALLOW" for d in decisions), f"Expected all ALLOW, got: {decisions}"

    def test_data_leak_last_call_blocked(self):
        result = run("data_leak")
        last = result["actions"][-1]
        assert last["decision"] == "BLOCK", (
            f"Expected BLOCK on data-leak email, got {last['decision']}: {last['reasons']}"
        )

    def test_prompt_injection_subsequent_delete_blocked_or_escalated(self):
        result = run("prompt_injection")
        # First call reads poisoned doc (may ALLOW – it's the quarantine that counts)
        # Second call (delete_record) should be BLOCK or ESCALATE due to session risk
        second = result["actions"][1]
        assert second["decision"] in ("BLOCK", "ESCALATE"), (
            f"Expected BLOCK/ESCALATE after injection, got {second['decision']}: {second['reasons']}"
        )

    def test_suspicious_payment_escalates(self):
        result = run("suspicious_payment")
        action = result["actions"][0]
        assert action["decision"] == "ESCALATE", (
            f"Expected ESCALATE for suspicious payment, got {action['decision']}: {action['reasons']}"
        )

    def test_runaway_loop_eventually_blocks(self):
        result = run("runaway_loop")
        decisions = [a["decision"] for a in result["actions"]]
        assert "BLOCK" in decisions, (
            f"Expected at least one BLOCK from runaway loop, got: {set(decisions)}"
        )
        # The agent should be paused after the rate limit trips
        from backend.app.db import connection
        c = connection()
        row = c.execute(
            "SELECT status FROM agents WHERE agent_id = 'finance-agent'"
        ).fetchone()
        c.close()
        assert row is not None
        assert row["status"] == "paused"

    def test_unknown_scenario_raises(self):
        with pytest.raises(ValueError):
            run("nonexistent_scenario")
