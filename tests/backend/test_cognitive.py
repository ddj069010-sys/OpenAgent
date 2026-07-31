"""
Unit tests for Phase 7: CognitiveEngine
"""

import pytest
from pathlib import Path
from backend.cognitive_engine import CognitiveEngine, CognitivePipelineResult
from backend.task_state_machine import TaskState


class TestCognitiveEngine:

    def test_cognitive_pipeline_flow(self, tmp_path):
        engine = CognitiveEngine(str(tmp_path))
        
        # We need run/ folder setup inside workspace
        (tmp_path / "run" / "memory").mkdir(parents=True, exist_ok=True)
        
        # Process a simple "check this" request
        res = engine.process_request("check this file", active_file="main.py")
        
        assert isinstance(res, CognitivePipelineResult)
        assert res.intent_detected == "verify_file:main.py"
        assert res.confidence_score == 0.90
        assert len(res.plan_steps) > 0
        assert res.validation_passed is True
        assert res.execution_result == "Tests Passed"
        
        # Verify the state machine reached COMPLETED
        assert engine.state_machine.state == TaskState.COMPLETED

    def test_general_query_flow(self, tmp_path):
        engine = CognitiveEngine(str(tmp_path))
        (tmp_path / "run" / "memory").mkdir(parents=True, exist_ok=True)
        
        res = engine.process_request("refactor the login screen")
        assert res.intent_detected == "refactor_code"
        assert res.confidence_score == 0.80
        assert res.validation_passed is True
        assert engine.state_machine.state == TaskState.COMPLETED
