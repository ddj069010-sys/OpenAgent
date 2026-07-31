"""
OpenAgent Session Recovery  (Phase 8.4)

On every session start (model swap, reboot, VS Code restart),
loads state in a fixed, efficient order — never rescans the repository.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from backend.project_state import ProjectState, ProjectStateStore
from backend.adr_store import ADRStore


@dataclass
class SessionContext:
    """Complete recovered session context — passed to the ProjectDirector."""
    project_state:   ProjectState
    accepted_adrs:   int
    recent_commits:  List[str]    = field(default_factory=list)
    active_task:     Optional[str] = None
    recovery_notes:  List[str]    = field(default_factory=list)
    ready:           bool          = False


class SessionRecovery:
    """
    Recovers full session context without re-scanning the codebase.

    Recovery order (mirroring the spec):
        1. Load project state
        2. Load ADRs
        3. Load recent commits
        4. Load active task / checkpoint
        5. Mark ready
    """

    def __init__(
        self,
        state_store: ProjectStateStore,
        adr_store:   ADRStore,
        workspace:   str | Path,
    ) -> None:
        self.state_store = state_store
        self.adr_store   = adr_store
        self.workspace   = Path(workspace)

    def recover(self) -> SessionContext:
        notes: List[str] = []

        # ── 1. Project state ──────────────────────────────────────────────────
        ps = self.state_store.load()
        notes.append(f"Loaded project state: phase={ps.phase}, status={ps.status}")

        # ── 2. ADRs ───────────────────────────────────────────────────────────
        accepted = self.adr_store.accepted()
        notes.append(f"Loaded {len(accepted)} accepted ADRs")

        # ── 3. Recent commits ─────────────────────────────────────────────────
        recent_commits = self._git_log(limit=5)
        if recent_commits:
            notes.append(f"Loaded {len(recent_commits)} recent commits")
        else:
            notes.append("Git log unavailable — workspace may not be a repo")

        # ── 4. Active task from project state ─────────────────────────────────
        active_task = ps.active_feature or ps.current_goal
        notes.append(f"Active task: {active_task}")

        return SessionContext(
            project_state  = ps,
            accepted_adrs  = len(accepted),
            recent_commits = recent_commits,
            active_task    = active_task,
            recovery_notes = notes,
            ready          = True,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _git_log(self, limit: int = 5) -> List[str]:
        try:
            result = subprocess.run(
                ["git", "log", f"--max-count={limit}", "--oneline"],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        except Exception:
            pass
        return []
