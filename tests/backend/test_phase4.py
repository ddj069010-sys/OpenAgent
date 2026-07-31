"""
Unit tests for Phase 4 subsystems:
  ContextEngine, ReflectionEngine, TaskPlanner
(Target Architecture v3 §22, §23, §24)
"""

import pytest
from pathlib import Path

from backend.context_engine import ContextEngine, ContextAssembly
from backend.reflection_engine import (
    ReflectionEngine, FailureClass, RepairDecision,
    RetryBudget,
)
from backend.task_planner import (
    TaskPlanner, PlannedTask, SubTask,
    CyclicDependencyError,
)
from backend.task_state_machine import TaskState
from backend.repository_index import RepositoryIndex


# ═══════════════════════════════════════════════════════════════════════════
# ContextEngine tests
# ═══════════════════════════════════════════════════════════════════════════

class TestContextEngine:

    def _setup(self, tmp_path: Path) -> ContextEngine:
        (tmp_path / "auth.py").write_text(
            "class AuthManager:\n    def login(self, user): pass\n    def logout(self): pass\n"
        )
        (tmp_path / "db.py").write_text(
            "def connect(host): pass\ndef disconnect(): pass\n"
        )
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        return ContextEngine(idx)

    def test_assemble_returns_context_assembly(self, tmp_path):
        engine = self._setup(tmp_path)
        result = engine.assemble("Fix the login bug in AuthManager", tmp_path)
        assert isinstance(result, ContextAssembly)

    def test_assemble_finds_relevant_snippets(self, tmp_path):
        engine = self._setup(tmp_path)
        result = engine.assemble("Fix the login method in AuthManager", tmp_path)
        assert len(result.snippets) > 0

    def test_token_budget_respected(self, tmp_path):
        engine = self._setup(tmp_path)
        result = engine.assemble("fix login", tmp_path)
        assert result.total_chars <= result.budget_chars

    def test_format_prompt_block_empty_on_no_snippets(self, tmp_path):
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        engine = ContextEngine(idx)
        result = engine.assemble("nothing matches this at all xyz123", tmp_path)
        block = engine.format_prompt_block(result)
        assert block == "" or "Context" in block

    def test_keyword_extraction_from_backticks(self, tmp_path):
        engine = self._setup(tmp_path)
        result = engine.assemble("Fix `login` in `AuthManager`", tmp_path)
        assert result.total_chars >= 0  # Just ensure no crash

    def test_summary_includes_request(self, tmp_path):
        engine = self._setup(tmp_path)
        result = engine.assemble("Fix the connect function", tmp_path)
        assert "connect" in result.summary.lower() or "Fix" in result.summary

    def test_small_budget_causes_truncation(self, tmp_path):
        idx = RepositoryIndex(tmp_path)
        idx.index_workspace()
        (tmp_path / "big.py").write_text("def foo(): " + "pass  # comment\n" * 200)
        idx.index_workspace()
        engine = ContextEngine(idx, budget_chars=50)
        result = engine.assemble("fix foo in big", tmp_path)
        assert result.total_chars <= 50


# ═══════════════════════════════════════════════════════════════════════════
# ReflectionEngine tests
# ═══════════════════════════════════════════════════════════════════════════

