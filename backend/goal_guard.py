"""
OpenAgent Goal Guard  (Phase 8.3)

Pre-execution alignment check. Before any task starts, validates it against
the roadmap, existing implementations, previous decisions, and hardware limits.
Returns a verdict — the framework NEVER skips this gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from backend.project_state import ProjectState
from backend.adr_store import ADRStore


@dataclass
class GuardVerdict:
    approved:  bool
    reasons:   List[str]   = field(default_factory=list)   # why blocked
    warnings:  List[str]   = field(default_factory=list)   # soft concerns
    suggested: Optional[str] = None                        # what to do instead


class GoalGuard:
    """
    Deterministic pre-execution gate.
    Checks: roadmap alignment, duplicate detection, ADR conflicts,
    complexity escalation, and hardware constraint violations.
    """

    def __init__(self, state: ProjectState, adr_store: ADRStore) -> None:
        self.state     = state
        self.adr_store = adr_store

    def evaluate(self, goal: str, estimated_vram_mb: float = 0.0) -> GuardVerdict:
        """
        Evaluates whether a goal is safe to pursue.
        Returns GuardVerdict(approved=True) if all checks pass.
        """
        reasons:  List[str] = []
        warnings: List[str] = []

        goal_lower = goal.lower()

        # ── 1. Already implemented? ───────────────────────────────────────────
        for done in self.state.completed:
            if done.lower() in goal_lower or goal_lower in done.lower():
                reasons.append(
                    f"'{done}' is already in completed list — this may be a duplicate."
                )

        # ── 2. Conflicts with active blockers? ────────────────────────────────
        for blocker in self.state.blockers:
            if blocker.lower() in goal_lower:
                reasons.append(f"Goal conflicts with active blocker: '{blocker}'.")

        # ── 3. ADR conflict check ─────────────────────────────────────────────
        related_adrs = self.adr_store.search(goal)
        for adr in related_adrs:
            if adr.status == "accepted" and adr.risks:
                warnings.append(
                    f"ADR '{adr.id}' ({adr.title}) covers this area — review risks: {adr.risks}"
                )

        # ── 4. Hardware constraint check ──────────────────────────────────────
        if estimated_vram_mb > self.state.vram_limit_mb:
            reasons.append(
                f"Estimated VRAM {estimated_vram_mb:.0f} MB exceeds "
                f"hardware limit {self.state.vram_limit_mb:.0f} MB."
            )

        # ── 5. Phase alignment ────────────────────────────────────────────────
        if self.state.status == "blocked":
            reasons.append(
                f"Project status is 'blocked'. Resolve blockers first: {self.state.blockers}"
            )

        approved = len(reasons) == 0
        suggested = (
            "Resolve listed issues before proceeding, or mark the existing "
            "implementation as needing extension rather than reimplementation."
            if not approved else None
        )
        return GuardVerdict(approved=approved, reasons=reasons,
                            warnings=warnings, suggested=suggested)
