"""
OpenAgent Developer Experience Dashboard & Diagnostics  (Phase 6 — Phase H)

Generates markdown and structured reports covering system health, resource consumption,
environment diagnostics, and capability lists.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from backend.environment_bootstrap import BootstrapResult
    from backend.resource_scheduler import ResourceTelemetry
    from backend.capability_registry import CapabilityRegistry


class DeveloperExperience:
    """
    Consolidates diagnostics and generates a human-readable Dev Experience Dashboard.
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root)
        self.dashboard_path = self.root / "run" / "artifacts" / "dx_dashboard.md"

    def generate_dashboard(
        self,
        bootstrap: BootstrapResult,
        telemetry: ResourceTelemetry,
        registry: CapabilityRegistry,
        perf_metrics: Dict[str, float]
    ) -> str:
        """
        Builds a comprehensive markdown report. Writes it to run/artifacts/dx_dashboard.md
        and returns the markdown content.
        """
        # Determine overall health status
        health_status = "🟢 HEALTHY" if bootstrap.is_healthy else "🔴 DEGRADED"
        
        lines = [
            "# OpenAgent Diagnostics & Developer Dashboard",
            f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"System Status: **{health_status}**\n",
            "## 1. Environment Diagnostics",
            f"- **Python Version**: `{bootstrap.python_version}`",
            f"- **Node.js Version**: `{bootstrap.node_version}`",
            f"- **GPU / VRAM Present**: `{'Yes' if bootstrap.gpu_available else 'No'}`",
            f"- **tmux Available**: `{'Yes' if bootstrap.tmux_available else 'No'}`",
            f"- **Workspace Write Access**: `{'Yes' if bootstrap.workspace_ok else 'No'}`\n",
        ]

        # Port status
        lines.append("### Network Ports Status")
        for port, open_status in bootstrap.ports_available.items():
            status_indicator = "✓ Open (Available)" if open_status else "✗ Blocked (In Use)"
            lines.append(f"- Port `{port}`: {status_indicator}")
        lines.append("")

        # Resource Telemetry
        lines.append("## 2. Resource Telemetry")
        lines.append(f"- **VRAM Usage**: `{telemetry.vram_used_mb} MB` / `3800 MB` max")
        lines.append(f"- **RAM Usage**: `{telemetry.ram_used_mb} MB` / `20000 MB` max")
        lines.append(f"- **CPU Usage**: `{telemetry.cpu_percent}%` used")
        lines.append(f"- **Context Window Size**: `{telemetry.context_size} chars` used\n")

        # Capabilities
        lines.append("## 3. Registered Capabilities")
        for cap_name in ["docker", "gpu.nvidia", "local_model_manager", "browser.playwright", "tmux", "git"]:
            available = registry.has(cap_name)
            status_symbol = "🟢 Available" if available else "🔴 Unavailable"
            lines.append(f"- **{cap_name}**: {status_symbol}")
        lines.append("")

        # Performance Metrics
        lines.append("## 4. Performance Metrics")
        for metric, duration in perf_metrics.items():
            lines.append(f"- **{metric}**: `{duration:.3f}s` latency")
        lines.append("")

        # Critical Errors (if any)
        if bootstrap.critical_errors:
            lines.append("## 🔴 Critical Failures")
            for err in bootstrap.critical_errors:
                lines.append(f"- {err}")
            lines.append("")

        content = "\n".join(lines)
        
        # Write to file
        try:
            self.dashboard_path.parent.mkdir(parents=True, exist_ok=True)
            self.dashboard_path.write_text(content, encoding="utf-8")
        except OSError:
            pass

        return content