class TestReflectionEngine:

    def test_classifies_syntax_error(self):
        engine = ReflectionEngine(max_attempts=3)
        decision = engine.reflect("SyntaxError: invalid syntax at line 42", "t1")
        assert decision.failure_class == FailureClass.SYNTAX_ERROR
        assert decision.should_retry is True

    def test_classifies_import_error(self):
        engine = ReflectionEngine(max_attempts=3)
        decision = engine.reflect("ModuleNotFoundError: No module named 'requests'", "t2")
        assert decision.failure_class == FailureClass.IMPORT_ERROR
        assert decision.strategy == "resolve_import_and_install"

    def test_classifies_context_exceeded(self):
        engine = ReflectionEngine(max_attempts=3)
        decision = engine.reflect("ContextWindowExceededError: prompt is too long", "t3")
        assert decision.failure_class == FailureClass.CONTEXT_EXCEEDED
        assert decision.strategy == "compress_context_and_retry"

    def test_classifies_test_failure(self):
        engine = ReflectionEngine(max_attempts=3)
        decision = engine.reflect("FAILED tests/test_auth.py::test_login - AssertionError", "t4")
        assert decision.failure_class == FailureClass.TEST_FAILURE
        assert decision.strategy == "diagnose_failing_tests_and_patch"

    def test_budget_exhaustion_triggers_escalation(self):
        engine = ReflectionEngine(max_attempts=2)
        engine.reflect("SyntaxError: invalid syntax", "t5")
        engine.reflect("SyntaxError: invalid syntax", "t5")
        decision = engine.reflect("SyntaxError: invalid syntax", "t5")
        assert decision.should_escalate is True
        assert decision.should_retry is False

    def test_repeated_failure_detection(self):
        engine = ReflectionEngine(max_attempts=5)
        engine.reflect("SyntaxError: invalid syntax at line 42", "t6")
        engine.reflect("SyntaxError: invalid syntax at line 42", "t6")
        decision = engine.reflect("SyntaxError: invalid syntax at line 42", "t6")
        assert decision.failure_class == FailureClass.REPEATED_FAILURE

    def test_reset_clears_budget(self):
        engine = ReflectionEngine(max_attempts=2)
        engine.reflect("SyntaxError", "t7")
        engine.reflect("SyntaxError", "t7")
        engine.reset("t7")
        decision = engine.reflect("SyntaxError", "t7")
        assert decision.should_retry is True

    def test_quality_score_decreases_with_failures(self):
        engine = ReflectionEngine(max_attempts=4)
        score_before = engine.quality_score("t8")
        engine.reflect("error", "t8")
        score_after = engine.quality_score("t8")
        assert score_before > score_after
        assert 0.0 <= score_after <= 1.0

    def test_unknown_error_escalates(self):
        engine = ReflectionEngine(max_attempts=3)
        decision = engine.reflect("", "t9")
        assert decision.failure_class == FailureClass.UNKNOWN
        assert decision.should_escalate is True


# ═══════════════════════════════════════════════════════════════════════════
# TaskPlanner tests
# ═══════════════════════════════════════════════════════════════════════════

class TestTaskPlanner:

    def _simple_plan(self) -> list[PlannedTask]:
        a = PlannedTask(id="a", description="Setup env", priority=1)
        b = PlannedTask(id="b", description="Write code", priority=2, depends_on=["a"])
        c = PlannedTask(id="c", description="Run tests", priority=3, depends_on=["b"])
        return [a, b, c]

    def test_build_returns_ordered_tasks(self):
        planner = TaskPlanner()
        ordered = planner.build(self._simple_plan())
        ids = [t.id for t in ordered]
        assert ids.index("a") < ids.index("b") < ids.index("c")

    def test_initial_ready_tasks_are_roots(self):
        planner = TaskPlanner()
        planner.build(self._simple_plan())
        ready = planner.ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "a"

    def test_completing_task_unblocks_dependents(self):
        planner = TaskPlanner()
        planner.build(self._simple_plan())
        planner.transition("a", TaskState.PLANNING)
        planner.transition("a", TaskState.RUNNING)
        planner.mark_complete("a")
        ready = planner.ready_tasks()
        assert ready[0].id == "b"

    def test_cyclic_dependency_raises(self):
        a = PlannedTask(id="a", description="A", depends_on=["b"])
        b = PlannedTask(id="b", description="B", depends_on=["a"])
        planner = TaskPlanner()
        with pytest.raises(CyclicDependencyError):
            planner.build([a, b])

    def test_unknown_dependency_raises(self):
        a = PlannedTask(id="a", description="A", depends_on=["nonexistent"])
        planner = TaskPlanner()
        with pytest.raises(ValueError):
            planner.build([a])

    def test_is_complete_when_all_terminal(self):
        planner = TaskPlanner()
        planner.build([PlannedTask(id="a", description="A")])
        assert not planner.is_complete()
        planner.transition("a", TaskState.PLANNING)
        planner.transition("a", TaskState.RUNNING)
        planner.mark_complete("a")
        assert planner.is_complete()

    def test_summary_counts_by_state(self):
        planner = TaskPlanner()
        planner.build(self._simple_plan())
        s = planner.summary()
        assert s["total"] == 3
        assert "queued" in s["by_state"]
        assert s["complete"] is False

    def test_priority_ordering_in_ready_tasks(self):
        # Two independent tasks — lower priority number = served first
        a = PlannedTask(id="a", description="A", priority=5)
        b = PlannedTask(id="b", description="B", priority=1)
        planner = TaskPlanner()
        planner.build([a, b])
        ready = planner.ready_tasks()
        assert ready[0].id == "b"   # priority 1 before priority 5

    def test_mark_failed_transitions_correctly(self):
        planner = TaskPlanner()
        planner.build([PlannedTask(id="a", description="A")])
        planner.mark_failed("a")
        assert planner.get("a").state == TaskState.FAILED
        assert planner.get("a").is_terminal
