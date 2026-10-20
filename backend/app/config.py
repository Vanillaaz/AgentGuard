from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = Path(os.getenv("AGENTGUARD_POLICY", ROOT / "policies/default.yaml"))
DB_PATH = Path(os.getenv("AGENTGUARD_DB", ROOT / "agentguard.db"))
LLM_MODE = os.getenv("LLM_MODE", "live" if os.getenv("GEMINI_API_KEY") else "mock").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
API_KEY = os.getenv("AGENTGUARD_API_KEY", "change-me")
APPROVAL_TIMEOUT = int(os.getenv("APPROVAL_TIMEOUT", "60"))
