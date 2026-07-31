"""
Unit tests for Phase 8: AutonomousExecutionFramework
"""

import pytest
from backend.autonomous_execution_framework import (
    AutonomousExecutionFramework,
    ContextCompressor,
    ExecutionGraph,
    ExecutionNode,
    SkillType,
    SelfEvaluationReport
)
from backend.task_state_machine import TaskState, TaskStateMachine
from backend.reflection_engine import ReflectionEngine


class TestAutonomousExecutionFramework:

    def test_context_compressor(self):
        compressor = ContextCompressor(token_budget=100)
        
        snippets = [
            {"content": "def function_one(): pass", "score": 0.95},
            {"content": "class AnotherClass: pass" * 100, "score": 0.50},  # long snippet
            {"content": "def function_three(): pass", "score": 0.80}
        ]
        
        compressed = compressor.compress(snippets)
        
        # Verify it ranked by score and excluded the long snippet to stay within budget
        assert len(compressed) == 2
        assert compressed[0]["content"] == "def function_one(): pass"
        assert compressed[1]["content"] == "def function_three(): pass"

    def test_execution_graph_dependency_resolution(self):
        graph = ExecutionGraph()
        
        state_machine = TaskStateMachine(task_id="graph_test")
        reflection = ReflectionEngine()
        
        # Add dependency chain
        node1 = ExecutionNode(id="step1", name="Step One", action=lambda: "done1")
        node2 = ExecutionNode(id="step2", name="Step Two", depends_on={"step1"}, action=lambda: "done2")
        
        graph.add_node(node1)
        graph.add_node(node2)
        
        success = graph.execute_all(state_machine, reflection)
        
        assert success is True
        assert node1.state == TaskState.COMPLETED
        assert node2.state == TaskState.COMPLETED
        assert state_machine.state == TaskState.COMPLETED

    def test_framework_skill_pipelines(self):
        framework = AutonomousExecutionFramework(workspace_root="/tmp")
        
        # Execute architectural pipeline
        report_arch = framework.execute_skill(SkillType.ARCHITECTURE, "analyze database flow")
        assert isinstance(report_arch, SelfEvaluationReport)
        assert report_arch.planning_quality == 0.95
        assert report_arch.failure_reason is None
        
        # Execute debugging pipeline
        report_debug = framework.execute_skill(SkillType.DEBUGGING, "fix login test failure")
        assert report_debug.planning_quality == 0.95
        assert report_debug.confidence == 0.88
