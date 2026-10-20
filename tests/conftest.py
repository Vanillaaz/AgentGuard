"""pytest configuration – shared fixtures and test database isolation.

IMPORTANT: The DB path env var must be set BEFORE ``backend.app.config``
is imported.  We achieve this by setting it at module level (i.e. when
conftest.py itself is first executed by pytest) rather than in a fixture.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# ── Set DB path before any backend module is imported ─────────────────────────
# config.py reads DB_PATH at import time, so we must override it here first.
_tmp_db = Path(tempfile.gettempdir()) / "agentguard_test.db"
os.environ["AGENTGUARD_DB"] = str(_tmp_db)
os.environ["LLM_MODE"] = "mock"  # never call real Gemini in tests

import pytest


@pytest.fixture(scope="session", autouse=True)
def _init_db():
    """Create DB tables once per test session."""
    from backend.app.db import init_db
    init_db()


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset ALL mutable state before each test function.

    This gives each test a completely clean slate:
    - Engine globals (shadow, session_risk, listener)
    - Rate-limit sliding windows
    - All DB tables cleared (events, agents, approvals)
    """
    from backend.app.guard import engine
    from backend.app.guard.ratelimit import reset as reset_rl
    from backend.app.db import connection

    # Reset engine globals
    engine.global_shadow = False
    engine.session_risk.clear()
    engine.event_listener = None

    # Reset rate-limit sliding windows
    reset_rl()

    # Wipe all DB rows so tests get a clean audit chain
    c = connection()
    c.execute("DELETE FROM events")
    c.execute("DELETE FROM agents")
    c.execute("DELETE FROM approvals")
    c.execute("DELETE FROM email_outbox")
    c.commit()
    c.close()

    yield  # run the test

    # Post-test cleanup (mirror of setup)
    engine.global_shadow = False
    engine.session_risk.clear()
