"""
OpenAgent Architecture Decision Records  (Phase 8.2 — Decision Log)

Persistent ADR system. Every major architectural decision is recorded
so future sessions and future models understand the original rationale.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ADR:
    id:                   str
    title:                str
    status:               str          # "accepted" | "superseded" | "deprecated"
    decision:             str
    rationale:            str
    alternatives:         List[str]    = field(default_factory=list)
    rejection_reasons:    List[str]    = field(default_factory=list)
    affected_files:       List[str]    = field(default_factory=list)
    risks:                List[str]    = field(default_factory=list)
    related_issue:        Optional[str] = None
    created_at:           float        = field(default_factory=time.time)
    superseded_by:        Optional[str] = None


class ADRStore:
    """
    Append-only store for Architecture Decision Records, backed by a JSON file.
    Searched before any architectural component is modified.
    """

    def __init__(self, adr_file: str | Path) -> None:
        self.path = Path(adr_file)
        self._records: Dict[str, ADR] = {}
        self._load()

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for r in raw:
                adr = ADR(**r)
                self._records[adr.id] = adr

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(a) for a in self._records.values()]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ── Mutations ─────────────────────────────────────────────────────────────

    def record(self, adr: ADR) -> ADR:
        """Adds or replaces an ADR entry and persists."""
        self._records[adr.id] = adr
        self._save()
        return adr

    def supersede(self, old_id: str, new_id: str) -> None:
        """Marks an ADR as superseded by a newer decision."""
        if old_id in self._records:
            self._records[old_id].status = "superseded"
            self._records[old_id].superseded_by = new_id
        self._save()

    # ── Queries ───────────────────────────────────────────────────────────────

    def search(self, keyword: str) -> List[ADR]:
        """Full-text search over title, decision, and rationale fields."""
        kw = keyword.lower()
        return [
            a for a in self._records.values()
            if kw in a.title.lower()
            or kw in a.decision.lower()
            or kw in a.rationale.lower()
        ]

    def for_file(self, file_path: str) -> List[ADR]:
        """Returns all ADRs that mention a specific file."""
        return [
            a for a in self._records.values()
            if any(file_path in f for f in a.affected_files)
        ]

    def get(self, adr_id: str) -> Optional[ADR]:
        return self._records.get(adr_id)

    def all_records(self) -> List[ADR]:
        return list(self._records.values())

    def accepted(self) -> List[ADR]:
        return [a for a in self._records.values() if a.status == "accepted"]
