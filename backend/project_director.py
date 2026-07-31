"""
OpenAgent Project Director  (Phase 8.1–8.10 Coordinator)

Top-level coordinator. Sits above the Cognitive Engine and the LLM.
Its only job is to maintain continuity, enforce engineering rules, prevent
drift, and decide what needs to happen next.

It NEVER calls the LLM directly. It prepares a fully enriched context
packet that the Cognitive Engine and LLM receive.

Architecture:
    User Request
        ↓
    Project Director          ← this module
        ├─ Session Recovery   (8.4) — load state without repo scan
        ├─ Project State      (8.1) — current phase / goals / blockers
        ├─ Goal Guard         (8.3) — reject duplicates / hardware violations
        ├─ ADR Store          (8.2) — check prior architectural decisions
        ├─ Rules Engine       (8.10) — fire deterministic rules
        ├─ Change Impact      (8.6) — estimate blast radius
        ├─ Drift Detector     (8.5) — catch architectural drift
        ├─ Engineering Journal(8.7) — record decisions
        └─ Execution Framework (8) — delegate execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from backend.project_state import ProjectState, ProjectStateStore
from backend.adr_store import ADRStore
from backend.goal_guard import GoalGuard, GuardVerdict
from backend.session_recovery import SessionRecovery, SessionContext
from backend.drift_detector import DriftDetector, DriftReport
from backend.change_impact import ChangeImpactAnalyzer, ChangeImpact
from backend.engineering_journal import EngineeringJournal, JournalEntry
from backend.rules_engine import RulesEngine, RulesReport


@dataclass
class DirectorDecision:
    """Fully enriched context packet handed to the Cognitive Engine."""
    approved:        bool
    session_context: SessionContext
    project_state:   ProjectState
    guard_verdict:   GuardVerdict
    rules_report:    RulesReport
    drift_report:    Optional[DriftReport]  = None
    impact:          Optional[ChangeImpact] = None
    rejection_reason: Optional[str]        = None
    pre_exec_notes:  List[str]             = field(default_factory=list)


class ProjectDirector:
    """
    Single entry-point for all incoming requests.
    Enforces the Pre-Execution Checklist (8.9) deterministically.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root)

        # ── Persistent stores ─────────────────────────────────────────────────
        run_dir = self.root / "run" / "state"
        self.state_store = ProjectStateStore(run_dir / "project_state.json")
        self.adr_store   = ADRStore(run_dir / "adrs.json")
        self.journal     = EngineeringJournal(run_dir / "journal.jsonl")

        # ── Stateless analyzers ───────────────────────────────────────────────
        self.rules_engine = RulesEngine()
        self.drift_det    = DriftDetector(self.root)
        self.impact_anal  = ChangeImpactAnalyzer(self.root)
        self.recovery     = SessionRecovery(self.state_store, self.adr_store, self.root)

        # ── Bootstrap: recover session on every instantiation ─────────────────
        self._session: Optional[SessionContext] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start_session(self) -> SessionContext:
        """
        Phase 8.4 — fast session recovery.
        Call once at the start of every session.
        """
        self._session = self.recovery.recover()
        return self._session

    def handle_request(
        self,
        goal:            str,
        target_file:     Optional[str] = None,
        estimated_vram:  float         = 0.0,
        run_drift_scan:  bool          = False,
    ) -> DirectorDecision:
        """
        Phase 8.9 Pre-Execution Checklist:
          1. Load session context
          2. Evaluate Goal Guard
          3. Apply Rules Engine
          4. Estimate Change Impact (if target_file given)
          5. Optionally run Drift Detector
          6. Return DirectorDecision to Cognitive Engine
        """
        notes: List[str] = []

        # ── Step 1: Ensure session is loaded ──────────────────────────────────
        ctx = self._session or self.start_session()
        ps  = ctx.project_state

        # ── Step 2: Goal Guard ────────────────────────────────────────────────
        guard   = GoalGuard(ps, self.adr_store)
        verdict = guard.evaluate(goal, estimated_vram_mb=estimated_vram)
        notes.append(f"GoalGuard: {'✓ approved' if verdict.approved else '✗ blocked'}")

        if not verdict.approved:
            return DirectorDecision(
                approved         = False,
                session_context  = ctx,
                project_state    = ps,
                guard_verdict    = verdict,
                rules_report     = RulesReport(),
                rejection_reason = "; ".join(verdict.reasons),
                pre_exec_notes   = notes,
            )

        # ── Step 3: Rules Engine ──────────────────────────────────────────────
        rules_file = target_file or f"backend/{goal.lower().replace(' ', '_')}.py"
        rules_rpt  = self.rules_engine.evaluate(rules_file)
        notes.append(self.rules_engine.summarize(rules_rpt))

        # ── Step 4: Change Impact ─────────────────────────────────────────────
        impact: Optional[ChangeImpact] = None
        if target_file:
            full_path = self.root / target_file
            if full_path.exists():
                impact = self.impact_anal.analyze(full_path)
                notes.append(impact.summary)

        # ── Step 5: Drift scan (optional — run post-change, not pre) ──────────
        drift: Optional[DriftReport] = None
        if run_drift_scan:
            drift = self.drift_det.scan()
            if not drift.clean:
                notes.append(
                    f"⚠ Drift detected: {len(drift.issues)} issues "
                    f"({sum(1 for i in drift.issues if i.severity == 'ERROR')} errors)"
                )

        return DirectorDecision(
            approved        = True,
            session_context = ctx,
            project_state   = ps,
            guard_verdict   = verdict,
            rules_report    = rules_rpt,
            drift_report    = drift,
            impact          = impact,
            pre_exec_notes  = notes,
        )

    def record_completion(
        self,
        completed: List[str],
        next_steps: List[str],
        decisions:  List[str] | None = None,
        issues:     List[str] | None = None,
    ) -> JournalEntry:
        """
        Phase 8.7 — update journal and project state after task completion.
        """
        ps = self.state_store.state or self.state_store.load()
        for item in completed:
            self.state_store.mark_completed(item)

        entry = self.journal.log(
            completed  = completed,
            issues     = issues or [],
            decisions  = decisions or [],
            next_steps = next_steps,
            phase      = ps.phase,
        )
        if next_steps:
            self.state_store.update(next_step=next_steps[0])
        return entry
