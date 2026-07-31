"""
OpenAgent Execution Checkpoint  (Phase 8)

Node-granular crash recovery. After each node completes, its output
is checkpointed to SQLite. On restart, the execution graph resumes
from the last successfully completed node rather than from scratch.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class ExecutionCheckpoint:
    """
    Persists completed node results to SQLite so execution can be
    resumed after a crash without re-running already-finished nodes.
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS node_checkpoints (
        run_id      TEXT NOT NULL,
        node_id     TEXT NOT NULL,
        result      TEXT,
        completed_at REAL NOT NULL,
        PRIMARY KEY (run_id, node_id)
    );
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(self.DDL)
        self._conn.commit()

    # ── Write ─────────────────────────────────────────────────────────────────

    def save(self, run_id: str, node_id: str, result: Any) -> None:
        """Persists a completed node result."""
        self._conn.execute(
            "INSERT OR REPLACE INTO node_checkpoints (run_id, node_id, result, completed_at) "
            "VALUES (?, ?, ?, ?)",
            (run_id, node_id, json.dumps(result), time.time()),
        )
        self._conn.commit()

    # ── Read ──────────────────────────────────────────────────────────────────

    def is_complete(self, run_id: str, node_id: str) -> bool:
        """Returns True if this node already completed in a prior run."""
        row = self._conn.execute(
            "SELECT 1 FROM node_checkpoints WHERE run_id = ? AND node_id = ?",
            (run_id, node_id),
        ).fetchone()
        return row is not None

    def load(self, run_id: str, node_id: str) -> Optional[Any]:
        """Loads the persisted result for a node, or None if not found."""
        row = self._conn.execute(
            "SELECT result FROM node_checkpoints WHERE run_id = ? AND node_id = ?",
            (run_id, node_id),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def completed_nodes(self, run_id: str) -> List[str]:
        """Returns all node_ids that have been checkpointed for a run."""
        rows = self._conn.execute(
            "SELECT node_id FROM node_checkpoints WHERE run_id = ? ORDER BY completed_at",
            (run_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def clear(self, run_id: str) -> None:
        """Clears all checkpoints for a run (used when retrying from scratch)."""
        self._conn.execute(
            "DELETE FROM node_checkpoints WHERE run_id = ?", (run_id,)
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
