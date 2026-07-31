"""
OpenAgent Environment Bootstrap  (Phase 6 — Phase G)

Verifies runtime environment dependencies (GPU, Python, Node, tmux, ports,
models, and workspace integrity) and generates a diagnostic startup report.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class BootstrapResult:
    python_version:  str
    node_version:    str
    gpu_available:   bool
    tmux_available:  bool
    ports_available: Dict[int, bool]
    workspace_ok:    bool
    critical_errors: List[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return len(self.critical_errors) == 0


class EnvironmentBootstrap:
    """
    Validates host dependencies, permissions, and local binaries before starting OpenAgent.
    """

    def __init__(self, workspace_path: str | Path) -> None:
        self.workspace = Path(workspace_path)

    def run_checks(self) -> BootstrapResult:
        """Executes all diagnostics and returns a BootstrapResult."""
        critical_errors = []

        # 1. Python check
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        # 2. Node check
        node_ver = "not_found"
        try:
            node_ver = subprocess.check_output(["node", "--version"], stderr=subprocess.DEVNULL).decode().strip()
        except (subprocess.SubprocessError, FileNotFoundError):
            critical_errors.append("Node.js binary is missing — required for React frontend.")

        # 3. Tmux check
        tmux_ok = False
        try:
            subprocess.check_output(["tmux", "-V"], stderr=subprocess.DEVNULL)
            tmux_ok = True
        except (subprocess.SubprocessError, FileNotFoundError):
            critical_errors.append("tmux binary is missing — required for terminal multiplexer sandbox.")

        # 4. GPU/VRAM check
        gpu_ok = False
        # Check if nvidia-smi exists
        if shutil.which("nvidia-smi"):
            try:
                subprocess.check_output(["nvidia-smi"], stderr=subprocess.DEVNULL)
                gpu_ok = True
            except subprocess.SubprocessError:
                pass
        # Fallback check for common GPU device paths
        elif Path("/dev/nvidia0").exists() or Path("/dev/dri").exists():
            gpu_ok = True

        # 5. Workspace integrity check
        workspace_ok = False
        if self.workspace.exists() and os.access(self.workspace, os.W_OK):
            workspace_ok = True
        else:
            critical_errors.append(f"Workspace path '{self.workspace}' is unreadable or not writable.")

        # 6. Ports check (verify ports 18000, 19000, 3001)
        ports = [18000, 19000, 3001]
        ports_avail = {}
        for port in ports:
            # We can check port status via a socket bind attempt
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.bind(("127.0.0.1", port))
                ports_avail[port] = True
            except socket.error:
                ports_avail[port] = False
            finally:
                s.close()

        return BootstrapResult(
            python_version=py_ver,
            node_version=node_ver,
            gpu_available=gpu_ok,
            tmux_available=tmux_ok,
            ports_available=ports_avail,
            workspace_ok=workspace_ok,
            critical_errors=critical_errors
        )
