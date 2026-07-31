"""
OpenAgent Cost-Aware Planner  (Phase 8)

Before any execution begins, estimates the full cost of the task plan:
tokens, context window, VRAM, RAM, runtime, tool calls, and risk level.

This allows the agent to decide whether to use grep vs. the LLM,
or whether a task is safe to execute within current resource bounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from backend.execution_types import ExecutionNode, NodeCostEstimation


@dataclass
class PlanCostSummary:
    total_tokens:     int
    total_context:    int
    peak_vram_mb:     float
    peak_ram_mb:      float
    estimated_runtime: float
    total_tool_calls: int
    overall_risk:     str          # "LOW" | "MEDIUM" | "HIGH"
    safe_to_execute:  bool
    warnings:         List[str] = field(default_factory=list)


class CostAwarePlanner:
    """
    Pre-flight cost estimator. Inspects the execution graph nodes and
    summarises resource requirements before a single tool is called.
    """

    # Hardware limits for the RTX 3050 Laptop target machine
    VRAM_LIMIT_MB    = 3800.0
    RAM_LIMIT_MB     = 20_000.0
    TOKEN_WARN_LIMIT = 8_000
    RUNTIME_WARN_S   = 60.0

    def estimate(self, nodes: List[ExecutionNode]) -> PlanCostSummary:
        """
        Aggregates NodeCostEstimation across all planned nodes and
        emits a PlanCostSummary with feasibility verdict.
        """
        total_tokens      = 0
        total_context     = 0
        peak_vram         = 0.0
        peak_ram          = 0.0
        total_runtime     = 0.0
        total_tool_calls  = 0
        risk_scores       = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        max_risk          = 0
        warnings: List[str] = []

        for node in nodes:
            c = node.estimated_cost
            total_tokens     += c.est_tokens
            total_context    += c.est_context
            peak_vram         = max(peak_vram, c.est_vram_mb)
            peak_ram          = max(peak_ram,  c.est_ram_mb)
            total_runtime    += c.est_runtime
            total_tool_calls += c.est_tool_calls
            max_risk          = max(max_risk, risk_scores.get(c.est_risk, 0))

        overall_risk = ["LOW", "MEDIUM", "HIGH"][max_risk]
        safe = True

        if peak_vram > self.VRAM_LIMIT_MB:
            warnings.append(
                f"Peak VRAM {peak_vram:.0f} MB exceeds limit {self.VRAM_LIMIT_MB:.0f} MB — "
                "consider smaller model or reduced batch."
            )
            safe = False

        if peak_ram > self.RAM_LIMIT_MB:
            warnings.append(
                f"Peak RAM {peak_ram:.0f} MB exceeds limit {self.RAM_LIMIT_MB:.0f} MB."
            )
            safe = False

        if total_tokens > self.TOKEN_WARN_LIMIT:
            warnings.append(
                f"Estimated {total_tokens} tokens exceeds warn threshold {self.TOKEN_WARN_LIMIT} — "
                "consider compressing context."
            )

        if total_runtime > self.RUNTIME_WARN_S:
            warnings.append(
                f"Estimated runtime {total_runtime:.1f}s may feel slow — consider parallelism."
            )

        return PlanCostSummary(
            total_tokens=total_tokens,
            total_context=total_context,
            peak_vram_mb=peak_vram,
            peak_ram_mb=peak_ram,
            estimated_runtime=total_runtime,
            total_tool_calls=total_tool_calls,
            overall_risk=overall_risk,
            safe_to_execute=safe,
            warnings=warnings,
        )
