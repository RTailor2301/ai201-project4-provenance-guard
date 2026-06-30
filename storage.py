import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "provenance.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS content (
                content_id TEXT PRIMARY KEY,
                creator_id TEXT,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'classified',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)


def save_content(creator_id: str, text: str, status: str = "classified") -> str:
    content_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO content (content_id, creator_id, text, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (content_id, creator_id, text, status, now),
        )
    return content_id


def get_content(content_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM content WHERE content_id = ?", (content_id,)
        ).fetchone()
    return dict(row) if row else None


def update_status(content_id: str, status: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE content SET status = ? WHERE content_id = ?",
            (status, content_id),
        )


def append_log(entry: dict):
    now = datetime.now(timezone.utc).isoformat()
    entry.setdefault("timestamp", now)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (entry_json, created_at) VALUES (?, ?)",
            (json.dumps(entry), now),
        )


def get_log(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT entry_json FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    entries = [json.loads(row["entry_json"]) for row in rows]
    entries.reverse()
    return entries
