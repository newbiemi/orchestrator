"""
Memory layer — stores pipeline run outcomes for continuous learning.

Separate SQLite database (context/memory.db) so memory can be queried,
pruned, or migrated independently from the run log.

Each pipeline run can:
  1. recall() — load the last N relevant memories before calling nodes
  2. store()  — save the run outcome + score after completion

Relevance is determined by: same pipeline, ordered by recency.
input_hash allows deduplication and fast lookup for identical inputs.
"""
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "context" / "memory.db"

_initialized = False


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_memory_db() -> None:
    global _initialized
    if _initialized:
        return
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline        TEXT    NOT NULL,
                run_id          INTEGER,
                input_hash      TEXT    NOT NULL,
                input_preview   TEXT,
                output          TEXT    NOT NULL,
                score           REAL,
                prompt_variant  TEXT,
                timestamp       TEXT    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_pipeline ON memories(pipeline)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_hash ON memories(input_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_score ON memories(score)")
    _initialized = True


def input_to_hash(text: str) -> str:
    """SHA-256 hash of the input text, for dedup and fast lookup."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def store_memory(
    *,
    pipeline: str,
    run_id: Optional[int] = None,
    input_text: str,
    output: str,
    score: Optional[float] = None,
    prompt_variant: Optional[str] = None,
) -> int:
    """Store a run outcome as a memory. Returns the memory row id."""
    init_memory_db()
    h = input_to_hash(input_text)
    preview = input_text[:500]
    ts = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO memories
               (pipeline, run_id, input_hash, input_preview, output,
                score, prompt_variant, timestamp)
               VALUES (?,?,?,?,?,?,?,?)""",
            (pipeline, run_id, h, preview, output, score, prompt_variant, ts),
        )
        return cur.lastrowid


def recall_memories(
    pipeline: str,
    n: int = 5,
    min_score: Optional[float] = None,
) -> list[dict]:
    """
    Recall the last N memories for a pipeline, ordered by recency.
    If min_score is set, only return memories scored >= that threshold.
    """
    init_memory_db()
    with _get_conn() as conn:
        if min_score is not None:
            rows = conn.execute(
                """SELECT * FROM memories
                   WHERE pipeline = ? AND (score IS NULL OR score >= ?)
                   ORDER BY id DESC LIMIT ?""",
                (pipeline, min_score, n),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM memories
                   WHERE pipeline = ?
                   ORDER BY id DESC LIMIT ?""",
                (pipeline, n),
            ).fetchall()
    return [dict(r) for r in rows]


def format_memories_for_context(memories: list[dict]) -> str:
    """
    Format recalled memories into a text block suitable for prepending
    to a system message or injecting as context.

    Output format:
        Here are recent relevant runs for this pipeline:

        [Run #1 | score: 8.0 | 2026-04-04T10:00:00]
        Input: <preview>
        Output: <output>
        ---
    """
    if not memories:
        return ""

    lines = ["Here are recent relevant runs for this pipeline:\n"]
    for m in reversed(memories):  # oldest first for chronological context
        score_str = f" | score: {m['score']}" if m.get("score") is not None else ""
        ts = m["timestamp"][:19].replace("T", " ")
        lines.append(f"[Run #{m['id']}{score_str} | {ts}]")
        if m.get("input_preview"):
            lines.append(f"Input: {m['input_preview'][:200]}")
        # Truncate output to avoid blowing up context
        output_preview = m["output"][:500] if m["output"] else "(empty)"
        lines.append(f"Output: {output_preview}")
        lines.append("---")

    return "\n".join(lines)


def update_score(memory_id: int, score: float) -> None:
    """Update the score for an existing memory (used by prompt optimizer)."""
    init_memory_db()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE memories SET score = ? WHERE id = ?",
            (score, memory_id),
        )
