"""
OpenAgent Persistent Memory  (Phase 6 — Phase F)

Long-lived memory storage to store architecture decisions, previous fixes,
known bugs, and coding conventions across restarts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class MemoryItem:
    category:    str  # "arch_decision" | "prev_fix" | "known_bug" | "convention" | "failed_approach" | "todo"
    content:     str
    timestamp:   float
    metadata:    Optional[dict] = None


class PersistentMemory:
    """
    Structured storage for agent experience, learning history, and conventions.
    """

    def __init__(self, memory_file_path: str | Path) -> None:
        self.file_path = Path(memory_file_path)
        self._memory: List[MemoryItem] = []
        self._load()

    def add(self, category: str, content: str, metadata: Optional[dict] = None) -> None:
        """Adds a memory item to the store and persists it."""
        import time
        item = MemoryItem(
            category=category,
            content=content,
            timestamp=time.time(),
            metadata=metadata or {}
        )
        self._memory.append(item)
        self._save()

    def get_by_category(self, category: str) -> List[MemoryItem]:
        """Returns all memory items matching the category."""
        return [m for m in self._memory if m.category == category]

    def search(self, keyword: str) -> List[MemoryItem]:
        """Searches memory contents for a keyword (case-insensitive)."""
        keyword = keyword.lower()
        return [m for m in self._memory if keyword in m.content.lower()]

    def all_memories(self) -> List[MemoryItem]:
        return list(self._memory)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text(encoding="utf-8"))
                self._memory = [MemoryItem(**m) for m in data]
            except Exception:
                pass

    def _save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(m) for m in self._memory]
        self.file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
