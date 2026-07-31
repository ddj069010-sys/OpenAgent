"""
OpenAgent Performance Profiler  (Phase 6 — Phase I)

Provides microsecond-accurate operation timing, execution count tracking,
and bottleneck reporting for critical subsystems.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ProfileRecord:
    operation:       str
    calls:           int = 0
    total_time:      float = 0.0
    min_time:        float = float("inf")
    max_time:        float = 0.0

    @property
    def avg_time(self) -> float:
        return self.total_time / self.calls if self.calls > 0 else 0.0


class PerformanceProfiler:
    """
    Measures latency bottlenecks across core tasks and suggests improvements.
    """

    def __init__(self) -> None:
        self.records: Dict[str, ProfileRecord] = {}

    def record_time(self, operation: str, duration: float) -> None:
        """Saves a duration measurement for the operation."""
        rec = self.records.get(operation)
        if not rec:
            rec = ProfileRecord(operation=operation)
            self.records[operation] = rec

        rec.calls += 1
        rec.total_time += duration
        rec.min_time = min(rec.min_time, duration)
        rec.max_time = max(rec.max_time, duration)

    def generate_report(self) -> str:
        """
        Creates a readable markdown report summarizing current timing logs and
        identifying potential performance improvements.
        """
        lines = [
            "# OpenAgent Subsystems Performance Profile",
            "| Operation | Calls | Avg Time (s) | Min (s) | Max (s) | Total (s) |",
            "| :--- | :---: | :---: | :---: | :---: | :---: |"
        ]

        bottlenecks = []

        for name, rec in sorted(self.records.items(), key=lambda x: x[1].total_time, reverse=True):
            lines.append(
                f"| {name} | {rec.calls} | {rec.avg_time:.4f} | {rec.min_time:.4f} | "
                f"{rec.max_time:.4f} | {rec.total_time:.4f} |"
            )
            # Flag operations taking over 500ms on average as bottlenecks
            if rec.avg_time > 0.5:
                bottlenecks.append(name)

        lines.append("")
        lines.append("## Bottlenecks & Recommendations")
        if not bottlenecks:
            lines.append("- No major performance bottlenecks detected. System is running optimized.")
        else:
            for b in bottlenecks:
                lines.append(f"- **{b}** has high latency. Consider caching, indexing filters, or parallel execution.")

        return "\n".join(lines)
