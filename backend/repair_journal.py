"""
OpenAgent Repair Journal  (Phase 8)

Structured failure tracking store. Every node-level failure is recorded
with its cause, repair strategy attempted, and outcome. The planner
uses this to avoid repeating identical failed fixes across retries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class RepairEntry:
    task_id:          str
    node_id:          str
    failure_class:    str          # e.g. "SYNTAX", "IMPORT", "API", "LOGIC"
    error_message:    str
    repair_strategy:  str
    outcome:          str          # "SUCCESS" | "FAILURE" | "PARTIAL"
    timestamp:        float = field(default_factory=time.time)
    attempt_number:   int = 1


class RepairJournal:
    """
    Append-only in-memory journal tracking all repair attempts.

    The planner queries this to avoid repeating strategies that
    have already failed for the same (task_id, node_id) pair.
    """

    def __init__(self) -> None:
        self._entries: List[RepairEntry] = []
        # Index: (task_id, node_id) -> list of RepairEntry
        self._index:   Dict[tuple, List[RepairEntry]] = {}

    def record(
        self,
        task_id:         str,
        node_id:         str,
        failure_class:   str,
        error_message:   str,
        repair_strategy: str,
        outcome:         str,
    ) -> RepairEntry:
        """Records a repair attempt and returns the entry."""
        key = (task_id, node_id)
        attempt = len(self._index.get(key, [])) + 1
        entry = RepairEntry(
            task_id=task_id,
            node_id=node_id,
            failure_class=failure_class,
            error_message=error_message,
            repair_strategy=repair_strategy,
            outcome=outcome,
            attempt_number=attempt,
        )
        self._entries.append(entry)
        self._index.setdefault(key, []).append(entry)
        return entry

    def failed_strategies(self, task_id: str, node_id: str) -> List[str]:
        """Returns all repair strategies that have already failed for this node."""
        entries = self._index.get((task_id, node_id), [])
        return [e.repair_strategy for e in entries if e.outcome == "FAILURE"]

    def success_count(self) -> int:
        return sum(1 for e in self._entries if e.outcome == "SUCCESS")

    def failure_count(self) -> int:
        return sum(1 for e in self._entries if e.outcome == "FAILURE")

    def all_entries(self) -> List[RepairEntry]:
        return list(self._entries)

    def summary(self) -> Dict[str, int]:
        return {
            "total":   len(self._entries),
            "success": self.success_count(),
            "failure": self.failure_count(),
        }
