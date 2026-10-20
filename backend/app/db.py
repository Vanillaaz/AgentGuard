from __future__ import annotations
import sqlite3
from .config import DB_PATH

def connection() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def init_db() -> None:
    c = connection()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY,timestamp TEXT,agent_id TEXT,session_id TEXT,tool TEXT,arguments_json TEXT,decision TEXT,score INTEGER,reasons_json TEXT,shadow INTEGER,prev_hash TEXT,hash TEXT);
    CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY,event_id TEXT,agent_id TEXT,session_id TEXT,tool TEXT,arguments_json TEXT,status TEXT,created_at TEXT,decided_at TEXT);
    CREATE TABLE IF NOT EXISTS agents (agent_id TEXT PRIMARY KEY,status TEXT NOT NULL DEFAULT 'active',shadow INTEGER NOT NULL DEFAULT 0,calls INTEGER NOT NULL DEFAULT 0,blocked INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS email_outbox (id TEXT PRIMARY KEY,recipient TEXT NOT NULL,subject TEXT NOT NULL,body TEXT NOT NULL,attachments_json TEXT NOT NULL,agent_id TEXT,session_id TEXT,status TEXT NOT NULL,created_at TEXT NOT NULL,approval_id TEXT);
    ''')
    c.commit(); c.close()
