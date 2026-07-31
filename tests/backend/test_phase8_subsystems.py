"""
Unit tests for Phase 8 sub-components:
  - CostAwarePlanner
  - RepairJournal
  - ExecutionCheckpoint
"""

import pytest
from pathlib import Path

from backend.execution_types import ExecutionNode, NodeCostEstimation
from backend.cost_aware_planner import CostAwarePlanner, PlanCostSummary
from backend.repair_journal import RepairJournal, RepairEntry
from backend.execution_checkpoint import ExecutionCheckpoint


# ── CostAwarePlanner ──────────────────────────────────────────────────────────

class TestCostAwarePlanner:

    def test_safe_plan_within_limits(self):
        planner = CostAwarePlanner()
        nodes = [
            ExecutionNode(
                id="a", name="Indexing",
                estimated_cost=NodeCostEstimation(
                    est_tokens=500, est_vram_mb=1200, est_ram_mb=4000,
                    est_runtime=2.0, est_tool_calls=3, est_risk="LOW"
                )
            ),
            ExecutionNode(
                id="b", name="Patch",
                estimated_cost=NodeCostEstimation(
                    est_tokens=800, est_vram_mb=2000, est_ram_mb=5000,
                    est_runtime=5.0, est_tool_calls=5, est_risk="MEDIUM"
                )
            ),
        ]
        summary = planner.estimate(nodes)

        assert isinstance(summary, PlanCostSummary)
        assert summary.total_tokens == 1300
        assert summary.peak_vram_mb == 2000.0
        assert summary.overall_risk == "MEDIUM"
        assert summary.safe_to_execute is True

    def test_unsafe_plan_exceeds_vram(self):
        planner = CostAwarePlanner()
        nodes = [
            ExecutionNode(
                id="x", name="HeavyInference",
                estimated_cost=NodeCostEstimation(
                    est_tokens=5000, est_vram_mb=5000,   # over the 3800 MB limit
                    est_ram_mb=2000, est_runtime=30.0,
                    est_tool_calls=10, est_risk="HIGH"
                )
            ),
        ]
        summary = planner.estimate(nodes)

        assert summary.safe_to_execute is False
        assert any("VRAM" in w for w in summary.warnings)
        assert summary.overall_risk == "HIGH"


# ── RepairJournal ─────────────────────────────────────────────────────────────

class TestRepairJournal:

    def test_record_and_query_failed_strategies(self):
        journal = RepairJournal()

        journal.record(
            task_id="t1", node_id="n1",
            failure_class="SYNTAX",
            error_message="unexpected indent",
            repair_strategy="auto_indent_fix",
            outcome="FAILURE",
        )
        journal.record(
            task_id="t1", node_id="n1",
            failure_class="SYNTAX",
            error_message="unexpected indent",
            repair_strategy="rewrite_block",
            outcome="SUCCESS",
        )

        failed = journal.failed_strategies("t1", "n1")
        assert "auto_indent_fix" in failed
        assert "rewrite_block" not in failed

    def test_summary_counts(self):
        journal = RepairJournal()
        journal.record("t1", "n1", "IMPORT", "No module named x", "pip_install", "FAILURE")
        journal.record("t1", "n1", "IMPORT", "No module named x", "stub_module", "SUCCESS")

        s = journal.summary()
        assert s["total"] == 2
        assert s["success"] == 1
        assert s["failure"] == 1

    def test_attempt_number_increments(self):
        journal = RepairJournal()
        e1 = journal.record("t2", "n2", "LOGIC", "wrong output", "fix_a", "FAILURE")
        e2 = journal.record("t2", "n2", "LOGIC", "wrong output", "fix_b", "FAILURE")

        assert e1.attempt_number == 1
        assert e2.attempt_number == 2


# ── ExecutionCheckpoint ───────────────────────────────────────────────────────

class TestExecutionCheckpoint:

    def test_save_and_load(self, tmp_path):
        db = tmp_path / "checkpoints.db"
        cp = ExecutionCheckpoint(db)

        cp.save("run1", "node_a", {"status": "done", "lines": 42})

        assert cp.is_complete("run1", "node_a") is True
        result = cp.load("run1", "node_a")
        assert result["status"] == "done"
        assert result["lines"] == 42

        cp.close()

    def test_not_found_returns_none(self, tmp_path):
        cp = ExecutionCheckpoint(tmp_path / "cp.db")
        assert cp.is_complete("run_x", "missing_node") is False
        assert cp.load("run_x", "missing_node") is None
        cp.close()

    def test_completed_nodes_ordering(self, tmp_path):
        cp = ExecutionCheckpoint(tmp_path / "cp.db")
        cp.save("runA", "step1", "result1")
        cp.save("runA", "step2", "result2")
        cp.save("runA", "step3", "result3")

        nodes = cp.completed_nodes("runA")
        assert nodes == ["step1", "step2", "step3"]
        cp.close()

    def test_clear_removes_entries(self, tmp_path):
        cp = ExecutionCheckpoint(tmp_path / "cp.db")
        cp.save("runB", "nodeX", "output")
        assert cp.is_complete("runB", "nodeX") is True
        cp.clear("runB")
        assert cp.is_complete("runB", "nodeX") is False
        cp.close()
