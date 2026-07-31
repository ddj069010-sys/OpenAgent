"""
OpenAgent Crash Recovery & Checkpoint Store  (Target Architecture v3 §20.3, §21.8)

Persists task state, execution plan, tool outputs, and context summaries to SQLite
so the agent can resume after a crash, reboot, or model swap — never starting from scratch.

Design decisions:
- SQLite with WAL mode: safe concurrent reads, no external process required.
- Each checkpoint is immutable (append-only); rollback = restore prior checkpoint.
- Schema versioned via `schema_version` in the `meta` table.
- Never silently trusts a checkpoint — validates hash on restore.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from backend.task_state_machine import TaskState

_SCHEMA_VERSION = 1
_DEFAULT_DB_PATH = Path.home() / ".openhands" / "agent-canvas" / "openagent_checkpoints.db"


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class TaskCheckpoint:
    """Immutable snapshot of a task at a point in time."""
    task_id:          str
    state:            str                        # TaskState value
    goal:             str                        # Original user goal
    plan:             list[dict]                 # Serialised task DAG nodes
    tool_outputs:     list[dict]                 # History of tool results
    context_summary:  str                        # Compressed context for model
    terminal_history: list[str]                  # Bash command outputs
    created_at:       float = field(default_factory=time.time)
    extra:            dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """SHA-256 of serialised content — used to detect corruption."""
        payload = json.dumps({
            "task_id": self.task_id,
            "state":   self.state,
            "goal":    self.goal,
            "plan":    self.plan,
        }, sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()


# ── Store ────────────────────────────────────────────────────────────────────

class CheckpointStore:
    """
    SQLite-backed, append-only checkpoint store.

    Every call to `save()` appends a new row — it never overwrites.
    `restore_latest()` returns the most recent valid checkpoint for a task.
    """

    def __init__(self, db_path: Path = _DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO meta VALUES ('schema_version', '{_SCHEMA_VERSION}');

                CREATE TABLE IF NOT EXISTS checkpoints (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id        TEXT    NOT NULL,
                    state          TEXT    NOT NULL,
                    content_hash   TEXT    NOT NULL,
                    payload        TEXT    NOT NULL,  -- JSON blob
                    created_at     REAL    NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_id ON checkpoints (task_id, created_at DESC);
            """)

    def save(self, checkpoint: TaskCheckpoint) -> int:
        """Persists a checkpoint. Returns the new row id."""
        payload = json.dumps(asdict(checkpoint))
        with self._connection() as conn:
            cur = conn.execute(
                "INSERT INTO checkpoints (task_id, state, content_hash, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (checkpoint.task_id, checkpoint.state,
                 checkpoint.content_hash, payload, checkpoint.created_at)
            )
            return cur.lastrowid

    def restore_latest(self, task_id: str) -> Optional[TaskCheckpoint]:
        """
        Returns the most recent valid checkpoint for task_id.
        Validates content_hash before returning — never silently trusts storage.
        Returns None if no valid checkpoint exists.
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT payload, content_hash FROM checkpoints "
                "WHERE task_id = ? ORDER BY created_at DESC LIMIT 10",
                (task_id,)
            ).fetchall()

        for payload_str, stored_hash in rows:
            data = json.loads(payload_str)
            cp = TaskCheckpoint(**data)
            if cp.content_hash == stored_hash:
                return cp
        return None

    def list_interrupted_tasks(self) -> list[dict]:
        """
        Returns tasks that were in a non-terminal state when last checkpointed.
        Used at startup to offer resume for crashed sessions.
        """
        terminal = {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT task_id, state, MAX(created_at) as last_seen "
                "FROM checkpoints GROUP BY task_id"
            ).fetchall()
        return [
            {"task_id": r[0], "state": r[1], "last_seen": r[2]}
            for r in rows
            if r[1] not in terminal
        ]

    def validate(self) -> bool:
        """Runs SQLite integrity check. Returns True if store is healthy."""
        with self._connection() as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
        return result[0] == "ok"


# ── Module-level singleton resolved via container ────────────────────────────
# Import and call get_checkpoint_store() or resolve via container.

def get_checkpoint_store(db_path: Path = _DEFAULT_DB_PATH) -> CheckpointStore:
    """Returns a CheckpointStore instance (not cached — use container for singleton)."""
    return CheckpointStore(db_path)
