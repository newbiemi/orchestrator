"""
SQLite logger for all pipeline node executions.
Schema: pipeline_name, node_name, model, input_text, output_text,
        latency_ms, token_count, timestamp
"""
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "context" / "runs.db"

_initialized = False


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline    TEXT    NOT NULL,
                node        TEXT    NOT NULL,
                model       TEXT    NOT NULL,
                input_text  TEXT,
                output_text TEXT,
                latency_ms  INTEGER,
                token_count INTEGER,
                status      TEXT    DEFAULT 'ok',
                error       TEXT,
                timestamp   TEXT    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline ON runs(pipeline)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON runs(timestamp)")
        # Schema migration: add cache columns to existing DBs (SQLite has no ADD COLUMN IF NOT EXISTS)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        for col, typedef in [
            ("cache_creation_input_tokens", "INTEGER"),
            ("cache_read_input_tokens",     "INTEGER"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {typedef}")
    _initialized = True


def log_run(
    *,
    pipeline: str,
    node: str,
    model: str,
    input_text: str,
    output_text: str,
    latency_ms: int,
    token_count: int,
    status: str = "ok",
    error: Optional[str] = None,
    cache_creation_input_tokens: Optional[int] = None,
    cache_read_input_tokens: Optional[int] = None,
) -> int:
    """Insert a run record. Returns the new row id."""
    init_db()
    ts = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO runs
               (pipeline, node, model, input_text, output_text,
                latency_ms, token_count, status, error, timestamp,
                cache_creation_input_tokens, cache_read_input_tokens)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pipeline, node, model, input_text, output_text,
             latency_ms, token_count, status, error, ts,
             cache_creation_input_tokens, cache_read_input_tokens),
        )
        return cur.lastrowid


def fetch_runs(pipeline: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Return recent runs as a list of dicts."""
    init_db()
    with _get_conn() as conn:
        if pipeline:
            rows = conn.execute(
                "SELECT * FROM runs WHERE pipeline=? ORDER BY id DESC LIMIT ?",
                (pipeline, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]
