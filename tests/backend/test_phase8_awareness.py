"""
Unit tests for Phase 8.1–8.10 & Project Director
"""

import pytest
from pathlib import Path
from datetime import date

from backend.project_state import ProjectState, ProjectStateStore
from backend.adr_store import ADRStore, ADR
from backend.goal_guard import GoalGuard, GuardVerdict
from backend.session_recovery import SessionRecovery, SessionContext
from backend.drift_detector import DriftDetector, DriftReport
from backend.change_impact import ChangeImpactAnalyzer, ChangeImpact
from backend.engineering_journal import EngineeringJournal, JournalEntry
from backend.rules_engine import RulesEngine, RulesReport
from backend.project_director import ProjectDirector, DirectorDecision


# ── Phase 8.1 — ProjectStateStore ────────────────────────────────────────────

class TestProjectState:

    def test_default_state_created_if_missing(self, tmp_path):
        store = ProjectStateStore(tmp_path / "state.json")
        ps = store.load()
        assert isinstance(ps, ProjectState)
        assert ps.phase == 8
        assert ps.status == "healthy"

    def test_update_persists(self, tmp_path):
        store = ProjectStateStore(tmp_path / "state.json")
        store.load()
        store.update(current_goal="ProjectDirector", phase=9)
        store2 = ProjectStateStore(tmp_path / "state.json")
        ps2 = store2.load()
        assert ps2.current_goal == "ProjectDirector"
        assert ps2.phase == 9

    def test_mark_completed(self, tmp_path):
        store = ProjectStateStore(tmp_path / "state.json")
        store.load()
        store.mark_completed("DriftDetector")
        assert "DriftDetector" in store.state.completed

    def test_blockers(self, tmp_path):
        store = ProjectStateStore(tmp_path / "state.json")
        store.load()
        store.add_blocker("VRAM OOM on 13B model")
        assert "VRAM OOM on 13B model" in store.state.blockers
        store.clear_blocker("VRAM OOM on 13B model")
        assert "VRAM OOM on 13B model" not in store.state.blockers


# ── Phase 8.2 — ADRStore ─────────────────────────────────────────────────────

class TestADRStore:

    def _adr(self, id_: str, title: str) -> ADR:
        return ADR(
            id=id_, title=title, status="accepted",
            decision=f"Use {title}", rationale="Performance and simplicity",
            affected_files=[f"backend/{id_}.py"],
        )

    def test_record_and_retrieve(self, tmp_path):
        store = ADRStore(tmp_path / "adrs.json")
        adr = self._adr("adr-001", "SQLite WAL mode")
        store.record(adr)
        assert store.get("adr-001") is not None
        assert store.get("adr-001").title == "SQLite WAL mode"

    def test_search(self, tmp_path):
        store = ADRStore(tmp_path / "adrs.json")
        store.record(self._adr("adr-002", "incremental indexing"))
        results = store.search("incremental")
        assert len(results) == 1
        assert results[0].id == "adr-002"

    def test_supersede(self, tmp_path):
        store = ADRStore(tmp_path / "adrs.json")
        store.record(self._adr("adr-003", "FAISS vector store"))
        store.record(self._adr("adr-004", "TF-IDF instead of FAISS"))
        store.supersede("adr-003", "adr-004")
        assert store.get("adr-003").status == "superseded"
        assert store.get("adr-003").superseded_by == "adr-004"

    def test_for_file(self, tmp_path):
        store = ADRStore(tmp_path / "adrs.json")
        store.record(self._adr("adr-005", "container IoC"))
        results = store.for_file("backend/adr-005.py")
        assert len(results) == 1


# ── Phase 8.3 — GoalGuard ────────────────────────────────────────────────────

class TestGoalGuard:

    def _state(self) -> ProjectState:
        return ProjectState(
            phase=8, architecture_ver="v3",
            current_goal="test", active_feature=None, active_bug=None,
            git_branch="main", latest_commit="abc", hardware_profile="rtx3050",
            active_model="qwen", vram_limit_mb=3800.0,
            completed=["IoC", "ContextEngine"], blockers=[],
        )

    def test_approved_for_new_goal(self, tmp_path):
        store = ADRStore(tmp_path / "adrs.json")
        guard = GoalGuard(self._state(), store)
        verdict = guard.evaluate("Build new drift detector")
        assert verdict.approved is True

    def test_blocked_for_duplicate(self, tmp_path):
        store = ADRStore(tmp_path / "adrs.json")
        guard = GoalGuard(self._state(), store)
        verdict = guard.evaluate("Rebuild the IoC container from scratch")
        assert verdict.approved is False
        assert any("IoC" in r for r in verdict.reasons)

    def test_blocked_for_vram_excess(self, tmp_path):
        store = ADRStore(tmp_path / "adrs.json")
        guard = GoalGuard(self._state(), store)
        verdict = guard.evaluate("load a new model", estimated_vram_mb=5000)
        assert verdict.approved is False
        assert any("VRAM" in r for r in verdict.reasons)


# ── Phase 8.4 — SessionRecovery ──────────────────────────────────────────────

