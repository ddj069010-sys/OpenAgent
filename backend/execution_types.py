"""
OpenAgent Execution Types  (Phase 8)

Shared dataclasses for the execution graph layer.
Kept in a separate module to prevent circular imports between
autonomous_execution_framework and cost_aware_planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Set

from backend.task_state_machine import TaskState


@dataclass
class NodeCostEstimation:
    est_tokens:     int   = 0
    est_context:    int   = 0
    est_vram_mb:    float = 0.0
    est_ram_mb:     float = 0.0
    est_runtime:    float = 0.0
    est_tool_calls: int   = 0
    est_risk:       str   = "LOW"   # "LOW" | "MEDIUM" | "HIGH"


@dataclass
class ExecutionNode:
    id:                    str
    name:                  str
    depends_on:            Set[str]           = field(default_factory=set)
    estimated_cost:        NodeCostEstimation  = field(default_factory=NodeCostEstimation)
    required_capabilities: List[str]           = field(default_factory=list)
    state:                 TaskState           = TaskState.QUEUED
    retry_count:           int                 = 0
    max_retries:           int                 = 3
    action:                Optional[Callable[[], Any]] = None
    result:                Optional[Any]        = None
    error:                 Optional[str]        = None
