"""
OpenAgent Engineering Journal  (Phase 8.7)

Continuous append-only journal giving future sessions immediate context
without re-reading the entire git history.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class JournalEntry:
    date:       str              # ISO date "YYYY-MM-DD"
    completed:  List[str]        = field(default_factory=list)
    issues:     List[str]        = field(default_factory=list)
    decisions:  List[str]        = field(default_factory=list)
    reasons:    List[str]        = field(default_factory=list)
    next_steps: List[str]        = field(default_factory=list)
    timestamp:  float            = field(default_factory=time.time)
    phase:      Optional[int]    = None


class EngineeringJournal:
    """
    Append-only JSON-Lines journal for continuous project narrative.
    Each entry captures daily progress so any new session immediately
    understands context without mining git blame.
    """

    def __init__(self, journal_file: str | Path) -> None:
        self.path = Path(journal_file)
        self._entries: List[JournalEntry] = []
        self._load()

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self._entries.append(JournalEntry(**json.loads(line)))
        except Exception:
            pass   # corrupt lines — do not crash, just skip

    def _append(self, entry: JournalEntry) -> None:
        """Appends a single JSON-Lines record to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry)) + "\n")

    # ── API ───────────────────────────────────────────────────────────────────

    def log(
        self,
        completed:  List[str] | None = None,
        issues:     List[str] | None = None,
        decisions:  List[str] | None = None,
        reasons:    List[str] | None = None,
        next_steps: List[str] | None = None,
        phase:      int | None       = None,
    ) -> JournalEntry:
        """Creates and persists a new journal entry for today."""
        from datetime import date as _date
        entry = JournalEntry(
            date       = str(_date.today()),
            completed  = completed  or [],
            issues     = issues     or [],
            decisions  = decisions  or [],
            reasons    = reasons    or [],
            next_steps = next_steps or [],
            phase      = phase,
        )
        self._entries.append(entry)
        self._append(entry)
        return entry

    def recent(self, n: int = 5) -> List[JournalEntry]:
        """Returns the n most recent journal entries."""
        return self._entries[-n:]

    def for_phase(self, phase: int) -> List[JournalEntry]:
        """Returns all entries logged for a specific phase."""
        return [e for e in self._entries if e.phase == phase]

    def all_entries(self) -> List[JournalEntry]:
        return list(self._entries)

    def last_next_steps(self) -> List[str]:
        """Returns next_steps from the most recent entry (session bootstrap hint)."""
        if not self._entries:
            return []
        return self._entries[-1].next_steps