class TestSessionRecovery:

    def test_recovery_loads_state(self, tmp_path):
        state_store = ProjectStateStore(tmp_path / "state.json")
        adr_store   = ADRStore(tmp_path / "adrs.json")
        recovery    = SessionRecovery(state_store, adr_store, tmp_path)
        ctx = recovery.recover()
        assert ctx.ready is True
        assert ctx.project_state.phase == 8
        assert isinstance(ctx.recovery_notes, list)
        assert len(ctx.recovery_notes) > 0


# ── Phase 8.5 — DriftDetector ────────────────────────────────────────────────

class TestDriftDetector:

    def test_no_drift_on_empty_workspace(self, tmp_path):
        det = DriftDetector(tmp_path)
        report = det.scan()
        # No backend dir → no issues
        assert isinstance(report, DriftReport)

    def test_detects_duplicate_class(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "mod_a.py").write_text("class MyService:\n    pass\n")
        (backend / "mod_b.py").write_text("class MyService:\n    pass\n")
        det = DriftDetector(tmp_path)
        report = det.scan()
        dup_issues = [i for i in report.issues if i.category == "DUPLICATE"]
        assert len(dup_issues) >= 1


# ── Phase 8.6 — ChangeImpactAnalyzer ─────────────────────────────────────────

class TestChangeImpact:

    def test_impact_on_existing_file(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        target = backend / "resource_scheduler.py"
        target.write_text("class ResourceScheduler:\n    def start(self): pass\n")
        (backend / "container.py").write_text(
            "from backend.resource_scheduler import ResourceScheduler\n"
        )
        anal   = ChangeImpactAnalyzer(tmp_path)
        impact = anal.analyze(target)
        assert isinstance(impact, ChangeImpact)
        assert "container.py" in impact.dependent_modules
        assert impact.risk_level in ("LOW", "MEDIUM", "HIGH")


# ── Phase 8.7 — EngineeringJournal ───────────────────────────────────────────

class TestEngineeringJournal:

    def test_log_and_retrieve(self, tmp_path):
        journal = EngineeringJournal(tmp_path / "journal.jsonl")
        entry = journal.log(
            completed=["DriftDetector"],
            decisions=["Use mtime-based scanning"],
            next_steps=["Build ProjectDirector"],
            phase=8,
        )
        assert entry.date == str(date.today())
        assert "DriftDetector" in entry.completed

    def test_persistence_across_instances(self, tmp_path):
        path = tmp_path / "j.jsonl"
        EngineeringJournal(path).log(completed=["Phase8.1"], phase=8)
        j2 = EngineeringJournal(path)
        assert len(j2.all_entries()) == 1
        assert j2.last_next_steps() == []

    def test_recent_returns_last_n(self, tmp_path):
        j = EngineeringJournal(tmp_path / "j.jsonl")
        for i in range(7):
            j.log(completed=[f"task{i}"])
        assert len(j.recent(3)) == 3


# ── Phase 8.10 — RulesEngine ─────────────────────────────────────────────────

class TestRulesEngine:

    def test_backend_file_triggers_test_rule(self):
        engine = RulesEngine()
        report = engine.evaluate("backend/new_service.py")
        assert len(report.violations) > 0
        # Must include test-suite action
        actions = [a for v in report.violations for a in v.actions_needed]
        assert any("pytest" in a for a in actions)

    def test_config_file_triggers_schema_rule(self):
        engine = RulesEngine()
        report = engine.evaluate("config/new_setting.yaml")
        actions = [a for v in report.violations for a in v.actions_needed]
        assert any("schema" in a.lower() or "Pydantic" in a for a in actions)

    def test_summarize_output(self):
        engine = RulesEngine()
        report = engine.evaluate("backend/container.py")
        summary = engine.summarize(report)
        assert "Required actions" in summary or "✓" in summary


# ── Project Director (integration) ───────────────────────────────────────────

class TestProjectDirector:

    def test_start_session_and_handle_request(self, tmp_path):
        director = ProjectDirector(tmp_path)
        ctx = director.start_session()
        assert ctx.ready is True

        decision = director.handle_request("Build a new drift analyser")
        assert isinstance(decision, DirectorDecision)
        # "drift" doesn't match completed items → should be approved
        assert decision.approved is True

    def test_goal_guard_blocks_duplicate_via_director(self, tmp_path):
        director = ProjectDirector(tmp_path)
        director.start_session()
        # "IoC" is in the default completed list
        decision = director.handle_request("rebuild the IoC from scratch")
        assert decision.approved is False
        assert decision.rejection_reason is not None

    def test_record_completion_updates_journal(self, tmp_path):
        director = ProjectDirector(tmp_path)
        director.start_session()
        entry = director.record_completion(
            completed=["ProjectDirector"],
            next_steps=["Phase 9 multi-agent"],
            decisions=["ProjectDirector sits above CognitiveEngine"],
        )
        assert "ProjectDirector" in entry.completed
        assert entry.next_steps == ["Phase 9 multi-agent"]
