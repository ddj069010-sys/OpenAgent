"""
OpenAgent Intelligent Cognitive Layer & Adaptive Execution Engine (Phase 7 — Phases A to J)

The brain of the agent. Orchestrates intent analysis, repository context selection,
tool selection, multi-stage planning, validation, and long-term memory updates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pathlib import Path

from backend.container import container
from backend.task_state_machine import TaskStateMachine, TaskState
from backend.task_planner import TaskPlanner, PlannedTask
from backend.reflection_engine import ReflectionEngine, FailureClass


@dataclass
class CognitivePipelineResult:
    intent_detected:  str
    confidence_score: float
    plan_steps:       List[str]
    context_size:     int
    validation_passed: bool
    reflection_notes: Optional[str] = None
    execution_result: Any = None


class CognitiveEngine:
    """
    Executes user requests through the structured 13-stage reasoning loop:
    User → Intent → Classifier → Repo → Capabilities → Context → Planner
         → Resource Scheduler → Tool → Execution → Validation → Reflection → Memory
    """

    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = workspace_root
        self.state_machine = TaskStateMachine(task_id="cognitive_goal_root")

    def process_request(self, user_request: str, active_file: Optional[str] = None) -> CognitivePipelineResult:
        """Runs the entire cognitive processing pipeline."""
        self.state_machine.transition(TaskState.PLANNING)
        
        # 1. Intent Analyzer & Task Classifier (Phase A & F)
        intent, confidence = self._analyze_intent(user_request, active_file)
        
        # 2. Repository Awareness (Phase B)
        repo_idx = container.resolve("RepositoryIndex")(self.workspace_root)
        repo_idx.index_workspace()
        
        # 3. Context Selection (Phase C)
        ctx_engine = container.resolve("ContextEngine")(repo_idx)
        ctx_result = ctx_engine.assemble(user_request, str(repo_idx._root))
        
        # 4. Multi-Stage Planning (Phase E)
        planner = container.resolve("TaskPlanner")
        plan_nodes = [
            PlannedTask(id="t1", description=f"Analyze and resolve: {intent}", priority=1),
            PlannedTask(id="t2", description="Verify file modifications and run validations", priority=2, depends_on=["t1"])
        ]
        planner.build(plan_nodes)
        plan_steps = [n.description for n in planner.ready_tasks()]

        # 5. Resource Scheduling & Tool Selection (Phase D)
        scheduler = container.resolve("ResourceScheduler")
        # Run in resource-controlled scope
        scheduler.start_inference()
        
        tool = self._select_tool(intent)
        
        # Simulating Tool Execution
        self.state_machine.transition(TaskState.RUNNING)
        exec_out = self._execute_tool(tool, active_file)
        
        scheduler.end_inference()

        # 6. Validation (Phase G)
        self.state_machine.transition(TaskState.TESTING)
        valid = self._validate_execution(exec_out)

        # 7. Reflection & Recovery (Phase H)
        ref_notes = None
        if not valid:
            self.state_machine.transition(TaskState.REPAIRING)
            reflection_engine = container.resolve("ReflectionEngine")
            ref_notes = reflection_engine.reflect(
                raw_output="Validation failed: syntax or test failure in mock execute.",
                task_id="t1"
            )
        
        # 8. Memory Update (Phase I & J)
        memory = container.resolve("PersistentMemory")(Path(self.workspace_root) / "run" / "memory" / "experience.json")
        memory.add(
            category="successful_patch" if valid else "failed_approach",
            content=f"Request: {user_request} | Intent: {intent} | Validation: {valid}",
            metadata={"confidence": confidence}
        )

        self.state_machine.transition(TaskState.COMPLETED if valid else TaskState.FAILED)

        return CognitivePipelineResult(
            intent_detected=intent,
            confidence_score=confidence,
            plan_steps=plan_steps,
            context_size=len(ctx_result.snippets),
            validation_passed=valid,
            reflection_notes=ref_notes,
            execution_result=exec_out
        )

    # ── Internal Helpers ──────────────────────────────────────────────────────

    def _analyze_intent(self, request: str, active_file: Optional[str]) -> tuple[str, float]:
        """Infers user intent and calculates confidence metrics (Phase A/F)."""
        req_lower = request.lower()
        confidence = 0.90
        
        if active_file and ("check this" in req_lower or "test this" in req_lower):
            return f"verify_file:{active_file}", confidence
        elif "fix" in req_lower or "bug" in req_lower:
            return "bug_fix", 0.85
        elif "refactor" in req_lower:
            return "refactor_code", 0.80
        return "general_query", 0.70

    def _select_tool(self, intent: str) -> str:
        """Determines the appropriate tool based on inferred intent (Phase D)."""
        if intent.startswith("verify_file"):
            return "run_tests"
        if intent == "bug_fix":
            return "apply_patch"
        return "view_file"

    def _execute_tool(self, tool: str, file_path: Optional[str]) -> str:
        # Mock execution logic returning success indicators
        if tool == "run_tests":
            return "Tests Passed"
        return "Success"

    def _validate_execution(self, output: str) -> bool:
        # Simplistic validation check
        return "Success" in output or "Passed" in output
